"""Button platform: test sound."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import UnifiAiSpeakerConfigEntry
from .api import UnifiAiSpeakerError
from .coordinator import UnifiAiSpeakerCoordinator
from .entity import UnifiAiSpeakerEntity

DESCRIPTION = ButtonEntityDescription(
    key="test_sound",
    translation_key="test_sound",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiAiSpeakerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the test-sound button for each speaker."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        TestSoundButton(coordinator, speaker_id) for speaker_id in coordinator.data
    )


class TestSoundButton(UnifiAiSpeakerEntity, ButtonEntity):
    """Play the speaker's test sound."""

    entity_description = DESCRIPTION

    def __init__(
        self, coordinator: UnifiAiSpeakerCoordinator, speaker_id: str
    ) -> None:
        """Initialise the button."""
        super().__init__(coordinator, speaker_id)
        self._attr_unique_id = f"{speaker_id}_test_sound"

    async def async_press(self) -> None:
        """Trigger the test sound."""
        try:
            await self.coordinator.client.test_sound(self._speaker_id)
        except UnifiAiSpeakerError as err:
            raise HomeAssistantError(f"Failed to play test sound: {err}") from err
