"""Constants for the temperature history stats integration."""

from datetime import timedelta

DOMAIN = "temperature_history_stats"
DEFAULT_HOURS = 24
DEFAULT_NAME = "Temperature History"
DEFAULT_SCAN_INTERVAL = timedelta(seconds=300)

CONF_ENTITY_ID = "entity_id"
CONF_HOURS = "hours"
CONF_NAME = "name"
CONF_SCAN_INTERVAL = "scan_interval"
