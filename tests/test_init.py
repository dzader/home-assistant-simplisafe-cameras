"""Verify child cameras follow native authentication reloads and unload cleanly."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.config_entries import ConfigEntryChange, ConfigEntryState
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components import simplirtc
from custom_components.simplirtc.const import ATTR_CONFIG_ENTRY_ID, DOMAIN


async def test_native_runtime_reload_rebinds_existing_child(monkeypatch):
	old_runtime, new_runtime = object(), object()
	parent = SimpleNamespace(
		domain="simplisafe",
		entry_id="native",
		state=ConfigEntryState.LOADED,
		runtime_data=new_runtime,
	)
	child = SimpleNamespace(
		data={ATTR_CONFIG_ENTRY_ID: "native"},
		entry_id="camera",
		state=ConfigEntryState.LOADED,
		runtime_data=old_runtime,
	)
	entries = SimpleNamespace(
		async_get_entry=Mock(return_value=parent),
		async_entries=Mock(return_value=[child]),
		async_loaded_entries=Mock(return_value=[]),
		async_reload=AsyncMock(),
	)
	tasks = []
	hass = SimpleNamespace(
		config_entries=entries,
		async_create_task=lambda work: tasks.append(asyncio.create_task(work)),
	)
	connect = Mock()
	monkeypatch.setattr(simplirtc, "async_when_setup", Mock())
	monkeypatch.setattr(simplirtc, "async_dispatcher_connect", connect)
	discovery = Mock()
	monkeypatch.setattr(simplirtc.discovery_flow, "async_create_flow", discovery)
	await simplirtc.async_setup(hass, {DOMAIN: {}})
	listener = connect.call_args.args[2]
	listener(ConfigEntryChange.UPDATED, parent)
	await asyncio.gather(*tasks)
	entries.async_reload.assert_awaited_once_with("camera")
	discovery.assert_not_called()
	child.runtime_data = new_runtime
	listener(ConfigEntryChange.UPDATED, parent)
	await asyncio.gather(*tasks)
	assert entries.async_reload.await_count == 1


async def test_native_unload_only_unloads_camera_child(monkeypatch):
	parent = SimpleNamespace(
		domain="simplisafe", entry_id="native", state=ConfigEntryState.NOT_LOADED
	)
	child = SimpleNamespace(
		data={ATTR_CONFIG_ENTRY_ID: "native"}, entry_id="camera", state=ConfigEntryState.LOADED
	)
	entries = SimpleNamespace(
		async_get_entry=Mock(return_value=parent),
		async_entries=Mock(return_value=[child]),
		async_loaded_entries=Mock(return_value=[]),
		async_unload=AsyncMock(),
	)
	tasks = []
	hass = SimpleNamespace(
		config_entries=entries,
		async_create_task=lambda work: tasks.append(asyncio.create_task(work)),
	)
	connect = Mock()
	monkeypatch.setattr(simplirtc, "async_when_setup", Mock())
	monkeypatch.setattr(simplirtc, "async_dispatcher_connect", connect)
	await simplirtc.async_setup(hass, {DOMAIN: {}})
	connect.call_args.args[2](ConfigEntryChange.UPDATED, parent)
	await asyncio.gather(*tasks)
	entries.async_unload.assert_awaited_once_with("camera")


async def test_entry_waits_for_native_authentication():
	parent = SimpleNamespace(state=ConfigEntryState.SETUP_RETRY)
	hass = SimpleNamespace(
		config_entries=SimpleNamespace(async_get_entry=Mock(return_value=parent))
	)
	entry = SimpleNamespace(data={ATTR_CONFIG_ENTRY_ID: "native"})
	with pytest.raises(ConfigEntryNotReady, match="native SimpliSafe integration"):
		await simplirtc.async_setup_entry(hass, entry)


async def test_platform_unload_keeps_native_integration_running():
	unload = AsyncMock(return_value=True)
	hass = SimpleNamespace(config_entries=SimpleNamespace(async_unload_platforms=unload))
	entry = object()
	assert await simplirtc.async_unload_entry(hass, entry)
	unload.assert_awaited_once_with(entry, simplirtc.PLATFORMS)
