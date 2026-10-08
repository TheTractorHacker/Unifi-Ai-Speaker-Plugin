"""Integration setup, entity, and service tests."""

from __future__ import annotations

import pytest
from custom_components.unifi_ai_speaker.api import Speaker, UnifiConnectionError
from custom_components.unifi_ai_speaker.const import (
    CONF_ALARM_WEBHOOK_ID,
    DOMAIN,
    SERVICE_CANCEL_ALARM_MUTE,
    SERVICE_MUTE_FOR_ALARM_DISARM,
    SERVICE_TRIGGER_ALARM,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import SPEAKER_NO_MIC, SPEAKER_WITH_MIC

MIC_ID = SPEAKER_WITH_MIC["id"]
NOMIC_ID = SPEAKER_NO_MIC["id"]


def _entity_id(hass: HomeAssistant, unique_id: str) -> str:
    ent_reg = er.async_get(hass)
    entity_id = ent_reg.async_get_entity_id("number", DOMAIN, unique_id)
    return entity_id


async def test_setup_creates_device_per_speaker(
    hass: HomeAssistant, setup_integration
) -> None:
    """One HA device is created per discovered speaker."""
    dev_reg = dr.async_get(hass)
    assert dev_reg.async_get_device(identifiers={(DOMAIN, MIC_ID)}) is not None
    assert dev_reg.async_get_device(identifiers={(DOMAIN, NOMIC_ID)}) is not None


async def test_mic_entities_only_for_mic_speakers(
    hass: HomeAssistant, setup_integration
) -> None:
    """Mic switch/volume exist only for speakers whose hasMic flag is true."""
    ent_reg = er.async_get(hass)
    assert ent_reg.async_get_entity_id("switch", DOMAIN, f"{MIC_ID}_mic_enabled")
    assert not ent_reg.async_get_entity_id(
        "switch", DOMAIN, f"{NOMIC_ID}_mic_enabled"
    )
    assert ent_reg.async_get_entity_id("number", DOMAIN, f"{MIC_ID}_mic_volume")
    assert not ent_reg.async_get_entity_id(
        "number", DOMAIN, f"{NOMIC_ID}_mic_volume"
    )


async def test_volume_number_reports_and_sets(
    hass: HomeAssistant, setup_integration, mock_api
) -> None:
    """The volume number reflects state and writes through the API."""
    entity_id = _entity_id(hass, f"{MIC_ID}_volume")
    assert hass.states.get(entity_id).state == "75"

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id, "value": 50},
        blocking=True,
    )
    mock_api.set_volume.assert_awaited_with(MIC_ID, 50)


async def test_mic_switch_toggles(
    hass: HomeAssistant, setup_integration, mock_api
) -> None:
    """The mic switch calls set_mic_enabled."""
    ent_reg = er.async_get(hass)
    entity_id = ent_reg.async_get_entity_id("switch", DOMAIN, f"{MIC_ID}_mic_enabled")
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": entity_id}, blocking=True
    )
    mock_api.set_mic_enabled.assert_awaited_with(MIC_ID, False)


async def test_test_sound_button(
    hass: HomeAssistant, setup_integration, mock_api
) -> None:
    """Pressing the button calls test_sound."""
    ent_reg = er.async_get(hass)
    entity_id = ent_reg.async_get_entity_id("button", DOMAIN, f"{MIC_ID}_test_sound")
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )
    mock_api.test_sound.assert_awaited_with(MIC_ID)


async def test_test_sound_button_api_failure(
    hass: HomeAssistant, setup_integration, mock_api
) -> None:
    """A failing test-sound API call surfaces as a HomeAssistantError."""
    mock_api.test_sound.side_effect = UnifiConnectionError("speaker unreachable")
    ent_reg = er.async_get(hass)
    entity_id = ent_reg.async_get_entity_id("button", DOMAIN, f"{MIC_ID}_test_sound")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "button", "press", {"entity_id": entity_id}, blocking=True
        )


async def test_speaker_offline_marks_volume_unavailable(
    hass: HomeAssistant, setup_integration, mock_api
) -> None:
    """A disconnected speaker's volume entity goes unavailable; connection
    state still reports it so the problem is visible, not hidden."""
    offline = {**SPEAKER_WITH_MIC, "state": "DISCONNECTED"}
    mock_api.get_speakers.return_value = [
        Speaker.from_api(offline),
        Speaker.from_api(SPEAKER_NO_MIC),
    ]
    runtime = setup_integration.runtime_data
    await runtime.coordinator.async_refresh()
    await hass.async_block_till_done()

    volume_entity = _entity_id(hass, f"{MIC_ID}_volume")
    assert hass.states.get(volume_entity).state == "unavailable"

    ent_reg = er.async_get(hass)
    conn_entity = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{MIC_ID}_connection_state"
    )
    assert hass.states.get(conn_entity).state == "DISCONNECTED"


