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


async def test_arming_plays_confirmation_sound_when_enabled(
    hass: HomeAssistant, setup_integration_with_panel_and_arm_sound, mock_api
) -> None:
    """Settling into an armed state plays the chirp on every speaker once."""
    hass.states.async_set(PANEL_ENTITY_ID, "armed_away")
    await hass.async_block_till_done()

    mock_api.test_sound.assert_any_await(MIC_ID)
    mock_api.test_sound.assert_any_await(NOMIC_ID)
    assert mock_api.test_sound.await_count == 2


async def test_arm_sound_disabled_by_default(
    hass: HomeAssistant, setup_integration_with_panel, mock_api
) -> None:
    """Without the option enabled, arming never plays the chirp."""
    hass.states.async_set(PANEL_ENTITY_ID, "armed_away")
    await hass.async_block_till_done()

    mock_api.test_sound.assert_not_awaited()


async def test_arming_countdown_does_not_play_sound(
    hass: HomeAssistant, setup_integration_with_panel_and_arm_sound, mock_api
) -> None:
    """The transient 'arming' countdown state is not a fully-armed state."""
    hass.states.async_set(PANEL_ENTITY_ID, "arming")
    await hass.async_block_till_done()

    mock_api.test_sound.assert_not_awaited()


async def test_remaining_armed_does_not_replay_sound(
    hass: HomeAssistant, setup_integration_with_panel_and_arm_sound, mock_api
) -> None:
    """An attribute-only refresh while already armed must not re-chirp."""
    hass.states.async_set(PANEL_ENTITY_ID, "armed_away")
    await hass.async_block_till_done()
    assert mock_api.test_sound.await_count == 2
    mock_api.test_sound.reset_mock()

    # Still armed_away, just a state-changed event with new attributes.
    hass.states.async_set(PANEL_ENTITY_ID, "armed_away", {"extra": "attr"})
    await hass.async_block_till_done()

    mock_api.test_sound.assert_not_awaited()


async def test_arm_sound_skipped_for_muted_speaker(
    hass: HomeAssistant, setup_integration_with_panel_and_arm_sound, mock_api
) -> None:
    """A speaker currently muted for an alarm does not chirp when (re-)armed."""
    hass.states.async_set(PANEL_ENTITY_ID, "triggered")
    await hass.async_block_till_done()
    hass.states.async_set(PANEL_ENTITY_ID, "disarmed")
    await hass.async_block_till_done()
    mock_api.set_volume.assert_any_await(MIC_ID, 0)  # now muted
    mock_api.test_sound.reset_mock()

    hass.states.async_set(PANEL_ENTITY_ID, "armed_away")
    await hass.async_block_till_done()

    # MIC_ID is still muted (the 10-minute restore hasn't elapsed) so it's
    # skipped; the re-arm still cancels its mute via the existing logic,
    # restoring real volume, but that's a separate assertion from chiming.
    mock_api.test_sound.assert_not_awaited()
