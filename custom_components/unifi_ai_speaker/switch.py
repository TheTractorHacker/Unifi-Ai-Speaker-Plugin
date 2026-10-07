"""Switch platform: microphone enable/disable."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import UnifiAiSpeakerConfigEntry
from .api import UnifiAiSpeakerError
from .coordinator import UnifiAiSpeakerCoordinator
from .entity import UnifiAiSpeakerEntity

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

MIC_DESCRIPTION = SwitchEntityDescription(
    key="mic_enabled",
    translation_key="mic_enabled",
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiAiSpeakerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the microphone switch for speakers that have a mic."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        MicrophoneSwitch(coordinator, speaker_id)
        for speaker_id, speaker in coordinator.data.items()
        if speaker.has_mic
    )


class MicrophoneSwitch(UnifiAiSpeakerEntity, SwitchEntity):
    """Enable or disable the speaker microphone."""

    entity_description = MIC_DESCRIPTION

    def __init__(
        self, coordinator: UnifiAiSpeakerCoordinator, speaker_id: str
    ) -> None:
        """Initialise the switch."""
        super().__init__(coordinator, speaker_id)
        self._attr_unique_id = f"{speaker_id}_mic_enabled"

    @property
    def is_on(self) -> bool | None:
        """Return whether the microphone is enabled."""
        if (speaker := self.speaker) is None:
            return None
        return speaker.is_mic_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the microphone."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the microphone."""
        await self._async_set(False)

    async def _async_set(self, enabled: bool) -> None:
        try:
            updated = await self.coordinator.client.set_mic_enabled(
                self._speaker_id, enabled
            )
        except UnifiAiSpeakerError as err:
            raise HomeAssistantError(f"Failed to set microphone: {err}") from err
        self.coordinator.update_speaker(updated)
