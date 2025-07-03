import numpy as np
from scipy.spatial import KDTree
import os
import math
import logging
import json
import traceback

from django.conf import settings
from django.core.cache import cache 
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
import openrouteservice
import polyline

from .models import FuelStation

logger = logging.getLogger(__name__)

FUEL_STATIONS_DATA = []
FUEL_STATIONS_KDTREE = None
FUEL_STATIONS_COORDS = None


FUEL_STATIONS_CACHE_KEY = "all_fuel_stations_data_and_kdtree"

# load fuel prices and cache them on redis

def load_fuel_prices():
    """
    Loads fuel prices from the database into memory and builds a KDTree for spatial querying.
    Attempts to load from Redis cache first.
    """
    global FUEL_STATIONS_DATA, FUEL_STATIONS_KDTREE, FUEL_STATIONS_COORDS

    if FUEL_STATIONS_DATA and FUEL_STATIONS_KDTREE is not None:
        logger.info("Fuel station data already loaded into application memory.")
        return FUEL_STATIONS_DATA

    #we try to  load from Redis cache since its in-memory, hence low latency
    cached_data_kdtree = cache.get(FUEL_STATIONS_CACHE_KEY)

    if cached_data_kdtree:
        try:
            FUEL_STATIONS_DATA = cached_data_kdtree['data']
            FUEL_STATIONS_COORDS = cached_data_kdtree['coords']
            # we reconstruct KDTree from numpy array (KDimensional Tree object itself is not directly picklable by default)
            FUEL_STATIONS_KDTREE = KDTree(FUEL_STATIONS_COORDS)
            logger.info(f"Loaded {len(FUEL_STATIONS_DATA)} fuel stations from Redis cache.")
            return FUEL_STATIONS_DATA
        except Exception as e:
            logger.error(f"Error loading fuel station data from Redis cache, reloading from DB: {e}", exc_info=True)
           

    # if data is not in cache, or cache load failed, load from Database
    data = []
    coords = []
    try:
        stations = FuelStation.objects.all()
        for station in stations:
            if station.latitude is not None and station.longitude is not None:
                data.append({
                    'name': station.truckstop_name,
                    'city': station.city,
                    'state': station.state,
                    'latitude': station.latitude,
                    'longitude': station.longitude,
                    'fuel_price_per_gallon': station.retail_price,
                    'db_id': station.pk
                })
                coords.append([station.latitude, station.longitude])
            else:
                logger.warning(f"Skipping station {station.pk} due to missing lat/lon in DB: {station.truckstop_name}")

        FUEL_STATIONS_DATA = data
        FUEL_STATIONS_COORDS = np.array(coords)
        FUEL_STATIONS_KDTREE = KDTree(FUEL_STATIONS_COORDS)

        logger.info(f"Loaded {len(FUEL_STATIONS_DATA)} fuel stations from database and built KDTree.")

        
        # we store the raw data and coordinates, and rebuild the KDTree on retrieval.
        data_to_cache = {
            'data': FUEL_STATIONS_DATA,
             #convert numpy array to list for JSON serialization
            'coords': FUEL_STATIONS_COORDS.tolist()
        }
        cache.set(FUEL_STATIONS_CACHE_KEY, data_to_cache)
        logger.info(f"Stored {len(FUEL_STATIONS_DATA)} fuel stations in Redis cache.")

    except Exception as e:
        logger.error(f"Critical error loading fuel prices from database: {e}", exc_info=True)
        FUEL_STATIONS_DATA = []
        FUEL_STATIONS_KDTREE = None
        FUEL_STATIONS_COORDS = None
    return data

# we call the loading of fuel prices upon the app starting
load_fuel_prices()


#  OpenRouteService Integration
ors_client = openrouteservice.Client(key=os.getenv("ORS_API_KEY") or settings.ORS_API_KEY,timeout=180)
geolocator = Nominatim(user_agent="fuel_optimizer_app_route_finder")

def get_coordinates_from_location_name(location_name):
    """converting a location name (e.g., 'Nairobi, Kenya') to (latitude, longitude)."""
    
    cache_key = f"geocode:{location_name}"
    cached_coords = cache.get(cache_key)
    if cached_coords:
        logger.debug(f"Cache Hit for geocode: {location_name}")
        return tuple(cached_coords)

    try:
        location = geolocator.geocode(f"{location_name}, USA", timeout=5)
        if location:
            coords = (location.latitude, location.longitude)
            #cache geocode for 1 week to reduce latency for frequently fetched data
            cache.set(cache_key, coords, timeout=60 * 60 * 24 * 7) 
            logger.debug(f"Geocoded '{location_name}' to {coords}, cached.")
            return coords
        logger.warning(f"Geocoding returned no result for: {location_name}")
        return None
    except Exception as e:
        logger.error(f"Error geocoding '{location_name}': {e}")
        return None

