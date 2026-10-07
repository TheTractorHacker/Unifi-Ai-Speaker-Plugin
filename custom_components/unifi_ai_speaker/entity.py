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
        """Entity is available while the speaker is known to the console."""
        return self.coordinator.last_update_success and self.speaker is not None


def _format_mac(mac: str) -> str:
    """Format a bare MAC (e.g. ``24A43C3DFEB9``) as ``24:a4:3c:3d:fe:b9``."""
    cleaned = mac.replace(":", "").replace("-", "").lower()
    if len(cleaned) != 12:
        return mac.lower()
    return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))
