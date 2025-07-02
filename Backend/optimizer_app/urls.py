from django.urls import path
from .views import OptimizeFuelRouteAPIView

urlpatterns = [
    path('api/v1/route-and-fuel/', OptimizeFuelRouteAPIView.as_view(), name='route_and_fuel'),
]