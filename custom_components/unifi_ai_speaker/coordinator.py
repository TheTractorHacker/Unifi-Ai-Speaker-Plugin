"""DataUpdateCoordinator for UniFi AI Speaker.

Polls the console on a modest interval (speakers change state slowly). The
Integration API does expose a WebSocket (``/v1/subscribe/devices``), but polling
every 30 seconds is simpler and reliable for a speaker, so that is used here.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    Speaker,
    UnifiAiSpeakerApiClient,
    UnifiAuthError,
    UnifiConnectionError,
)
from .const import DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


class UnifiAiSpeakerCoordinator(DataUpdateCoordinator[dict[str, Speaker]]):
    """Coordinator that keeps the set of speakers up to date."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: UnifiAiSpeakerApiClient,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
        self.client = client
        self.console_version: str | None = None

    @callback
    def update_speaker(self, speaker: Speaker) -> None:
        """Optimistically apply a freshly-written speaker and notify listeners.

        Used after a successful write so entities reflect the change at once
        without waiting for the next poll (and without a debounced refresh).
        """
        data = dict(self.data or {})
        data[speaker.id] = speaker
        self.async_set_updated_data(data)

    async def _async_update_data(self) -> dict[str, Speaker]:
        """Fetch all speakers and index them by id."""
        try:
            if self.console_version is None:
                meta = await self.client.get_meta_info()
                self.console_version = meta.get("applicationVersion")
            speakers = await self.client.get_speakers()
        except UnifiAuthError as err:
            # Surface as an auth problem so HA starts a reauth flow.
            from homeassistant.exceptions import ConfigEntryAuthFailed

            raise ConfigEntryAuthFailed(str(err)) from err
        except UnifiConnectionError as err:
            raise UpdateFailed(str(err)) from err

        return {speaker.id: speaker for speaker in speakers}
