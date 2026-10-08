"""Tests for the UniFi Protect speaker API client."""

from __future__ import annotations

import pytest
from custom_components.unifi_ai_speaker.api import (
    Speaker,
    UnifiAiSpeakerApiClient,
    UnifiAuthError,
    UnifiConnectionError,
    UnifiNotFoundError,
    normalize_host,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import ALL_SPEAKERS, API_KEY, BASE, HOST, SPEAKER_WITH_MIC


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.1.0.29", "10.1.0.29"),
        ("https://10.1.0.29", "10.1.0.29"),
        ("https://10.1.0.29/", "10.1.0.29"),
        ("http://10.1.0.29/proxy/protect", "10.1.0.29"),
        ("10.1.0.29:443", "10.1.0.29:443"),
        ("  10.1.0.29  ", "10.1.0.29"),
    ],
)
def test_normalize_host(raw: str, expected: str) -> None:
    """Host normalization strips scheme, path, and whitespace."""
    assert normalize_host(raw) == expected


def test_speaker_from_api_parses_all_fields() -> None:
    """Speaker.from_api reads the verified field names."""
    speaker = Speaker.from_api(SPEAKER_WITH_MIC)
    assert speaker.id == "spk-with-mic"
    assert speaker.volume == 75
    assert speaker.mic_volume == 40
    assert speaker.is_mic_enabled is True
    assert speaker.has_mic is True
    assert speaker.model == "UP AI Speaker"
    assert speaker.speaker_status == "idle"
    assert speaker.available is True


def test_speaker_from_api_tolerates_missing_optionals() -> None:
    """Missing optional blocks fall back to safe defaults."""
    speaker = Speaker.from_api({"id": "x", "mac": "AABBCCDDEEFF"})
    assert speaker.volume == 0
    assert speaker.has_mic is False
    assert speaker.state == "DISCONNECTED"
    assert speaker.available is False


def _client(hass: HomeAssistant) -> UnifiAiSpeakerApiClient:
    return UnifiAiSpeakerApiClient(HOST, API_KEY, async_get_clientsession(hass))


async def test_get_speakers(hass: HomeAssistant, aioclient_mock) -> None:
    """Discovery returns all parsed speakers."""
    aioclient_mock.get(f"{BASE}/speakers", json=ALL_SPEAKERS)
    speakers = await _client(hass).get_speakers()
    assert {s.id for s in speakers} == {"spk-with-mic", "spk-no-mic"}


async def test_auth_failure_raises(hass: HomeAssistant, aioclient_mock) -> None:
    """401 maps to UnifiAuthError and the key is not leaked."""
    aioclient_mock.get(f"{BASE}/speakers", status=401)
    with pytest.raises(UnifiAuthError) as err:
        await _client(hass).get_speakers()
    assert API_KEY not in str(err.value)


async def test_not_found_raises(hass: HomeAssistant, aioclient_mock) -> None:
    """404 maps to UnifiNotFoundError."""
    aioclient_mock.get(f"{BASE}/speakers/nope", status=404)
    with pytest.raises(UnifiNotFoundError):
        await _client(hass).get_speaker("nope")


async def test_server_error_raises_connection(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """5xx maps to UnifiConnectionError."""
    aioclient_mock.get(f"{BASE}/speakers", status=503)
    with pytest.raises(UnifiConnectionError):
        await _client(hass).get_speakers()


async def test_set_volume_patches_correct_body(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """set_volume issues PATCH with the verified 'volume' key and clamps range."""
    updated = {**SPEAKER_WITH_MIC, "volume": 100}
    aioclient_mock.patch(f"{BASE}/speakers/spk-with-mic", json=updated)
    result = await _client(hass).set_volume("spk-with-mic", 150)  # clamp to 100
    assert result.volume == 100
    assert aioclient_mock.mock_calls[-1][2] == {"volume": 100}


async def test_set_mic_volume_and_enabled(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """Mic volume and mic-enabled send the verified keys."""
    aioclient_mock.patch(
        f"{BASE}/speakers/spk-with-mic",
        json={**SPEAKER_WITH_MIC, "micVolume": 20, "isMicEnabled": False},
    )
    await _client(hass).set_mic_volume("spk-with-mic", 20)
    assert aioclient_mock.mock_calls[-1][2] == {"micVolume": 20}
    await _client(hass).set_mic_enabled("spk-with-mic", False)
    assert aioclient_mock.mock_calls[-1][2] == {"isMicEnabled": False}


async def test_test_sound_posts(hass: HomeAssistant, aioclient_mock) -> None:
    """Test-sound posts to the verified endpoint."""
    aioclient_mock.post(f"{BASE}/speakers/spk-with-mic/test-sound", status=204)
    await _client(hass).test_sound("spk-with-mic")
    assert str(aioclient_mock.mock_calls[-1][1]).endswith(
        "/speakers/spk-with-mic/test-sound"
    )


async def test_trigger_alarm_webhook_posts_to_verified_endpoint(
    hass: HomeAssistant, aioclient_mock
) -> None:
    """trigger_alarm_webhook posts to the alarm-manager webhook endpoint."""
    webhook_id = "695d5662-8940-42ef-a3e9-239cbd873d91"
    aioclient_mock.post(f"{BASE}/alarm-manager/webhook/{webhook_id}", status=204)
    await _client(hass).trigger_alarm_webhook(webhook_id)
    assert str(aioclient_mock.mock_calls[-1][1]).endswith(
        f"/alarm-manager/webhook/{webhook_id}"
    )
