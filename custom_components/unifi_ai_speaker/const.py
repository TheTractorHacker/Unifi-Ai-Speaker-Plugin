"""Constants for the UniFi AI Speaker integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "unifi_ai_speaker"

MANUFACTURER: Final = "Ubiquiti"

# ---------------------------------------------------------------------------
# Config entry / options keys
# ---------------------------------------------------------------------------
CONF_HOST: Final = "host"
CONF_API_KEY: Final = "api_key"
CONF_VERIFY_SSL: Final = "verify_ssl"

CONF_RESTORE_DELAY: Final = "restore_delay"
CONF_ALARM_PANEL: Final = "alarm_panel"
CONF_PLAY_TEST_SOUND: Final = "play_test_sound_after_restore"
CONF_TEST_SOUND_DELAY: Final = "test_sound_delay"

# Verify SSL defaults to False because UniFi Protect consoles ship with a
# self-signed certificate on the LAN. The config flow explains the trade-off.
DEFAULT_VERIFY_SSL: Final = False

# The alarm audio is ~1 minute and Protect repeats it 5 times (~5 minutes).
# Default restore delay is 10 minutes to guarantee playback has finished.
DEFAULT_RESTORE_DELAY: Final = 600  # seconds
MIN_RESTORE_DELAY: Final = 10
MAX_RESTORE_DELAY: Final = 3600

# A short confirmation chime once the alarm mute ends, so a listener knows
# it's over. On by default. The chime plays at its OWN (typically quieter)
# volume rather than the speaker's real/alarm volume -- a speaker mounted for
# an alarm, doorbell, or announcement use may normally run quite loud, and the
# chime is just a notification, not another alarm. Only after the chime plays
# is the speaker restored to the real saved volume. test_sound_delay is used
# both before playing it (let the quiet-volume change apply) and after (give
# the chime time to actually sound before the volume changes again).
CONF_TEST_SOUND_VOLUME: Final = "test_sound_volume"
DEFAULT_PLAY_TEST_SOUND: Final = True
DEFAULT_TEST_SOUND_DELAY: Final = 2  # seconds
MIN_TEST_SOUND_DELAY: Final = 0
MAX_TEST_SOUND_DELAY: Final = 30
DEFAULT_TEST_SOUND_VOLUME: Final = 30  # 0-100, independent of the real volume

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
API_BASE_PATH: Final = "/proxy/protect/integration/v1"
API_KEY_HEADER: Final = "X-API-Key"
REQUEST_TIMEOUT: Final = 15  # seconds

UPDATE_INTERVAL: Final = timedelta(seconds=30)

VOLUME_MIN: Final = 0
VOLUME_MAX: Final = 100

# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------
SERVICE_MUTE_FOR_ALARM_DISARM: Final = "mute_for_alarm_disarm"
SERVICE_RESTORE_ALARM_VOLUME: Final = "restore_alarm_volume"
SERVICE_CANCEL_ALARM_MUTE: Final = "cancel_alarm_mute"

ATTR_RESTORE_DELAY: Final = "restore_delay"

# ---------------------------------------------------------------------------
# Persistent storage (alarm mute state) — survives Home Assistant restarts
# ---------------------------------------------------------------------------
STORAGE_VERSION: Final = 1
STORAGE_KEY_PREFIX: Final = DOMAIN  # per-entry key: "unifi_ai_speaker.<entry_id>"
