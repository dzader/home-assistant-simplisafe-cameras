"""Publisher loss must release native HA sessions so dashboard recovery can reopen."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from homeassistant.components.camera import WebRTCError

from custom_components.simplirtc.livekit import LiveKitSession, LiveKitSignal, WarmupSession
from custom_components.simplirtc.protobufs.livekit_models_pb2 import (
	AUDIO,
	CAMERA,
	CLIENT_INITIATED,
	MICROPHONE,
	VIDEO,
	ParticipantInfo,
	TrackInfo,
)
from custom_components.simplirtc.protobufs.livekit_rtc_pb2 import (
	JoinResponse,
	ParticipantUpdate,
	SignalResponse,
	SubscriptionPermissionUpdate,
)


def publisher(sid="camera", video="camera-video"):
	return ParticipantInfo(
		sid=sid,
		state=ParticipantInfo.ACTIVE,
		is_publisher=True,
		tracks=[
			TrackInfo(sid=f"{sid}-audio", type=AUDIO, source=MICROPHONE),
			TrackInfo(sid=video, type=VIDEO, source=CAMERA),
		],
	)


def departure(sid="camera"):
	return SignalResponse(
		update=ParticipantUpdate(
			participants=[
				ParticipantInfo(
					sid=sid,
					state=ParticipantInfo.DISCONNECTED,
					disconnect_reason=CLIENT_INITIATED,
				)
			]
		)
	)


class SignalFixture(LiveKitSignal):
	def __init__(self, responses):
		super().__init__(
			ws=SimpleNamespace(closed=False), logger=logging.getLogger(__name__), task_group=Mock()
		)
		self.responses = responses
		self.parsed = []
		self.send = AsyncMock()
		self._start_ping = Mock()

	async def messages(self):
		for response in self.responses:
			self.parsed.append(response)
			yield self._response_message(response)


async def test_camera_departure_sends_one_native_error_and_forgets_session(camera_factory):
	create, _, _, api = camera_factory
	entity = create("mist")
	connection = SignalFixture(
		[
			SignalResponse(join=JoinResponse(other_participants=[publisher()], ping_interval=5)),
			departure(),
			SignalResponse(update=ParticipantUpdate(participants=[publisher("replacement")])),
		]
	)
	session = LiveKitSession(
		livekit_url="wss://private.example.test",
		user_token="private-token",
		send_ice_servers=Mock(),
	)

	async def read():
		await session._run_browser_signal(connection)

	session._read = AsyncMock(side_effect=read)
	session.start(lambda: entity._session_closed(session))
	reader = session._reader_task
	entity._pool_unused_webrtc_session(session)
	received = []
	error_ready = asyncio.Event()

	def send(message):
		received.append(message)
		if isinstance(message, WebRTCError):
			error_ready.set()

	await entity.async_handle_async_webrtc_offer("native-offer", "browser", send)
	await asyncio.wait_for(error_ready.wait(), 0.5)
	assert reader is not None
	await asyncio.gather(reader, return_exceptions=True)
	assert session.camera_video_ended
	assert [message.code for message in received if isinstance(message, WebRTCError)] == [
		"stream_closed"
	]
	assert "ended the live view" in received[-1].message
	assert not entity._sessions
	assert not entity._session_senders
	assert not entity._offer_timers
	assert not entity._unused_session_expirations
	assert len(connection.parsed) == 2
	entity._session_closed(session)
	assert len(received) == 1
	api.async_request.assert_not_awaited()


async def test_other_participant_departure_keeps_camera_video():
	connection = SignalFixture(
		[
			SignalResponse(join=JoinResponse(other_participants=[publisher()], ping_interval=5)),
			departure("other-viewer"),
		]
	)
	session = LiveKitSession(livekit_url="wss://test", user_token="token", send_ice_servers=Mock())
	on_close = Mock()
	session._on_close = on_close
	await session._run_browser_signal(connection)
	assert not session.camera_video_ended
	assert not connection.camera_video_ended
	on_close.assert_not_called()


async def test_removing_one_of_two_camera_publishers_keeps_browser_session():
	connection = SignalFixture(
		[
			SignalResponse(
				join=JoinResponse(
					other_participants=[publisher(), publisher("other-camera", "other-video")],
					ping_interval=5,
				)
			),
			departure(),
		]
	)
	session = LiveKitSession(livekit_url="wss://test", user_token="token", send_ice_servers=Mock())
	on_close = Mock()
	session._on_close = on_close
	await session._run_browser_signal(connection)
	assert not session.camera_video_ended
	on_close.assert_not_called()


async def test_initial_warmup_accepts_replacement_publisher_after_temporary_loss():
	connection = SignalFixture(
		[
			SignalResponse(update=ParticipantUpdate(participants=[publisher()])),
			departure(),
			SignalResponse(
				update=ParticipantUpdate(
					participants=[publisher("replacement", "replacement-video")]
				)
			),
			SignalResponse(
				subscription_permission_update=SubscriptionPermissionUpdate(
					track_sid="replacement-audio", allowed=True
				)
			),
			SignalResponse(
				subscription_permission_update=SubscriptionPermissionUpdate(
					track_sid="replacement-video", allowed=True
				)
			),
		]
	)
	warmup = WarmupSession(connection, ice_servers=[])
	await warmup.run()
	assert not warmup._waiting_for_track_permissions
	assert not connection.camera_video_ended
	assert len(connection.parsed) == 5
