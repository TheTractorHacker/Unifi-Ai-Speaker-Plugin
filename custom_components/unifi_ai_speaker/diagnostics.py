"""Diagnostics for UniFi AI Speaker.

The API key and any credential-bearing material are never included.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from . import UnifiAiSpeakerConfigEntry
from .const import CONF_API_KEY, DOMAIN

TO_REDACT = {CONF_API_KEY, "api_key", "mac"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UnifiAiSpeakerConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator

    integration = await async_get_integration(hass, DOMAIN)

    speakers = []
    for speaker in (coordinator.data or {}).values():
        mute_state = runtime.mute.get_state(speaker.id)
        speakers.append(
            {
                "id": speaker.id,
                "model": speaker.model,
                "state": speaker.state,
                "volume": speaker.volume,
                "mic_volume": speaker.mic_volume,
                "is_mic_enabled": speaker.is_mic_enabled,
                "has_mic": speaker.has_mic,
                "speaker_status": speaker.speaker_status,
                "speaker_mode": speaker.speaker_mode,
                "mac": speaker.mac,
                "alarm_mute": asdict(mute_state) if mute_state else None,
            }
        )

    return {
        "integration_version": integration.version,
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "console_version": coordinator.console_version,
        "speakers": async_redact_data(speakers, TO_REDACT),
        "api_limitations": (
            "The UniFi Protect Integration API does not expose speaker "
            "firmware version, IP address, or last-seen timestamp, so "
            "those fields cannot be included here."
        ),
    }
