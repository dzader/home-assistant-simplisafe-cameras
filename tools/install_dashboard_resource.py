#!/usr/bin/env python3
"""Install one reviewed SimpliSafe dashboard resource with hashes and rollback.

The only destination is www/simplisafe-camera-live.js. A null before_sha256
requires absence; a digest requires that exact existing file. Existing file
permissions are preserved. New files use 0600 and new directories use 0700.
Home Assistant configuration and other dashboard resources are never modified.
This standalone module uses only Python 3.12 standard-library modules.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

RESOURCE_PATH = "www/simplisafe-camera-live.js"
MAX_BYTES = 1024 * 1024


class ResourceError(Exception):
    """The dashboard resource cannot be validated or safely installed."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _validated_data(payload: dict) -> bytes:
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("path") != RESOURCE_PATH
        or "before_sha256" not in payload
        or (
            payload["before_sha256"] is not None
            and not _valid_digest(payload["before_sha256"])
        )
        or not _valid_digest(payload.get("after_sha256"))
        or not isinstance(payload.get("data_base64"), str)
        or len(payload["data_base64"]) > 4 * ((MAX_BYTES + 2) // 3)
    ):
        raise ResourceError("Unsupported dashboard resource payload")
    try:
        data = base64.b64decode(payload["data_base64"], validate=True)
        data.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise ResourceError(
            "Dashboard resource must be base64-encoded UTF-8"
        ) from error
    if not data or len(data) > MAX_BYTES or b"\x00" in data:
        raise ResourceError("Dashboard resource is empty, oversized or contains NUL")
    if _digest(data) != payload["after_sha256"]:
        raise ResourceError("Dashboard resource checksum mismatch")
    return data


def create_payload(source: Path, before_sha256: str | None = None) -> dict:
    """Package reviewed JS bytes, declaring the expected existing hash or absence."""
    source = Path(source)
    if not source.is_file() or source.stat().st_size > MAX_BYTES:
        raise ResourceError("Dashboard source is missing or oversized")
    data = source.read_bytes()
    payload = {
        "schema_version": 1,
        "path": RESOURCE_PATH,
        "before_sha256": before_sha256,
        "after_sha256": _digest(data),
        "data_base64": base64.b64encode(data).decode("ascii"),
    }
    _validated_data(payload)
    return payload


def _real_directory(path: Path, *, absent_ok: bool = False) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ResourceError(f"Expected a real directory: {path}")
    if not absent_ok and not path.is_dir():
        raise ResourceError(f"Required directory is missing: {path}")


def _installed_state(
    target: Path, before_sha256: str | None
) -> tuple[bytes | None, int]:
    _real_directory(target.parent, absent_ok=True)
    if target.is_symlink() or (
        target.exists() and not stat.S_ISREG(target.stat().st_mode)
    ):
        raise ResourceError("Dashboard destination must be a real regular file")
    if before_sha256 is None:
        if target.exists():
            raise ResourceError("Dashboard destination must be absent")
        return None, 0o600
    if not target.is_file() or target.stat().st_size > MAX_BYTES:
        raise ResourceError(
            "Expected existing dashboard resource is missing or oversized"
        )
    data = target.read_bytes()
    if _digest(data) != before_sha256:
        raise ResourceError("Existing dashboard resource checksum mismatch")
    return data, stat.S_IMODE(target.stat().st_mode)


