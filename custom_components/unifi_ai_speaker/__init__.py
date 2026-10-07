"""The UniFi AI Speaker integration.

Speaker-only integration for the UniFi Protect local Integration API. It is a
deliberate, standalone companion to Home Assistant's official UniFi Protect
integration and does not touch cameras, NVRs, doorbells, or that integration's
entities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

if TYPE_CHECKING:
    from homeassistant.helpers.event import EventStateChangedData

from .api import UnifiAiSpeakerApiClient
from .const import (
    CONF_ALARM_PANEL,
    CONF_API_KEY,
    CONF_HOST,
    CONF_PLAY_TEST_SOUND,
    CONF_RESTORE_DELAY,
    CONF_TEST_SOUND_DELAY,
    CONF_VERIFY_SSL,
    DEFAULT_PLAY_TEST_SOUND,
    DEFAULT_RESTORE_DELAY,
    DEFAULT_TEST_SOUND_DELAY,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    STORAGE_VERSION,
)
from .coordinator import UnifiAiSpeakerCoordinator
from .mute import AlarmMuteController
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

# alarm_control_panel states that mean "not disarmed" -> a mute must be lifted.
_ACTIVE_ALARM_STATES = {
    "arming",
    "pending",
    "triggered",
    "armed_home",
    "armed_away",
    "armed_night",
    "armed_vacation",
    "armed_custom_bypass",
}


@dataclass
class RuntimeData:
    """Runtime objects stored on the config entry."""

    coordinator: UnifiAiSpeakerCoordinator
    mute: AlarmMuteController
    cancel_alarm_tracker: CALLBACK_TYPE | None = None


type UnifiAiSpeakerConfigEntry = ConfigEntry[RuntimeData]


async def async_setup_entry(
    hass: HomeAssistant, entry: UnifiAiSpeakerConfigEntry
) -> bool:
    """Set up UniFi AI Speaker from a config entry."""
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)
    client = UnifiAiSpeakerApiClient(
        entry.data[CONF_HOST], entry.data[CONF_API_KEY], session
    )

    coordinator = UnifiAiSpeakerCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
    mute = AlarmMuteController(hass, coordinator, store)
    await mute.async_load()

    entry.runtime_data = RuntimeData(coordinator=coordinator, mute=mute)

    _setup_alarm_panel_monitor(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Domain-wide services (registered once, resolve targets across entries).
    async_setup_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_reload_on_update))

    _LOGGER.info(
        "Set up UniFi AI Speaker on %s with %d speaker(s)",
        client.host,
        len(coordinator.data),
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: UnifiAiSpeakerConfigEntry
) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime = entry.runtime_data
        runtime.mute.async_unload()
        if runtime.cancel_alarm_tracker is not None:
            runtime.cancel_alarm_tracker()
    return unload_ok


async def _async_reload_on_update(
    hass: HomeAssistant, entry: UnifiAiSpeakerConfigEntry
) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _setup_alarm_panel_monitor(
    hass: HomeAssistant, entry: UnifiAiSpeakerConfigEntry
) -> None:
    """Optionally watch an alarm panel for a fully GUI, no-YAML alarm setup.

    This is opt-in: pick a panel in the integration's Options (a dropdown —
    no YAML) and both halves of the alarm workflow happen automatically:

    * The panel goes from ``triggered`` to ``disarmed`` -> every speaker on
      this entry is muted for the alarm (same as calling
      ``mute_for_alarm_disarm``), with no Alarmo action to configure.
    * The panel enters any non-disarmed state while a speaker is muted -> the
      mute is cancelled and the real volume restored immediately, so a new
      alarm is audible (re-trigger safety).

    Only an explicit ``triggered -> disarmed`` transition auto-mutes, not
    every disarm, so routine "arm away -> disarm on arrival" doesn't cause an
    unnecessary, unaudible volume blip. Works with any ``alarm_control_panel``
    entity (Alarmo or HA's built-in alarm panel), not just Alarmo specifically.

    When no panel is configured the integration stays fully decoupled and
    relies on the explicit ``unifi_ai_speaker.*`` services instead.
    """
    panel_entity = entry.options.get(CONF_ALARM_PANEL)
    if not panel_entity:
        return

    restore_delay = entry.options.get(CONF_RESTORE_DELAY, DEFAULT_RESTORE_DELAY)
    play_test_sound = entry.options.get(
        CONF_PLAY_TEST_SOUND, DEFAULT_PLAY_TEST_SOUND
    )
    test_sound_delay = entry.options.get(
        CONF_TEST_SOUND_DELAY, DEFAULT_TEST_SOUND_DELAY
    )

    @callback
    def _handle(event: Event[EventStateChangedData]) -> None:
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None:
            return
        runtime = entry.runtime_data

        was_triggered = old_state is not None and old_state.state == "triggered"
        if new_state.state == "disarmed" and was_triggered:
            for speaker_id in list(runtime.coordinator.data):
                _LOGGER.info(
                    "Alarm panel %s disarmed after triggering; "
                    "auto-muting speaker %s for %ss",
                    panel_entity,
                    speaker_id,
                    restore_delay,
                )
                hass.async_create_task(
                    runtime.mute.async_mute(
                        speaker_id,
                        restore_delay,
                        play_test_sound=play_test_sound,
                        test_sound_delay=test_sound_delay,
                    )
                )
            return

        if new_state.state in _ACTIVE_ALARM_STATES:
            for speaker_id in list(runtime.coordinator.data):
                if runtime.mute.is_muted(speaker_id):
                    _LOGGER.info(
                        "Alarm panel %s became %s; cancelling mute on speaker %s",
                        panel_entity,
                        new_state.state,
                        speaker_id,
                    )
                    hass.async_create_task(runtime.mute.async_cancel(speaker_id))

    entry.runtime_data.cancel_alarm_tracker = async_track_state_change_event(
        hass, [panel_entity], _handle
    )
