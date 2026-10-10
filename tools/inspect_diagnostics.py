#!/usr/bin/env python3
"""Print only camera provider/type fields from a local HA diagnostic export.

The original file may contain private account data. This tool never echoes raw
objects, file contents, identifiers, addresses, names, URLs, or tokens.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any


PROVIDERS = {"simplisafe": "flv", "kvs": "kinesis", "mist": "livekit"}
CAMERA_TYPES = {
    0: "camera", 1: "doorbell", 2: "outdoor_camera", 99: "unknown",
    "camera": "camera", "doorbell": "doorbell",
    "outdoor_camera": "outdoor_camera", "unknown": "unknown",
}


def values(value: Any) -> list[Mapping[str, Any]]:
    candidates = value.values() if isinstance(value, Mapping) else value
    if not isinstance(candidates, (list, tuple)) and not isinstance(value, Mapping):
        return []
    return [item for item in candidates if isinstance(item, Mapping)]


def summarize(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("Expected a Home Assistant diagnostics object")
    data = payload.get("data", payload)
    if not isinstance(data, Mapping):
        raise ValueError("Expected a diagnostics data object")
    systems = values(data.get("systems", []))
    result: list[dict[str, Any]] = []
    for system in systems:
        version = system.get("version")
        safe_version = version if type(version) is int and version in {2, 3} else None
        cameras: list[dict[str, Any]] = []
        for camera in values(system.get("cameras", [])):
            settings = camera.get("camera_settings", camera.get("cameraSettings", {}))
            if not isinstance(settings, Mapping):
                settings = {}
            admin = settings.get("admin", {})
            if not isinstance(admin, Mapping):
                admin = {}
            provider = camera.get("webRTCProvider", admin.get("webRTCProvider"))
            safe_provider = provider if isinstance(provider, str) and provider in PROVIDERS else None
            backend = PROVIDERS.get(safe_provider, "flv" if provider is None else "unknown")
            raw_type = camera.get("camera_type", camera.get("cameraType", "unknown"))
            safe_type = CAMERA_TYPES.get(raw_type, "unknown") if isinstance(raw_type, (str, int)) else "unknown"
            cameras.append({
                "index": len(cameras),
                "camera_type": safe_type,
                "webRTCProvider": safe_provider if provider is None or safe_provider else "unknown",
                "backend": backend,
                "supported_provider": backend != "unknown",
            })
        result.append({"index": len(result), "version": safe_version, "cameras": cameras})
    return {"systems": result, "camera_count": sum(len(system["cameras"]) for system in result)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("diagnostics", type=Path, help="Local native SimpliSafe or SimpliRTC diagnostics JSON")
    args = parser.parse_args()
    try:
        payload = json.loads(args.diagnostics.read_text(encoding="utf-8"))
        print(json.dumps(summarize(payload), indent=2))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        parser.exit(1, "Unable to read a supported diagnostics JSON file. No file content was printed.\n")


if __name__ == "__main__":
    main()
