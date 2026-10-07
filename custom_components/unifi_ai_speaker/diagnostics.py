"""Diagnostics for UniFi AI Speaker.

The API key and any credential-bearing material are never included.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import UnifiAiSpeakerConfigEntry
from .const import CONF_API_KEY

TO_REDACT = {CONF_API_KEY, "api_key", "mac"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UnifiAiSpeakerConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator

    speakers = []
    for speaker in (coordinator.data or {}).values():
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
                "alarm_muted": runtime.mute.is_muted(speaker.id),
            }
        )

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "console_version": coordinator.console_version,
        "speakers": async_redact_data(speakers, TO_REDACT),
    }
