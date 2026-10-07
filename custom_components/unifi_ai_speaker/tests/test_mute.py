"""Tests for the alarm mute/restore state machine.

Covers every scenario in the spec: 75->0->75, repeated disarm keeping the
original, re-trigger cancellation, timer restore, restart recovery, and
restore-while-API-unavailable.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.unifi_ai_speaker.api import Speaker, UnifiConnectionError
from custom_components.unifi_ai_speaker.const import DOMAIN, STORAGE_VERSION
from custom_components.unifi_ai_speaker.coordinator import UnifiAiSpeakerCoordinator
from custom_components.unifi_ai_speaker.mute import AlarmMuteController

from .const import SPEAKER_NO_MIC, SPEAKER_WITH_MIC

SPEAKER_ID = SPEAKER_WITH_MIC["id"]
OTHER_SPEAKER_ID = SPEAKER_NO_MIC["id"]
STORE_KEY = f"{DOMAIN}.test_entry"


@pytest.fixture
def env(hass: HomeAssistant):
    """Build a controller with a coordinator whose client is mocked.

    Seeds two independent speakers so tests can verify that operating on one
    (``SPEAKER_ID``) never affects the other (``OTHER_SPEAKER_ID``).
    ``set_volume`` mutates the in-memory speaker so reads reflect writes.
    """
    speakers = {
        SPEAKER_ID: Speaker.from_api(SPEAKER_WITH_MIC),
        OTHER_SPEAKER_ID: Speaker.from_api(SPEAKER_NO_MIC),
    }

    client = AsyncMock()
    client.host = "10.0.0.5"
    client.get_meta_info.return_value = {"applicationVersion": "7.3.70"}
    client.get_speakers.return_value = list(speakers.values())

    async def _set_volume(speaker_id, volume):
        speakers[speaker_id].volume = volume
        return speakers[speaker_id]

    client.set_volume.side_effect = _set_volume

    async def _get_speaker(speaker_id):
        return speakers[speaker_id]

    client.get_speaker.side_effect = _get_speaker

    entry = MockConfigEntry(domain=DOMAIN, entry_id="test_entry")
    entry.add_to_hass(hass)
    coordinator = UnifiAiSpeakerCoordinator(hass, entry, client)
    coordinator.data = speakers
    coordinator.last_update_success = True

    store = Store(hass, STORAGE_VERSION, STORE_KEY)
    controller = AlarmMuteController(hass, coordinator, store)

    return controller, client, speakers, store


async def test_mute_then_restore(hass: HomeAssistant, env) -> None:
    """75 -> 0 -> 75."""
    controller, client, speakers, _ = env

    await controller.async_mute(SPEAKER_ID, 600)
    assert controller.is_muted(SPEAKER_ID)
    assert controller.get_state(SPEAKER_ID).original_volume == 75
    assert speakers[SPEAKER_ID].volume == 0
    client.set_volume.assert_awaited_with(SPEAKER_ID, 0)

    await controller.async_restore(SPEAKER_ID)
    await hass.async_block_till_done()
    assert not controller.is_muted(SPEAKER_ID)
    assert speakers[SPEAKER_ID].volume == 75


async def test_repeated_disarm_keeps_original(hass: HomeAssistant, env) -> None:
    """A second disarm must not overwrite the saved volume with 0."""
    controller, client, speakers, _ = env

    await controller.async_mute(SPEAKER_ID, 600)
    assert controller.get_state(SPEAKER_ID).original_volume == 75

    # Second disarm 30s later while already muted (volume is currently 0).
    await controller.async_mute(SPEAKER_ID, 600)
    assert controller.get_state(SPEAKER_ID).original_volume == 75  # NOT 0

    # Only the initial mute wrote volume 0.
    zero_writes = [
        c for c in client.set_volume.await_args_list if c.args == (SPEAKER_ID, 0)
    ]
    assert len(zero_writes) == 1

    await controller.async_restore(SPEAKER_ID)
    await hass.async_block_till_done()
    assert speakers[SPEAKER_ID].volume == 75


async def test_retrigger_cancels_timer_and_restores(
    hass: HomeAssistant, env
) -> None:
    """Re-trigger: cancel pending restore, restore now, no stale restore later."""
    controller, client, speakers, _ = env

    await controller.async_mute(SPEAKER_ID, 600)
    await controller.async_cancel(SPEAKER_ID)
    await hass.async_block_till_done()

    assert not controller.is_muted(SPEAKER_ID)
    assert speakers[SPEAKER_ID].volume == 75
    restores = [c for c in client.set_volume.await_args_list if c.args[1] == 75]
    assert len(restores) == 1

    # Simulate the new alarm dropping volume, then let the OLD timer fire.
    speakers[SPEAKER_ID].volume = 20
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1200))
    await hass.async_block_till_done()

    # The cancelled timer must not have restored anything.
    assert speakers[SPEAKER_ID].volume == 20
    restores = [c for c in client.set_volume.await_args_list if c.args[1] == 75]
    assert len(restores) == 1


async def test_timer_restores_after_delay(hass: HomeAssistant, env) -> None:
    """The scheduled restore fires on its own when the delay elapses."""
    controller, client, speakers, _ = env

    await controller.async_mute(SPEAKER_ID, 60)
    assert speakers[SPEAKER_ID].volume == 0

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()

    assert not controller.is_muted(SPEAKER_ID)
    assert speakers[SPEAKER_ID].volume == 75


async def test_restart_recovery_overdue(hass: HomeAssistant, env, hass_storage) -> None:
    """An overdue mute is restored immediately on load."""
    controller, client, speakers, _ = env
    speakers[SPEAKER_ID].volume = 0  # was muted before the "restart"

    past = dt_util.utcnow().timestamp() - 10
    hass_storage[STORE_KEY] = {
        "version": STORAGE_VERSION,
        "data": {
            "speakers": {
                SPEAKER_ID: {
                    "original_volume": 75,
                    "restore_deadline": past,
                    "restore_delay": 600,
                }
            }
        },
    }

    await controller.async_load()
    await hass.async_block_till_done()

    assert speakers[SPEAKER_ID].volume == 75
    assert not controller.is_muted(SPEAKER_ID)


async def test_restart_recovery_pending(hass: HomeAssistant, env, hass_storage) -> None:
    """A still-pending mute is re-scheduled, not restored early."""
    controller, client, speakers, _ = env
    speakers[SPEAKER_ID].volume = 0

    future = dt_util.utcnow().timestamp() + 120
    hass_storage[STORE_KEY] = {
        "version": STORAGE_VERSION,
        "data": {
            "speakers": {
                SPEAKER_ID: {
                    "original_volume": 75,
                    "restore_deadline": future,
                    "restore_delay": 600,
                }
            }
        },
    }

    await controller.async_load()
    await hass.async_block_till_done()
    assert controller.is_muted(SPEAKER_ID)
    assert speakers[SPEAKER_ID].volume == 0  # not restored yet

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=121))
    await hass.async_block_till_done()
    assert speakers[SPEAKER_ID].volume == 75
    assert not controller.is_muted(SPEAKER_ID)


async def test_automatic_restore_plays_test_sound_when_enabled(
    hass: HomeAssistant, env
) -> None:
    """Timer-expired restore plays exactly one chime when the option is on."""
    controller, client, speakers, _ = env

    await controller.async_mute(
        SPEAKER_ID, 60, play_test_sound=True, test_sound_delay=0
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()

    assert speakers[SPEAKER_ID].volume == 75
    client.test_sound.assert_awaited_once_with(SPEAKER_ID)
    # The chime is followed by a coordinator refresh (re-fetches live state).
    client.get_speakers.assert_awaited()


async def test_chime_plays_at_quiet_volume_before_real_restore(
    hass: HomeAssistant, env
) -> None:
    """The chime plays at its own (quiet) volume, never at the real/alarm volume.

    Verifies both the exact volumes used and their order: set_volume(quiet)
    -> test_sound -> set_volume(real), never the reverse.
    """
    controller, client, speakers, _ = env

    await controller.async_mute(
        SPEAKER_ID,
        60,
        play_test_sound=True,
        test_sound_delay=0,
        test_sound_volume=15,
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()

    set_volume_calls = [
        c for c in client.mock_calls if c[0] == "set_volume"
    ]
    # First write after the initial mute-to-0 is the quiet chime volume...
    assert set_volume_calls[-2].args == (SPEAKER_ID, 15)
    # ...and only after that does the real volume get restored.
    assert set_volume_calls[-1].args == (SPEAKER_ID, 75)

    chime_index = next(
        i for i, c in enumerate(client.mock_calls) if c[0] == "test_sound"
    )
    quiet_write_index = client.mock_calls.index(set_volume_calls[-2])
    real_write_index = client.mock_calls.index(set_volume_calls[-1])
    assert quiet_write_index < chime_index < real_write_index


async def test_automatic_restore_skips_test_sound_when_disabled(
    hass: HomeAssistant, env
) -> None:
    """Timer-expired restore never chimes when the option is off."""
    controller, client, speakers, _ = env

    await controller.async_mute(
        SPEAKER_ID, 60, play_test_sound=False, test_sound_delay=0
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()

    assert speakers[SPEAKER_ID].volume == 75
    client.test_sound.assert_not_awaited()


async def test_manual_restore_plays_test_sound_when_enabled(
    hass: HomeAssistant, env
) -> None:
    """A user-requested early restore also plays the chime, once."""
    controller, client, speakers, _ = env

    await controller.async_mute(
        SPEAKER_ID, 600, play_test_sound=True, test_sound_delay=0
    )
    await controller.async_restore(SPEAKER_ID)
    await hass.async_block_till_done()

    assert speakers[SPEAKER_ID].volume == 75
    client.test_sound.assert_awaited_once_with(SPEAKER_ID)


async def test_repeated_disarm_does_not_duplicate_test_sound(
    hass: HomeAssistant, env
) -> None:
    """A second disarm while muted must not cause two chimes on restore."""
    controller, client, speakers, _ = env

    await controller.async_mute(
        SPEAKER_ID, 600, play_test_sound=True, test_sound_delay=0
    )
    await controller.async_mute(
        SPEAKER_ID, 600, play_test_sound=True, test_sound_delay=0
    )
    await controller.async_restore(SPEAKER_ID)
    await hass.async_block_till_done()

    client.test_sound.assert_awaited_once_with(SPEAKER_ID)


async def test_cancel_never_plays_test_sound(hass: HomeAssistant, env) -> None:
    """Re-trigger/cancel restores the volume but never plays the chime."""
    controller, client, speakers, _ = env

    await controller.async_mute(
        SPEAKER_ID, 600, play_test_sound=True, test_sound_delay=0
    )
    await controller.async_cancel(SPEAKER_ID)
    await hass.async_block_till_done()

    assert speakers[SPEAKER_ID].volume == 75
    client.test_sound.assert_not_awaited()


async def test_quiet_volume_failure_skips_test_sound_and_retries(
    hass: HomeAssistant, env
) -> None:
    """A failure writing the quiet chime volume never plays the chime.

    The underlying write is flaky-once (console briefly offline) so the
    pending retry timer is exercised to completion rather than left dangling.
    """
    controller, client, speakers, _ = env

    calls = {"n": 0}

    async def _flaky(speaker_id, volume):
        if volume == 30 and calls["n"] == 0:  # the quiet test-sound volume
            calls["n"] += 1
            raise UnifiConnectionError("offline")
        speakers[speaker_id].volume = volume
        return speakers[speaker_id]

    await controller.async_mute(
        SPEAKER_ID,
        60,
        play_test_sound=True,
        test_sound_delay=0,
        test_sound_volume=30,
    )
    client.set_volume.side_effect = _flaky

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()

    assert controller.is_muted(SPEAKER_ID)  # retry still pending
    client.test_sound.assert_not_awaited()

    # The retry succeeds; the chime plays once, then the real volume restores.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=130))
    await hass.async_block_till_done()

    assert not controller.is_muted(SPEAKER_ID)
    assert speakers[SPEAKER_ID].volume == 75
    client.test_sound.assert_awaited_once_with(SPEAKER_ID)


async def test_final_restore_failure_does_not_replay_test_sound(
    hass: HomeAssistant, env
) -> None:
    """A failure AFTER the chime has already played must not replay it on retry."""
    controller, client, speakers, _ = env

    calls = {"n": 0}

    async def _flaky(speaker_id, volume):
        if volume == 75 and calls["n"] == 0:  # the final, post-chime restore
            calls["n"] += 1
            raise UnifiConnectionError("offline")
        speakers[speaker_id].volume = volume
        return speakers[speaker_id]

    await controller.async_mute(
        SPEAKER_ID,
        60,
        play_test_sound=True,
        test_sound_delay=0,
        test_sound_volume=30,
    )
    client.set_volume.side_effect = _flaky

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()

    # The chime already played during this first (partially failed) attempt.
    assert controller.is_muted(SPEAKER_ID)  # retry still pending
    assert speakers[SPEAKER_ID].volume == 30  # stuck at the quiet volume
    client.test_sound.assert_awaited_once_with(SPEAKER_ID)

    # The retry resumes at the final write only — no second chime.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=130))
    await hass.async_block_till_done()

    assert not controller.is_muted(SPEAKER_ID)
    assert speakers[SPEAKER_ID].volume == 75
    client.test_sound.assert_awaited_once_with(SPEAKER_ID)  # still just once


async def test_test_sound_api_failure_still_clears_mute_state(
    hass: HomeAssistant, env
) -> None:
    """A failing confirmation chime is logged but does not break the restore."""
    controller, client, speakers, _ = env
    client.test_sound.side_effect = UnifiConnectionError("speaker busy")

    await controller.async_mute(
        SPEAKER_ID, 600, play_test_sound=True, test_sound_delay=0
    )
    await controller.async_restore(SPEAKER_ID)
    await hass.async_block_till_done()

    assert speakers[SPEAKER_ID].volume == 75
    assert not controller.is_muted(SPEAKER_ID)


async def test_stale_restore_cannot_play_test_sound_after_cancel(
    hass: HomeAssistant, env
) -> None:
    """A restore already in flight when cancel fires must never chime.

    Simulates the race: the automatic restore's write of the quiet
    chime-playback volume is in progress (held open with an Event) when a
    re-trigger/cancel arrives. Cancel's own restore (``maybe_test_sound``
    false) writes the real volume directly and is not blocked by this, so it
    completes immediately; the stale call is then released and must detect it
    has been superseded before ever calling test_sound.

    Note on the real device volume in this adversarial ordering: because an
    in-flight HTTP write cannot be cancelled, releasing the stale write here
    deliberately lets it complete *after* cancel's write, so it can transiently
    overwrite the device with the quiet volume. What's guaranteed regardless
    is the *tracked* state: the mute is cleared and the chime never plays.
    The next coordinator poll (or any subsequent write) reconciles the
    displayed volume.
    """
    controller, client, speakers, _ = env
    quiet_write_started = asyncio.Event()
    release_quiet_write = asyncio.Event()
    real_set_volume = client.set_volume.side_effect

    async def _blocking_set_volume(speaker_id, volume):
        if volume == 30:  # the quiet test-sound volume
            quiet_write_started.set()
            await release_quiet_write.wait()
        return await real_set_volume(speaker_id, volume)

    await controller.async_mute(
        SPEAKER_ID,
        60,
        play_test_sound=True,
        test_sound_delay=0,
        test_sound_volume=30,
    )
    client.set_volume.side_effect = _blocking_set_volume

    # Let the timer fire; its restore blocks while writing the quiet volume.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await quiet_write_started.wait()

    # A new alarm retriggers while that write is still in flight. Cancel's own
    # restore writes the real volume directly (not the quiet one) and is not
    # blocked, so it runs to completion on its own.
    cancel_task = hass.async_create_task(controller.async_cancel(SPEAKER_ID))
    await cancel_task
    assert not controller.is_muted(SPEAKER_ID)
    assert speakers[SPEAKER_ID].volume == 75

    # Now let the stale write resume; it must detect it was superseded and
    # stop before ever playing the chime.
    release_quiet_write.set()
    await hass.async_block_till_done()

    client.test_sound.assert_not_awaited()
    assert not controller.is_muted(SPEAKER_ID)


async def test_multiple_speakers_maintain_independent_mute_state(
    hass: HomeAssistant, env
) -> None:
    """Muting/restoring one speaker never touches another's state or volume."""
    controller, client, speakers, _ = env

    await controller.async_mute(SPEAKER_ID, 600)
    assert controller.is_muted(SPEAKER_ID)
    assert not controller.is_muted(OTHER_SPEAKER_ID)
    assert speakers[SPEAKER_ID].volume == 0
    assert speakers[OTHER_SPEAKER_ID].volume == 63  # untouched

    await controller.async_mute(OTHER_SPEAKER_ID, 60)
    assert controller.get_state(SPEAKER_ID).original_volume == 75
    assert controller.get_state(OTHER_SPEAKER_ID).original_volume == 63

    # The shorter-delay speaker's timer fires without affecting the other.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert not controller.is_muted(OTHER_SPEAKER_ID)
    assert speakers[OTHER_SPEAKER_ID].volume == 63
    assert controller.is_muted(SPEAKER_ID)  # still pending, 600s delay
    assert speakers[SPEAKER_ID].volume == 0

    # Clean up the still-pending timer rather than leaving it dangling.
    await controller.async_restore(SPEAKER_ID)
    await hass.async_block_till_done()


async def test_restore_retries_when_api_unavailable(
    hass: HomeAssistant, env
) -> None:
    """A transient restore failure keeps the mute and retries later."""
    controller, client, speakers, _ = env

    await controller.async_mute(SPEAKER_ID, 60)

    # Make the restore write fail once (console offline), succeed afterwards.
    calls = {"n": 0}

    async def _flaky(speaker_id, volume):
        if volume == 75 and calls["n"] == 0:
            calls["n"] += 1
            raise UnifiConnectionError("offline")
        speakers[speaker_id].volume = volume
        return speakers[speaker_id]

    client.set_volume.side_effect = _flaky

    # Timer fires -> restore attempt fails -> state kept, retry scheduled.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert controller.is_muted(SPEAKER_ID)
    assert speakers[SPEAKER_ID].volume == 0

    # Retry (60s later) succeeds.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=130))
    await hass.async_block_till_done()
    assert not controller.is_muted(SPEAKER_ID)
    assert speakers[SPEAKER_ID].volume == 75
