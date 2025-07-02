from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .serializers import RouteRequestSerializer, RouteResponseSerializer
from .utils import get_coordinates_from_location_name, get_route_data, find_optimal_fuel_stops, load_fuel_prices
import logging
import math # Import math for ceil function

logger = logging.getLogger(__name__)

# IMPORTANT: For production, it's generally recommended to call load_fuel_prices()
# from your app's AppConfig.ready() method instead of at the module level in views.py.
# This ensures it's loaded only once when the Django app fully starts up.
load_fuel_prices() # Keeping it here as per your current setup, but consider AppConfig.ready()

class OptimizeFuelRouteAPIView(APIView):

    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        if not serializer.is_valid():
            logger.warning(f"Bad request for fuel optimization: {serializer.errors}")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        start_location_name = serializer.validated_data['start_location']
        end_location_name = serializer.validated_data['end_location']
        # You might add transport_mode to the serializer if you want to support it
        # transport_mode = serializer.validated_data.get('transport_mode', 'driving-car')


        # 1. Get Coordinates
        start_coords = get_coordinates_from_location_name(start_location_name)
        end_coords = get_coordinates_from_location_name(end_location_name)

        if not start_coords:
            logger.error(f"Failed to geocode start location: {start_location_name}")
            return Response({'error': f"Could not find coordinates for start location: {start_location_name}. Please provide a more specific address (e.g., 'City, State, Country')."},
                            status=status.HTTP_400_BAD_REQUEST)
        if not end_coords:
            logger.error(f"Failed to geocode end location: {end_location_name}")
            return Response({'error': f"Could not find coordinates for end location: {end_location_name}. Please provide a more specific address (e.g., 'City, State, Country')."},
                            status=status.HTTP_400_BAD_REQUEST)

        logger.info(f"Geocoded: Start ({start_location_name}) -> {start_coords}, End ({end_location_name}) -> {end_coords}")

        # 2. Get Route Data from ORS (single call)
        # Pass transport_mode if you add it to serializer and get_route_data
        route_geometry, total_distance_miles, initial_route_duration_seconds = get_route_data(start_coords, end_coords)


        if not route_geometry:
            logger.error(f"Failed to retrieve route from mapping API for {start_location_name} to {end_location_name}. Check ORS API key/logs.")
            return Response({'error': 'Failed to retrieve route from mapping API. Please try again or check the provided locations.'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        logger.info(f"Route fetched: {len(route_geometry)} points, {total_distance_miles:.2f} miles, {initial_route_duration_seconds:.0f} seconds.")

        # 3. Find Optimal Fuel Stops & Calculate Total Cost and Detour Duration
        optimal_fuel_stops, total_fuel_cost, total_detour_duration_seconds = find_optimal_fuel_stops(
            route_geometry, total_distance_miles, start_coords, end_coords
        )

        # Handle cases where fueling logic might indicate a failure
        if total_fuel_cost == -1: # This is a specific error code from find_optimal_fuel_stops
             logger.error("Fueling optimization failed: No viable fueling plan found.")
             return Response({'error': 'Failed to find a viable fueling plan for the route. '
                                       'This might happen if no reachable fuel stations are found, '
                                       'or the route is too long for the vehicle range.'},
                             status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Calculate final total duration (initial route + accumulated detours)
        final_total_duration_seconds = initial_route_duration_seconds + total_detour_duration_seconds
        final_total_duration_minutes = math.ceil(final_total_duration_seconds / 60) # Round up to nearest minute

        response_data = {
            'route_geometry': route_geometry, # List of [lat, lon] pairs
            'total_distance_miles': round(total_distance_miles, 2),
            'optimal_fuel_stops': optimal_fuel_stops, # List of dictionaries for each stop
            'total_fuel_cost_usd': round(total_fuel_cost, 2),
            'start_coords': list(start_coords), # Convert tuple to list for consistency if desired by frontend
            'end_coords': list(end_coords),     # Convert tuple to list for consistency if desired by frontend
            'estimated_total_trip_duration_minutes': final_total_duration_minutes # New: Total duration
        }

        # Validate and return response
        # Ensure your RouteResponseSerializer includes 'estimated_total_trip_duration_minutes'
        response_serializer = RouteResponseSerializer(response_data)
        logger.info("Fuel optimization successful.")
        return Response(response_serializer.data, status=status.HTTP_200_OK)