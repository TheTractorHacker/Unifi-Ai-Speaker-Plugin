"""Alarm mute/restore state management for UniFi AI Speakers.

Behaviour
---------
On *disarm* we remember the speaker's current volume, drop it to 0 so Alarm
Manager playback is instantly silent, and schedule a restore after a delay
(default 10 minutes, long enough for the ~5 minute repeated alarm clip to end).

Guarantees
----------
* **No original-volume corruption.** A second disarm while already muted keeps
  the first saved volume (it never saves the current ``0``); it only re-extends
  the restore deadline.
* **Re-trigger safety.** ``cancel`` (called when the alarm re-arms/re-triggers)
  cancels the pending restore and restores the real volume immediately so the
  new alarm is audible. No stale timer can later touch the volume.
* **Restart recovery.** Mute state (original volume + absolute restore deadline)
  is persisted. On startup an overdue mute is restored immediately and a
  still-pending mute is re-scheduled for its remaining time.

All volume reads/writes go through the coordinator's API client. A per-speaker
view of the state powers the diagnostic "Alarm Mute Active" binary sensor.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import UnifiAiSpeakerError, UnifiConnectionError
from .coordinator import UnifiAiSpeakerCoordinator

_LOGGER = logging.getLogger(__name__)

# How long to wait before retrying a restore that failed because the speaker or
# console was temporarily unreachable.
_RESTORE_RETRY_SECONDS = 60


@dataclass(slots=True)
class MuteState:
    """Persisted mute state for a single speaker."""

    original_volume: int
    restore_deadline: float  # absolute UTC timestamp (seconds)
    restore_delay: int  # seconds, for diagnostics


class AlarmMuteController:
    """Owns the mute/restore lifecycle for all speakers on one console."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: UnifiAiSpeakerCoordinator,
        store: Store,
    ) -> None:
        """Initialise the controller."""
        self._hass = hass
        self._coordinator = coordinator
        self._store = store
        self._states: dict[str, MuteState] = {}
        self._timers: dict[str, CALLBACK_TYPE] = {}

    # ------------------------------------------------------------------
    # Public state helpers (used by the diagnostic binary sensor)
    # ------------------------------------------------------------------
    def is_muted(self, speaker_id: str) -> bool:
        """Return whether a speaker is currently alarm-muted."""
        return speaker_id in self._states

    def get_state(self, speaker_id: str) -> MuteState | None:
        """Return the mute state for a speaker, if muted."""
        return self._states.get(speaker_id)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def async_load(self) -> None:
        """Load persisted mute state and recover timers after a restart."""
        stored = await self._store.async_load()
        if not stored:
            return

        now = dt_util.utcnow().timestamp()
        for speaker_id, raw in (stored.get("speakers") or {}).items():
            try:
                state = MuteState(
                    original_volume=int(raw["original_volume"]),
                    restore_deadline=float(raw["restore_deadline"]),
                    restore_delay=int(raw.get("restore_delay", 0)),
                )
            except (KeyError, TypeError, ValueError):
                _LOGGER.warning("Discarding corrupt mute state for %s", speaker_id)
                continue

            self._states[speaker_id] = state
            if state.restore_deadline <= now:
                _LOGGER.info(
                    "Restoring overdue alarm mute for speaker %s after restart",
                    speaker_id,
                )
                await self._async_do_restore(speaker_id, reason="startup (overdue)")
            else:
                remaining = int(state.restore_deadline - now)
                _LOGGER.info(
                    "Re-scheduling alarm mute restore for speaker %s in %ss",
                    speaker_id,
                    remaining,
                )
                self._schedule(speaker_id, state.restore_deadline)

        # Persist again in case overdue entries were cleared during recovery.
        await self._async_persist()

    def async_unload(self) -> None:
        """Cancel all pending timers (does not change volume)."""
        for cancel in self._timers.values():
            cancel()
        self._timers.clear()

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------
    async def async_mute(self, speaker_id: str, restore_delay: int) -> None:
        """Mute a speaker for the alarm-disarm window.

        If already muted, the original volume is preserved and only the restore
        deadline is extended.
        """
        now = dt_util.utcnow().timestamp()
        deadline = now + restore_delay

        if (existing := self._states.get(speaker_id)) is not None:
            # Already muted — never overwrite the saved volume with the current 0.
            _LOGGER.debug(
                "Speaker %s already alarm-muted (original=%s); extending restore",
                speaker_id,
                existing.original_volume,
            )
            existing.restore_deadline = deadline
            existing.restore_delay = restore_delay
            self._schedule(speaker_id, deadline)
            await self._async_persist()
            return

        original_volume = await self._current_volume(speaker_id)

        try:
            updated = await self._coordinator.client.set_volume(speaker_id, 0)
        except UnifiAiSpeakerError as err:
            raise HomeAssistantMuteError(
                f"Could not mute speaker {speaker_id}: {err}"
            ) from err
        self._coordinator.update_speaker(updated)

        self._states[speaker_id] = MuteState(
            original_volume=original_volume,
            restore_deadline=deadline,
            restore_delay=restore_delay,
        )
        self._schedule(speaker_id, deadline)
        await self._async_persist()
        _LOGGER.info(
            "Temporarily muted speaker %s (saved volume %s, restore in %ss)",
            speaker_id,
            original_volume,
            restore_delay,
        )
        self._notify()

    async def async_restore(self, speaker_id: str) -> None:
        """Restore a speaker's saved volume now and clear its mute state."""
        if speaker_id not in self._states:
            _LOGGER.debug("Restore requested for %s but it is not muted", speaker_id)
            return
        self._cancel_timer(speaker_id)
        await self._async_do_restore(speaker_id, reason="manual restore")
        await self._async_persist()

    async def async_cancel(self, speaker_id: str) -> None:
        """Cancel a pending mute and restore immediately (re-trigger safety).

        Semantically identical to :meth:`async_restore` but named for the
        alarm-re-armed / re-triggered case: the pending timer is cancelled and
        the true volume is restored at once so the new alarm is audible.
        """
        if speaker_id not in self._states:
            return
        self._cancel_timer(speaker_id)
        await self._async_do_restore(speaker_id, reason="alarm re-trigger")
        await self._async_persist()
        self._notify()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _current_volume(self, speaker_id: str) -> int:
        """Return the speaker's present volume from cache, or fetch it live."""
        speaker = (self._coordinator.data or {}).get(speaker_id)
        if speaker is not None:
            return speaker.volume
        # Not in cache yet — fetch once.
        live = await self._coordinator.client.get_speaker(speaker_id)
        return live.volume

    async def _async_do_restore(self, speaker_id: str, *, reason: str) -> None:
        """Write the saved volume back and drop the state entry.

        On a transient failure, keep the state and retry shortly; the persisted
        deadline still protects against a lost restore across restarts.
        """
        state = self._states.get(speaker_id)
        if state is None:
            return
        try:
            updated = await self._coordinator.client.set_volume(
                speaker_id, state.original_volume
            )
        except UnifiConnectionError:
            _LOGGER.warning(
                "Could not restore speaker %s volume yet (%s); retrying in %ss",
                speaker_id,
                reason,
                _RESTORE_RETRY_SECONDS,
            )
            retry_at = dt_util.utcnow().timestamp() + _RESTORE_RETRY_SECONDS
            self._schedule(speaker_id, retry_at)
            return
        except UnifiAiSpeakerError as err:
            # Non-transient (e.g. speaker removed) — give up and clear state.
            _LOGGER.error(
                "Giving up restoring speaker %s volume (%s): %s",
                speaker_id,
                reason,
                err,
            )
            self._states.pop(speaker_id, None)
            self._notify()
            return
        else:
            self._coordinator.update_speaker(updated)

        self._states.pop(speaker_id, None)
        _LOGGER.info(
            "Restored speaker %s to volume %s (%s)",
            speaker_id,
            state.original_volume,
            reason,
        )
        self._notify()

    def _schedule(self, speaker_id: str, deadline: float) -> None:
        """(Re)schedule a one-shot restore at an absolute UTC deadline."""
        self._cancel_timer(speaker_id)

        async def _fire(_now: Any) -> None:
            self._timers.pop(speaker_id, None)
            await self._async_do_restore(speaker_id, reason="timer expired")
            await self._async_persist()

        when = dt_util.utc_from_timestamp(deadline)
        self._timers[speaker_id] = async_track_point_in_utc_time(
            self._hass, _fire, when
        )

    def _cancel_timer(self, speaker_id: str) -> None:
        if cancel := self._timers.pop(speaker_id, None):
            cancel()

    async def _async_persist(self) -> None:
        """Write current mute state to disk."""
        data = {
            "speakers": {
                speaker_id: asdict(state)
                for speaker_id, state in self._states.items()
            }
        }
        await self._store.async_save(data)

    def _notify(self) -> None:
        """Push state to entities that reflect mute status."""
        self._coordinator.async_update_listeners()


class HomeAssistantMuteError(UnifiAiSpeakerError):
    """Raised when a mute operation cannot be completed."""
