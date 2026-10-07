"""Services for the UniFi AI Speaker integration.

Three thin, loosely-coupled actions are exposed so alarm panels (e.g. Alarmo)
can drive the mute lifecycle without this integration depending on them:

* ``mute_for_alarm_disarm`` — save volume, set to 0, schedule restore.
* ``restore_alarm_volume``  — restore the saved volume now.
* ``cancel_alarm_mute``     — cancel the pending restore and restore now
                              (use on alarm re-arm/re-trigger).

Targets are resolved from entity/device/area selectors to the underlying
speaker ids, so any entity of a speaker device may be targeted.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_extract_referenced_entity_ids

from .const import (
    ATTR_RESTORE_DELAY,
    CONF_RESTORE_DELAY,
    DEFAULT_RESTORE_DELAY,
    DOMAIN,
    MAX_RESTORE_DELAY,
    MIN_RESTORE_DELAY,
    SERVICE_CANCEL_ALARM_MUTE,
    SERVICE_MUTE_FOR_ALARM_DISARM,
    SERVICE_RESTORE_ALARM_VOLUME,
)

_LOGGER = logging.getLogger(__name__)

_MUTE_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Optional(ATTR_RESTORE_DELAY): vol.All(
            vol.Coerce(int),
            vol.Range(min=MIN_RESTORE_DELAY, max=MAX_RESTORE_DELAY),
        ),
    }
)
_TARGET_ONLY_SCHEMA = cv.make_entity_service_schema({})


@callback
def _resolve_targets(
    hass: HomeAssistant, call: ServiceCall
) -> list[tuple[object, str]]:
    """Return ``(runtime_data, speaker_id)`` pairs referenced by the call."""
    selected = async_extract_referenced_entity_ids(hass, call)
    entity_ids = selected.referenced | selected.indirectly_referenced

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    # speaker_id -> entry_id, collected from targeted entities' devices.
    speaker_to_entry: dict[str, str] = {}
    for entity_id in entity_ids:
        entity = ent_reg.async_get(entity_id)
        if entity is None or entity.platform != DOMAIN or entity.device_id is None:
            continue
        device = dev_reg.async_get(entity.device_id)
        if device is None:
            continue
        for domain, identifier in device.identifiers:
            if domain == DOMAIN:
                speaker_to_entry[identifier] = entity.config_entry_id or ""

    results: list[tuple[object, str]] = []
    for speaker_id, entry_id in speaker_to_entry.items():
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or not hasattr(entry, "runtime_data"):
            continue
        results.append((entry.runtime_data, speaker_id))
    return results


def _restore_delay_for(runtime: object, call: ServiceCall) -> int:
    """Resolve the restore delay: call data > entry option > default."""
    if ATTR_RESTORE_DELAY in call.data:
        return int(call.data[ATTR_RESTORE_DELAY])
    entry = getattr(runtime, "coordinator").config_entry
    return int(entry.options.get(CONF_RESTORE_DELAY, DEFAULT_RESTORE_DELAY))


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register integration services once for the domain."""
    if hass.services.has_service(DOMAIN, SERVICE_MUTE_FOR_ALARM_DISARM):
        return

    async def _async_mute(call: ServiceCall) -> None:
        targets = _resolve_targets(hass, call)
        if not targets:
            raise ServiceValidationError(
                "No UniFi AI Speaker was found for the given target"
            )
        for runtime, speaker_id in targets:
            delay = _restore_delay_for(runtime, call)
            try:
                await runtime.mute.async_mute(speaker_id, delay)
            except HomeAssistantError:
                raise
            except Exception as err:  # noqa: BLE001 - surface as HA error
                raise HomeAssistantError(
                    f"Failed to mute speaker {speaker_id}: {err}"
                ) from err

    async def _async_restore(call: ServiceCall) -> None:
        for runtime, speaker_id in _resolve_targets(hass, call):
            await runtime.mute.async_restore(speaker_id)

    async def _async_cancel(call: ServiceCall) -> None:
        for runtime, speaker_id in _resolve_targets(hass, call):
            await runtime.mute.async_cancel(speaker_id)

    hass.services.async_register(
        DOMAIN, SERVICE_MUTE_FOR_ALARM_DISARM, _async_mute, schema=_MUTE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RESTORE_ALARM_VOLUME, _async_restore, schema=_TARGET_ONLY_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CANCEL_ALARM_MUTE, _async_cancel, schema=_TARGET_ONLY_SCHEMA
    )
