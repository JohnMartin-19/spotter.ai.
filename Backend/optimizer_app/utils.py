import numpy as np
from scipy.spatial import KDTree  # Add this import at the top
import os
import math
import logging
import json # Import json for debugging prints if needed
import traceback # Import traceback for detailed error logging

from django.conf import settings
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
import openrouteservice
import polyline

from .models import FuelStation  # Assuming FuelStation model exists in your app

logger = logging.getLogger(__name__)

# --- Global Variables for Fuel Station Data ---
FUEL_STATIONS_DATA = []
FUEL_STATIONS_KDTREE = None
FUEL_STATIONS_COORDS = None  # Stores (latitude, longitude) for KDTree

# --- 1. Fuel Station Data Loading and KDTree Construction ---

def load_fuel_prices():
    """
    Loads fuel prices from the database into memory and builds a KDTree for spatial querying.
    """
    global FUEL_STATIONS_DATA, FUEL_STATIONS_KDTREE, FUEL_STATIONS_COORDS
    if FUEL_STATIONS_DATA:
        logger.info("Fuel station data already loaded from DB.")
        return FUEL_STATIONS_DATA

    data = []
    coords = []  # To store (latitude, longitude) for the KDTree
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
                    'db_id': station.pk  # Keep a reference to original station if needed
                })
                # KDTree typically works well with (latitude, longitude)
                coords.append([station.latitude, station.longitude])
            else:
                logger.warning(f"Skipping station {station.pk} due to missing lat/lon in DB: {station.truckstop_name}")

        FUEL_STATIONS_DATA = data
        FUEL_STATIONS_COORDS = np.array(coords)
        FUEL_STATIONS_KDTREE = KDTree(FUEL_STATIONS_COORDS)

        logger.info(f"Loaded {len(FUEL_STATIONS_DATA)} fuel stations and built KDTree.")
    except Exception as e:
        logger.error(f"Critical error loading fuel prices from database: {e}", exc_info=True)
        FUEL_STATIONS_DATA = []
        FUEL_STATIONS_KDTREE = None  # Ensure it's cleared if error
        FUEL_STATIONS_COORDS = None
    return data

# Call load_fuel_prices when the module is imported (i.e., when Django app starts)
load_fuel_prices()

# --- 2. OpenRouteService Integration ---
# Ensure settings.ORS_API_KEY is correctly configured in your Django settings.py
ors_client = openrouteservice.Client(key=os.getenv("ORS_API_KEY") or settings.ORS_API_KEY)
geolocator = Nominatim(user_agent="fuel_optimizer_app_route_finder")

def get_coordinates_from_location_name(location_name):
    """Converts a location name (e.g., 'Nairobi, Kenya') to (latitude, longitude)."""
    try:
        # Appending "Kenya" or "USA" can help disambiguate for Nominatim
        location = geolocator.geocode(f"{location_name}, USA", timeout=5) 
        if location:
            return location.latitude, location.longitude
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
        return None, 0, 0 # Return distance and duration as well

    try:
        # ORS expects coordinates as (longitude, latitude)
        coords = [(start_coords[1], start_coords[0]), (end_coords[1], end_coords[0])]

        routes = ors_client.directions(
            coordinates=coords,
            profile=transport_mode, # Use the passed transport_mode
            format='json',
            units='mi',
            geometry=True, # Ensure geometry is returned (as encoded polyline)
            instructions=False,
            radiuses=[-1, -1] # No radius for start/end points
        )

        if not routes or not routes.get('routes'):
            logger.warning(f"No route found for {start_coords} to {end_coords}. ORS Response: {routes}")
            return None, 0, 0

        encoded_geometry = routes['routes'][0]['geometry']
        raw_route_geometry = polyline.decode(encoded_geometry)

        # Convert decoded tuples to lists of [lat, lon] to match previous structure
        route_geometry = [list(coord) for coord in raw_route_geometry]

        total_distance_miles = routes['routes'][0]['summary']['distance']
        total_duration_seconds = routes['routes'][0]['summary']['duration'] # Get duration too

        return route_geometry, total_distance_miles, total_duration_seconds

    except openrouteservice.exceptions.ApiError as e:
        logger.error(f"OpenRouteService API Error: {e.args[0] if e.args else e}")
        return None, 0, 0
    except Exception as e:
        logger.error(f"Error fetching route data: {e}", exc_info=True)
        return None, 0, 0

# --- 3. Core Fueling Algorithm ---

