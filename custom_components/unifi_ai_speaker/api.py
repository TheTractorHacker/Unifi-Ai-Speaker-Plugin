"""Thin async client for the UniFi Protect local Integration API.

Only the speaker-related surface of the API is implemented. Every endpoint and
field name used here was verified against the official UniFi Protect Integration
API OpenAPI specification (v7.3.70):

    GET   /v1/meta/info
    GET   /v1/speakers
    GET   /v1/speakers/{id}
    PATCH /v1/speakers/{id}          body: {name?, volume?, micVolume?, isMicEnabled?}
    POST  /v1/speakers/{id}/test-sound   body: {volume?}

Authentication is the ``X-API-Key`` header. The API key is treated as a secret
and is never logged, stored in attributes, or included in diagnostics/errors.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from json import loads as json_loads
from typing import Any

import aiohttp

from .const import (
    API_BASE_PATH,
    API_KEY_HEADER,
    REQUEST_TIMEOUT,
    VOLUME_MAX,
    VOLUME_MIN,
)

_LOGGER = logging.getLogger(__name__)


class UnifiAiSpeakerError(Exception):
    """Base error for the UniFi AI Speaker API client."""


class UnifiAuthError(UnifiAiSpeakerError):
    """Raised when authentication fails (HTTP 401/403)."""


class UnifiConnectionError(UnifiAiSpeakerError):
    """Raised when the console is unreachable or returns a server error."""


class UnifiNotFoundError(UnifiAiSpeakerError):
    """Raised when a speaker id is not found (HTTP 404)."""


@dataclass(slots=True)
class Speaker:
    """A parsed UniFi AI Speaker.

    Only fields that the Integration API actually returns are represented.
    """

    id: str
    name: str | None
    model: str | None
    mac: str
    guid: str | None
    state: str  # CONNECTED / CONNECTING / DISCONNECTED
    volume: int
    mic_volume: int
    is_mic_enabled: bool
    has_mic: bool
    speaker_status: str | None  # idle / streaming / playing / tts_playing / uploading
    speaker_mode: str | None  # listen / talk

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Speaker:
        """Build a Speaker from a raw API object, tolerating missing optionals."""
        speaker_state = data.get("speakerState") or {}
        feature_flags = data.get("featureFlags") or {}
        return cls(
            id=data["id"],
            name=data.get("name"),
            model=data.get("type"),
            mac=data.get("mac", ""),
            guid=data.get("guid"),
            state=data.get("state", "DISCONNECTED"),
            volume=int(data.get("volume", 0)),
            mic_volume=int(data.get("micVolume", 0)),
            is_mic_enabled=bool(data.get("isMicEnabled", False)),
            has_mic=bool(feature_flags.get("hasMic", False)),
            speaker_status=speaker_state.get("status"),
            speaker_mode=speaker_state.get("mode"),
        )

    @property
    def available(self) -> bool:
        """Whether the speaker is currently reachable by the console."""
        return self.state == "CONNECTED"


def normalize_host(raw_host: str) -> str:
    """Return a bare ``host`` or ``host:port`` from arbitrary user input.

    Accepts values like ``10.1.0.29``, ``https://10.1.0.29``,
    ``https://10.1.0.29/`` or ``10.1.0.29:443`` and strips scheme/path.
    """
    host = raw_host.strip()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.strip("/")
    # Drop any trailing path the user may have pasted.
    host = host.split("/", 1)[0]
    return host


class UnifiAiSpeakerApiClient:
    """Minimal async client for UniFi Protect speakers."""

    def __init__(
        self,
        host: str,
        api_key: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialise the client.

        ``session`` is supplied by Home Assistant and is responsible for TLS
        verification behaviour (a no-verify session is used when the user
        disables Verify SSL). The client never creates its own session.
        """
        self._host = normalize_host(host)
        self._api_key = api_key
        self._session = session
        self._base_url = f"https://{self._host}{API_BASE_PATH}"

    @property
    def host(self) -> str:
        """Return the normalized host (no scheme)."""
        return self._host

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Perform a request and translate transport/HTTP errors.

        Never includes the API key or Authorization headers in any log line or
        raised exception message.
        """
        url = f"{self._base_url}{path}"
        headers = {
            API_KEY_HEADER: self._api_key,
            "Accept": "application/json",
        }
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                resp = await self._session.request(
                    method, url, headers=headers, json=json
                )
        except (TimeoutError, asyncio.TimeoutError) as err:
            raise UnifiConnectionError(f"Timeout talking to {self._host}") from err
        except aiohttp.ClientError as err:
            # Deliberately avoid str(err) leaking full URLs with query params.
            raise UnifiConnectionError(
                f"Connection error talking to {self._host}"
            ) from err

        async with resp:
            if resp.status in (401, 403):
                raise UnifiAuthError("Invalid API key or insufficient permissions")
            if resp.status == 404:
                raise UnifiNotFoundError("Resource not found")
            if resp.status == 429:
                raise UnifiConnectionError("Rate limited by console (HTTP 429)")
            if resp.status >= 500:
                raise UnifiConnectionError(f"Console error (HTTP {resp.status})")
            if resp.status >= 400:
                raise UnifiAiSpeakerError(f"Unexpected response (HTTP {resp.status})")

            if resp.status == 204:
                return None
            raw = await resp.read()
            if not raw:
                return None
            try:
                return json_loads(raw)
            except ValueError as err:
                raise UnifiAiSpeakerError("Invalid JSON in response") from err

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    async def get_meta_info(self) -> dict[str, Any]:
        """Return console meta info, e.g. ``{"applicationVersion": "..."}``."""
        return await self._request("GET", "/meta/info") or {}

    async def get_speakers(self) -> list[Speaker]:
        """Return every speaker known to the console."""
        data = await self._request("GET", "/speakers") or []
        return [Speaker.from_api(item) for item in data]

    async def get_speaker(self, speaker_id: str) -> Speaker:
        """Return a single speaker."""
        data = await self._request("GET", f"/speakers/{speaker_id}")
        return Speaker.from_api(data)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    async def _patch_speaker(self, speaker_id: str, body: dict[str, Any]) -> Speaker:
        data = await self._request("PATCH", f"/speakers/{speaker_id}", json=body)
        return Speaker.from_api(data)

    async def set_volume(self, speaker_id: str, volume: int) -> Speaker:
        """Set the speaker volume (0-100)."""
        volume = max(VOLUME_MIN, min(VOLUME_MAX, int(volume)))
        return await self._patch_speaker(speaker_id, {"volume": volume})

    async def set_mic_volume(self, speaker_id: str, volume: int) -> Speaker:
        """Set the microphone volume (0-100)."""
        volume = max(VOLUME_MIN, min(VOLUME_MAX, int(volume)))
        return await self._patch_speaker(speaker_id, {"micVolume": volume})

    async def set_mic_enabled(self, speaker_id: str, enabled: bool) -> Speaker:
        """Enable or disable the microphone."""
        return await self._patch_speaker(speaker_id, {"isMicEnabled": bool(enabled)})

    async def test_sound(self, speaker_id: str, volume: int | None = None) -> None:
        """Play the speaker test sound, optionally at a specific volume."""
        body: dict[str, Any] = {}
        if volume is not None:
            body["volume"] = max(VOLUME_MIN, min(VOLUME_MAX, int(volume)))
        await self._request("POST", f"/speakers/{speaker_id}/test-sound", json=body)

    async def trigger_alarm_webhook(self, webhook_id: str) -> None:
        """Trigger a configured UniFi Alarm Manager automation.

        ``POST /alarm-manager/webhook/{id}`` — the same endpoint the
        console-provided webhook URL calls (verified against the Protect
        Integration API OpenAPI spec). ``webhook_id`` is the user-defined
        trigger ID configured on the Alarm Manager automation, not a speaker
        ID. Returns 204 on success.
        """
        await self._request("POST", f"/alarm-manager/webhook/{webhook_id}")
