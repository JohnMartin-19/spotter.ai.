# fuel_optimizer_app/utils.py
import openrouteservice
import os
import math
from django.conf import settings
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
import logging
from .models import FuelStation 

logger = logging.getLogger(__name__)

# load fuel data
FUEL_STATIONS_DATA = []

def load_fuel_prices():
    """Loads fuel prices from the database into memory."""
    global FUEL_STATIONS_DATA
    if FUEL_STATIONS_DATA: 
        logger.info("Fuel station data already loaded from DB.")
        return FUEL_STATIONS_DATA

    data = []
    try:
        # Fetch all fuel stations from the database (very fast compared to geocoding)
        stations = FuelStation.objects.all()
        for station in stations:
            # Ensure latitude and longitude exist (they should after the load_fuel_data command)
            if station.latitude is not None and station.longitude is not None:
                data.append({
                    'name': station.truckstop_name,
                    'city': station.city,
                    'state': station.state,
                    'latitude': station.latitude,
                    'longitude': station.longitude,
                    'fuel_price_per_gallon': station.retail_price
                })
            else:
                logger.warning(f"Skipping station {station.pk} due to missing lat/lon in DB: {station.truckstop_name}")

        FUEL_STATIONS_DATA = data
        logger.info(f"Loaded {len(FUEL_STATIONS_DATA)} fuel stations from database.")
    except Exception as e:
        logger.error(f"Critical error loading fuel prices from database: {e}", exc_info=True)
        FUEL_STATIONS_DATA = [] # Clear in case of error
    return data

# Call load_fuel_prices when the module is imported (i.e., when Django app starts)
# This will now be very fast as it's a database read operation.
load_fuel_prices()


# --- 2. OpenRouteService Integration ---
ors_client = openrouteservice.Client(key=settings.ORS_API_KEY)
# Keep this geolocator for geocoding start/end points for route requests
# These are only two calls per API request, so no performance issue here.
geolocator = Nominatim(user_agent="fuel_optimizer_app_route_finder")

def get_coordinates_from_location_name(location_name):
    """Converts a location name (e.g., 'New York, NY') to (latitude, longitude)."""
    try:
        # A small sleep here can prevent hitting Nominatim's rate limit for route points,
        # though it's usually fast enough for just two calls.
        # sleep(0.5) # Optional: if you see errors from Nominatim for start/end points
        location = geolocator.geocode(f"{location_name}, USA", timeout=5)
        if location:
            return location.latitude, location.longitude
        logger.warning(f"Geocoding returned no result for: {location_name}")
        return None
    except Exception as e:
        logger.error(f"Error geocoding '{location_name}': {e}")
        return None

def get_route_data(start_coords, end_coords):
    """
    Fetches route data from OpenRouteService.
    start_coords and end_coords are (latitude, longitude) tuples.
    Returns route geometry (list of [lat, lon] pairs), total distance in miles.
    """
    if start_coords is None or end_coords is None:
        logger.error("Invalid coordinates provided to get_route_data. Cannot fetch route.")
        return None, 0

    try:
        # ORS expects coordinates as (longitude, latitude)
        coords = [(start_coords[1], start_coords[0]), (end_coords[1], end_coords[0])]
        
        routes = ors_client.directions(
            coordinates=coords,
            profile='driving-car',
            format='json',
            units='mi', # Request distance in miles
            geometry=True, # Ensure geometry is returned
            instructions=False,
            radiuses=[-1, -1] # No radius for start/end points
        )

        if not routes or not routes.get('routes'):
            logger.warning(f"No route found for {start_coords} to {end_coords}. ORS Response: {routes}")
            return None, 0

        route_geometry = routes['routes'][0]['geometry']['coordinates'] # This is [lon, lat] pairs
        total_distance_miles = routes['routes'][0]['summary']['distance']

        # Convert ORS geometry to list of (lat, lon) for easier use in frontend
        formatted_geometry = [[coord[1], coord[0]] for coord in route_geometry]

        return formatted_geometry, total_distance_miles

    except openrouteservice.exceptions.ApiError as e:
        logger.error(f"OpenRouteService API Error: {e.args[0] if e.args else e}") # Print error details
        return None, 0
    except Exception as e:
        logger.error(f"Error fetching route data: {e}", exc_info=True) # Full traceback
        return None, 0

# --- 3. Core Fueling Algorithm ---

VEHICLE_RANGE_MILES = 500
MILES_PER_GALLON = 10
FUEL_BUFFER_MILES = 50 # Amount of fuel to keep in reserve before forcing a stop

