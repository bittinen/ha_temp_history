"""Sensors for temperature history statistics."""

from __future__ import annotations

from datetime import datetime, timedelta
from functools import partial
import logging

_LOGGER = logging.getLogger(__name__)

from typing import Any

import voluptuous as vol

from homeassistant.components.recorder import get_instance as get_recorder_instance
from homeassistant.components.recorder import history as recorder_history
from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_ENTITY_ID,
    CONF_NAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_HOURS,
    CONF_SCAN_INTERVAL,
    DEFAULT_HOURS,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

STORAGE_VERSION = 1
STORAGE_KEY = "temperature_history_stats"

# YAML config schema for the platform (temperature source, window, scan interval).
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_ENTITY_ID): vol.Coerce(str),
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): vol.Coerce(str),
        vol.Optional(CONF_HOURS, default=DEFAULT_HOURS): vol.Coerce(int),
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): cv.time_period,
    }
)

# Human-readable suffixes for the stat sensors.
STAT_LABELS = {
    "average": "Average",
    "min": "Min",
    "max": "Max",
    "range": "Range",
    "all_time_min": "All-time Min",
    "all_time_max": "All-time Max",
}

async def async_create_coordinator(
    hass: HomeAssistant,
    entity_id: str,
    hours: int,
    scan_interval: timedelta,
) -> DataUpdateCoordinator:
    """Create a coordinator that computes rolling stats for one entity."""
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    store_data = await store.async_load() or {}
    entity_store = store_data.get(entity_id, {})
    all_time_min = entity_store.get("all_time_min")
    all_time_max = entity_store.get("all_time_max")

    async def _async_update_stats() -> dict[str, Any]:
        """Fetch history and compute statistics for the configured window."""
        nonlocal all_time_min, all_time_max
        # Query recorder history for the rolling window and compute stats.
        now = dt_util.utcnow()
        start = now - timedelta(hours=hours)

        unit = None
        state = hass.states.get(entity_id)
        if state is None:
            _LOGGER.debug(
                "No state found for %s; history query will likely be empty",
                entity_id,
            )
        else:
            _LOGGER.debug(
                "State for %s: state=%s unit=%s",
                entity_id,
                state.state,
                state.attributes.get(ATTR_UNIT_OF_MEASUREMENT),
            )
        if state is not None:
            unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)

        # Prefer async recorder API when available; fall back to executor job.
        if hasattr(recorder_history, "async_get_significant_states"):
            history = await recorder_history.async_get_significant_states(
                hass,
                start,
                now,
                [entity_id],
                include_start_time_state=True,
                significant_changes_only=False,
            )
        else:
            recorder = get_recorder_instance(hass)
            history = await recorder.async_add_executor_job(
                partial(
                    recorder_history.get_significant_states,
                    hass,
                    start,
                    now,
                    [entity_id],
                    include_start_time_state=True,
                    significant_changes_only=False,
                )
            )

        # Parse numeric samples from recorder state history.
        values: list[float] = []
        for item in history.get(entity_id, []):
            value = _coerce_float(item.state)
            if value is None:
                continue
            values.append(value)

        if values:
            updated = False
            window_min = min(values)
            window_max = max(values)
            if all_time_min is None or window_min < all_time_min:
                all_time_min = window_min
                updated = True
            if all_time_max is None or window_max > all_time_max:
                all_time_max = window_max
                updated = True
            if updated:
                store_data[entity_id] = {
                    "all_time_min": all_time_min,
                    "all_time_max": all_time_max,
                }
                await store.async_save(store_data)

        _LOGGER.debug(
            "Collected %s samples for %s between %s and %s",
            len(values),
            entity_id,
            start.isoformat(),
            now.isoformat(),
        )

        # Always return a payload, even when no data is available.
        if not values:
            return {
                "average": None,
                "min": None,
                "max": None,
                "range": None,
                "all_time_min": all_time_min,
                "all_time_max": all_time_max,
                "count": 0,
                "unit": unit,
                "start": start,
                "end": now,
            }

        min_value = min(values)
        max_value = max(values)

        return {
            "average": sum(values) / len(values),
            "min": min_value,
            "max": max_value,
            "range": max_value - min_value,
            "all_time_min": all_time_min,
            "all_time_max": all_time_max,
            "count": len(values),
            "unit": unit,
            "start": start,
            "end": now,
        }

    # Coordinator handles periodic refresh for all sensors.
    return DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="temperature_history_stats",
        update_method=_async_update_stats,
        update_interval=scan_interval,
    )


