"""Base entity for UniFi AI Speaker."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Speaker
from .const import DOMAIN, MANUFACTURER
from .coordinator import UnifiAiSpeakerCoordinator


class UnifiAiSpeakerEntity(CoordinatorEntity[UnifiAiSpeakerCoordinator]):
    """Common base: one Home Assistant device per discovered speaker."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: UnifiAiSpeakerCoordinator, speaker_id: str
    ) -> None:
        """Initialise with the owning coordinator and speaker id."""
        super().__init__(coordinator)
        self._speaker_id = speaker_id

        speaker = self.speaker
        mac = speaker.mac if speaker else ""
        connections = (
            {(CONNECTION_NETWORK_MAC, _format_mac(mac))} if mac else set()
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, speaker_id)},
            manufacturer=MANUFACTURER,
            model=speaker.model if speaker else None,
            name=speaker.name if speaker else None,
            connections=connections,
            serial_number=speaker_id,
        )

    @property
    def speaker(self) -> Speaker | None:
        """Return the current speaker object from coordinator data."""
        return (self.coordinator.data or {}).get(self._speaker_id)

    @property
    def available(self) -> bool:
        """Entity is available while the coordinator works and the speaker is connected.

        A speaker that is known to the console but currently ``DISCONNECTED``
        cannot actually act on writes (volume, mic), so controls correctly
        show as unavailable rather than silently accepting changes that
        won't apply. The diagnostic connection-state sensor overrides this to
        stay available precisely so it can report the disconnected state.
        """
        speaker = self.speaker
        return (
            self.coordinator.last_update_success
            and speaker is not None
            and speaker.available
        )


def _format_mac(mac: str) -> str:
    """Format a bare MAC (e.g. ``24A43C3DFEB9``) as ``24:a4:3c:3d:fe:b9``."""
    cleaned = mac.replace(":", "").replace("-", "").lower()
    if len(cleaned) != 12:
        return mac.lower()
    return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))
