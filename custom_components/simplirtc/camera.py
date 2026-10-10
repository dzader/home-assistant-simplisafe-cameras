"""Component providing support to the Simplisafe camera."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from secrets import token_hex
from typing import TypeVar, override
from urllib.parse import quote, urlsplit

from homeassistant.components.camera import (
	Camera as CameraEntity,
)
from homeassistant.components.camera import (
	CameraEntityDescription,
	CameraEntityFeature,
	WebRTCAnswer,
	WebRTCClientConfiguration,  # pyright: ignore[reportPrivateImportUsage]
	WebRTCError,
	WebRTCSendMessage,  # pyright: ignore[reportPrivateImportUsage]
)
from homeassistant.components.simplisafe import SimpliSafe
from homeassistant.components.simplisafe.entity import SimpliSafeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pydantic import Field, TypeAdapter
from pydantic.dataclasses import dataclass
from simplipy.device.camera import Camera
from simplipy.system.v3 import SystemV3
from simplipy.websocket import (
	EVENT_CAMERA_MOTION_DETECTED,
	WebsocketEvent,
)
from webrtc_models import (
	RTCIceCandidateInit,
	RTCIceServer,
)

from .backend import CameraBackend, describe_backend
from .kinesis import KinesisSession
from .legacy import LegacyStreamRelay
from .livekit import LiveKitCredentialMetadata, LiveKitSession, read_credential_metadata
from .protobufs.livekit_rtc_pb2 import ICEServer as LiveKitIceServer
from .security import redact_live_view_logging
from .webrtc import (
	SimpliSafeWebRTCSession,
	WebRTCClientConfigurationMixin,
)

_LOGGER = logging.getLogger(__name__)
WEBRTC_URL_BASE = "https://app-hub.prd.aser.simplisafe.com/v2"
UNUSED_WEBRTC_SESSION_TTL = 30
WEBRTC_PREPARATION_TIMEOUT = 30
WEBRTC_OFFER_TIMEOUT = 30
LIVE_VIEW_ATTEMPTS = 5
LIVE_VIEW_RETRY_DELAY = 2
MAX_UNUSED_SESSIONS = 2
_StreamResponseT = TypeVar("_StreamResponseT")


async def async_setup_entry(
	hass: HomeAssistant,  # pyright: ignore[reportUnusedParameter]
	entry: ConfigEntry[SimpliSafe],
	async_add_entities: AddEntitiesCallback,
) -> None:
	"""Set up a SimpliSafe Camera."""
	simplisafe = entry.runtime_data

	cameras: list[SimpliSafeCameraBase] = []

	for system in simplisafe.systems.values():
		if not isinstance(system, SystemV3):
			_LOGGER.warning(
				"Skipping camera setup for V%d system: %s", system.version, system.system_id
			)
			continue

		for camera in system.cameras.values():
			provider, backend, reason = describe_backend(camera.camera_settings)
			if backend == CameraBackend.UNKNOWN:
				_LOGGER.warning("Skipping unsupported camera provider %r (%s)", provider, reason)
				continue
			entity_type = (
				SimpliSafeLegacyCamera if backend == CameraBackend.LEGACY else SimpliSafeCamera
			)
			cameras.append(entity_type(simplisafe, system, camera))

	async_add_entities(cameras)


@dataclass(kw_only=True, slots=True)
class KinesisIceServer:
	urls: list[str]
	username: str | None = None
	credential: str | None = None


@dataclass(kw_only=True, slots=True)
class KinesisResponse:
	signedChannelEndpoint: str
	clientId: str
	iceServers: list[KinesisIceServer]


@dataclass(kw_only=True, slots=True)
class LiveKitResponse:
	liveKitDetails: LiveKitDetails


@dataclass(kw_only=True, slots=True)
class LiveKitDetails:
	liveKitURL: str
	userToken: str


@dataclass(kw_only=True, slots=True)
class EventMediaLink:
	href: str


@dataclass(kw_only=True, slots=True)
class EventVideo:
	links: dict[str, EventMediaLink] = Field(alias="_links")


@dataclass(kw_only=True, slots=True)
class EventHistoryEvent:
	eventTimestamp: int | float | None = None
	sensorSerial: str | None = None
	video: dict[str, EventVideo] | None = None
	videoStartedBy: str | None = None


@dataclass(kw_only=True, slots=True)
class EventHistoryResponse:
	events: list[EventHistoryEvent]


class SimpliSafeCameraBase(  # pyright: ignore[reportUnsafeMultipleInheritance]
	SimpliSafeEntity,
	CameraEntity,
):
	"""An implementation of a Simplisafe camera."""

	def __init__(
		self,
		simplisafe: SimpliSafe,
		system: SystemV3,
		device: Camera,
	) -> None:
		"""Initialize the SimpliSafe camera."""
		super().__init__(
			simplisafe,
			system,
			device=device,
			additional_websocket_events=(EVENT_CAMERA_MOTION_DETECTED,),
		)
		self.entity_description = CameraEntityDescription(
			key="live_view",
		)
		CameraEntity.__init__(self)

		self._attr_unique_id = f"{super().unique_id}-camera"
		self._attr_supported_features |= CameraEntityFeature.STREAM
		self._device: Camera
		self._snapshot_template_url: str | None = None
		redact_live_view_logging()

	@override
	async def async_camera_image(
		self,
		width: int | None = None,
		height: int | None = None,
	) -> bytes | None:
		"""Return the latest snapshot from this camera's event history."""
		_ = height
		if self._snapshot_template_url is None:
			await self._async_refresh_snapshot_template_url()

		if snapshot_template_url := self._snapshot_template_url:
			return await self._simplisafe._api.async_media(  # pyright: ignore[reportPrivateUsage]
				snapshot_template_url.replace(
					"{&width}", f"&width={width}" if width is not None else ""
				)
			)

		return None

	async def _async_refresh_snapshot_template_url(self) -> None:
		"""Update the cached snapshot template from this camera's event history."""
		history = TypeAdapter(EventHistoryResponse).validate_python(
			await self._simplisafe._api.async_request(  # pyright: ignore[reportPrivateUsage]
				"get",
				f"subscriptions/{self._system.system_id}/events?numEvents=50",
			)
		)

		newest_video: EventVideo | None = None
		newest_timestamp = 0
		camera_serials = self._camera_event_serials()
		for event in history.events:
			if event.sensorSerial not in camera_serials:
				continue
			if event.video is None or event.videoStartedBy is None:
				continue
			if not (video := event.video.get(event.videoStartedBy)):
				continue
			if (timestamp := event.eventTimestamp or 0) <= newest_timestamp:
				continue
			newest_video, newest_timestamp = video, timestamp

		if newest_video and (snapshot := newest_video.links.get("snapshot/jpg")):
			self._snapshot_template_url = snapshot.href

	@override
	@callback
	def async_update_from_websocket_event(self, event: WebsocketEvent) -> None:
		"""Cache the snapshot template from a camera motion event."""
		if event.media_urls and (url := event.media_urls.get("image_url")):
			self._snapshot_template_url = url

	@override
	@callback
	def _handle_websocket_update(self, event: WebsocketEvent) -> None:
		"""Ignore camera motion events for other cameras."""
		if (
			event.event_type == EVENT_CAMERA_MOTION_DETECTED
			and event.sensor_serial not in self._camera_event_serials()
		):
			return
		super()._handle_websocket_update(event)

	@callback
	def _camera_event_serials(self) -> set[str]:
		"""Return camera identifiers that may appear in events."""
		serials = {self._device.serial}
		if isinstance(camera_data := self._system.camera_data.get(self._device.serial), Mapping):
			for key in ("uuid", "serial"):
				if isinstance(serial := camera_data.get(key), str) and serial:
					serials.add(serial)
		return serials

	@override
	async def async_will_remove_from_hass(self) -> None:
		"""Stop this camera's decoder even when stream preloading was enabled."""
		if self.stream is not None:
			self.stream.dynamic_stream_settings.preload_stream = False
			await self.stream.stop()
			self.stream = None
		await super().async_will_remove_from_hass()

	@property
	def _camera_uuid(self) -> str:
		"""Use the API UUID when camera events use a hardware serial instead."""
		data = self._system.camera_data.get(self._device.serial, {})
		uuid = data.get("uuid") if isinstance(data, Mapping) else None
		return uuid if isinstance(uuid, str) and uuid else self._device.serial

	@property
	def _web_rtc_provider(self) -> str | None:
		return describe_backend(self._device.camera_settings)[0]

	@property
	def extra_state_attributes(self) -> dict[str, str | None]:
		"""Return transport information without provider URLs or credentials."""
		provider, backend, _ = describe_backend(self._device.camera_settings)
		return {"webrtc_provider": provider, "stream_backend": backend.value}


