"""Test actual HA camera interfaces, backend selection, and signaling lifecycle."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.components.camera import StreamType, WebRTCAnswer, WebRTCError
from homeassistant.exceptions import HomeAssistantError

from custom_components.simplirtc import camera
from custom_components.simplirtc.backend import CameraBackend, describe_backend


@pytest.mark.parametrize(
	("settings", "backend", "reason"),
	[
		({}, CameraBackend.LEGACY, None),
		({"admin": {}}, CameraBackend.LEGACY, None),
		({"admin": {"webRTCProvider": "simplisafe"}}, CameraBackend.LEGACY, None),
		({"admin": {"webRTCProvider": "kvs"}}, CameraBackend.KINESIS, None),
		({"admin": {"webRTCProvider": "mist"}}, CameraBackend.LIVEKIT, None),
		({"admin": {"webRTCProvider": "future"}}, CameraBackend.UNKNOWN, "unknown_webrtc_provider"),
		({"admin": "invalid"}, CameraBackend.UNKNOWN, "unexpected_settings_schema"),
	],
)
def test_provider_selection(settings, backend, reason):
	result = describe_backend(settings)
	assert result[1:] == (backend, reason)


async def test_real_ha_camera_capabilities(camera_factory):
	create, _, _, _ = camera_factory
	legacy = create(None)
	webrtc = create("kvs")
	assert legacy.camera_capabilities.frontend_stream_types == {StreamType.HLS}
	assert webrtc.camera_capabilities.frontend_stream_types == {StreamType.WEB_RTC}
	assert "private-test-token" not in await legacy.stream_source()


async def test_unknown_provider_skipped(camera_factory):
	_, manager, system, _ = camera_factory
	system.camera_data["hardware-serial"]["cameraSettings"]["admin"]["webRTCProvider"] = "future"
	add_entities = Mock()
	await camera.async_setup_entry(None, SimpleNamespace(runtime_data=manager), add_entities)
	add_entities.assert_called_once_with([])


async def test_live_view_uuid_host_and_wakeup(camera_factory, monkeypatch):
	create, _, _, api = camera_factory
	entity = create("mist")
	api.async_request.side_effect = [
		{"cameraStatus": "sleeping"},
		{"cameraStatus": "waking"},
		{"liveKitDetails": {"liveKitURL": "wss://live.example.test", "userToken": "secret"}},
	]
	monkeypatch.setattr(camera, "LIVE_VIEW_RETRY_DELAY", 0)
	result = await entity._create_stream(camera.LiveKitResponse)
	assert result.liveKitDetails.userToken == "secret"
	assert api.async_request.await_count == 3
	assert api.async_request.call_args.args[1] == "cameras/api-camera-uuid/123/live-view"
	assert api.async_request.call_args.kwargs["headers"] == {
		"Host": "app-hub.prd.aser.simplisafe.com"
	}


async def test_preparation_timeout_closes_signaling(camera_factory, monkeypatch):
	create, _, _, api = camera_factory
	entity = create("mist")
	api.async_request.return_value = {
		"liveKitDetails": {"liveKitURL": "wss://live.test", "userToken": "secret"}
	}
	session = Mock()
	monkeypatch.setattr(camera, "LiveKitSession", Mock(return_value=session))
	monkeypatch.setattr(camera, "WEBRTC_PREPARATION_TIMEOUT", 0.01)
	with pytest.raises(HomeAssistantError, match="Unable to prepare"):
		await entity.async_prepare_webrtc_client_configuration()
	session.close.assert_called_once()
	assert not entity._preparing_sessions
	assert not entity._preparation_tasks
	assert not entity._unused_session_expirations


async def test_invalid_credentials_response_stays_private(camera_factory, caplog):
	create, _, _, api = camera_factory
	entity = create("mist")
	api.async_request.return_value = {"liveKitDetails": {"userToken": "SECRET_RESPONSE_TOKEN"}}
	with pytest.raises(HomeAssistantError) as failure:
		await entity.async_prepare_webrtc_client_configuration()
	assert "SECRET_RESPONSE_TOKEN" not in str(failure.value)
	assert "SECRET_RESPONSE_TOKEN" not in caplog.text


async def test_offer_failure_closes_and_forgets_session(camera_factory):
	create, _, _, _ = camera_factory
	entity = create()
	session = Mock(handle_offer=AsyncMock(side_effect=RuntimeError("private-provider-url")))
	entity._pool_unused_webrtc_session(session)
	with pytest.raises(HomeAssistantError, match="Unable to negotiate"):
		await entity.async_handle_async_webrtc_offer("offer", "client", Mock())
	session.close.assert_called_once()
	assert not entity._sessions
	assert not entity._offer_timers


async def test_provider_close_notifies_browser(camera_factory):
	create, _, _, _ = camera_factory
	entity = create()
	session = Mock(handle_offer=AsyncMock())
	entity._pool_unused_webrtc_session(session)
	send = Mock()
	await entity.async_handle_async_webrtc_offer("offer", "client", send)
	entity._session_closed(session)
	assert isinstance(send.call_args.args[0], WebRTCError)
	assert not entity._sessions
	assert not entity._offer_timers


async def test_first_answer_timeout_and_success(camera_factory, monkeypatch):
	create, _, _, _ = camera_factory
	entity = create()
	session = Mock(handle_offer=AsyncMock())
	entity._pool_unused_webrtc_session(session)
	send = Mock()
	await entity.async_handle_async_webrtc_offer("offer", "client", send)
	forward = session.handle_offer.call_args.args[1]
	forward(WebRTCAnswer("answer"))
	assert not entity._offer_timers
	entity.close_webrtc_session("client")
	entity._pool_unused_webrtc_session(session)
	await entity.async_handle_async_webrtc_offer("offer", "client", send)
	entity._offer_timed_out("client")
	assert isinstance(send.call_args.args[0], WebRTCError)
	assert not entity._sessions


async def test_unload_cancels_inflight_preparation(camera_factory):
	create, _, _, api = camera_factory
	entity = create("mist")
	started = asyncio.Event()

	async def blocked(*args, **kwargs):
		started.set()
		await asyncio.Future()

	api.async_request.side_effect = blocked
	preparation = asyncio.create_task(entity.async_prepare_webrtc_client_configuration())
	await started.wait()
	await entity.async_will_remove_from_hass()
	assert preparation.cancelled()
	assert not entity._preparation_tasks
	with pytest.raises(HomeAssistantError, match="unloaded"):
		await entity.async_prepare_webrtc_client_configuration()


async def test_unload_stops_preloaded_legacy_decoder(camera_factory):
	create, _, _, _ = camera_factory
	entity = create(None)
	stream = SimpleNamespace(
		dynamic_stream_settings=SimpleNamespace(preload_stream=True), stop=AsyncMock()
	)
	entity.stream = stream
	await entity.async_will_remove_from_hass()
	stream.stop.assert_awaited_once()
	assert not stream.dynamic_stream_settings.preload_stream
	assert entity.stream is None
