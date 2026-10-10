"""Config and options flow for UniFi AI Speaker."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    UnifiAiSpeakerApiClient,
    UnifiAuthError,
    UnifiConnectionError,
    normalize_host,
)
from .const import (
    CONF_ALARM_PANEL,
    CONF_ALARM_WEBHOOK_ID,
    CONF_API_KEY,
    CONF_HOST,
    CONF_PLAY_ARM_SOUND,
    CONF_PLAY_TEST_SOUND,
    CONF_RESTORE_DELAY,
    CONF_SILENCE_METHOD,
    CONF_TEST_SOUND_DELAY,
    CONF_TEST_SOUND_VOLUME,
    CONF_VERIFY_SSL,
    DEFAULT_PLAY_ARM_SOUND,
    DEFAULT_PLAY_TEST_SOUND,
    DEFAULT_RESTORE_DELAY,
    DEFAULT_TEST_SOUND_DELAY,
    DEFAULT_TEST_SOUND_VOLUME,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    MAX_RESTORE_DELAY,
    MAX_TEST_SOUND_DELAY,
    MIN_RESTORE_DELAY,
    MIN_TEST_SOUND_DELAY,
    SILENCE_METHOD_MUTE,
    SILENCE_METHOD_TEST_SOUND,
    VOLUME_MAX,
    VOLUME_MIN,
)

_LOGGER = logging.getLogger(__name__)


async def _validate(hass, host: str, api_key: str, verify_ssl: bool) -> int:
    """Validate connectivity and auth; return the number of speakers found.

    Raises ``UnifiAuthError`` / ``UnifiConnectionError`` on failure.
    """
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)
    client = UnifiAiSpeakerApiClient(host, api_key, session)
    # meta/info confirms the API key is valid before we enumerate devices.
    await client.get_meta_info()
    speakers = await client.get_speakers()
    return len(speakers)


class UnifiAiSpeakerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UniFi AI Speaker config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise reauth bookkeeping."""
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = normalize_host(user_input[CONF_HOST])
            api_key = user_input[CONF_API_KEY]
            verify_ssl = user_input[CONF_VERIFY_SSL]

            await self.async_set_unique_id(host)
            self._abort_if_unique_id_configured()

            try:
                count = await _validate(self.hass, host, api_key, verify_ssl)
            except UnifiAuthError:
                errors["base"] = "invalid_auth"
            except UnifiConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating UniFi AI Speaker")
                errors["base"] = "unknown"
            else:
                if count == 0:
                    errors["base"] = "no_speakers"
                else:
                    return self.async_create_entry(
                        title=f"UniFi AI Speaker ({host})",
                        data={
                            CONF_HOST: host,
                            CONF_API_KEY: api_key,
                            CONF_VERIFY_SSL: verify_ssl,
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                    vol.Required(CONF_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Required(
                        CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL
                    ): BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when the API key stops working."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect a new API key."""
        errors: dict[str, str] = {}
        assert self._reauth_entry is not None
        data = self._reauth_entry.data

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            try:
                await _validate(
                    self.hass,
                    data[CONF_HOST],
                    api_key,
                    data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                )
            except UnifiAuthError:
                errors["base"] = "invalid_auth"
            except UnifiConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    self._reauth_entry,
                    data={**data, CONF_API_KEY: api_key},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user edit host / API key / Verify SSL from the UI.

        Reached via the config entry's ⋮ menu → Reconfigure. The API key
        field is optional: leave it blank to keep the currently stored key.
        """
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            host = normalize_host(user_input[CONF_HOST])
            api_key = user_input.get(CONF_API_KEY) or entry.data[CONF_API_KEY]
            verify_ssl = user_input[CONF_VERIFY_SSL]

            duplicate = any(
                other.entry_id != entry.entry_id
                and normalize_host(other.data.get(CONF_HOST, "")) == host
                for other in self.hass.config_entries.async_entries(DOMAIN)
            )
            if duplicate:
                errors["base"] = "already_configured"
            else:
                try:
                    count = await _validate(self.hass, host, api_key, verify_ssl)
                except UnifiAuthError:
                    errors["base"] = "invalid_auth"
                except UnifiConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "Unexpected error validating UniFi AI Speaker"
                    )
                    errors["base"] = "unknown"
                else:
                    if count == 0:
                        errors["base"] = "no_speakers"
                    else:
                        self.hass.config_entries.async_update_entry(
                            entry,
                            unique_id=host,
                            title=f"UniFi AI Speaker ({host})",
                            data={
                                CONF_HOST: host,
                                CONF_API_KEY: api_key,
                                CONF_VERIFY_SSL: verify_ssl,
                            },
                        )
                        await self.hass.config_entries.async_reload(
                            entry.entry_id
                        )
                        return self.async_abort(reason="reconfigure_successful")

        current = entry.data
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HOST, default=current[CONF_HOST]
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                    vol.Optional(CONF_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Required(
                        CONF_VERIFY_SSL,
                        default=current.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                    ): BooleanSelector(),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> UnifiAiSpeakerOptionsFlow:
        """Return the options flow."""
        return UnifiAiSpeakerOptionsFlow()


class UnifiAiSpeakerOptionsFlow(OptionsFlow):
    """Options: alarm-disarm silence behaviour, alarm trigger, and panel.

    These are global, entry-wide settings (not per-speaker): every speaker
    discovered on this console shares them. That matches the existing
    per-call ``restore_delay``/``play_test_sound``/``test_sound_delay``/
    ``test_sound_volume``/``webhook_id`` service fields, which can still
    override these per invocation if ever needed.

    ``silence_method`` is a presentation-only choice shown here as "Mute
    only" vs "Play a test sound" — it is converted to the underlying
    ``CONF_PLAY_TEST_SOUND`` boolean on save, so every other module keeps
    reading/writing that one boolean unchanged.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            cleaned = dict(user_input)
            method = cleaned.pop(CONF_SILENCE_METHOD, SILENCE_METHOD_TEST_SOUND)
            cleaned[CONF_PLAY_TEST_SOUND] = method == SILENCE_METHOD_TEST_SOUND
            # Empty optional fields disable the corresponding feature.
            if not cleaned.get(CONF_ALARM_PANEL):
                cleaned.pop(CONF_ALARM_PANEL, None)
            if not cleaned.get(CONF_ALARM_WEBHOOK_ID):
                cleaned.pop(CONF_ALARM_WEBHOOK_ID, None)
            return self.async_create_entry(title="", data=cleaned)

        options = self.config_entry.options
        current_method = (
            SILENCE_METHOD_TEST_SOUND
            if options.get(CONF_PLAY_TEST_SOUND, DEFAULT_PLAY_TEST_SOUND)
            else SILENCE_METHOD_MUTE
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_RESTORE_DELAY,
                        default=options.get(
                            CONF_RESTORE_DELAY, DEFAULT_RESTORE_DELAY
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_RESTORE_DELAY,
                            max=MAX_RESTORE_DELAY,
                            step=1,
                            unit_of_measurement="seconds",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_SILENCE_METHOD, default=current_method
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=SILENCE_METHOD_MUTE,
                                    label="Mute only (silent)",
                                ),
                                SelectOptionDict(
                                    value=SILENCE_METHOD_TEST_SOUND,
                                    label="Play a test sound",
                                ),
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Required(
                        CONF_TEST_SOUND_DELAY,
                        default=options.get(
                            CONF_TEST_SOUND_DELAY, DEFAULT_TEST_SOUND_DELAY
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_TEST_SOUND_DELAY,
                            max=MAX_TEST_SOUND_DELAY,
                            step=0.5,
                            unit_of_measurement="seconds",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_TEST_SOUND_VOLUME,
                        default=options.get(
                            CONF_TEST_SOUND_VOLUME, DEFAULT_TEST_SOUND_VOLUME
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=VOLUME_MIN,
                            max=VOLUME_MAX,
                            step=1,
                            unit_of_measurement="%",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(
                        CONF_ALARM_PANEL,
                        description={
                            "suggested_value": options.get(CONF_ALARM_PANEL)
                        },
                    ): EntitySelector(
                        EntitySelectorConfig(domain="alarm_control_panel")
                    ),
                    vol.Required(
                        CONF_PLAY_ARM_SOUND,
                        default=options.get(
                            CONF_PLAY_ARM_SOUND, DEFAULT_PLAY_ARM_SOUND
                        ),
                    ): BooleanSelector(),
                    vol.Optional(
                        CONF_ALARM_WEBHOOK_ID,
                        description={
                            "suggested_value": options.get(CONF_ALARM_WEBHOOK_ID)
                        },
                    ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                }
            ),
        )
