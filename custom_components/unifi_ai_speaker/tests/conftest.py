"""Pytest fixtures for UniFi AI Speaker tests.

These tests use ``pytest-homeassistant-custom-component``. Run them from a
Home Assistant development environment, e.g.::

    pip install pytest-homeassistant-custom-component
    pytest custom_components/unifi_ai_speaker/tests -q

The config directory (the parent of ``custom_components``) must be importable,
which the path shim below arranges when running in-place.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Make ``custom_components.unifi_ai_speaker`` importable when running in place.
_CONFIG_DIR = Path(__file__).resolve().parents[3]
if str(_CONFIG_DIR) not in sys.path:
    sys.path.insert(0, str(_CONFIG_DIR))

from custom_components.unifi_ai_speaker.const import (  # noqa: E402
    CONF_API_KEY,
    CONF_HOST,
    CONF_VERIFY_SSL,
    DOMAIN,
)
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
)

from .const import ALL_SPEAKERS, API_KEY, HOST, META_INFO  # noqa: E402

pytest_plugins = ("pytest_homeassistant_custom_component",)


@pytest.fixture(scope="session", autouse=True)
def _warm_dns_resolver():
    """Start aiohttp's pycares DNS thread once, before per-test thread snapshots.

    aiohttp's async resolver spawns a daemon thread the first time any real
    ``ClientSession`` is created. Warming it at session scope keeps the test
    harness's per-test "no lingering threads" check clean; it is purely a test
    artifact and unrelated to integration behaviour.
    """
    channel = None
    try:
        import pycares

        channel = pycares.Channel()
    except Exception:  # noqa: BLE001 - best effort, resolver may differ
        pass
    yield
    del channel


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of the custom integration in every test."""
    yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a mock config entry for the integration."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"UniFi AI Speaker ({HOST})",
        unique_id=HOST,
        data={
            CONF_HOST: HOST,
            CONF_API_KEY: API_KEY,
            CONF_VERIFY_SSL: False,
        },
    )


@pytest.fixture
def mock_api():
    """Patch the API client with an AsyncMock, pre-seeded with sample data.

    Each API method is a plain AsyncMock; tests can assert calls and tweak
    return values / side effects.
    """
    from custom_components.unifi_ai_speaker.api import Speaker

    speakers = {s["id"]: Speaker.from_api(s) for s in ALL_SPEAKERS}

    client = AsyncMock()
    client.host = HOST
    client.get_meta_info.return_value = META_INFO
    client.get_speakers.return_value = list(speakers.values())

    async def _get_speaker(speaker_id):
        return speakers[speaker_id]

    client.get_speaker.side_effect = _get_speaker

    with patch(
        "custom_components.unifi_ai_speaker.UnifiAiSpeakerApiClient",
        return_value=client,
    ), patch(
        "custom_components.unifi_ai_speaker.config_flow.UnifiAiSpeakerApiClient",
        return_value=client,
    ):
        yield client


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_api
) -> ConfigEntry:
    """Set up the integration with a mocked API and return the entry."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