VEHICLE_RANGE_MILES = 500
MILES_PER_GALLON = 10
FUEL_BUFFER_MILES = 50  # Amount of fuel to keep in reserve before forcing a stop

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
        return [], 0, 0 # Return stops, total cost, total duration

    optimal_stops = []
    current_location = list(start_coords) # Make mutable for updates
    current_fuel_cost = 0.0
    current_range = VEHICLE_RANGE_MILES  # Start with a full tank (initial fuel cost handled at start)

    # Assume initial tank is filled to cover the first leg, adding to total cost
    # This ensures initial fuel is accounted for.
    initial_fill_cost = (VEHICLE_RANGE_MILES / MILES_PER_GALLON) * _get_average_fuel_price()
    current_fuel_cost += initial_fill_cost
    logger.info(f"Initial tank filled. Cost: {initial_fill_cost:.2f}.")

    # Initialize current position on the route by finding the closest point to start_coords
    min_dist_to_start_rp = float('inf')
    current_route_point_idx = 0
    for idx, rp_coords in enumerate(route_geometry):
        dist = geodesic(start_coords, tuple(rp_coords)).miles
        if dist < min_dist_to_start_rp:
            min_dist_to_start_rp = dist
            current_route_point_idx = idx

    distance_traversed_along_route = 0.0
    # Calculate distance traversed to the *initial* current_route_point_idx
    # (This assumes the start_coords might not be exactly on a route point)
    for i in range(current_route_point_idx):
        distance_traversed_along_route += geodesic(
            tuple(route_geometry[i]),
            tuple(route_geometry[i+1])
        ).miles

    total_trip_duration_seconds = 0 # To accumulate duration for detours
    # Assuming initial trip duration is already handled by ORS route

    while distance_traversed_along_route < total_distance_miles:
        # Determine current effective range (how much more we can drive before needing a stop)
        effective_range_for_planning = current_range - FUEL_BUFFER_MILES

        # Calculate remaining distance to the *end of the trip* from current_route_point_idx
        dist_to_end_from_current_point = 0.0
        for i in range(current_route_point_idx, len(route_geometry) - 1):
            dist_to_end_from_current_point += geodesic(
                tuple(route_geometry[i]),
                tuple(route_geometry[i+1])
            ).miles

        if current_range >= dist_to_end_from_current_point:
            # We can make it to the destination on the current fuel.
            logger.info(f"Sufficient fuel to reach destination. Remaining distance: {dist_to_end_from_current_point:.2f} miles.")
            break # Exit the loop, no more stops needed

        # We need a stop. Find candidates for a refill.
        candidate_stops_for_leg = []

        # Find potential "lookahead" points on the route within our effective planning range
        lookahead_route_points_for_query = []
        distance_on_route_segment_lookahead = 0.0
        for i in range(current_route_point_idx, len(route_geometry)):
            point = route_geometry[i] # [lat, lon]
            if i > current_route_point_idx:
                distance_on_route_segment_lookahead += geodesic(
                    tuple(route_geometry[i-1]),
                    tuple(point)
                ).miles

            lookahead_route_points_for_query.append(point)

            if distance_on_route_segment_lookahead > effective_range_for_planning:
                break # Reached max lookahead distance for finding stations


        # --- Optimized Candidate Search using KDTree ---
        # Define a search radius around the route points (e.g., 50 miles for a fuel station detour)
        search_radius_miles = 50
        # Approximate conversion from miles to degrees for KDTree query
        # Using 69 miles/degree as a rough average. A more precise calc would use current latitude.
        search_radius_degrees = search_radius_miles / 69.0

        unique_candidate_station_indices = set()
        for route_point_coords in lookahead_route_points_for_query: # [lat, lon]
            # Query KDTree for stations within search_radius_degrees of this route point
            indices = FUEL_STATIONS_KDTREE.query_ball_point(route_point_coords, r=search_radius_degrees)
            unique_candidate_station_indices.update(indices)

        if not unique_candidate_station_indices:
            logger.warning("No fuel stations found near the current lookahead route segment. Cannot proceed.")
            # This can happen if route segment is too short or no stations nearby
            break # Or handle failure more robustly

        # Now, iterate only through the *nearby* unique candidate stations
        viable_candidates = []
        for station_idx in unique_candidate_station_indices:
            station = FUEL_STATIONS_DATA[station_idx]
            station_coords = (station['latitude'], station['longitude']) # (lat, lon)

            # Calculate actual geodesic distance from the *current physical location* to the station
            dist_to_candidate_station = geodesic(tuple(current_location), station_coords).miles

            # Check if we can physically reach this candidate station from current location
            if dist_to_candidate_station > current_range:
                continue # Cannot reach this station with current fuel

            # Find the closest point on the *full* route_geometry to this candidate station
            # This helps in knowing where to "rejoin" the route
            min_dist_to_rp = float('inf')
            closest_route_point_on_route_idx = -1
            for rp_idx, rp_coords in enumerate(route_geometry):
                dist = geodesic(station_coords, tuple(rp_coords)).miles
                if dist < min_dist_to_rp:
                    min_dist_to_rp = dist
                    closest_route_point_on_route_idx = rp_idx

            # Heuristics for a viable candidate stop:
            # 1. Station must be physically reachable with current fuel (already checked: `dist_to_candidate_station <= current_range`).
            # 2. Station must be "near" the route (e.g., within `search_radius_miles` to avoid excessive detours).
            # 3. Station must be "ahead" on the route: `closest_route_point_on_route_idx` must be >= `current_route_point_idx`.
            #    This ensures we are moving forward.
            if (min_dist_to_rp < search_radius_miles and
                closest_route_point_on_route_idx >= current_route_point_idx):

                # Estimate detour distance: current_location -> station -> closest_point_on_route
                detour_dist = geodesic(tuple(current_location), station_coords).miles + \
                              geodesic(station_coords, tuple(route_geometry[closest_route_point_on_route_idx])).miles

                # This is a critical check: can we afford the detour plus future travel?
                # Ensure the entire detour is within the current tank's range
                if detour_dist <= current_range:
                    viable_candidates.append({
                        'station': station,
                        'dist_from_current_location': dist_to_candidate_station,
                        'price': station['fuel_price_per_gallon'],
                        'route_point_idx': closest_route_point_on_route_idx, # Index on main route geometry
                        'detour_distance': detour_dist # Store detour distance for more accurate consumption
                    })

        if not viable_candidates:
            logger.error("No *viable* (reachable and on-route) fuel stations found for the current leg.")
            break # Cannot make a stop, likely stuck

        # Sort candidates: prioritize cheapest, then furthest along the route to minimize stops
        # Sort by price (ascending), then by route_point_idx (descending - to go as far as possible)
        viable_candidates.sort(key=lambda x: (x['price'], -x['route_point_idx']))

        chosen_stop = viable_candidates[0]
        chosen_station = chosen_stop['station']
        detour_dist_for_chosen_stop = chosen_stop['detour_distance']

        # Calculate fuel consumption for the detour
        fuel_consumed_for_detour_gallons = detour_dist_for_chosen_stop / MILES_PER_GALLON
        current_range -= detour_dist_for_chosen_stop # Consume fuel for detour

        # Calculate how much fuel to add to fill up the tank
        fuel_needed_for_fillup_gallons = (VEHICLE_RANGE_MILES - current_range) / MILES_PER_GALLON
        # Ensure we don't try to add negative fuel if current_range somehow became > VEHICLE_RANGE_MILES
        fuel_needed_for_fillup_gallons = max(0, fuel_needed_for_fillup_gallons)

        cost_of_fillup = fuel_needed_for_fillup_gallons * chosen_station['fuel_price_per_gallon']

        current_fuel_cost += cost_of_fillup
        current_range = VEHICLE_RANGE_MILES  # Tank is full after stop

        # Estimate detour duration (very rough, could use ORS for actual detour duration)
        # Assuming average speed of 40 mph for detour calculation
        detour_duration_seconds = (detour_dist_for_chosen_stop / 40.0) * 3600 # Convert hours to seconds
        total_trip_duration_seconds += detour_duration_seconds


        optimal_stops.append({
            'location': f"{chosen_station['name']} ({chosen_station['city']}, {chosen_station['state']})",
            'latitude': chosen_station['latitude'],
            'longitude': chosen_station['longitude'],
            'fuel_price_per_gallon': chosen_station['fuel_price_per_gallon'],
            'distance_from_start_miles': round(distance_traversed_along_route + detour_dist_for_chosen_stop, 2), # Distance from start including detour
            'fuel_added_gallons': round(fuel_needed_for_fillup_gallons, 2),
            'cost_at_this_stop': round(cost_of_fillup, 2)
        })

        # Update current location to the chosen fuel station's coordinates
        current_location = [chosen_station['latitude'], chosen_station['longitude']]

        # Advance the `current_route_point_idx` to resume simulation from the point closest to this station
        current_route_point_idx = chosen_stop['route_point_idx']

        # Recalculate distance_traversed_along_route up to the new current_route_point_idx
        # This accurately reflects the distance along the *original* route to where we rejoined it.
        distance_traversed_along_route = 0.0
        for i in range(current_route_point_idx):
            distance_traversed_along_route += geodesic(
                tuple(route_geometry[i]),
                tuple(route_geometry[i+1])
            ).miles

        logger.info(f"Made a stop at {chosen_station['name']}. Total cost: {current_fuel_cost:.2f}.")

    # Add the initial route duration to the accumulated detour durations
    # The `total_duration_seconds` should be returned from `get_route_data`
    # and then added to the accumulated detour durations.
    # The calling view should handle adding initial route duration.
    logger.info(f"Optimization complete. Total fuel cost: {current_fuel_cost:.2f}.")
    return optimal_stops, current_fuel_cost, total_trip_duration_seconds

def _get_average_fuel_price():
    """Helper to get an average fuel price if no specific station is chosen or for initial tank fill."""
    if not FUEL_STATIONS_DATA:
        logger.warning("No fuel station data available to calculate average price. Using default.")
        return 3.5  # Default fallback if no data loaded
    # Ensure all fuel prices are valid numbers before summing
    valid_prices = [s['fuel_price_per_gallon'] for s in FUEL_STATIONS_DATA if s['fuel_price_per_gallon'] is not None and s['fuel_price_per_gallon'] > 0]
    if not valid_prices:
        logger.warning("No valid fuel prices found to calculate average. Using default.")
        return 3.5
    return sum(valid_prices) / len(valid_prices)