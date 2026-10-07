"""Sensor platform: speaker playback status and connection state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant

from . import UnifiAiSpeakerConfigEntry
from .api import Speaker
from .coordinator import UnifiAiSpeakerCoordinator
from .entity import UnifiAiSpeakerEntity

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback


@dataclass(frozen=True, kw_only=True)
class SpeakerSensorDescription(SensorEntityDescription):
    """Describes a speaker sensor."""

    value_fn: Callable[[Speaker], str | None]


SENSORS: tuple[SpeakerSensorDescription, ...] = (
    SpeakerSensorDescription(
        key="speaker_status",
        translation_key="speaker_status",
        device_class=SensorDeviceClass.ENUM,
        options=["idle", "streaming", "playing", "tts_playing", "uploading"],
        value_fn=lambda s: s.speaker_status,
    ),
    SpeakerSensorDescription(
        key="connection_state",
        translation_key="connection_state",
        device_class=SensorDeviceClass.ENUM,
        options=["CONNECTED", "CONNECTING", "DISCONNECTED"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.state,
    ),
    SpeakerSensorDescription(
        key="speaker_mode",
        translation_key="speaker_mode",
        device_class=SensorDeviceClass.ENUM,
        options=["listen", "talk"],
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.speaker_mode,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiAiSpeakerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for each speaker."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        SpeakerSensor(coordinator, speaker_id, description)
        for speaker_id in coordinator.data
        for description in SENSORS
    )


class SpeakerSensor(UnifiAiSpeakerEntity, SensorEntity):
    """A diagnostic/status sensor for a speaker."""

    entity_description: SpeakerSensorDescription

    def __init__(
        self,
        coordinator: UnifiAiSpeakerCoordinator,
        speaker_id: str,
        description: SpeakerSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, speaker_id)
        self.entity_description = description
        self._attr_unique_id = f"{speaker_id}_{description.key}"

    @property
    def available(self) -> bool:
        """Connection-state sensor stays available to report DISCONNECTED.

        Every other sensor defers to the base class, which already requires
        the speaker to be connected.
        """
        if self.entity_description.key == "connection_state":
            return self.coordinator.last_update_success and self.speaker is not None
        return super().available

    @property
    def native_value(self) -> str | None:
        """Return the current sensor value."""
        if (speaker := self.speaker) is None:
            return None
        return self.entity_description.value_fn(speaker)
