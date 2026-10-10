"""Real Home Assistant and stock simplipy objects, without user credentials."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from simplipy.system.v3 import SystemV3

from custom_components.simplirtc import camera


@pytest.fixture
async def camera_factory(tmp_path, monkeypatch):
	hass = HomeAssistant(str(tmp_path))
	monkeypatch.setattr(
		dr, "async_get_device_id_by_identifier", lambda *args, **kwargs: None, raising=False
	)
	api = SimpleNamespace(
		access_token="private-test-token",
		async_request=AsyncMock(),
		subscription_data={123: {"location": {"system": {"cameras": [], "serial": "station"}}}},
	)
	system = SystemV3(api, 123)
	system.camera_data = {
		"hardware-serial": {
			"uuid": "api-camera-uuid",
			"model": "SS001",
			"status": "online",
			"cameraSettings": {"cameraName": "Private Camera", "admin": {"webRTCProvider": "kvs"}},
		},
	}
	system.generate_device_objects()
	manager = SimpleNamespace(
		_api=api,
		coordinator=SimpleNamespace(hass=hass, config_entry=SimpleNamespace(entry_id="native")),
		initial_event_to_use={123: {}},
		systems={123: system},
	)
	created = []

	def create(provider="kvs"):
		system.camera_data["hardware-serial"]["cameraSettings"]["admin"]["webRTCProvider"] = (
			provider
		)
		entity_type = (
			camera.SimpliSafeLegacyCamera
			if provider in {None, "simplisafe"}
			else camera.SimpliSafeCamera
		)
		entity = entity_type(manager, system, system.cameras["hardware-serial"])
		entity.hass = hass
		entity.entity_id = "camera.test"
		created.append(entity)
		return entity

	yield create, manager, system, api
	for entity in created:
		await entity.async_will_remove_from_hass()