def find_optimal_fuel_stops(route_geometry, total_distance_miles, start_coords, end_coords):
    """
    Calculates optimal fuel stops along the route.
    route_geometry: List of [lat, lon] points defining the path.
    total_distance_miles: Total route distance.
    """
    if not route_geometry or not FUEL_STATIONS_DATA:
        logger.warning("No route geometry or no fuel station data for fueling calculation.")
        return [], 0 # Or handle appropriately

    optimal_stops = []
    current_location = start_coords
    current_fuel_cost = 0.0
    current_range = VEHICLE_RANGE_MILES # Start with a full tank (initial fuel cost handled at start)

    # Simplified initial fuel cost: Assume initial tank is filled at the average price
    # if the total distance is greater than zero and we expect to use fuel.
    # This is a bit of an assumption but covers the "total money spent on fuel"
    # if the trip starts with an empty tank.
    if total_distance_miles > 0:
        # Assuming we fill up to 500 miles range at the start
        current_fuel_cost += VEHICLE_RANGE_MILES / MILES_PER_GALLON * _get_average_fuel_price()


    # Iterate through the route points. This assumes route_geometry points are sufficiently granular.
    # We need to simulate driving along this path.
    
    # Track the actual point on the route we are at
    current_route_point_idx = 0
    distance_traversed_along_route = 0.0 # Total distance from start_coords

    while distance_traversed_along_route < total_distance_miles:
        # Determine current effective range (how much more we can drive before running low)
        effective_range = current_range - FUEL_BUFFER_MILES # Drive until we have buffer remaining

        # Calculate remaining distance on current tank
        remaining_distance_on_tank = current_range

        # Check if we can reach the end of the trip on current fuel
        # Distance from current_route_point_idx to end of route
        dist_to_end_from_current_point = 0.0
        for i in range(current_route_point_idx, len(route_geometry) - 1):
            dist_to_end_from_current_point += geodesic(
                (route_geometry[i][0], route_geometry[i][1]),
                (route_geometry[i+1][0], route_geometry[i+1][1])
            ).miles

        if remaining_distance_on_tank >= dist_to_end_from_current_point:
            # We can make it to the destination, no more stops needed
            # The remaining fuel in the tank is enough for the rest of the trip.
            # Add cost for the final segment (if not already accounted for by initial fill)
            # This logic needs to be careful not to double count
            # For simplicity, let's assume total_fuel_cost only tracks fill-ups,
            # and the initial fillup covers the first segment.
            # No new fuel stop, just consume what's left.
            logger.info(f"Reached destination. Remaining distance: {dist_to_end_from_current_point} miles.")
            break
        
        # We need a stop. Find candidates for a refill.
        # Look for the cheapest fuel station within our 'effective_range'
        # along the *remaining* path.
        
        candidate_stops_for_leg = []
        
        # Iterate along the route geometry, starting from current_route_point_idx
        # Accumulate distance and check for fuel stations nearby.
        
        distance_lookahead = 0.0
        lookahead_route_points = []
        
        # Collect route points within the current effective range
        for i in range(current_route_point_idx, len(route_geometry)):
            point = route_geometry[i]
            if i > current_route_point_idx:
                distance_lookahead += geodesic(
                    (route_geometry[i-1][0], route_geometry[i-1][1]),
                    (point[0], point[1])
                ).miles
            
            lookahead_route_points.append(point)

            if distance_lookahead > effective_range:
                break # Reached max lookahead distance on current tank

        # Now, check nearby stations for each point in `lookahead_route_points`
        # and consider their actual distance from `current_location`
        for route_point in lookahead_route_points:
            for station in FUEL_STATIONS_DATA:
                station_coords = (station['latitude'], station['longitude'])
                
                # Option 1: Straight-line distance from current location to station (simple)
                # dist_to_station = geodesic((current_location[0], current_location[1]), station_coords).miles
                
                # Option 2: Distance along the route to this station (more accurate, but requires more logic or another ORS call)
                # For this demo, let's use straight-line for simplicity, but acknowledge the limitation.
                
                # Let's consider stations within a small radius of the *route point*
                # and then calculate actual distance from our *current location* to that station
                # and back onto the route. This is too complex for the given time.

                # Simplified Approach:
                # Find the cheapest fuel station that is within the VEHICLE_RANGE_MILES straight-line distance
                # from the *current physical location* (which is the last fuel stop or start).
                # This doesn't strictly adhere to "along the route" for the detour, but it's practical.

                # Calculate straight-line distance from `current_location` to the `station_coords`
                # And ensure we don't pick stations "behind" us (heuristic)
                
                dist_to_candidate_station = geodesic((current_location[0], current_location[1]), station_coords).miles
                
                # A better "along the route" heuristic: Ensure the station is geographically *between*
                # the current location and the end point, or near the path.
                # Simplest check: is it within a reasonable radius of the *actual route geometry*?
                # This requires checking if a station is within a "buffer" around the route.
                
                # To quickly implement "along the route":
                # Find the point on the `route_geometry` closest to the station.
                # If that closest point is *ahead* of `current_route_point_idx`, then consider it.
                
                if dist_to_candidate_station <= current_range: # Can we physically reach it?
                    # Check if the station is close to the route and ahead of our current position index
                    # 50 miles off-route buffer is a rough estimate for "close enough"
                    min_dist_to_rp = float('inf')
                    closest_route_point_idx = -1
                    for rp_idx, rp_coords in enumerate(route_geometry):
                        dist = geodesic(station_coords, (rp_coords[0], rp_coords[1])).miles
                        if dist < min_dist_to_rp:
                            min_dist_to_rp = dist
                            closest_route_point_idx = rp_idx
                    
                    if min_dist_to_rp < 50 and closest_route_point_idx >= current_route_point_idx: # 50 miles off-route buffer
                        candidate_stops_for_leg.append({
                            'station': station,
                            'dist_from_current_location': dist_to_candidate_station,
                            'price': station['fuel_price_per_gallon'],
                            'route_point_idx': closest_route_point_idx # Index on main route
                        })

        if not candidate_stops_for_leg:
            logger.error("No reachable fuel stations found for the current leg of the trip.")
            return [], -1 # Indicate failure

        # Sort candidates: cheapest first, then furthest along the route to maximize segment length
        candidate_stops_for_leg.sort(key=lambda x: (x['price'], x['route_point_idx']))

        chosen_stop = candidate_stops_for_leg[0]
        chosen_station = chosen_stop['station']
        
        # Calculate how much fuel we need to drive to this station and then proceed
        distance_to_station_from_current_location = chosen_stop['dist_from_current_location']
        
        # This is where it gets complex. The simplest approach for "total money spent":
        # Assume we fill up to full tank at the chosen station.
        
        fuel_needed_for_fillup = (VEHICLE_RANGE_MILES - current_range) / MILES_PER_GALLON
        
        # Ensure we don't try to add negative fuel or add more than capacity if we're not empty
        # If current_range is already at max, this value will be 0.
        if fuel_needed_for_fillup < 0:
            fuel_needed_for_fillup = VEHICLE_RANGE_MILES / MILES_PER_GALLON # Assuming we fill up to full

        cost_of_fillup = fuel_needed_for_fillup * chosen_station['fuel_price_per_gallon']
        
        current_fuel_cost += cost_of_fillup
        current_range = VEHICLE_RANGE_MILES # Tank is full after stop

        optimal_stops.append({
            'location': f"{chosen_station['name']} ({chosen_station['city']}, {chosen_station['state']})",
            'latitude': chosen_station['latitude'],
            'longitude': chosen_station['longitude'],
            'fuel_price_per_gallon': chosen_station['fuel_price_per_gallon'],
            'distance_from_previous_stop_miles': round(distance_traversed_along_route, 2), # This is dist from START
            'fuel_added_gallons': round(fuel_needed_for_fillup, 2),
            'cost_at_this_stop': round(cost_of_fillup, 2)
        })
        
        # Update current location to the chosen fuel station
        current_location = (chosen_station['latitude'], chosen_station['longitude'])
        
        # Advance the `current_route_point_idx` to resume simulation from the point closest to this station
        current_route_point_idx = chosen_stop['route_point_idx']
        
        # Update `distance_traversed_along_route` to reflect distance to this new point on the route
        # This requires re-calculating cumulative distance up to `current_route_point_idx`
        distance_traversed_along_route = 0.0
        for i in range(current_route_point_idx):
            distance_traversed_along_route += geodesic(
                (route_geometry[i][0], route_geometry[i][1]),
                (route_geometry[i+1][0], route_geometry[i+1][1])
            ).miles
        
        logger.info(f"Made a stop at {chosen_station['name']}. Total cost: {current_fuel_cost}")

    return optimal_stops, current_fuel_cost

def _get_average_fuel_price():
    """Helper to get an average fuel price if no specific station is chosen or for initial tank fill."""
    if not FUEL_STATIONS_DATA:
        logger.warning("No fuel station data available to calculate average price. Using default.")
        return 3.5 # Default fallback if no data loaded
    return sum(s['fuel_price_per_gallon'] for s in FUEL_STATIONS_DATA) / len(FUEL_STATIONS_DATA)