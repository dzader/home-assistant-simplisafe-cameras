"""LiveKit diagnostics explain media state changes without exposing room secrets."""

import base64
import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.simplirtc import camera, livekit
from custom_components.simplirtc.protobufs.livekit_models_pb2 import (
	AUDIO,
	CAMERA,
	MICROPHONE,
	PARTICIPANT_REMOVED,
	VIDEO,
	ParticipantInfo,
	Room,
	TrackInfo,
)
from custom_components.simplirtc.protobufs.livekit_rtc_pb2 import (
	JoinResponse,
	LeaveRequest,
	ParticipantUpdate,
	Pong,
	RoomMovedResponse,
	SessionDescription,
	SignalResponse,
	SubscriptionPermissionUpdate,
	SubscriptionResponse,
)


def jwt(room="PRIVATE_ROOM", subject="PRIVATE_SUBJECT", issued=1000, expires=1300):
	payload = (
		base64.urlsafe_b64encode(
			json.dumps(
				{"exp": expires, "iat": issued, "sub": subject, "video": {"room": room}}
			).encode()
		)
		.decode()
		.rstrip("=")
	)
	return f"header.{payload}.PRIVATE_SIGNATURE"


@pytest.mark.parametrize("token", ["not-a-jwt", "a.not!base64.c", "a.W10.c", "x" * 16385])
def test_invalid_claims_are_optional_diagnostics(token):
	assert livekit.read_credential_metadata(token) == livekit.LiveKitCredentialMetadata()


def test_claims_are_unverified_and_identifiers_are_not_in_repr():
	metadata = livekit.read_credential_metadata(jwt())
	assert metadata.expires_at == 1300
	assert metadata.issued_at == 1000
	assert metadata.room == "PRIVATE_ROOM"
	assert metadata.subject == "PRIVATE_SUBJECT"
	assert "PRIVATE" not in repr(metadata)
	assert livekit.read_credential_metadata(jwt(expires=True)).expires_at is None


async def test_live_view_claim_continuity_and_bounded_health_logs(
	camera_factory, monkeypatch, caplog
):
	create, _, _, api = camera_factory
	entity = create("mist")
	clock = [0]
	monkeypatch.setattr(
		camera, "time", SimpleNamespace(time=lambda: 1000, monotonic=lambda: clock[0])
	)
	caplog.set_level(logging.INFO)
	response = {"cameraStatus": "online", "liveKitDetails": {"userToken": jwt()}}
	api.async_request.return_value = response
	await entity._request_live_view()
	assert "claimed_credential_lifetime_s=300" in caplog.text
	assert "claimed_credential_expires_in_s=300" in caplog.text
	await entity._request_live_view()
	assert "same_room=True same_subject=True" in caplog.text
	count = len(caplog.records)
	for _ in range(5):
		await entity._request_live_view()
	assert len(caplog.records) == count
	clock[0] = 61
	await entity._request_live_view()
	assert len(caplog.records) == count + 1
	assert "request_count=8" in caplog.text
	api.async_request.return_value = {
		"cameraStatus": "online",
		"liveKitDetails": {"userToken": jwt("PRIVATE_NEW_ROOM", "PRIVATE_NEW_SUBJECT")},
	}
	await entity._request_live_view()
	assert "same_room=False same_subject=False" in caplog.text
	api.async_request.return_value = {"cameraStatus": {"unexpected": "PRIVATE_STATUS"}}
	await entity._request_live_view()
	assert "status=unknown" in caplog.text
	assert "PRIVATE" not in caplog.text
	assert jwt() not in caplog.text


def signal(monkeypatch):
	result = livekit.LiveKitSignal(
		ws=SimpleNamespace(closed=False),
		logger=logging.getLogger("test.simplirtc.livekit"),
		task_group=Mock(),
		credential_metadata=livekit.read_credential_metadata(jwt()),
	)
	monkeypatch.setattr(result, "_start_ping", Mock())
	return result


