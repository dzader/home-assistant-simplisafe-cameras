"""Verify the narrow runtime updater with real temporary files and release ZIPs."""

from __future__ import annotations

import base64
import json
import os
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest

from tools import apply_runtime_delta as delta


def release(path: Path, version: str, camera: bytes, **extra: bytes) -> Path:
    payload = {
        delta.PREFIX + "manifest.json": json.dumps(
            {"domain": "simplirtc", "version": version}
        ).encode(),
        delta.PREFIX + "camera.py": camera,
        delta.PREFIX + "const.py": b"UNCHANGED = True\n",
        delta.PREFIX + "__init__.py": b"# unchanged\n",
        "README.md": b"public instructions\n",
    }
    payload.update(extra)
    sums = "".join(f"{delta._digest(data)}  {name}\n" for name, data in payload.items())
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in payload.items():
            archive.writestr(name, data)
        archive.writestr("SHA256SUMS", sums)
    return path


@pytest.fixture
def installed(tmp_path: Path) -> tuple[dict, Path, dict[str, bytes]]:
    old = release(tmp_path / "old.zip", "1.1.0", b"VALUE = 1\n")
    new = release(tmp_path / "new.zip", "1.1.1", b"VALUE = 2\n")
    payload = delta.create_delta(old, new)
    root = tmp_path / "config"
    root.mkdir()
    (root / "configuration.yaml").write_bytes(b"default_config:\nsimplirtc:\n")
    runtime, _ = delta._release(old)
    for name, data in runtime.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return payload, root, runtime


def test_dry_run_and_verified_install(
    installed: tuple[dict, Path, dict[str, bytes]],
) -> None:
    payload, root, originals = installed
    configuration = (root / "configuration.yaml").read_bytes()
    plan = delta.apply_delta(payload, root, dry_run=True)
    assert plan["files"] == [delta.PREFIX + "camera.py", delta.PREFIX + "manifest.json"]
    assert not (root / ".simplisafe-streaming-backups").exists()
    receipt = delta.apply_delta(payload, root)
    assert receipt["status"] == "complete"
    assert (root / (delta.PREFIX + "camera.py")).read_bytes() == b"VALUE = 2\n"
    assert (
        json.loads((root / (delta.PREFIX + "manifest.json")).read_text())["version"]
        == "1.1.1"
    )
    for name in plan["files"]:
        assert (
            Path(receipt["backup_dir"]) / "original" / name
        ).read_bytes() == originals[name]
    assert (root / (delta.PREFIX + "const.py")).read_bytes() == originals[
        delta.PREFIX + "const.py"
    ]
    assert (root / (delta.PREFIX + "__init__.py")).read_bytes() == originals[
        delta.PREFIX + "__init__.py"
    ]
    assert (root / "configuration.yaml").read_bytes() == configuration
    assert not (root / ".simplisafe-streaming-install.lock").exists()


def test_verify_all_before_hashes_before_writes(
    installed: tuple[dict, Path, dict[str, bytes]],
) -> None:
    payload, root, originals = installed
    (root / (delta.PREFIX + "manifest.json")).write_bytes(b'{"edited":true}')
    with pytest.raises(delta.DeltaError, match="previous release"):
        delta.apply_delta(payload, root)
    assert (root / (delta.PREFIX + "camera.py")).read_bytes() == originals[
        delta.PREFIX + "camera.py"
    ]
    assert not (root / ".simplisafe-streaming-backups").exists()


@pytest.mark.parametrize(
    "case", ["path", "digest", "syntax", "manifest", "duplicate", "base64"]
)
def test_invalid_payload_is_rejected_before_writes(
    installed: tuple[dict, Path, dict[str, bytes]], case: str
) -> None:
    payload, root, _ = installed
    payload = deepcopy(payload)
    item = payload["files"][0]
    if case == "path":
        item["path"] = "../camera.py"
    elif case == "digest":
        item["after_sha256"] = "0" * 64
    elif case == "syntax":
        data = b"def invalid(\n"
        item["data_base64"] = base64.b64encode(data).decode()
        item["after_sha256"] = delta._digest(data)
    elif case == "manifest":
        item = payload["files"][1]
        data = b'{"domain":"other","version":"1.1.1"}'
        item["data_base64"] = base64.b64encode(data).decode()
        item["after_sha256"] = delta._digest(data)
    elif case == "duplicate":
        payload["files"].append(deepcopy(item))
    else:
        item["data_base64"] = "!invalid!"
    with pytest.raises(delta.DeltaError):
        delta.apply_delta(payload, root)
    assert not (root / ".simplisafe-streaming-backups").exists()


