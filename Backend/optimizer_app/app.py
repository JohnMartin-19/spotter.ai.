from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)

class OptimizerAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'optimizer_app'

    def ready(self):
        # to avoid circular imports if utils imports models from this app
        from .utils import load_fuel_prices
        logger.info("Calling load_fuel_prices from AppConfig.ready()")
        load_fuel_prices()