def test_publisher_departure_records_video_loss_without_identifiers(monkeypatch, caplog):
	connection = signal(monkeypatch)
	caplog.set_level(logging.INFO)
	publisher = ParticipantInfo(
		sid="PRIVATE_PARTICIPANT_UUID",
		identity="PRIVATE_CAMERA_UUID",
		name="PRIVATE_CAMERA_NAME",
		metadata="PRIVATE_METADATA",
		state=ParticipantInfo.ACTIVE,
		is_publisher=True,
		tracks=[
			TrackInfo(sid="PRIVATE_VIDEO_TRACK", type=VIDEO, source=CAMERA),
			TrackInfo(sid="PRIVATE_AUDIO_TRACK", type=AUDIO, source=MICROPHONE),
		],
	)
	join = SignalResponse(
		join=JoinResponse(
			room=Room(name="PRIVATE_ROOM"),
			participant=ParticipantInfo(identity="PRIVATE_SUBJECT"),
			other_participants=[publisher],
			ping_interval=20,
			ping_timeout=60,
		)
	)
	assert connection._response_message(join)[0] == "join"
	assert "claimed_room_matches=True claimed_subject_matches=True" in caplog.text
	assert "active_camera_video=1" in caplog.text
	connection._response_message(
		SignalResponse(
			update=ParticipantUpdate(
				participants=[
					ParticipantInfo(
						sid=publisher.sid,
						state=ParticipantInfo.DISCONNECTED,
						disconnect_reason=PARTICIPANT_REMOVED,
					)
				]
			)
		)
	)
	assert "camera video tracks removed" in caplog.text
	assert "removed=1 remaining=0" in caplog.text
	assert "disconnect_reason_counts=[(4, 1)]" in caplog.text
	assert "PRIVATE" not in caplog.text


def test_continuity_events_and_subscription_failure_stay_private(monkeypatch, caplog):
	connection = signal(monkeypatch)
	caplog.set_level(logging.INFO)
	events = [
		SignalResponse(
			subscription_permission_update=SubscriptionPermissionUpdate(
				participant_sid="PRIVATE_PARTICIPANT", track_sid="PRIVATE_TRACK", allowed=False
			)
		),
		SignalResponse(
			subscription_response=SubscriptionResponse(track_sid="PRIVATE_TRACK", err=1)
		),
		SignalResponse(refresh_token=jwt(expires=1600)),
		SignalResponse(
			room_moved=RoomMovedResponse(
				token=jwt("PRIVATE_OTHER_ROOM", "PRIVATE_OTHER_SUBJECT"),
				room=Room(name="PRIVATE_OTHER_ROOM"),
			)
		),
		SignalResponse(
			leave=LeaveRequest(action=LeaveRequest.RECONNECT, reason=14, can_reconnect=True)
		),
		SignalResponse(
			offer=SessionDescription(
				type="offer", sdp="m=audio 9\r\nm=video 9\r\na=PRIVATE_SDP\r\n"
			)
		),
	]
	for event in events:
		connection._response_message(event)
	assert "allowed=False" in caplog.text
	assert "error_code=1" in caplog.text
	assert "kind=refresh_token same_room=True same_subject=True" in caplog.text
	assert "kind=room_moved same_room=False same_subject=False" in caplog.text
	assert "action_code=2 reason_code=14 can_reconnect=True" in caplog.text
	assert "kind=offer audio_sections=1 video_sections=1" in caplog.text
	assert "PRIVATE" not in caplog.text
	assert jwt(expires=1600) not in caplog.text


def test_signaling_health_log_is_bounded(monkeypatch, caplog):
	connection = signal(monkeypatch)
	clock = [0]
	monkeypatch.setattr(livekit, "time", SimpleNamespace(monotonic=lambda: clock[0]))
	connection._started_at = 0
	caplog.set_level(logging.INFO)
	for _ in range(10):
		connection._response_message(SignalResponse(pong_resp=Pong()))
	assert len(caplog.records) == 1
	clock[0] = 61
	connection._response_message(SignalResponse(pong_resp=Pong()))
	assert len(caplog.records) == 2
	assert "elapsed_s=61 pings_sent=0 pongs_received=11" in caplog.text
