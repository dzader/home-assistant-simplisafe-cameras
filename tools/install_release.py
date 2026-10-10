#!/usr/bin/env python3
"""Validate and install the SimpliSafe streaming release using Python 3.12.

No Home Assistant or third-party Python packages are required. Only the
``custom_components/simplirtc`` payload is installed. Every archive payload
must be covered by SHA256SUMS; configuration and an existing component are
backed up before any replacement. Home Assistant must be restarted separately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

COMPONENT_PREFIX = "custom_components/simplirtc/"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_MEMBERS = 1024
RUNTIME_SUFFIXES = {".py", ".json", ".yaml", ".yml", ".png", ".svg", ".md", ".txt"}
CONFIG_KEYS = ("simplirtc", "stream", "default_config")


class InstallError(Exception):
    """The archive or local configuration cannot be installed safely."""


def sha256(data: bytes) -> str:
    """Return a SHA-256 digest without retaining the input."""
    return hashlib.sha256(data).hexdigest()


def _safe_member_name(name: str, *, directory: bool = False) -> str:
    """Reject ambiguous, absolute, or escaping archive and manifest paths."""
    if directory and name.endswith("/"):
        name = name[:-1]
    if (
        not name
        or "\\" in name
        or ":" in name
        or any(ord(character) < 32 for character in name)
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or PurePosixPath(name).is_absolute()
    ):
        raise InstallError("Archive contains an unsafe path")
    return name


def validate_archive(archive_path: Path) -> dict[str, bytes]:
    """Read a bounded release archive, validate all hashes, and select runtime files."""
    if not archive_path.is_file() or archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise InstallError("Archive is missing or exceeds the size limit")
    payload: dict[str, bytes] = {}
    seen: set[str] = set()
    total_size = 0
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if len(members) > MAX_MEMBERS:
                raise InstallError("Archive contains too many entries")
            for member in members:
                name = _safe_member_name(member.filename, directory=member.is_dir())
                if name in seen:
                    raise InstallError("Archive contains a duplicate path")
                seen.add(name)
                mode = stat.S_IFMT(member.external_attr >> 16)
                if mode not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise InstallError("Archive contains a symlink or special file")
                if member.flag_bits & 1:
                    raise InstallError("Encrypted archive entries are unsupported")
                if member.is_dir():
                    if mode == stat.S_IFREG:
                        raise InstallError(
                            "Archive contains inconsistent directory metadata"
                        )
                    continue
                if mode == stat.S_IFDIR:
                    raise InstallError("Archive contains inconsistent file metadata")
                total_size += member.file_size
                if member.file_size > MAX_FILE_BYTES or total_size > MAX_TOTAL_BYTES:
                    raise InstallError("Archive exceeds the uncompressed size limit")
                data = archive.read(member)
                if len(data) != member.file_size:
                    raise InstallError("Archive contains inconsistent file sizes")
                payload[name] = data
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise InstallError("Unable to read a valid release ZIP") from error

    manifest = payload.pop("SHA256SUMS", None)
    if manifest is None:
        raise InstallError("Archive is missing SHA256SUMS")
    try:
        lines = manifest.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise InstallError("SHA256SUMS must be UTF-8 text") from error
    expected: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-fA-F]{64})  (.+)", line)
        if match is None:
            raise InstallError("SHA256SUMS contains an invalid entry")
        name = _safe_member_name(match[2])
        if name in expected:
            raise InstallError("SHA256SUMS contains a duplicate entry")
        expected[name] = match[1].lower()
    if expected.keys() != payload.keys():
        raise InstallError("SHA256SUMS does not exactly cover the archive payload")
    for name, data in payload.items():
        if sha256(data) != expected[name]:
            raise InstallError(f"SHA-256 mismatch for {name}")

    runtime: dict[str, bytes] = {}
    for name, data in payload.items():
        if not name.startswith(COMPONENT_PREFIX):
            continue
        relative = name.removeprefix(COMPONENT_PREFIX)
        path = PurePosixPath(relative)
        if (
            path.suffix not in RUNTIME_SUFFIXES
            and path.name != "py.typed"
            and not path.name.startswith("LICENSE")
        ):
            raise InstallError(f"Unsupported runtime file: {name}")
        if "__pycache__" in path.parts or any(
            part.startswith(".") for part in path.parts
        ):
            raise InstallError("Archive contains a hidden or cached runtime file")
        runtime[relative] = data
    if "__init__.py" not in runtime or "manifest.json" not in runtime:
        raise InstallError("Archive is missing the component entry point or manifest")
    try:
        component_manifest = json.loads(runtime["manifest.json"])
    except (UnicodeDecodeError, ValueError) as error:
        raise InstallError("Component manifest is not valid JSON") from error
    if (
        not isinstance(component_manifest, dict)
        or component_manifest.get("domain") != "simplirtc"
    ):
        raise InstallError("Component manifest has an unexpected domain")
    return runtime


def updated_configuration(original: bytes) -> tuple[bytes, list[str]]:
    """Append missing top-level keys while preserving every existing byte."""
    try:
        text = original.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise InstallError("configuration.yaml must be UTF-8 text") from error
    significant = [
        line
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    content = [line for line in significant if line.strip() not in {"---", "..."}]
    if (
        any(line.strip() == "..." for line in significant)
        or sum(line.strip() == "---" for line in significant) > 1
    ):
        raise InstallError("configuration.yaml must contain one open YAML document")
    if content and content[0].lstrip().startswith(("{", "[", "- ", "%")):
        raise InstallError("configuration.yaml requires a top-level block mapping")
    present: set[str] = set()
    for key in CONFIG_KEYS:
        pattern = re.compile(rf"^(?:{key}|'{key}'|\"{key}\")\s*:", re.MULTILINE)
        matches = pattern.findall(text)
        if len(matches) > 1:
            raise InstallError(
                f"configuration.yaml contains duplicate top-level {key} keys"
            )
        if matches:
            present.add(key)
    appended: list[str] = []
    if "simplirtc" not in present:
        appended.append("simplirtc")
    if "stream" not in present and "default_config" not in present:
        appended.append("stream")
    if not appended:
        return original, appended
    newline = b"\r\n" if b"\r\n" in original else b"\n"
    separator = b"" if not original or original.endswith((b"\n", b"\r")) else newline
    suffix = newline.join(f"{key}:".encode("ascii") for key in appended) + newline
    return original + separator + suffix, appended


def _tree_hashes(directory: Path) -> dict[str, str]:
    """Record existing files, refusing to follow links or special files."""
    if directory.is_symlink() or not directory.is_dir():
        raise InstallError("Existing simplirtc must be a real directory")
    hashes: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise InstallError(
                "Existing simplirtc contains a symlink; back it up manually first"
            )
        if path.is_file():
            hashes[path.relative_to(directory).as_posix()] = sha256(path.read_bytes())
        elif not path.is_dir():
            raise InstallError("Existing simplirtc contains a special file")
    return hashes


def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Write a file completely before replacing its destination."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_receipt(path: Path, receipt: dict) -> None:
    """Save a receipt without any configuration values or credentials."""
    _atomic_write(path, (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode())


def install_archive(
    archive_path: Path, config_dir: Path, *, dry_run: bool = False
) -> dict:
    """Install a validated component transactionally, retaining rollback backups."""
    archive_path = Path(archive_path).resolve()
    runtime = validate_archive(archive_path)
    try:
        config_dir = Path(config_dir).resolve(strict=True)
    except OSError as error:
        raise InstallError(
            "Home Assistant configuration directory does not exist"
        ) from error
    configuration = config_dir / "configuration.yaml"
    if configuration.is_symlink() or not configuration.is_file():
        raise InstallError(
            "A real configuration.yaml is required in the configuration directory"
        )
    original_configuration = configuration.read_bytes()
    config_mode = stat.S_IMODE(configuration.stat().st_mode)
    new_configuration, appended_keys = updated_configuration(original_configuration)
    components = config_dir / "custom_components"
    backup_root = config_dir / ".simplisafe-streaming-backups"
    for directory in (components, backup_root):
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise InstallError(f"Expected a real directory at {directory}")
    target = components / "simplirtc"
    had_component = target.exists() or target.is_symlink()
    old_hashes = _tree_hashes(target) if had_component else None
    plan = {
        "status": "dry-run",
        "config_dir": str(config_dir),
        "runtime_file_count": len(runtime),
        "will_replace_component": had_component,
        "appended_keys": appended_keys,
        "archive_sha256": sha256(archive_path.read_bytes()),
    }
    if dry_run:
        return plan

    lock_path = config_dir / ".simplisafe-streaming-install.lock"
    try:
        lock_descriptor = os.open(
            lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
    except FileExistsError as error:
        raise InstallError(
            "Another install is active; inspect the install lock before retrying"
        ) from error
    os.close(lock_descriptor)
    operation_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex
    backup = backup_root / operation_id
    receipt_path = backup / "receipt.json"
    stage: Path | None = None
    previous = components / f".simplirtc-previous-{operation_id}"
    configuration_written = False
    component_installed = False
    previous_moved = False
    receipt: dict = {}
    try:
        backup_root.mkdir(mode=0o700, exist_ok=True)
        backup.mkdir(mode=0o700)
        shutil.copy2(configuration, backup / "configuration.yaml")
        if had_component:
            shutil.copytree(target, backup / "custom_components" / "simplirtc")
        receipt = {
            "schema_version": 1,
            "status": "prepared",
            "backup_dir": str(backup),
            "receipt_path": str(receipt_path),
            "archive": {"path": str(archive_path), "sha256": plan["archive_sha256"]},
            "configuration": {
                "path": str(configuration),
                "backup_path": str(backup / "configuration.yaml"),
                "before_sha256": sha256(original_configuration),
                "after_sha256": sha256(new_configuration),
                "appended_keys": appended_keys,
            },
            "component": {
                "path": str(target),
                "backup_path": str(backup / "custom_components" / "simplirtc")
                if had_component
                else None,
                "before_hashes": old_hashes,
                "installed_hashes": {
                    name: sha256(data) for name, data in sorted(runtime.items())
                },
            },
            "manual_rollback": [
                "Stop Home Assistant before restoring files.",
                "Copy the backed-up configuration.yaml over the recorded configuration path.",
                "Move the installed simplirtc directory aside; restore the component backup if one is recorded.",
                "If no previous component backup is recorded, leave simplirtc absent after moving it aside.",
                "Restart Home Assistant after restoring both configuration and component files.",
            ],
        }
        _write_receipt(receipt_path, receipt)
        components.mkdir(exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".simplirtc-stage-", dir=components))
        for name, data in runtime.items():
            destination = stage.joinpath(*PurePosixPath(name).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            destination.chmod(0o644)
        if (
            configuration.is_symlink()
            or configuration.read_bytes() != original_configuration
        ):
            raise InstallError(
                "configuration.yaml changed during installation; retry with the current file"
            )
        if had_component:
            if _tree_hashes(target) != old_hashes:
                raise InstallError(
                    "Existing simplirtc changed during installation; retry"
                )
            os.replace(target, previous)
            previous_moved = True
        elif target.exists() or target.is_symlink():
            raise InstallError("simplirtc appeared during installation; retry")
        os.replace(stage, target)
        stage = None
        component_installed = True
        if new_configuration != original_configuration:
            _atomic_write(configuration, new_configuration, config_mode)
            configuration_written = True
        receipt["status"] = "complete"
        _write_receipt(receipt_path, receipt)
    except BaseException as error:
        rollback_errors: list[str] = []
        if configuration_written:
            try:
                _atomic_write(configuration, original_configuration, config_mode)
            except OSError as rollback_error:
                rollback_errors.append(f"configuration: {rollback_error}")
        if component_installed:
            try:
                failed_component = backup / "failed-install-component"
                os.replace(target, failed_component)
                receipt["failed_component_path"] = str(failed_component)
            except OSError as rollback_error:
                rollback_errors.append(f"component removal: {rollback_error}")
        if previous_moved:
            try:
                os.replace(previous, target)
            except OSError as rollback_error:
                rollback_errors.append(f"component restore: {rollback_error}")
        if receipt:
            receipt["status"] = "rollback_failed" if rollback_errors else "rolled_back"
            receipt["error"] = str(error)
            receipt["rollback_errors"] = rollback_errors
            try:
                _write_receipt(receipt_path, receipt)
            except OSError:
                pass
        if rollback_errors:
            raise InstallError(
                f"Install failed and rollback needs manual repair. Backups: {backup}"
            ) from error
        raise InstallError(
            f"Install failed; original files restored. Backups: {backup}"
        ) from error
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        lock_path.unlink(missing_ok=True)
    if previous_moved:
        try:
            shutil.rmtree(previous)
        except OSError as error:
            receipt["cleanup_warning"] = (
                f"Previous directory retained at {previous}: {error}"
            )
            _write_receipt(receipt_path, receipt)
    return receipt


def main() -> int:
    """Run a dry-run or backed-up installation and print its receipt."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="The finished release ZIP")
    parser.add_argument("--config-dir", type=Path, default=Path("/homeassistant"))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and preview without writing files",
    )
    arguments = parser.parse_args()
    try:
        receipt = install_archive(
            arguments.archive, arguments.config_dir, dry_run=arguments.dry_run
        )
    except (InstallError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