async def async_setup_platform(
    hass: HomeAssistant,
    config: dict[str, Any],
    async_add_entities: AddEntitiesCallback,
    discovery_info: dict[str, Any] | None = None,
) -> None:
    """Set up temperature history stats sensors from YAML.

    Args:
        hass: Home Assistant instance.
        config: Platform configuration for the temperature stats sensor.
        async_add_entities: Callback to register created entities.
        discovery_info: Optional discovery payload (unused for YAML config).
    """
    _LOGGER.info("async_setup_platform called for %s", config.get(CONF_ENTITY_ID))
    entity_id = config[CONF_ENTITY_ID]
    name = config[CONF_NAME]
    hours = config[CONF_HOURS]
    scan_interval = config[CONF_SCAN_INTERVAL]

    _LOGGER.debug(
        "Setting up temperature_history_stats: entity_id=%s name=%s hours=%s scan_interval=%s",
        entity_id,
        name,
        hours,
        scan_interval,
    )

    coordinator = await async_create_coordinator(
        hass,
        entity_id,
        hours,
        scan_interval,
    )

    # Use async_refresh here to avoid ConfigEntryError in YAML setups.
    await coordinator.async_refresh()

    async_add_entities(
        [
            TemperatureHistoryStatSensor(coordinator, entity_id, name, hours, "average"),
            TemperatureHistoryStatSensor(coordinator, entity_id, name, hours, "min"),
            TemperatureHistoryStatSensor(coordinator, entity_id, name, hours, "max"),
            TemperatureHistoryStatSensor(coordinator, entity_id, name, hours, "range"),
            TemperatureHistoryStatSensor(coordinator, entity_id, name, hours, "all_time_min"),
            TemperatureHistoryStatSensor(coordinator, entity_id, name, hours, "all_time_max"),
        ]
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up temperature history stats sensors from a config entry."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator = entry_data["coordinator"]
    entity_id = entry_data["entity_id"]
    name = entry_data["name"]
    hours = entry_data["hours"]

    async_add_entities(
        [
            TemperatureHistoryStatSensor(coordinator, entity_id, name, hours, "average"),
            TemperatureHistoryStatSensor(coordinator, entity_id, name, hours, "min"),
            TemperatureHistoryStatSensor(coordinator, entity_id, name, hours, "max"),
            TemperatureHistoryStatSensor(coordinator, entity_id, name, hours, "range"),
            TemperatureHistoryStatSensor(coordinator, entity_id, name, hours, "all_time_min"),
            TemperatureHistoryStatSensor(coordinator, entity_id, name, hours, "all_time_max"),
        ]
    )


class TemperatureHistoryStatSensor(CoordinatorEntity[DataUpdateCoordinator], SensorEntity):
    """Sensor exposing one statistic for a temperature entity."""

    _attr_should_poll = False
    _attr_icon = "mdi:thermometer"

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        source_entity_id: str,
        base_name: str,
        hours: int,
        stat_key: str,
    ) -> None:
        """Initialize a stats sensor for one statistic.

        Args:
            coordinator: Shared coordinator supplying computed stats.
            source_entity_id: Entity ID to pull history from.
            base_name: Base display name for the sensor.
            hours: Rolling time window length in hours.
            stat_key: Statistic key to expose ("average", "min", "max", "range").
        """
        super().__init__(coordinator)
        self._source_entity_id = source_entity_id
        self._base_name = base_name
        self._hours = hours
        self._stat_key = stat_key
        self._attr_name = f"{base_name} {STAT_LABELS[stat_key]}"
        self._attr_unique_id = f"{source_entity_id}_{stat_key}_history_{hours}h"

    @property
    def native_value(self) -> float | None:
        """Return the rounded statistic value for this sensor."""
        # Round for stable display and to avoid excess sensor state churn.
        data = self.coordinator.data or {}
        value = data.get(self._stat_key)
        if value is None:
            return None
        return round(value, 3)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit of measurement from the source entity."""
        # Mirror the source entity's unit if present.
        data = self.coordinator.data or {}
        return data.get("unit")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return metadata about the sample window and counts."""
        # Provide context about the window and sample count.
        data = self.coordinator.data or {}
        start = data.get("start")
        end = data.get("end")
        return {
            "source_entity_id": self._source_entity_id,
            "hours": self._hours,
            "sample_count": data.get("count", 0),
            "window_start": _format_dt(start),
            "window_end": _format_dt(end),
        }


def _coerce_float(value: str | None) -> float | None:
    """Safely convert a recorder state string to float.

    Args:
        value: State string from recorder history.
    """
    # Convert recorder state strings to floats, ignoring non-numeric entries.
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_dt(value: datetime | None) -> str | None:
    """Format a datetime for display attributes.

    Args:
        value: UTC datetime to convert into local ISO-8601 string.
    """
    # Format datetimes for attributes in local time.
    if value is None:
        return None
    return dt_util.as_local(value).isoformat()
