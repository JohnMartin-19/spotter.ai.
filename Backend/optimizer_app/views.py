from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .serializers import RouteRequestSerializer, RouteResponseSerializer
from .utils import get_coordinates_from_location_name, get_route_data, find_optimal_fuel_stops, load_fuel_prices
import logging

logger = logging.getLogger(__name__)

# Ensure fuel prices are loaded when the app is ready
load_fuel_prices()

class OptimizeFuelRouteAPIView(APIView):
   
    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        start_location_name = serializer.validated_data['start_location']
        end_location_name = serializer.validated_data['end_location']

        # 1. Get Coordinates
        start_coords = get_coordinates_from_location_name(start_location_name)
        end_coords = get_coordinates_from_location_name(end_location_name)

        if not start_coords:
            return Response({'error': f"Could not find coordinates for start location: {start_location_name}"},
                            status=status.HTTP_400_BAD_REQUEST)
        if not end_coords:
            return Response({'error': f"Could not find coordinates for end location: {end_location_name}"},
                            status=status.HTTP_400_BAD_REQUEST)
        
        logger.info(f"Start Coords: {start_coords}, End Coords: {end_coords}")

        # 2. Get Route Data from ORS (single call)
        route_geometry, total_distance_miles = get_route_data(start_coords, end_coords)

        if not route_geometry:
            return Response({'error': 'Failed to retrieve route from mapping API. Check logs for details.'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        logger.info(f"Route geometry points: {len(route_geometry)}, Total distance: {total_distance_miles} miles")

        # 3. Find Optimal Fuel Stops & Calculate Total Cost
        optimal_fuel_stops, total_fuel_cost = find_optimal_fuel_stops(
            route_geometry, total_distance_miles, start_coords, end_coords
        )
        
        if total_fuel_cost == -1: # Indicates an error in fueling logic, e.g., no reachable station
             return Response({'error': 'Failed to find a viable fueling plan for the route. '
                                       'Check if fuel stations are reachable or route is too long.'},
                             status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        response_data = {
            'route_geometry': route_geometry,
            'total_distance_miles': total_distance_miles,
            'optimal_fuel_stops': optimal_fuel_stops,
            'total_fuel_cost_usd': round(total_fuel_cost, 2),
            'start_coords': list(start_coords), # For frontend convenience
            'end_coords': list(end_coords)     # For frontend convenience
        }
        
        # Validate and return response
        response_serializer = RouteResponseSerializer(response_data)
        return Response(response_serializer.data, status=status.HTTP_200_OK)