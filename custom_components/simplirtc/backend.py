"""Select a camera transport without guessing unknown provider contracts."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any


class CameraBackend(StrEnum):
	LEGACY = "flv"
	KINESIS = "kinesis"
	LIVEKIT = "livekit"
	UNKNOWN = "unknown"


def describe_backend(settings: object) -> tuple[str | None, CameraBackend, str | None]:
	"""Describe a camera's provider, including older cameras without admin data."""
	if not isinstance(settings, Mapping):
		return None, CameraBackend.UNKNOWN, "unexpected_settings_schema"
	admin: Any = settings.get("admin")
	if admin is not None and not isinstance(admin, Mapping):
		return None, CameraBackend.UNKNOWN, "unexpected_settings_schema"
	provider = admin.get("webRTCProvider") if admin else None
	if provider is None or provider == "simplisafe":
		return provider, CameraBackend.LEGACY, None
	if provider == "kvs":
		return provider, CameraBackend.KINESIS, None
	if provider == "mist":
		return provider, CameraBackend.LIVEKIT, None
	return str(provider), CameraBackend.UNKNOWN, "unknown_webrtc_provider"
