"""Binary sensor platform: diagnostic alarm-mute status."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import UnifiAiSpeakerConfigEntry
from .coordinator import UnifiAiSpeakerCoordinator
from .entity import UnifiAiSpeakerEntity
from .mute import AlarmMuteController

DESCRIPTION = BinarySensorEntityDescription(
    key="alarm_mute_active",
    translation_key="alarm_mute_active",
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UnifiAiSpeakerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the alarm-mute binary sensor for each speaker."""
    coordinator = entry.runtime_data.coordinator
    mute = entry.runtime_data.mute
    async_add_entities(
        AlarmMuteBinarySensor(coordinator, mute, speaker_id)
        for speaker_id in coordinator.data
    )


class AlarmMuteBinarySensor(UnifiAiSpeakerEntity, BinarySensorEntity):
    """Shows whether an alarm-disarm mute is currently active."""

    entity_description = DESCRIPTION

    def __init__(
        self,
        coordinator: UnifiAiSpeakerCoordinator,
        mute: AlarmMuteController,
        speaker_id: str,
    ) -> None:
        """Initialise the binary sensor."""
        super().__init__(coordinator, speaker_id)
        self._mute = mute
        self._attr_unique_id = f"{speaker_id}_alarm_mute_active"

    @property
    def available(self) -> bool:
        """Available whenever the coordinator is working (reflects local state)."""
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        """Return whether the speaker is currently alarm-muted."""
        return self._mute.is_muted(self._speaker_id)

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        """Expose the saved volume and restore time (never any credentials)."""
        state = self._mute.get_state(self._speaker_id)
        if state is None:
            return None
        return {
            "original_volume": state.original_volume,
            "restore_delay": state.restore_delay,
            "restore_at": dt_util.utc_from_timestamp(
                state.restore_deadline
            ).isoformat(),
        }
