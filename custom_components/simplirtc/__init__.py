"""Integration providing support to the Simplisafe camera."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.simplisafe import (
	DOMAIN as SIMPLISAFE_DOMAIN,
)
from homeassistant.components.simplisafe import (
	SimpliSafe,
)
from homeassistant.config_entries import (
	SIGNAL_CONFIG_ENTRY_CHANGED,
	SOURCE_SYSTEM,
	ConfigEntry,
	ConfigEntryChange,
	ConfigEntryState,
)
from homeassistant.const import (
	Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import (
	config_validation as cv,
)
from homeassistant.helpers import (
	discovery_flow,
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.typing import ConfigType
from homeassistant.setup import async_when_setup

from .const import (
	ATTR_CONFIG_ENTRY_ID,
	DOMAIN,
)
from .webrtc import async_register_webrtc_client_config_handler

PLATFORMS = [
	Platform.CAMERA,
	Platform.EVENT,
]

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)
_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
	"""Set up the Simplirtc component."""
	async_when_setup(hass, Platform.CAMERA, async_register_webrtc_client_config_handler)

	if DOMAIN not in config:
		return True

	sync_lock = asyncio.Lock()

	async def synchronize(entry_id: str) -> None:
		"""Serialize native reloads and evaluate the latest parent state."""
		async with sync_lock:
			parent = hass.config_entries.async_get_entry(entry_id)
			children = [
				child
				for child in hass.config_entries.async_entries(DOMAIN)
				if child.data.get(ATTR_CONFIG_ENTRY_ID) == entry_id
			]
			if parent is None:
				await _async_remove_config_entries(hass, entry_id)
			elif parent.state == ConfigEntryState.LOADED:
				if not children:
					discovery_flow.async_create_flow(
						hass,
						DOMAIN,
						context={"source": SOURCE_SYSTEM},
						data={ATTR_CONFIG_ENTRY_ID: entry_id},
					)
				for child in children:
					if (
						child.state != ConfigEntryState.LOADED
						or child.runtime_data is not parent.runtime_data
					):
						await hass.config_entries.async_reload(child.entry_id)
			elif parent.state == ConfigEntryState.NOT_LOADED:
				for child in children:
					if child.state == ConfigEntryState.LOADED:
						await hass.config_entries.async_unload(child.entry_id)

	@callback
	def async_config_entry_changed(change: ConfigEntryChange, entry: ConfigEntry) -> None:
		if entry.domain != SIMPLISAFE_DOMAIN:
			return
		if change == ConfigEntryChange.REMOVED or entry.state in {
			ConfigEntryState.LOADED,
			ConfigEntryState.NOT_LOADED,
		}:
			hass.async_create_task(synchronize(entry.entry_id))

	async_dispatcher_connect(
		hass,
		SIGNAL_CONFIG_ENTRY_CHANGED,
		async_config_entry_changed,
	)

	for entry in hass.config_entries.async_loaded_entries(SIMPLISAFE_DOMAIN):
		async_config_entry_changed(ConfigEntryChange.ADDED, entry)

	return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry[SimpliSafe]) -> bool:
	"""Set up SimpliSafe from a config entry."""
	entry_id: str = entry.data[ATTR_CONFIG_ENTRY_ID]

	simplisafe_entry: ConfigEntry[SimpliSafe] | None = hass.config_entries.async_get_entry(entry_id)
	if simplisafe_entry is None:
		_LOGGER.debug("Skipping setup for missing SimpliSafe entry: %s", entry_id)
		return False

	if simplisafe_entry.state != ConfigEntryState.LOADED:
		raise ConfigEntryNotReady("The native SimpliSafe integration is not loaded")
	entry.runtime_data = simplisafe_entry.runtime_data
	await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
	return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
	"""Unload camera and event entities without unloading the native alarm integration."""
	return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_remove_config_entries(hass: HomeAssistant, entry_id: str):
	await asyncio.gather(
		*(
			hass.config_entries.async_remove(entry.entry_id)
			for entry in hass.config_entries.async_entries(DOMAIN)
			if entry.data.get(ATTR_CONFIG_ENTRY_ID) == entry_id
		)
	)
