"""Alarm mute/restore state management for UniFi AI Speakers.

Behaviour
---------
On *disarm* we remember the speaker's current volume, drop it to 0 so Alarm
Manager playback is instantly silent, and schedule a restore after a delay
(default 10 minutes, long enough for the ~5 minute repeated alarm clip to end).
Optionally, once the volume is restored, a short confirmation chime (the
speaker's existing test-sound) is played so a listener knows the mute period
has ended.

Guarantees
----------
* **No original-volume corruption.** A second disarm while already muted keeps
  the first saved volume (it never saves the current ``0``); it only re-extends
  the restore deadline (and refreshes the test-sound settings for this entry).
* **Re-trigger safety.** ``cancel`` (called when the alarm re-arms/re-triggers)
  cancels the pending restore and restores the real volume immediately so the
  new alarm is audible. No stale timer can later touch the volume, and a
  restore already in flight when ``cancel`` is called is invalidated via a
  per-speaker generation token so it can never play a confirmation chime (or
  clobber newer state) after the fact. See ``_async_do_restore``.
* **Restart recovery.** Mute state (original volume + absolute restore
  deadline + the test-sound settings in effect at mute time) is persisted. On
  startup an overdue mute is restored immediately and a still-pending mute is
  re-scheduled for its remaining time; the confirmation chime still plays at
  most once if it was enabled and hasn't played yet.

All volume reads/writes go through the coordinator's API client. The
confirmation chime uses the API client's existing ``test_sound`` method
directly — it is never simulated via a Home Assistant button press. A
per-speaker view of the state powers the diagnostic "Alarm Mute Active"
binary sensor.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import UnifiAiSpeakerError, UnifiConnectionError
from .const import DEFAULT_PLAY_TEST_SOUND, DEFAULT_TEST_SOUND_DELAY
from .coordinator import UnifiAiSpeakerCoordinator

_LOGGER = logging.getLogger(__name__)

# How long to wait before retrying a restore that failed because the speaker or
# console was temporarily unreachable.
_RESTORE_RETRY_SECONDS = 60


@dataclass(slots=True)
class MuteState:
    """Persisted mute state for a single speaker.

    ``play_test_sound``/``test_sound_delay`` capture the settings in effect at
    mute time (or at the most recent repeated disarm), so a later restore —
    even after a Home Assistant restart — honours them without needing the
    original caller's options again.
    """

    original_volume: int
    restore_deadline: float  # absolute UTC timestamp (seconds)
    restore_delay: int  # seconds, for diagnostics
    play_test_sound: bool = DEFAULT_PLAY_TEST_SOUND
    test_sound_delay: float = DEFAULT_TEST_SOUND_DELAY


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
        # Per-speaker generation counter. Bumped whenever a NEW user-initiated
        # action (mute, manual restore, cancel) starts, so an already-running
        # restore from an earlier action can detect it has been superseded
        # and must not play the confirmation chime or touch shared state.
        self._generations: dict[str, int] = {}

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
                    play_test_sound=bool(
                        raw.get("play_test_sound", DEFAULT_PLAY_TEST_SOUND)
                    ),
                    test_sound_delay=float(
                        raw.get("test_sound_delay", DEFAULT_TEST_SOUND_DELAY)
                    ),
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
                await self._async_do_restore(
                    speaker_id, reason="startup (overdue)", maybe_test_sound=True
                )
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
    async def async_mute(
        self,
        speaker_id: str,
        restore_delay: int,
        *,
        play_test_sound: bool = False,
        test_sound_delay: float = DEFAULT_TEST_SOUND_DELAY,
    ) -> None:
        """Mute a speaker for the alarm-disarm window.

        If already muted, the original volume is preserved and only the
        restore deadline and test-sound settings are refreshed.

        ``play_test_sound`` defaults to off here (unlike the Options flow's
        on-by-default), because every real caller (the services and the
        panel monitor) always resolves and passes the effective option
        explicitly. Defaulting the method itself to off keeps direct/test
        callers that don't care about the chime free of surprise real-time
        sleeps.
        """
        self._bump_generation(speaker_id)
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
            existing.play_test_sound = play_test_sound
            existing.test_sound_delay = test_sound_delay
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
            play_test_sound=play_test_sound,
            test_sound_delay=test_sound_delay,
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
        """Restore a speaker's saved volume now and clear its mute state.

        This is a user-requested early end to the mute, so it is treated the
        same as the automatic restore for confirmation-chime purposes: if the
        option is enabled, the chime plays once restoration completes.
        """
        if speaker_id not in self._states:
            _LOGGER.debug("Restore requested for %s but it is not muted", speaker_id)
            return
        self._bump_generation(speaker_id)
        self._cancel_timer(speaker_id)
        await self._async_do_restore(
            speaker_id, reason="manual restore", maybe_test_sound=True
        )
        await self._async_persist()

    async def async_cancel(self, speaker_id: str) -> None:
        """Cancel a pending mute and restore immediately (re-trigger safety).

        Semantically identical to :meth:`async_restore` but named for the
        alarm-re-armed / re-triggered case: the pending timer is cancelled and
        the true volume is restored at once so the new alarm is audible. The
        confirmation chime never plays on this path, even if a restore from
        the mute that's being cancelled is already in flight.
        """
        if speaker_id not in self._states:
            return
        self._bump_generation(speaker_id)
        self._cancel_timer(speaker_id)
        await self._async_do_restore(
            speaker_id, reason="alarm re-trigger", maybe_test_sound=False
        )
        await self._async_persist()
        self._notify()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _bump_generation(self, speaker_id: str) -> int:
        """Invalidate any restore currently in flight for this speaker."""
        gen = self._generations.get(speaker_id, 0) + 1
        self._generations[speaker_id] = gen
        return gen

    async def _current_volume(self, speaker_id: str) -> int:
        """Return the speaker's present volume from cache, or fetch it live."""
        speaker = (self._coordinator.data or {}).get(speaker_id)
        if speaker is not None:
            return speaker.volume
        # Not in cache yet — fetch once.
        live = await self._coordinator.client.get_speaker(speaker_id)
        return live.volume

    async def _async_do_restore(
        self, speaker_id: str, *, reason: str, maybe_test_sound: bool
    ) -> None:
        """Write the saved volume back and, if appropriate, play one chime.

        ``maybe_test_sound`` says whether this call path is eligible at all
        for the confirmation chime (true for a normal/manual/startup restore,
        false for a cancel/re-trigger restore) — the final decision also
        depends on the per-mute ``play_test_sound`` option and a generation
        check so a superseded restore can never play it.

        On a transient volume-restore failure, state is kept and a retry is
        scheduled; the persisted deadline still protects against a lost
        restore across restarts. A confirmation-chime failure is logged but
        never fails the restore — the volume was already corrected.
        """
        state = self._states.get(speaker_id)
        if state is None:
            return
        generation = self._generations.get(speaker_id, 0)

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
            if self._generations.get(speaker_id, 0) == generation:
                self._states.pop(speaker_id, None)
                self._notify()
            return
        else:
            self._coordinator.update_speaker(updated)

        # A newer mute/restore/cancel started while the volume write above
        # was in flight: that newer action owns the final state and chime
        # decision now, so this stale call must stop here.
        if self._generations.get(speaker_id, 0) != generation:
            _LOGGER.debug(
                "Restore for speaker %s (%s) superseded; not finishing it",
                speaker_id,
                reason,
            )
            return

        if maybe_test_sound and state.play_test_sound:
            await self._async_play_confirmation_chime(
                speaker_id, state.test_sound_delay, generation
            )

        if self._generations.get(speaker_id, 0) != generation:
            return

        self._states.pop(speaker_id, None)
        _LOGGER.info(
            "Restored speaker %s to volume %s (%s)",
            speaker_id,
            state.original_volume,
            reason,
        )
        self._notify()

    async def _async_play_confirmation_chime(
        self, speaker_id: str, test_sound_delay: float, generation: int
    ) -> None:
        """Wait for the volume change to apply, then play the test sound.

        Uses the API client's existing ``test_sound`` method directly (never
        a simulated button press). A failure here is logged and swallowed —
        the volume restore already succeeded and must not be undone or
        retried just because the confirmation chime failed.
        """
        if test_sound_delay > 0:
            await asyncio.sleep(test_sound_delay)

        # Re-check after the sleep: a cancel/new mute may have superseded us.
        if self._generations.get(speaker_id, 0) != generation:
            return

        try:
            await self._coordinator.client.test_sound(speaker_id)
        except UnifiAiSpeakerError as err:
            _LOGGER.warning(
                "Post-restore confirmation chime failed for speaker %s: %s",
                speaker_id,
                err,
            )
            return

        if self._generations.get(speaker_id, 0) != generation:
            return

        await self._coordinator.async_refresh()

    def _schedule(self, speaker_id: str, deadline: float) -> None:
        """(Re)schedule a one-shot restore at an absolute UTC deadline."""
        self._cancel_timer(speaker_id)

        async def _fire(_now: Any) -> None:
            self._timers.pop(speaker_id, None)
            await self._async_do_restore(
                speaker_id, reason="timer expired", maybe_test_sound=True
            )
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
