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
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.const import ATTR_AREA_ID, ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    ATTR_RESTORE_DELAY,
    CONF_PLAY_TEST_SOUND,
    CONF_RESTORE_DELAY,
    CONF_TEST_SOUND_DELAY,
    CONF_TEST_SOUND_VOLUME,
    DEFAULT_PLAY_TEST_SOUND,
    DEFAULT_RESTORE_DELAY,
    DEFAULT_TEST_SOUND_DELAY,
    DEFAULT_TEST_SOUND_VOLUME,
    DOMAIN,
    MAX_RESTORE_DELAY,
    MAX_TEST_SOUND_DELAY,
    MIN_RESTORE_DELAY,
    MIN_TEST_SOUND_DELAY,
    SERVICE_CANCEL_ALARM_MUTE,
    SERVICE_MUTE_FOR_ALARM_DISARM,
    SERVICE_RESTORE_ALARM_VOLUME,
    VOLUME_MAX,
    VOLUME_MIN,
)

if TYPE_CHECKING:
    from . import RuntimeData

_LOGGER = logging.getLogger(__name__)

_ATTR_PLAY_TEST_SOUND = "play_test_sound"

_MUTE_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Optional(ATTR_RESTORE_DELAY): vol.All(
            vol.Coerce(int),
            vol.Range(min=MIN_RESTORE_DELAY, max=MAX_RESTORE_DELAY),
        ),
        vol.Optional(_ATTR_PLAY_TEST_SOUND): cv.boolean,
        vol.Optional(CONF_TEST_SOUND_DELAY): vol.All(
            vol.Coerce(float),
            vol.Range(min=MIN_TEST_SOUND_DELAY, max=MAX_TEST_SOUND_DELAY),
        ),
        vol.Optional(CONF_TEST_SOUND_VOLUME): vol.All(
            vol.Coerce(int),
            vol.Range(min=VOLUME_MIN, max=VOLUME_MAX),
        ),
    }
)
_TARGET_ONLY_SCHEMA = cv.make_entity_service_schema({})


@callback
def _resolve_targets(
    hass: HomeAssistant, call: ServiceCall
) -> list[tuple[RuntimeData, str]]:
    """Return ``(runtime_data, speaker_id)`` pairs referenced by the call.

    Resolves the entity_id/device_id/area_id target selector by reading the
    entity and device registries directly, rather than relying on a generic
    HA service-target helper (whose name/location has changed between HA
    versions). The registry ``.entities``/``.devices`` mappings and
    ``RegistryEntry``/``DeviceEntry`` attributes used here are part of the
    long-stable registry data model.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    entity_ids: set[str] = set(cv.ensure_list(call.data.get(ATTR_ENTITY_ID) or []))
    device_ids: set[str] = set(cv.ensure_list(call.data.get(ATTR_DEVICE_ID) or []))
    area_ids: set[str] = set(cv.ensure_list(call.data.get(ATTR_AREA_ID) or []))

    if area_ids:
        for device in dev_reg.devices.values():
            if device.area_id in area_ids:
                device_ids.add(device.id)
        for entity in ent_reg.entities.values():
            if entity.area_id in area_ids:
                entity_ids.add(entity.entity_id)

    if device_ids:
        for entity in ent_reg.entities.values():
            if entity.device_id in device_ids:
                entity_ids.add(entity.entity_id)

    # speaker_id -> entry_id, collected from targeted entities' devices.
    speaker_to_entry: dict[str, str] = {}
    for entity_id in entity_ids:
        target_entity = ent_reg.entities.get(entity_id)
        if (
            target_entity is None
            or target_entity.platform != DOMAIN
            or target_entity.device_id is None
        ):
            continue
        target_device = dev_reg.devices.get(target_entity.device_id)
        if target_device is None:
            continue
        for domain, identifier in target_device.identifiers:
            if domain == DOMAIN:
                speaker_to_entry[identifier] = target_entity.config_entry_id or ""

    results: list[tuple[RuntimeData, str]] = []
    for speaker_id, entry_id in speaker_to_entry.items():
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or not hasattr(entry, "runtime_data"):
            continue
        results.append((entry.runtime_data, speaker_id))
    return results


def _restore_delay_for(runtime: RuntimeData, call: ServiceCall) -> int:
    """Resolve the restore delay: call data > entry option > default."""
    if ATTR_RESTORE_DELAY in call.data:
        return int(call.data[ATTR_RESTORE_DELAY])
    entry = runtime.coordinator.config_entry
    assert entry is not None  # always set; this coordinator is entry-scoped
    return int(entry.options.get(CONF_RESTORE_DELAY, DEFAULT_RESTORE_DELAY))


def _play_test_sound_for(runtime: RuntimeData, call: ServiceCall) -> bool:
    """Resolve whether to play the confirmation chime: call data > option."""
    if _ATTR_PLAY_TEST_SOUND in call.data:
        return bool(call.data[_ATTR_PLAY_TEST_SOUND])
    entry = runtime.coordinator.config_entry
    assert entry is not None  # always set; this coordinator is entry-scoped
    return bool(entry.options.get(CONF_PLAY_TEST_SOUND, DEFAULT_PLAY_TEST_SOUND))


def _test_sound_delay_for(runtime: RuntimeData, call: ServiceCall) -> float:
    """Resolve the confirmation-chime delay: call data > entry option > default."""
    if CONF_TEST_SOUND_DELAY in call.data:
        return float(call.data[CONF_TEST_SOUND_DELAY])
    entry = runtime.coordinator.config_entry
    assert entry is not None  # always set; this coordinator is entry-scoped
    return float(
        entry.options.get(CONF_TEST_SOUND_DELAY, DEFAULT_TEST_SOUND_DELAY)
    )


def _test_sound_volume_for(runtime: RuntimeData, call: ServiceCall) -> int:
    """Resolve the chime's own playback volume: call data > entry option > default."""
    if CONF_TEST_SOUND_VOLUME in call.data:
        return int(call.data[CONF_TEST_SOUND_VOLUME])
    entry = runtime.coordinator.config_entry
    assert entry is not None  # always set; this coordinator is entry-scoped
    return int(
        entry.options.get(CONF_TEST_SOUND_VOLUME, DEFAULT_TEST_SOUND_VOLUME)
    )


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
            play_test_sound = _play_test_sound_for(runtime, call)
            test_sound_delay = _test_sound_delay_for(runtime, call)
            test_sound_volume = _test_sound_volume_for(runtime, call)
            try:
                await runtime.mute.async_mute(
                    speaker_id,
                    delay,
                    play_test_sound=play_test_sound,
                    test_sound_delay=test_sound_delay,
                    test_sound_volume=test_sound_volume,
                )
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
