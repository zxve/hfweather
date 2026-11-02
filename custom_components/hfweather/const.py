"""Identity and conservative defaults for the existing v7 API."""

from homeassistant.const import Platform

DOMAIN = "hfweather"
LEGACY_DOMAIN = "heweather"
NAME = "Hfweather"
PLATFORMS = [Platform.SENSOR, Platform.WEATHER]
ATTRIBUTION = "Data provided by QWeather / 和风天气"
DEFAULT_HOST = "devapi.qweather.com"
DEFAULT_INTERVAL = 30  # Minutes; preserve existing explicit values.
DAILY_CHOICES = (3, 7, 10, 15, 30)
HOURLY_CHOICES = (24, 72, 168)
CONF_API_HOST = "api_host"
CONF_LOCATION_MODE = "location_mode"
CONF_LOCATION_ENTITY = "location_entity"
CONF_LEGACY_ENTRY = "legacy_entry_id"
LOCATION_MODES = ("fixed", "home", "entity")
LOCATION_DISTANCE_KM = 5.0
LOCATION_DEBOUNCE_SECONDS = 60
OPTIONAL_MAX_AGE = 3600