def _write(path: Path, data: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fchmod(stream.fileno(), mode)
        os.fsync(stream.fileno())


def _receipt(path: Path, value: dict) -> None:
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    staged = path.with_name("receipt.pending")
    staged.unlink(missing_ok=True)
    _write(staged, data)
    os.replace(staged, path)


def install_resource(payload: dict, config_dir: Path, *, dry_run: bool = False) -> dict:
    """Validate bytes and expected state, retain originals, and atomically install."""
    data = _validated_data(payload)
    config_dir = Path(config_dir)
    _real_directory(config_dir)
    config_dir = config_dir.resolve(strict=True)
    configuration = config_dir / "configuration.yaml"
    if configuration.is_symlink() or not configuration.is_file():
        raise ResourceError("A real Home Assistant configuration.yaml is required")
    configuration_sha256 = _digest(configuration.read_bytes())
    target = config_dir / RESOURCE_PATH
    original, mode = _installed_state(target, payload["before_sha256"])
    backup_root = config_dir / ".simplisafe-streaming-backups"
    _real_directory(backup_root, absent_ok=True)
    result = {
        "status": "dry-run",
        "path": RESOURCE_PATH,
        "before_sha256": payload["before_sha256"],
        "after_sha256": payload["after_sha256"],
        "configuration_sha256": configuration_sha256,
        "mode": oct(mode),
    }
    if dry_run:
        return result
    lock = config_dir / ".simplisafe-streaming-install.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ResourceError("Another installation is active") from error
    os.close(descriptor)
    backup = backup_root / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-resource-" + uuid4().hex
    )
    receipt = {
        **result,
        "status": "prepared",
        "backup_dir": str(backup),
        "original_path": str(backup / "original.js") if original is not None else None,
        "original_absent": original is None,
    }
    replaced = False
    created_www = False
    try:
        _real_directory(backup_root, absent_ok=True)
        backup_root.mkdir(mode=0o700, exist_ok=True)
        backup.mkdir(mode=0o700)
        if original is not None:
            _write(backup / "original.js", original)
        _write(backup / "resource.js", data, mode)
        _receipt(backup / "receipt.json", receipt)
        current, current_mode = _installed_state(target, payload["before_sha256"])
        if current != original or current_mode != mode:
            raise ResourceError(
                "Dashboard resource changed while installation was prepared"
            )
        if not target.parent.exists():
            target.parent.mkdir(mode=0o700)
            created_www = True
        _real_directory(target.parent)
        os.replace(backup / "resource.js", target)
        replaced = True
        if _digest(target.read_bytes()) != payload["after_sha256"]:
            raise ResourceError("Installed dashboard checksum verification failed")
        if _digest(configuration.read_bytes()) != configuration_sha256:
            raise ResourceError(
                "Home Assistant configuration changed during installation"
            )
        receipt["status"] = "complete"
        _receipt(backup / "receipt.json", receipt)
    except BaseException as error:
        rollback_errors: list[str] = []
        if replaced:
            try:
                if original is None:
                    target.unlink()
                else:
                    _write(backup / "restore.js", original, mode)
                    os.replace(backup / "restore.js", target)
                    if target.read_bytes() != original:
                        raise ResourceError("Restored dashboard verification failed")
            except (OSError, ResourceError):
                rollback_errors.append(RESOURCE_PATH)
        if created_www:
            try:
                target.parent.rmdir()
            except OSError:
                rollback_errors.append("www")
        receipt["status"] = "rollback_failed" if rollback_errors else "rolled_back"
        receipt["rollback_errors"] = rollback_errors
        try:
            _receipt(backup / "receipt.json", receipt)
        except OSError:
            pass
        message = (
            "manual recovery required" if rollback_errors else "original state restored"
        )
        raise ResourceError(
            f"Dashboard install failed; {message}. Backups: {backup}"
        ) from error
    finally:
        lock.unlink(missing_ok=True)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("source", type=Path)
    build.add_argument("output", type=Path)
    build.add_argument("--before-sha256")
    apply = commands.add_parser("apply")
    apply.add_argument("payload", type=Path)
    apply.add_argument("--config-dir", type=Path, default=Path("/homeassistant"))
    apply.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    try:
        if arguments.command == "build":
            payload = create_payload(arguments.source, arguments.before_sha256)
            arguments.output.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            result = {
                "payload_path": str(arguments.output),
                "after_sha256": payload["after_sha256"],
            }
        else:
            if arguments.payload.stat().st_size > 2 * MAX_BYTES:
                raise ResourceError("Dashboard payload file is oversized")
            result = install_resource(
                json.loads(arguments.payload.read_text(encoding="utf-8")),
                arguments.config_dir,
                dry_run=arguments.dry_run,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ResourceError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
