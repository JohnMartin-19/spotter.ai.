from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .serializers import RouteRequestSerializer, RouteResponseSerializer
from .utils import get_coordinates_from_location_name, get_route_data, find_optimal_fuel_stops, load_fuel_prices
import logging
import math 

logger = logging.getLogger(__name__)


load_fuel_prices() 

class OptimizeFuelRouteAPIView(APIView):

    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        if not serializer.is_valid():
            logger.warning(f"Bad request for fuel optimization: {serializer.errors}")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        start_location_name = serializer.validated_data['start_location']
        end_location_name = serializer.validated_data['end_location']
        

        # getting user requested coordinates
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

        #make a single API call to get route data from ORS 
        
        route_geometry, total_distance_miles, initial_route_duration_seconds = get_route_data(start_coords, end_coords)


        if not route_geometry:
            logger.error(f"Failed to retrieve route from mapping API for {start_location_name} to {end_location_name}. Check ORS API key/logs.")
            return Response({'error': 'Failed to retrieve route from mapping API. Please try again or check the provided locations.'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        logger.info(f"Route fetched: {len(route_geometry)} points, {total_distance_miles:.2f} miles, {initial_route_duration_seconds:.0f} seconds.")

        # 3. try to calc the optimal fuel stops & total cost
        optimal_fuel_stops, total_fuel_cost, total_detour_duration_seconds = find_optimal_fuel_stops(
            route_geometry, total_distance_miles, start_coords, end_coords
        )

        # handle cases where fueling logic might indicate a failure
        if total_fuel_cost == -1:
             logger.error("Fueling optimization failed: No viable fueling plan found.")
             return Response({'error': 'Failed to find a viable fueling plan for the route. '
                                       'This might happen if no reachable fuel stations are found, '
                                       'or the route is too long for the vehicle range.'},
                             status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # calculate final total duration (initial route + accumulated detours)
        final_total_duration_seconds = initial_route_duration_seconds + total_detour_duration_seconds
        final_total_duration_minutes = math.ceil(final_total_duration_seconds / 60) 

        response_data = {
            # returns a list of [lat, lon] pairs
            'route_geometry': route_geometry, 
            'total_distance_miles': round(total_distance_miles, 2),
            # list of dictionaries for each optimal fuel stop
            'optimal_fuel_stops': optimal_fuel_stops, 
            'total_fuel_cost_usd': round(total_fuel_cost, 2),
            # we try to convert tuple to list for consistency if desired by frontend
            'start_coords': list(start_coords), 
            'end_coords': list(end_coords),     
            'estimated_total_trip_duration_minutes': final_total_duration_minutes 
        }
        response_serializer = RouteResponseSerializer(response_data)
        logger.info("Fuel optimization successful.")
        return Response(response_serializer.data, status=status.HTTP_200_OK)