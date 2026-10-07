"""Sample API payloads for tests (no real console data)."""

from __future__ import annotations

HOST = "10.0.0.5"
API_KEY = "test-api-key-should-never-be-logged"
BASE = f"https://{HOST}/proxy/protect/integration/v1"

META_INFO = {"applicationVersion": "7.3.70"}

SPEAKER_WITH_MIC = {
    "id": "spk-with-mic",
    "modelKey": "speaker",
    "state": "CONNECTED",
    "name": "Garage AI Speaker",
    "type": "UP AI Speaker",
    "guid": "11111111-1111-1111-1111-111111111111",
    "mac": "24A43C3DFEB9",
    "volume": 75,
    "micVolume": 40,
    "isMicEnabled": True,
    "speakerState": {"status": "idle", "mode": "listen"},
    "featureFlags": {"hasMic": True},
}

SPEAKER_NO_MIC = {
    "id": "spk-no-mic",
    "modelKey": "speaker",
    "state": "CONNECTED",
    "name": "Yard Horn Speaker",
    "type": "UP AI Horn Speaker",
    "guid": "22222222-2222-2222-2222-222222222222",
    "mac": "D021F98B6AED",
    "volume": 63,
    "micVolume": 0,
    "isMicEnabled": False,
    "speakerState": {"status": "idle", "mode": "listen"},
    "featureFlags": {"hasMic": False},
}

ALL_SPEAKERS = [SPEAKER_WITH_MIC, SPEAKER_NO_MIC]
