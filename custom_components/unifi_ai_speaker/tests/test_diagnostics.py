"""Tests for diagnostics credential redaction and content."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.unifi_ai_speaker.const import (
    CONF_API_KEY,
    DOMAIN,
    SERVICE_CANCEL_ALARM_MUTE,
    SERVICE_MUTE_FOR_ALARM_DISARM,
)
from custom_components.unifi_ai_speaker.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .const import API_KEY, SPEAKER_WITH_MIC

MIC_ID = SPEAKER_WITH_MIC["id"]


async def test_diagnostics_redacts_api_key(
    hass: HomeAssistant, setup_integration
) -> None:
    """The API key never appears anywhere in the diagnostics payload."""
    diagnostics = await async_get_config_entry_diagnostics(hass, setup_integration)

    assert diagnostics["entry"]["data"][CONF_API_KEY] == "**REDACTED**"
    assert API_KEY not in str(diagnostics)


async def test_diagnostics_redacts_mac_and_includes_expected_fields(
    hass: HomeAssistant, setup_integration
) -> None:
    """MAC addresses are redacted; useful non-sensitive fields are present."""
    diagnostics = await async_get_config_entry_diagnostics(hass, setup_integration)

    speaker = next(s for s in diagnostics["speakers"] if s["id"] == MIC_ID)
    assert speaker["mac"] == "**REDACTED**"
    assert speaker["model"] == SPEAKER_WITH_MIC["type"]
    assert speaker["has_mic"] is True

    assert diagnostics["integration_version"] is not None
    assert "api_limitations" in diagnostics
    assert diagnostics["console_version"] == "7.3.70"


async def test_diagnostics_includes_mute_lifecycle_state(
    hass: HomeAssistant, setup_integration, mock_api
) -> None:
    """A muted speaker's lifecycle state (never the API key) is visible."""
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device(identifiers={(DOMAIN, MIC_ID)})
    await hass.services.async_call(
        DOMAIN,
        SERVICE_MUTE_FOR_ALARM_DISARM,
        {"device_id": device.id, "restore_delay": 120},
        blocking=True,
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, setup_integration)
    speaker = next(s for s in diagnostics["speakers"] if s["id"] == MIC_ID)
    assert speaker["alarm_mute"]["original_volume"] == 75
    assert speaker["alarm_mute"]["restore_delay"] == 120

    # Clean up the pending restore timer rather than leaving it dangling.
    await hass.services.async_call(
        DOMAIN, SERVICE_CANCEL_ALARM_MUTE, {"device_id": device.id}, blocking=True
    )
