
from rest_framework import serializers

class RouteRequestSerializer(serializers.Serializer):
    start_location = serializers.CharField(max_length=200, help_text="e.g., 'New York, NY'")
    end_location = serializers.CharField(max_length=200, help_text="e.g., 'Los Angeles, CA'")

class FuelStopSerializer(serializers.Serializer):
    location = serializers.CharField(max_length=255)
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    fuel_price_per_gallon = serializers.FloatField()
    distance_from_previous_stop_miles = serializers.FloatField(required=False) 
    fuel_added_gallons = serializers.FloatField()
    cost_at_this_stop = serializers.FloatField()

class RouteResponseSerializer(serializers.Serializer):
   
    route_geometry = serializers.ListField(
        child=serializers.ListField(child=serializers.FloatField(), min_length=2, max_length=2),
        help_text="List of [latitude, longitude] pairs defining the route path."
    )
    total_distance_miles = serializers.FloatField(help_text="Total distance of the route in miles.")
    optimal_fuel_stops = FuelStopSerializer(many=True, help_text="List of recommended fuel stops.")
    total_fuel_cost_usd = serializers.FloatField(help_text="Total estimated money spent on fuel.")
    start_coords = serializers.ListField(child=serializers.FloatField(), min_length=2, max_length=2, help_text="[latitude, longitude] of start.")
    end_coords = serializers.ListField(child=serializers.FloatField(), min_length=2, max_length=2, help_text="[latitude, longitude] of end.")
    estimated_total_trip_duration_minutes = serializers.IntegerField(
        help_text="Estimated total duration of the trip in minutes, including detours for fuel stops."
    )
    error = serializers.CharField(required=False, help_text="Error message if any.")