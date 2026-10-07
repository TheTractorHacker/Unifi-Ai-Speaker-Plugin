"""Number platform: speaker volume and microphone volume."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import UnifiAiSpeakerConfigEntry
from .api import Speaker, UnifiAiSpeakerApiClient, UnifiAiSpeakerError
from .const import VOLUME_MAX, VOLUME_MIN
from .coordinator import UnifiAiSpeakerCoordinator
from .entity import UnifiAiSpeakerEntity

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback


@dataclass(frozen=True, kw_only=True)
class SpeakerNumberDescription(NumberEntityDescription):
    """Describes a speaker number entity."""

    value_fn: Callable[[Speaker], int]
    set_fn: Callable[[UnifiAiSpeakerApiClient, str, int], Awaitable[Speaker]]
    requires_mic: bool = False


NUMBERS: tuple[SpeakerNumberDescription, ...] = (
    SpeakerNumberDescription(
        key="volume",
        translation_key="volume",
        native_min_value=VOLUME_MIN,
        native_max_value=VOLUME_MAX,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.BOX,
        value_fn=lambda s: s.volume,
        set_fn=lambda c, i, v: c.set_volume(i, v),
    ),
    SpeakerNumberDescription(
        key="mic_volume",
        translation_key="mic_volume",
        native_min_value=VOLUME_MIN,
        native_max_value=VOLUME_MAX,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.mic_volume,
        set_fn=lambda c, i, v: c.set_mic_volume(i, v),
        requires_mic=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiAiSpeakerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities for each speaker."""
    coordinator = entry.runtime_data.coordinator
    entities: list[SpeakerVolumeNumber] = []
    for speaker_id, speaker in coordinator.data.items():
        for description in NUMBERS:
            if description.requires_mic and not speaker.has_mic:
                continue
            entities.append(
                SpeakerVolumeNumber(coordinator, speaker_id, description)
            )
    async_add_entities(entities)


class SpeakerVolumeNumber(UnifiAiSpeakerEntity, NumberEntity):
    """A volume-style number control for a speaker."""

    entity_description: SpeakerNumberDescription

    def __init__(
        self,
        coordinator: UnifiAiSpeakerCoordinator,
        speaker_id: str,
        description: SpeakerNumberDescription,
    ) -> None:
        """Initialise the number entity."""
        super().__init__(coordinator, speaker_id)
        self.entity_description = description
        self._attr_unique_id = f"{speaker_id}_{description.key}"

    @property
    def native_value(self) -> int | None:
        """Return the current value from coordinator data."""
        if (speaker := self.speaker) is None:
            return None
        return self.entity_description.value_fn(speaker)

    async def async_set_native_value(self, value: float) -> None:
        """Write a new value to the speaker."""
        try:
            updated = await self.entity_description.set_fn(
                self.coordinator.client, self._speaker_id, int(value)
            )
        except UnifiAiSpeakerError as err:
            raise HomeAssistantError(f"Failed to set value: {err}") from err
        self.coordinator.update_speaker(updated)
