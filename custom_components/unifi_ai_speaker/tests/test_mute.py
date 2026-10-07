"""Tests for the alarm mute/restore state machine.

Covers every scenario in the spec: 75->0->75, repeated disarm keeping the
original, re-trigger cancellation, timer restore, restart recovery, and
restore-while-API-unavailable.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from custom_components.unifi_ai_speaker.api import Speaker, UnifiConnectionError
from custom_components.unifi_ai_speaker.const import DOMAIN, STORAGE_VERSION
from custom_components.unifi_ai_speaker.coordinator import UnifiAiSpeakerCoordinator
from custom_components.unifi_ai_speaker.mute import AlarmMuteController
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from .const import SPEAKER_WITH_MIC

SPEAKER_ID = SPEAKER_WITH_MIC["id"]
STORE_KEY = f"{DOMAIN}.test_entry"


@pytest.fixture
def env(hass: HomeAssistant):
    """Build a controller with a coordinator whose client is mocked.

    ``set_volume`` mutates the in-memory speaker so reads reflect writes.
    """
    speaker = Speaker.from_api(SPEAKER_WITH_MIC)
    speakers = {SPEAKER_ID: speaker}

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
