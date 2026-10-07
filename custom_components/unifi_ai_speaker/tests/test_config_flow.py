"""Tests for the config and options flow."""

from __future__ import annotations

from custom_components.unifi_ai_speaker.api import (
    UnifiAuthError,
    UnifiConnectionError,
)
from custom_components.unifi_ai_speaker.const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_RESTORE_DELAY,
    CONF_VERIFY_SSL,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .const import API_KEY


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


async def test_user_flow_success(hass: HomeAssistant, mock_api) -> None:
    """A valid host/key with speakers creates an entry."""
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: "https://10.0.0.5/", CONF_API_KEY: API_KEY, CONF_VERIFY_SSL: False},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == "10.0.0.5"  # normalized
    assert result["data"][CONF_API_KEY] == API_KEY


async def test_user_flow_invalid_auth(hass: HomeAssistant, mock_api) -> None:
    """A rejected key shows an auth error."""
    mock_api.get_meta_info.side_effect = UnifiAuthError("bad")
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: "10.0.0.5", CONF_API_KEY: "wrong", CONF_VERIFY_SSL: False},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass: HomeAssistant, mock_api) -> None:
    """An unreachable console shows a connection error."""
    mock_api.get_meta_info.side_effect = UnifiConnectionError("down")
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: "10.0.0.5", CONF_API_KEY: API_KEY, CONF_VERIFY_SSL: False},
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_no_speakers(hass: HomeAssistant, mock_api) -> None:
    """Connecting with zero speakers shows the no-speakers message."""
    mock_api.get_speakers.return_value = []
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: "10.0.0.5", CONF_API_KEY: API_KEY, CONF_VERIFY_SSL: False},
    )
    assert result["errors"] == {"base": "no_speakers"}


async def test_duplicate_console_aborts(
    hass: HomeAssistant, mock_api, mock_config_entry
) -> None:
    """A second entry for the same host aborts."""
    mock_config_entry.add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: "10.0.0.5", CONF_API_KEY: API_KEY, CONF_VERIFY_SSL: False},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(hass: HomeAssistant, setup_integration) -> None:
    """The options flow stores a custom restore delay."""
    result = await hass.config_entries.options.async_init(
        setup_integration.entry_id
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_RESTORE_DELAY: 300}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_integration.options[CONF_RESTORE_DELAY] == 300