async def test_services_registered(hass: HomeAssistant, setup_integration) -> None:
    """All four alarm services are registered."""
    assert hass.services.has_service(DOMAIN, SERVICE_MUTE_FOR_ALARM_DISARM)
    assert hass.services.has_service(DOMAIN, SERVICE_CANCEL_ALARM_MUTE)
    assert hass.services.has_service(DOMAIN, SERVICE_TRIGGER_ALARM)


async def test_mute_service_targets_device(
    hass: HomeAssistant, setup_integration, mock_api
) -> None:
    """The mute service resolves a device target to the right speaker."""
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device(identifiers={(DOMAIN, MIC_ID)})
    await hass.services.async_call(
        DOMAIN,
        SERVICE_MUTE_FOR_ALARM_DISARM,
        {"device_id": device.id, "restore_delay": 120},
        blocking=True,
    )
    mock_api.set_volume.assert_awaited_with(MIC_ID, 0)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CANCEL_ALARM_MUTE,
        {"device_id": device.id},
        blocking=True,
    )
    # Cancel restores the saved volume (75).
    mock_api.set_volume.assert_awaited_with(MIC_ID, 75)


async def test_mute_service_targets_entity(
    hass: HomeAssistant, setup_integration, mock_api
) -> None:
    """The mute service also resolves a plain entity_id target (Alarmo-style).

    Covers the legacy ``data: {entity_id: ...}`` call shape (no ``target:``
    key) that existing Alarmo YAML configurations rely on.
    """
    entity_id = _entity_id(hass, f"{MIC_ID}_volume")
    await hass.services.async_call(
        DOMAIN,
        SERVICE_MUTE_FOR_ALARM_DISARM,
        {"entity_id": entity_id, "restore_delay": 120},
        blocking=True,
    )
    mock_api.set_volume.assert_awaited_with(MIC_ID, 0)

    # Clean up the pending restore timer rather than leaving it dangling.
    await hass.services.async_call(
        DOMAIN, SERVICE_CANCEL_ALARM_MUTE, {"entity_id": entity_id}, blocking=True
    )


async def test_trigger_alarm_uses_configured_webhook_id(
    hass: HomeAssistant, setup_integration, mock_api
) -> None:
    """trigger_alarm posts using the webhook ID configured in Options."""
    hass.config_entries.async_update_entry(
        setup_integration,
        options={CONF_ALARM_WEBHOOK_ID: "configured-webhook-guid"},
    )
    await hass.async_block_till_done()
    entity_id = _entity_id(hass, f"{MIC_ID}_volume")
    await hass.services.async_call(
        DOMAIN, SERVICE_TRIGGER_ALARM, {"entity_id": entity_id}, blocking=True
    )
    mock_api.trigger_alarm_webhook.assert_awaited_once_with("configured-webhook-guid")


async def test_trigger_alarm_call_data_overrides_option(
    hass: HomeAssistant, setup_integration, mock_api
) -> None:
    """An explicit webhook_id field overrides the configured option."""
    hass.config_entries.async_update_entry(
        setup_integration,
        options={CONF_ALARM_WEBHOOK_ID: "configured-webhook-guid"},
    )
    await hass.async_block_till_done()
    entity_id = _entity_id(hass, f"{MIC_ID}_volume")
    await hass.services.async_call(
        DOMAIN,
        SERVICE_TRIGGER_ALARM,
        {"entity_id": entity_id, "webhook_id": "override-guid"},
        blocking=True,
    )
    mock_api.trigger_alarm_webhook.assert_awaited_once_with("override-guid")


async def test_trigger_alarm_without_webhook_id_raises(
    hass: HomeAssistant, setup_integration, mock_api
) -> None:
    """No configured option and no override raises a clear validation error."""
    entity_id = _entity_id(hass, f"{MIC_ID}_volume")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, SERVICE_TRIGGER_ALARM, {"entity_id": entity_id}, blocking=True
        )
    mock_api.trigger_alarm_webhook.assert_not_awaited()


async def test_trigger_alarm_targeting_multiple_entities_fires_once(
    hass: HomeAssistant, setup_integration, mock_api
) -> None:
    """Targeting several speaker entities on the same console only fires once."""
    hass.config_entries.async_update_entry(
        setup_integration,
        options={CONF_ALARM_WEBHOOK_ID: "configured-webhook-guid"},
    )
    await hass.async_block_till_done()
    mic_volume_entity = _entity_id(hass, f"{MIC_ID}_volume")
    nomic_volume_entity = _entity_id(hass, f"{NOMIC_ID}_volume")
    await hass.services.async_call(
        DOMAIN,
        SERVICE_TRIGGER_ALARM,
        {"entity_id": [mic_volume_entity, nomic_volume_entity]},
        blocking=True,
    )
    mock_api.trigger_alarm_webhook.assert_awaited_once_with("configured-webhook-guid")


async def test_unload(hass: HomeAssistant, setup_integration) -> None:
    """The entry unloads cleanly."""
    assert setup_integration.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert setup_integration.state is ConfigEntryState.NOT_LOADED
