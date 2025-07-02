# optimizer_app/management/commands/load_fuel_data.py
import csv
from django.core.management.base import BaseCommand, CommandError
from ...models import FuelStation 
from geopy.geocoders import Nominatim
from time import sleep
import os
from django.conf import settings 

class Command(BaseCommand):
    help = 'Loads fuel station data from CSV, geocodes locations, and saves to database.'

    def handle(self, *args, **options):
      
        input_file = os.path.join(settings.BASE_DIR, 'fuel-prices-for-be-assessment.csv')
        geolocator = Nominatim(user_agent="fuel_optimizer_geocoder_batch_command") 

        if not os.path.exists(input_file):
            raise CommandError(f"Input file not found at: {input_file}")

        self.stdout.write(self.style.SUCCESS('Starting fuel data loading and geocoding... This will take a long time due to Nominatim API rate limits (approx. 1 request/second).'))

        geocoded_count = 0
        skipped_count = 0
        total_rows = 0

        try:
            with open(input_file, mode='r', encoding='utf-8') as infile:
                reader = csv.DictReader(infile, delimiter=',')

                # Optional: Uncomment the lines below if you want to clear existing data
                # before a fresh import. Be cautious with this in production!
                # FuelStation.objects.all().delete()
                # self.stdout.write(self.style.WARNING('Cleared existing FuelStation data from database.'))

                for i, row in enumerate(reader):
                    total_rows += 1
                    if total_rows % 100 == 0: 
                        self.stdout.write(f"Processing row {total_rows}...")

                    # Extract data from CSV row based on your actual column headers
                    city = row.get('City')
                    state = row.get('State')
                    retail_price_str = row.get('Retail Price')

                    #use OPIS Truckstop ID as a unique identifier to avoid re-geocoding/duplicates
                    opis_id = row.get('OPIS Truckstop ID')

                    if not all([city, state, retail_price_str, opis_id]):
                        self.stderr.write(self.style.WARNING(f"Skipping row {total_rows} due to missing critical data: {row}"))
                        skipped_count += 1
                        continue

                    try:
                        # # Check if station with this OPIS ID already exists
                        # if FuelStation.objects.filter(opis_truckstop_id=opis_id).exists():
                        #     self.stdout.write(self.style.NOTICE(f"Station with OPIS ID {opis_id} already exists, skipping geocoding and saving."))
                        #     skipped_count += 1 # Count as skipped for this run, but it's already in DB
                        #     continue # Move to next row

                        # Perform geocoding
                        location_name = f"{city}, {state}, USA"
                        latitude, longitude = None, None

                        try:
                            location_geo = geolocator.geocode(location_name, timeout=10) # Increased timeout
                            if location_geo:
                                latitude = location_geo.latitude
                                longitude = location_geo.longitude
                            else:
                                self.stderr.write(self.style.WARNING(f"Could not geocode location for: {location_name}"))
                                skipped_count += 1
                                continue # Skip to next row if geocoding fails
                        except Exception as geo_e:
                            self.stderr.write(self.style.ERROR(f"Error geocoding {location_name}: {geo_e}"))
                            skipped_count += 1
                            continue # Skip to next row on geocoding error

                        # Create and save FuelStation instance to the database
                        FuelStation.objects.create(
                            opis_truckstop_id=opis_id,
                            truckstop_name=row.get('Truckstop Name'),
                            address=row.get('Address'),
                            city=city,
                            state=state,
                            rack_id=row.get('Rack ID'),
                            retail_price=float(retail_price_str),
                            latitude=latitude,
                            longitude=longitude
                        )
                        geocoded_count += 1
                        sleep(1.1) 

                    except ValueError as ve:
                        self.stderr.write(self.style.ERROR(f"Data conversion error for row {total_rows}: {ve} - {row}"))
                        skipped_count += 1
                    except Exception as e:
                        self.stderr.write(self.style.ERROR(f"Unhandled error processing row {total_rows}: {e} - {row}"))
                        skipped_count += 1

        except Exception as e:
            raise CommandError(f"Critical error during file processing: {e}")

        self.stdout.write(self.style.SUCCESS(f"Finished loading fuel data."))
        self.stdout.write(self.style.SUCCESS(f"Total rows in CSV: {total_rows}"))
        self.stdout.write(self.style.SUCCESS(f"Successfully geocoded and saved to DB: {geocoded_count}"))
        self.stdout.write(self.style.WARNING(f"Skipped or already existing rows: {skipped_count}"))