class SimpliSafeLegacyCamera(SimpliSafeCameraBase):
	"""Expose authenticated HTTPS FLV through Home Assistant's local decoder."""

	def __init__(self, simplisafe: SimpliSafe, system: SystemV3, device: Camera) -> None:
		super().__init__(simplisafe, system, device)
		self._relay = LegacyStreamRelay(self._simplisafe._api, self._camera_uuid)

	@override
	async def stream_source(self) -> str:
		"""Return a private loopback source accepted by the native stream worker."""
		try:
			return await self._relay.source()
		except Exception:
			raise HomeAssistantError("Unable to start the SimpliSafe camera stream relay") from None

	@override
	async def async_will_remove_from_hass(self) -> None:
		await self._relay.close()
		await super().async_will_remove_from_hass()


class SimpliSafeCamera(WebRTCClientConfigurationMixin, SimpliSafeCameraBase):
	"""Bridge cloud WebRTC signaling to Home Assistant's native browser player."""

	def __init__(self, simplisafe: SimpliSafe, system: SystemV3, device: Camera) -> None:
		super().__init__(simplisafe, system, device)
		self._sessions: dict[str, SimpliSafeWebRTCSession] = {}
		self._session_senders: dict[str, WebRTCSendMessage] = {}
		self._offer_timers: dict[str, asyncio.TimerHandle] = {}
		self._unused_session_expirations: dict[SimpliSafeWebRTCSession, asyncio.TimerHandle] = {}
		self._preparing_sessions: set[SimpliSafeWebRTCSession] = set()
		self._preparation_tasks: set[asyncio.Task] = set()
		self._stream_logger = _LOGGER.getChild(f"stream.{token_hex(4)}")
		self._initial_credential_metadata: LiveKitCredentialMetadata | None = None
		self._live_view_requests = 0
		self._last_live_view_log_at: float | None = None
		self._last_live_view_summary: tuple | None = None
		self._removed = False

	async def _request_live_view(self) -> dict:
		"""Request live view using the native integration's current authentication."""
		response = await self._simplisafe._api.async_request(
			"get",
			f"cameras/{quote(self._camera_uuid, safe='')}/{self._system.system_id}/live-view",
			url_base=WEBRTC_URL_BASE,
			headers={"Host": urlsplit(WEBRTC_URL_BASE).netloc},
		)
		self._log_live_view_metadata(response)
		return response

	def _log_live_view_metadata(self, response: Mapping) -> None:
		"""Report response health and claim continuity without logging credentials."""
		self._live_view_requests += 1
		if not isinstance(response, Mapping):
			return
		status = response.get("cameraStatus")
		if not isinstance(status, str) or status not in {
			"online",
			"offline",
			"sleeping",
			"waking",
			"unavailable",
		}:
			status = "unknown"
		metadata = LiveKitCredentialMetadata()
		if isinstance(details := response.get("liveKitDetails"), Mapping) and isinstance(
			token := details.get("userToken"), str
		):
			metadata = read_credential_metadata(token)
		initial = self._initial_credential_metadata
		same_room = (
			metadata.room == initial.room
			if initial is not None and initial.room is not None and metadata.room is not None
			else None
		)
		same_subject = (
			metadata.subject == initial.subject
			if initial is not None and initial.subject is not None and metadata.subject is not None
			else None
		)
		if initial is None and (metadata.room is not None or metadata.subject is not None):
			self._initial_credential_metadata = metadata
		summary = (status, same_room, same_subject)
		now = time.monotonic()
		if (
			self._last_live_view_log_at is None
			or now - self._last_live_view_log_at >= 60
			or summary != self._last_live_view_summary
		):
			self._last_live_view_log_at = now
			self._last_live_view_summary = summary
			self._stream_logger.info(
				"Live-view response status=%s request_count=%s same_room=%s same_subject=%s "
				"claimed_credential_lifetime_s=%s claimed_credential_expires_in_s=%s",
				*summary[:1],
				self._live_view_requests,
				*summary[1:],
				metadata.expires_at - metadata.issued_at
				if metadata.expires_at is not None and metadata.issued_at is not None
				else None,
				metadata.expires_at - int(time.time()) if metadata.expires_at is not None else None,
			)

	async def _create_stream(self, response_type: type[_StreamResponseT]) -> _StreamResponseT:
		"""Request ephemeral credentials, allowing a bounded battery-camera wakeup."""
		for attempt in range(LIVE_VIEW_ATTEMPTS):
			response = await self._request_live_view()
			if (
				response_type == LiveKitResponse
				and not response.get("liveKitDetails")
				and response.get("cameraStatus") in {"sleeping", "waking"}
				and attempt < LIVE_VIEW_ATTEMPTS - 1
			):
				await asyncio.sleep(LIVE_VIEW_RETRY_DELAY)
				continue
			return TypeAdapter(response_type).validate_python(response)
		raise HomeAssistantError("SimpliSafe camera did not wake for live view")

	@override
	async def async_will_remove_from_hass(self) -> None:
		"""Cancel preparations and close all signaling sessions during unload."""
		self._removed = True
		preparations = tuple(self._preparation_tasks)
		for task in preparations:
			task.cancel()
		for timer in self._offer_timers.values():
			timer.cancel()
		self._offer_timers.clear()
		for timer in self._unused_session_expirations.values():
			timer.cancel()
		sessions = (
			set(self._sessions.values())
			| set(self._unused_session_expirations)
			| self._preparing_sessions
		)
		self._sessions.clear()
		self._session_senders.clear()
		self._unused_session_expirations.clear()
		self._preparing_sessions.clear()
		for session in sessions:
			session.close()
		await asyncio.gather(*preparations, return_exceptions=True)
		await super().async_will_remove_from_hass()

	async def async_prepare_webrtc_client_configuration(self) -> WebRTCClientConfiguration:
		"""Create bounded provider signaling and report a safe client-facing failure."""
		if self._removed:
			raise HomeAssistantError("SimpliSafe camera has been unloaded")
		task = asyncio.current_task()
		assert task is not None
		self._preparation_tasks.add(task)
		try:
			async with asyncio.timeout(WEBRTC_PREPARATION_TIMEOUT):
				return await self._async_prepare_configuration()
		except asyncio.CancelledError:
			raise
		except Exception as err:
			_LOGGER.warning("Unable to prepare camera stream (%s)", type(err).__name__)
			raise HomeAssistantError(
				"Unable to prepare SimpliSafe live view; check camera connectivity and try again"
			) from None
		finally:
			self._preparation_tasks.discard(task)

	async def _async_prepare_configuration(self) -> WebRTCClientConfiguration:
		ice_servers: list[KinesisIceServer | LiveKitIceServer] = []
		match self._web_rtc_provider:
			case "mist":
				live_view = await self._create_stream(LiveKitResponse)
				ice_servers_ready: asyncio.Future[None] = self.hass.loop.create_future()

				def send_ice_servers(servers: list[LiveKitIceServer]) -> None:
					ice_servers.extend(servers)
					if not ice_servers_ready.done():
						ice_servers_ready.set_result(None)

				def close_before_ice_servers() -> None:
					if not ice_servers_ready.done():
						ice_servers_ready.set_exception(
							HomeAssistantError(
								f"LiveKit session closed before ICE servers were received for {self.entity_id}"
							)
						)

				session = LiveKitSession(
					livekit_url=live_view.liveKitDetails.liveKitURL,
					user_token=live_view.liveKitDetails.userToken,
					send_ice_servers=send_ice_servers,
					stream_logger=self._stream_logger.getChild("livekit"),
				)

				def on_close() -> None:
					close_before_ice_servers()
					self._session_closed(session)

				self._preparing_sessions.add(session)
				try:
					session.start(on_close)
					await ice_servers_ready
				except BaseException:
					session.close()
					raise
				finally:
					self._preparing_sessions.discard(session)
			case "kvs":
				live_view = await self._create_stream(KinesisResponse)
				ice_servers.extend(live_view.iceServers)
				session = KinesisSession(
					channel_endpoint=live_view.signedChannelEndpoint,
					client_id=live_view.clientId,
				)
				session.start(lambda: self._session_closed(session))
			case _ as provider:
				raise HomeAssistantError(
					f"Camera {self.name} has unknown webrtc provider {provider!r}"
				)

		self._pool_unused_webrtc_session(session)

		config = WebRTCClientConfiguration()
		config.configuration.ice_servers.extend(
			RTCIceServer(
				urls=list(ice_server.urls),
				username=ice_server.username or None,
				credential=ice_server.credential or None,
			)
			for ice_server in ice_servers
		)

		return config

	@override
	async def async_handle_async_webrtc_offer(
		self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
	) -> None:
		"""Handle a WebRTC offer."""
		if not (session := self._sessions.get(session_id)):
			if (session := next(iter(self._unused_session_expirations), None)) is None:
				raise HomeAssistantError(f"No prepared WebRTC session for {self.entity_id}")

			expiration = self._unused_session_expirations.pop(session)
			expiration.cancel()
			_LOGGER.debug("Using pooled WebRTC session for %s", self.entity_id)
			self._sessions[session_id] = session
		else:
			_LOGGER.debug("Renegotiating WebRTC session for %s", self.entity_id)
		self._session_senders[session_id] = send_message
		if (timer := self._offer_timers.pop(session_id, None)) is not None:
			timer.cancel()
		self._offer_timers[session_id] = self.hass.loop.call_later(
			WEBRTC_OFFER_TIMEOUT,
			self._offer_timed_out,
			session_id,
		)

		def forward_message(message) -> None:
			if isinstance(message, WebRTCAnswer):
				if (timer := self._offer_timers.pop(session_id, None)) is not None:
					timer.cancel()
			send_message(message)

		try:
			async with asyncio.timeout(WEBRTC_OFFER_TIMEOUT):
				await session.handle_offer(offer_sdp, forward_message)
		except BaseException as err:
			self.close_webrtc_session(session_id)
			if isinstance(err, asyncio.CancelledError):
				raise
			raise HomeAssistantError("Unable to negotiate SimpliSafe camera live view") from None

	@override
	async def async_on_webrtc_candidate(
		self, session_id: str, candidate: RTCIceCandidateInit
	) -> None:
		"""Handle a WebRTC candidate."""
		if session := self._sessions.get(session_id):
			await session.send_candidate(candidate)
		else:
			_LOGGER.debug("Ignoring WebRTC candidate for closed session %s", session_id)

	@override
	@callback
	def close_webrtc_session(self, session_id: str) -> None:
		"""Close a WebRTC session."""
		self._session_senders.pop(session_id, None)
		if (timer := self._offer_timers.pop(session_id, None)) is not None:
			timer.cancel()
		if session := self._sessions.pop(session_id, None):
			session.close()

	@callback
	def _pool_unused_webrtc_session(
		self,
		session: SimpliSafeWebRTCSession,
	) -> None:
		"""Pool a session prepared by a client config request."""
		if self._removed:
			session.close()
			return
		while len(self._unused_session_expirations) >= MAX_UNUSED_SESSIONS:
			self._expire(next(iter(self._unused_session_expirations)))
		self._unused_session_expirations[session] = self.hass.loop.call_later(
			UNUSED_WEBRTC_SESSION_TTL,
			self._expire,
			session,
		)

	@callback
	def _expire(self, session: SimpliSafeWebRTCSession) -> None:
		"""Close an unused prepared session if the offer never arrives."""
		if (timer := self._unused_session_expirations.pop(session, None)) is not None:
			timer.cancel()
			session.close()

	@callback
	def _session_closed(self, session: SimpliSafeWebRTCSession) -> None:
		"""Forget failed signaling and notify an attached browser instead of leaving stale sessions."""
		if (timer := self._unused_session_expirations.pop(session, None)) is not None:
			timer.cancel()
		for session_id, active in tuple(self._sessions.items()):
			if active is session:
				self._sessions.pop(session_id)
				if (timer := self._offer_timers.pop(session_id, None)) is not None:
					timer.cancel()
				if (send_message := self._session_senders.pop(session_id, None)) is not None:
					send_message(
						WebRTCError(
							"stream_closed",
							"SimpliSafe camera ended the live view; reconnect the camera"
							if getattr(session, "camera_video_ended", False) is True
							else "SimpliSafe camera signaling closed; reopen the live view",
						)
					)

	@callback
	def _offer_timed_out(self, session_id: str) -> None:
		"""Stop signaling if a provider never answers the browser offer."""
		if (send_message := self._session_senders.get(session_id)) is not None:
			send_message(
				WebRTCError(
					"stream_timeout", "SimpliSafe camera did not answer; try the live view again"
				)
			)
		self.close_webrtc_session(session_id)
