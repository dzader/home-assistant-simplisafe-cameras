#!/usr/bin/env python3
"""Build and apply a small, hashed SimpliRTC update using the standard library.

Only camera.py, livekit.py, const.py and manifest.json may change. Configuration is never
modified. The installed files must exactly match the previous release, and
original files and a receipt are retained before an atomic file replacement.
"""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import os
import re
import stat
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

PREFIX = "custom_components/simplirtc/"
ALLOWED_PATHS = {
    PREFIX + name for name in ("camera.py", "livekit.py", "const.py", "manifest.json")
}
MAX_BYTES = 32 * 1024 * 1024


class DeltaError(Exception):
    """An update cannot be validated or safely completed."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _release(path: Path) -> tuple[dict[str, bytes], str]:
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise DeltaError("Release ZIP is missing or exceeds the size limit")
    payload: dict[str, bytes] = {}
    size = 0
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            name = member.filename
            parts = name.removesuffix("/").split("/")
            if (
                PurePosixPath(name).is_absolute()
                or "\\" in name
                or ":" in name
                or any(part in {"", ".", ".."} for part in parts)
                or any(ord(character) < 32 for character in name)
                or stat.S_IFMT(member.external_attr >> 16)
                not in {0, stat.S_IFREG, stat.S_IFDIR}
                or member.flag_bits & 1
            ):
                raise DeltaError("Release ZIP contains an unsafe entry")
            if member.is_dir():
                continue
            if name in payload:
                raise DeltaError("Release ZIP contains a duplicate entry")
            size += member.file_size
            if size > MAX_BYTES:
                raise DeltaError("Release ZIP exceeds the uncompressed size limit")
            payload[name] = archive.read(member)
    sums = payload.pop("SHA256SUMS", None)
    if sums is None:
        raise DeltaError("Release ZIP is missing SHA256SUMS")
    expected: dict[str, str] = {}
    for line in sums.decode("utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None or match[2] in expected:
            raise DeltaError("Invalid SHA256SUMS entry")
        expected[match[2]] = match[1]
    if expected.keys() != payload.keys() or any(
        _digest(data) != expected[name] for name, data in payload.items()
    ):
        raise DeltaError("Release ZIP payload does not match SHA256SUMS")
    return {
        name: data for name, data in payload.items() if name.startswith(PREFIX)
    }, _digest(path.read_bytes())


def create_delta(old_release: Path, new_release: Path) -> dict:
    """Create a JSON-compatible payload only when every runtime change is allowed."""
    old, old_digest = _release(Path(old_release))
    new, new_digest = _release(Path(new_release))
    if old.keys() != new.keys():
        raise DeltaError("A delta cannot add or remove runtime files")
    changed = sorted(name for name in old if old[name] != new[name])
    if not changed or not set(changed).issubset(ALLOWED_PATHS):
        raise DeltaError("Runtime changes exceed the allowed delta paths")
    old_manifest = json.loads(old[PREFIX + "manifest.json"])
    new_manifest = json.loads(new[PREFIX + "manifest.json"])
    if PREFIX + "manifest.json" not in changed or old_manifest.get(
        "version"
    ) == new_manifest.get("version"):
        raise DeltaError("A runtime delta requires a manifest version update")
    payload = {
        "schema_version": 1,
        "old_version": old_manifest.get("version"),
        "new_version": new_manifest.get("version"),
        "old_release_sha256": old_digest,
        "new_release_sha256": new_digest,
        "files": [
            {
                "path": name,
                "before_sha256": _digest(old[name]),
                "after_sha256": _digest(new[name]),
                "data_base64": base64.b64encode(new[name]).decode("ascii"),
            }
            for name in changed
        ],
    }
    _validated_files(payload)
    return payload


def _validated_files(payload: dict) -> dict[str, bytes]:
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("files"), list)
        or not isinstance(payload.get("old_version"), str)
        or not isinstance(payload.get("new_version"), str)
        or not payload["old_version"]
        or not payload["new_version"]
        or payload["old_version"] == payload["new_version"]
    ):
        raise DeltaError("Unsupported delta payload")
    files: dict[str, bytes] = {}
    for item in payload["files"]:
        if not isinstance(item, dict) or item.get("path") not in ALLOWED_PATHS:
            raise DeltaError("Delta contains an unexpected runtime path")
        name = item["path"]
        if (
            name in files
            or not isinstance(item.get("data_base64"), str)
            or len(item["data_base64"]) > MAX_BYTES
        ):
            raise DeltaError("Delta contains duplicate or oversized data")
        for key in ("before_sha256", "after_sha256"):
            if (
                not isinstance(item.get(key), str)
                or re.fullmatch(r"[0-9a-f]{64}", item[key]) is None
            ):
                raise DeltaError("Delta contains an invalid digest")
        try:
            data = base64.b64decode(item["data_base64"], validate=True)
            text = data.decode("utf-8")
            if name.endswith(".py"):
                ast.parse(text, filename=name)
            else:
                manifest = json.loads(text)
                if (
                    not isinstance(manifest, dict)
                    or manifest.get("domain") != "simplirtc"
                    or manifest.get("version") != payload.get("new_version")
                ):
                    raise DeltaError(
                        "Delta manifest has an unexpected domain or version"
                    )
        except (ValueError, UnicodeDecodeError, SyntaxError) as error:
            raise DeltaError(f"Invalid runtime source for {name}") from error
        if _digest(data) != item["after_sha256"]:
            raise DeltaError(f"Delta checksum mismatch for {name}")
        files[name] = data
    if (
        not files
        or PREFIX + "manifest.json" not in files
        or sum(map(len, files.values())) > MAX_BYTES
    ):
        raise DeltaError("Delta must include a bounded manifest version update")
    return files


def _stage(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)


def _receipt(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def apply_delta(payload: dict, config_dir: Path, *, dry_run: bool = False) -> dict:
    """Verify every installed hash, back up originals, then replace and verify files."""
    files = _validated_files(payload)
    config_dir = Path(config_dir).resolve(strict=True)
    if not (config_dir / "configuration.yaml").is_file():
        raise DeltaError("Home Assistant configuration.yaml is required")
    originals: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    for item in payload["files"]:
        name = item["path"]
        target = config_dir / name
        if (
            any(
                (config_dir / Path(*PurePosixPath(name).parts[:index])).is_symlink()
                for index in range(1, len(PurePosixPath(name).parts) + 1)
            )
            or not target.is_file()
        ):
            raise DeltaError(f"Expected a real installed file: {name}")
        originals[name] = target.read_bytes()
        modes[name] = stat.S_IMODE(target.stat().st_mode)
        if _digest(originals[name]) != item["before_sha256"]:
            raise DeltaError(
                f"Installed file does not match the previous release: {name}"
            )
    backup_root = config_dir / ".simplisafe-streaming-backups"
    if backup_root.is_symlink() or (backup_root.exists() and not backup_root.is_dir()):
        raise DeltaError("Expected a real backup directory")
    result = {
        "status": "dry-run",
        "old_version": payload.get("old_version"),
        "new_version": payload.get("new_version"),
        "files": sorted(files),
    }
    if dry_run:
        return result
    lock = config_dir / ".simplisafe-streaming-install.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise DeltaError("Another installation is active") from error
    os.close(descriptor)
    backup = backup_root / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-delta-" + uuid4().hex
    )
    replaced: list[str] = []
    receipt = {
        **result,
        "status": "prepared",
        "backup_dir": str(backup),
        "file_hashes": [
            {key: item[key] for key in ("path", "before_sha256", "after_sha256")}
            for item in payload["files"]
        ],
    }
    try:
        backup_root.mkdir(mode=0o700, exist_ok=True)
        backup.mkdir(mode=0o700)
        for name in files:
            _stage(backup / "original" / name, originals[name], modes[name])
            _stage(backup / "staged" / name, files[name], modes[name])
        _receipt(backup / "receipt.json", receipt)
        if any(
            (config_dir / name).read_bytes() != data for name, data in originals.items()
        ):
            raise DeltaError("Installed files changed while the update was prepared")
        for name in files:
            os.replace(backup / "staged" / name, config_dir / name)
            replaced.append(name)
        if any(
            _digest((config_dir / item["path"]).read_bytes()) != item["after_sha256"]
            for item in payload["files"]
        ):
            raise DeltaError("Updated runtime checksum verification failed")
        receipt["status"] = "complete"
        _receipt(backup / "receipt.json", receipt)
    except BaseException as error:
        rollback_errors: list[str] = []
        for name in reversed(replaced):
            try:
                _stage(backup / "restore" / name, originals[name], modes[name])
                os.replace(backup / "restore" / name, config_dir / name)
            except OSError:
                rollback_errors.append(name)
        receipt["status"] = "rollback_failed" if rollback_errors else "rolled_back"
        receipt["rollback_errors"] = rollback_errors
        try:
            _receipt(backup / "receipt.json", receipt)
        except OSError:
            pass
        message = (
            "manual recovery is required"
            if rollback_errors
            else "original files restored"
        )
        raise DeltaError(
            f"Runtime update failed; {message}. Backups: {backup}"
        ) from error
    finally:
        lock.unlink(missing_ok=True)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("old_release", type=Path)
    build.add_argument("new_release", type=Path)
    build.add_argument("output", type=Path)
    apply = commands.add_parser("apply")
    apply.add_argument("payload", type=Path)
    apply.add_argument("--config-dir", type=Path, default=Path("/homeassistant"))
    apply.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    try:
        if arguments.command == "build":
            payload = create_delta(arguments.old_release, arguments.new_release)
            arguments.output.write_text(
                json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
            )
            result = {
                "payload_path": str(arguments.output),
                "files": [item["path"] for item in payload["files"]],
            }
        else:
            result = apply_delta(
                json.loads(arguments.payload.read_text()),
                arguments.config_dir,
                dry_run=arguments.dry_run,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (DeltaError, OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
