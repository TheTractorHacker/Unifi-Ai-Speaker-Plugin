"""Tests for the optional, GUI-only alarm panel auto-mute/auto-cancel.

When an alarm panel is picked in Options (a dropdown, no YAML), the
integration listens to it directly: triggered -> disarmed auto-mutes every
speaker on the entry, and any re-arm/re-trigger while muted cancels the mute
immediately. This is the no-YAML alternative to wiring up the
``unifi_ai_speaker.*`` services as Alarmo actions by hand.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .conftest import PANEL_ENTITY_ID
from .const import SPEAKER_NO_MIC, SPEAKER_WITH_MIC

MIC_ID = SPEAKER_WITH_MIC["id"]
NOMIC_ID = SPEAKER_NO_MIC["id"]


async def test_triggered_to_disarmed_auto_mutes_all_speakers(
    hass: HomeAssistant, setup_integration_with_panel, mock_api
) -> None:
    """A triggered -> disarmed transition mutes every speaker with no YAML."""
    hass.states.async_set(PANEL_ENTITY_ID, "triggered")
    await hass.async_block_till_done()
    mock_api.set_volume.reset_mock()

    hass.states.async_set(PANEL_ENTITY_ID, "disarmed")
    await hass.async_block_till_done()

    mock_api.set_volume.assert_any_await(MIC_ID, 0)
    mock_api.set_volume.assert_any_await(NOMIC_ID, 0)


async def test_routine_disarm_does_not_mute(
    hass: HomeAssistant, setup_integration_with_panel, mock_api
) -> None:
    """Disarming from armed_away (no alarm ever sounded) must not mute."""
    hass.states.async_set(PANEL_ENTITY_ID, "armed_away")
    await hass.async_block_till_done()
    mock_api.set_volume.reset_mock()

    hass.states.async_set(PANEL_ENTITY_ID, "disarmed")
    await hass.async_block_till_done()

    mock_api.set_volume.assert_not_awaited()


async def test_retrigger_while_muted_cancels_and_restores(
    hass: HomeAssistant, setup_integration_with_panel, mock_api
) -> None:
    """Re-arming/re-triggering while muted cancels the mute immediately."""
    hass.states.async_set(PANEL_ENTITY_ID, "triggered")
    await hass.async_block_till_done()
    hass.states.async_set(PANEL_ENTITY_ID, "disarmed")
    await hass.async_block_till_done()
    mock_api.set_volume.assert_any_await(MIC_ID, 0)

    mock_api.set_volume.reset_mock()
    hass.states.async_set(PANEL_ENTITY_ID, "triggered")
    await hass.async_block_till_done()

    mock_api.set_volume.assert_any_await(MIC_ID, 75)


async def test_no_panel_configured_stays_decoupled(
    hass: HomeAssistant, setup_integration, mock_api
) -> None:
    """Without a configured panel, no state-change listener is registered."""
    runtime = setup_integration.runtime_data
    assert runtime.cancel_alarm_tracker is None
