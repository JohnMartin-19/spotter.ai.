from django.db import models


class FuelStation(models.Model):
   
    opis_truckstop_id = models.CharField(max_length=50, help_text="Unique ID from OPIS data")
    truckstop_name = models.CharField(max_length=255, blank=True, null=True, help_text="Name of the truckstop")
    address = models.CharField(max_length=255, blank=True, null=True, help_text="Physical street address")
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=50)
    rack_id = models.CharField(max_length=50, blank=True, null=True, help_text="Rack ID from OPIS data")
    retail_price = models.FloatField(help_text="Fuel price per gallon")
    
    # New fields for geocoded coordinates
    latitude = models.FloatField(null=True, blank=True, help_text="Geocoded latitude")
    longitude = models.FloatField(null=True, blank=True, help_text="Geocoded longitude")

    class Meta:
        verbose_name = "Fuel Station"
        verbose_name_plural = "Fuel Stations"
    
    def __str__(self):
        return f"{self.truckstop_name} - {self.city}, {self.state} ({self.retail_price})"