def get_route_data(start_coords, end_coords, transport_mode='driving-car'):
    """
    Fetches route data from OpenRouteService.
    start_coords and end_coords are (latitude, longitude) tuples.
    Returns route geometry (list of [lat, lon] pairs), total distance in miles.
    """
    if start_coords is None or end_coords is None:
        logger.error("Invalid coordinates provided to get_route_data. Cannot fetch route.")
        return None, 0, 0

    #cache key for ORS route data with its relevant params
    
    route_cache_key = f"ors_route:{start_coords[0]},{start_coords[1]}-{end_coords[0]},{end_coords[1]}-{transport_mode}"
    cached_route_data = cache.get(route_cache_key)

    if cached_route_data:
        logger.debug(f"Cache Hit for ORS route: {start_coords} to {end_coords}")
        return (
            cached_route_data['route_geometry'],
            cached_route_data['total_distance_miles'],
            cached_route_data['total_duration_seconds']
        )

    try:
        coords = [(start_coords[1], start_coords[0]), (end_coords[1], end_coords[0])]

        routes = ors_client.directions(
            coordinates=coords,
            profile=transport_mode,
            format='json',
            units='mi',
            geometry=True,
            instructions=False,
            radiuses=[-1, -1]
        )

        if not routes or not routes.get('routes'):
            logger.warning(f"No route found for {start_coords} to {end_coords}. ORS Response: {routes}")
            return None, 0, 0

        encoded_geometry = routes['routes'][0]['geometry']
        raw_route_geometry = polyline.decode(encoded_geometry)
        route_geometry = [list(coord) for coord in raw_route_geometry]

        total_distance_miles = routes['routes'][0]['summary']['distance']
        total_duration_seconds = routes['routes'][0]['summary']['duration']

        # store the ORS response in cache
        data_to_cache = {
            'route_geometry': route_geometry,
            'total_distance_miles': total_distance_miles,
            'total_duration_seconds': total_duration_seconds
        }
        # caching ORS data can be cached for a reasonable time not to overload our redis
        cache.set(route_cache_key, data_to_cache, timeout=60 * 60 * 24)
        logger.debug(f"Stored ORS route for {start_coords} to {end_coords} in cache.")

        return route_geometry, total_distance_miles, total_duration_seconds

    except openrouteservice.exceptions.ApiError as e:
        logger.error(f"OpenRouteService API Error: {e.args[0] if e.args else e}")
        return None, 0, 0
    except Exception as e:
        logger.error(f"Error fetching route data: {e}", exc_info=True)
        return None, 0, 0

# calculating  Fueling 

VEHICLE_RANGE_MILES = 500
MILES_PER_GALLON = 10
FUEL_BUFFER_MILES = 50