def test_unexpected_archive_changes_are_rejected(tmp_path: Path) -> None:
    old = release(tmp_path / "old.zip", "1.1.0", b"VALUE = 1\n")
    new = release(
        tmp_path / "new.zip",
        "1.1.1",
        b"VALUE = 2\n",
        **{delta.PREFIX + "__init__.py": b"# unexpectedly changed\n"},
    )
    with pytest.raises(delta.DeltaError, match="allowed delta paths"):
        delta.create_delta(old, new)
    with zipfile.ZipFile(new, "a") as archive:
        archive.writestr("extra.txt", b"uncovered payload")
    with pytest.raises(delta.DeltaError, match="SHA256SUMS"):
        delta.create_delta(old, new)


def test_reviewed_livekit_update_is_backed_up_and_installed(tmp_path: Path) -> None:
    """The reviewed signaling-file update follows the same hash and backup checks."""
    name = delta.PREFIX + "livekit.py"
    old = release(
        tmp_path / "old.zip", "1.1.1", b"VALUE = 1\n", **{name: b"TELEMETRY = False\n"}
    )
    new = release(
        tmp_path / "new.zip", "1.1.2", b"VALUE = 2\n", **{name: b"TELEMETRY = True\n"}
    )
    payload = delta.create_delta(old, new)
    assert {item["path"] for item in payload["files"]} == {
        delta.PREFIX + "camera.py",
        name,
        delta.PREFIX + "manifest.json",
    }
    config = tmp_path / "config"
    config.mkdir()
    (config / "configuration.yaml").write_bytes(b"default_config:\n")
    runtime, _ = delta._release(old)
    for path, data in runtime.items():
        destination = config / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    receipt = delta.apply_delta(payload, config)
    assert (config / name).read_bytes() == b"TELEMETRY = True\n"
    assert (
        Path(receipt["backup_dir"]) / "original" / name
    ).read_bytes() == b"TELEMETRY = False\n"


def test_symlink_installed_file_is_rejected(
    installed: tuple[dict, Path, dict[str, bytes]],
) -> None:
    payload, root, originals = installed
    camera = root / (delta.PREFIX + "camera.py")
    camera.unlink()
    outside = root.parent / "camera.py"
    outside.write_bytes(originals[delta.PREFIX + "camera.py"])
    camera.symlink_to(outside)
    with pytest.raises(delta.DeltaError, match="real installed file"):
        delta.apply_delta(payload, root)
    assert outside.read_bytes() == originals[delta.PREFIX + "camera.py"]


def test_failed_replacement_restores_all_files(
    installed: tuple[dict, Path, dict[str, bytes]], monkeypatch: pytest.MonkeyPatch
) -> None:
    payload, root, originals = installed
    original_replace = os.replace
    failed = False

    def fail_manifest(source: Path, destination: Path) -> None:
        nonlocal failed
        if destination == root / (delta.PREFIX + "manifest.json") and not failed:
            failed = True
            raise OSError("simulated replacement failure")
        original_replace(source, destination)

    monkeypatch.setattr(delta.os, "replace", fail_manifest)
    with pytest.raises(delta.DeltaError, match="original files restored"):
        delta.apply_delta(payload, root)
    for name, data in originals.items():
        assert (root / name).read_bytes() == data
    receipt_path = next((root / ".simplisafe-streaming-backups").glob("*/receipt.json"))
    assert json.loads(receipt_path.read_text())["status"] == "rolled_back"


def test_failure_after_all_writes_restores_all_files(
    installed: tuple[dict, Path, dict[str, bytes]], monkeypatch: pytest.MonkeyPatch
) -> None:
    payload, root, originals = installed
    original_receipt = delta._receipt

    def fail_complete(path: Path, receipt: dict) -> None:
        if receipt["status"] == "complete":
            raise OSError("simulated completion failure")
        original_receipt(path, receipt)

    monkeypatch.setattr(delta, "_receipt", fail_complete)
    with pytest.raises(delta.DeltaError, match="original files restored"):
        delta.apply_delta(payload, root)
    for name, data in originals.items():
        assert (root / name).read_bytes() == data
    assert not (root / ".simplisafe-streaming-install.lock").exists()