def find_optimal_fuel_stops(route_geometry, total_distance_miles, start_coords, end_coords):
   
    """
    Calculates optimal fuel stops along the route.
    route_geometry: List of [lat, lon] points defining the path.
    total_distance_miles: Total route distance.
    start_coords: (lat, lon) of the trip start.
    end_coords: (lat, lon) of the trip end.
    """
    if not route_geometry or not FUEL_STATIONS_DATA or FUEL_STATIONS_KDTREE is None:
        logger.warning("No route geometry, no fuel station data, or KDTree not initialized for fueling calculation.")
        return [], 0, 0 

    optimal_stops = []
    current_location = list(start_coords)
    current_fuel_cost = 0.0
    current_range = VEHICLE_RANGE_MILES

    initial_fill_cost = (VEHICLE_RANGE_MILES / MILES_PER_GALLON) * _get_average_fuel_price()
    current_fuel_cost += initial_fill_cost
    logger.info(f"Initial tank filled. Cost: {initial_fill_cost:.2f}.")

    min_dist_to_start_rp = float('inf')
    current_route_point_idx = 0
    for idx, rp_coords in enumerate(route_geometry):
        dist = geodesic(start_coords, tuple(rp_coords)).miles
        if dist < min_dist_to_start_rp:
            min_dist_to_start_rp = dist
            current_route_point_idx = idx

    distance_traversed_along_route = 0.0
    for i in range(current_route_point_idx):
        distance_traversed_along_route += geodesic(
            tuple(route_geometry[i]),
            tuple(route_geometry[i+1])
        ).miles

    total_trip_duration_seconds = 0

    while distance_traversed_along_route < total_distance_miles:
        effective_range_for_planning = current_range - FUEL_BUFFER_MILES

        dist_to_end_from_current_point = 0.0
        for i in range(current_route_point_idx, len(route_geometry) - 1):
            dist_to_end_from_current_point += geodesic(
                tuple(route_geometry[i]),
                tuple(route_geometry[i+1])
            ).miles

        if current_range >= dist_to_end_from_current_point:
            logger.info(f"Sufficient fuel to reach destination. Remaining distance: {dist_to_end_from_current_point:.2f} miles.")
            break

        candidate_stops_for_leg = []

        lookahead_route_points_for_query = []
        distance_on_route_segment_lookahead = 0.0
        for i in range(current_route_point_idx, len(route_geometry)):
            point = route_geometry[i]
            if i > current_route_point_idx:
                distance_on_route_segment_lookahead += geodesic(
                    tuple(route_geometry[i-1]),
                    tuple(point)
                ).miles

            lookahead_route_points_for_query.append(point)

            if distance_on_route_segment_lookahead > effective_range_for_planning:
                break

        search_radius_miles = 50
        search_radius_degrees = search_radius_miles / 69.0

        unique_candidate_station_indices = set()
        for route_point_coords in lookahead_route_points_for_query:
            indices = FUEL_STATIONS_KDTREE.query_ball_point(route_point_coords, r=search_radius_degrees)
            unique_candidate_station_indices.update(indices)

        if not unique_candidate_station_indices:
            logger.warning("No fuel stations found near the current lookahead route segment. Cannot proceed.")
            break

        viable_candidates = []
        for station_idx in unique_candidate_station_indices:
            station = FUEL_STATIONS_DATA[station_idx]
            station_coords = (station['latitude'], station['longitude'])

            dist_to_candidate_station = geodesic(tuple(current_location), station_coords).miles

            if dist_to_candidate_station > current_range:
                continue

            min_dist_to_rp = float('inf')
            closest_route_point_on_route_idx = -1
            for rp_idx, rp_coords in enumerate(route_geometry):
                dist = geodesic(station_coords, tuple(rp_coords)).miles
                if dist < min_dist_to_rp:
                    min_dist_to_rp = dist
                    closest_route_point_on_route_idx = rp_idx

            if (min_dist_to_rp < search_radius_miles and
                closest_route_point_on_route_idx >= current_route_point_idx):

                detour_dist = geodesic(tuple(current_location), station_coords).miles + \
                              geodesic(station_coords, tuple(route_geometry[closest_route_point_on_route_idx])).miles

                if detour_dist <= current_range:
                    viable_candidates.append({
                        'station': station,
                        'dist_from_current_location': dist_to_candidate_station,
                        'price': station['fuel_price_per_gallon'],
                        'route_point_idx': closest_route_point_on_route_idx,
                        'detour_distance': detour_dist
                    })

        if not viable_candidates:
            logger.error("No *viable* (reachable and on-route) fuel stations found for the current leg.")
            break

        viable_candidates.sort(key=lambda x: (x['price'], -x['route_point_idx']))

        chosen_stop = viable_candidates[0]
        chosen_station = chosen_stop['station']
        detour_dist_for_chosen_stop = chosen_stop['detour_distance']

        fuel_consumed_for_detour_gallons = detour_dist_for_chosen_stop / MILES_PER_GALLON
        current_range -= detour_dist_for_chosen_stop

        fuel_needed_for_fillup_gallons = (VEHICLE_RANGE_MILES - current_range) / MILES_PER_GALLON
        fuel_needed_for_fillup_gallons = max(0, fuel_needed_for_fillup_gallons)

        cost_of_fillup = fuel_needed_for_fillup_gallons * chosen_station['fuel_price_per_gallon']

        current_fuel_cost += cost_of_fillup
        current_range = VEHICLE_RANGE_MILES

        detour_duration_seconds = (detour_dist_for_chosen_stop / 40.0) * 3600
        total_trip_duration_seconds += detour_duration_seconds

        optimal_stops.append({
            'location': f"{chosen_station['name']} ({chosen_station['city']}, {chosen_station['state']})",
            'latitude': chosen_station['latitude'],
            'longitude': chosen_station['longitude'],
            'fuel_price_per_gallon': chosen_station['fuel_price_per_gallon'],
            'distance_from_start_miles': round(distance_traversed_along_route + detour_dist_for_chosen_stop, 2),
            'fuel_added_gallons': round(fuel_needed_for_fillup_gallons, 2),
            'cost_at_this_stop': round(cost_of_fillup, 2),
            'detour_distance_miles': round(detour_dist_for_chosen_stop, 2), 
            'detour_duration_seconds': round(detour_duration_seconds, 2) 
        })

        current_location = [chosen_station['latitude'], chosen_station['longitude']]
        current_route_point_idx = chosen_stop['route_point_idx']

        distance_traversed_along_route = 0.0
        for i in range(current_route_point_idx):
            distance_traversed_along_route += geodesic(
                tuple(route_geometry[i]),
                tuple(route_geometry[i+1])
            ).miles

        logger.info(f"Made a stop at {chosen_station['name']}. Total cost: {current_fuel_cost:.2f}.")

    logger.info(f"Optimization complete. Total fuel cost: {current_fuel_cost:.2f}.")
    return optimal_stops, current_fuel_cost, total_trip_duration_seconds

def _get_average_fuel_price():
    
    """Helper to get an average fuel price if no specific station is chosen or for initial tank fill."""
    if not FUEL_STATIONS_DATA:
        logger.warning("No fuel station data available to calculate average price. Using default.")
        return 3.5
    valid_prices = [s['fuel_price_per_gallon'] for s in FUEL_STATIONS_DATA if s['fuel_price_per_gallon'] is not None and s['fuel_price_per_gallon'] > 0]
    if not valid_prices:
        logger.warning("No valid fuel prices found to calculate average. Using default.")
        return 3.5
    return sum(valid_prices) / len(valid_prices)