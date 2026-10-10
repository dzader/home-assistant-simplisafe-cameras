"""Verify dashboard resource deployment with real temporary configuration trees."""

from __future__ import annotations

import base64
import json
import os
import stat
from copy import deepcopy
from pathlib import Path

import pytest

from tools import install_dashboard_resource as resource


@pytest.fixture
def installation(tmp_path: Path) -> tuple[dict, Path]:
    config = tmp_path / "config"
    config.mkdir()
    (config / "configuration.yaml").write_bytes(
        b"default_config:\n# retained exactly\n"
    )
    (config / "unrelated.txt").write_bytes(b"other data\n")
    source = tmp_path / "reviewed.js"
    source.write_bytes(
        b"customElements.define('simplisafe-camera-live', class extends HTMLElement {});\n"
    )
    return resource.create_payload(source), config


def receipts(config: Path) -> list[Path]:
    return list((config / ".simplisafe-streaming-backups").glob("*/receipt.json"))


def test_new_resource_dry_run_and_install(installation: tuple[dict, Path]) -> None:
    payload, config = installation
    configuration = (config / "configuration.yaml").read_bytes()
    before = sorted(str(path.relative_to(config)) for path in config.rglob("*"))
    preview = resource.install_resource(payload, config, dry_run=True)
    assert preview["status"] == "dry-run"
    assert sorted(str(path.relative_to(config)) for path in config.rglob("*")) == before
    result = resource.install_resource(payload, config)
    target = config / resource.RESOURCE_PATH
    assert target.read_bytes() == base64.b64decode(payload["data_base64"])
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert result["status"] == "complete"
    assert result["original_absent"] is True
    assert result["original_path"] is None
    assert result["after_sha256"] == resource._digest(target.read_bytes())
    assert json.loads(receipts(config)[0].read_text()) == result
    assert (config / "configuration.yaml").read_bytes() == configuration
    assert (config / "unrelated.txt").read_bytes() == b"other data\n"
    assert not (config / ".simplisafe-streaming-install.lock").exists()


def test_existing_resource_backup_and_permissions_preserved(
    installation: tuple[dict, Path],
) -> None:
    payload, config = installation
    target = config / resource.RESOURCE_PATH
    target.parent.mkdir(mode=0o750)
    target.write_bytes(b"// original reviewed resource\n")
    target.chmod(0o640)
    other = target.parent / "existing-card.js"
    other.write_bytes(b"// unrelated\n")
    payload["before_sha256"] = resource._digest(target.read_bytes())
    result = resource.install_resource(payload, config)
    assert (
        Path(result["original_path"]).read_bytes() == b"// original reviewed resource\n"
    )
    assert stat.S_IMODE(Path(result["original_path"]).stat().st_mode) == 0o600
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o750
    assert other.read_bytes() == b"// unrelated\n"
    assert result["before_sha256"] == payload["before_sha256"]
    assert result["original_absent"] is False


@pytest.mark.parametrize(
    "case", ["path", "hash", "base64", "utf8", "nul", "missing_before", "oversized"]
)
def test_invalid_payload_never_writes(
    installation: tuple[dict, Path], case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload, config = installation
    payload = deepcopy(payload)
    if case == "path":
        payload["path"] = "www/../configuration.yaml"
    elif case == "hash":
        payload["after_sha256"] = "0" * 64
    elif case == "base64":
        payload["data_base64"] = "!not-base64!"
    elif case in {"utf8", "nul"}:
        data = b"\xff" if case == "utf8" else b"//\x00"
        payload["data_base64"] = base64.b64encode(data).decode()
        payload["after_sha256"] = resource._digest(data)
    elif case == "missing_before":
        del payload["before_sha256"]
    else:
        monkeypatch.setattr(resource, "MAX_BYTES", 8)
    with pytest.raises(resource.ResourceError):
        resource.install_resource(payload, config)
    assert not (config / "www").exists()
    assert not receipts(config)


@pytest.mark.parametrize(
    "case", ["unexpected_existing", "missing_existing", "wrong_existing"]
)
def test_expected_state_must_match_before_writes(
    installation: tuple[dict, Path], case: str
) -> None:
    payload, config = installation
    target = config / resource.RESOURCE_PATH
    if case != "missing_existing":
        target.parent.mkdir()
        target.write_bytes(b"// someone else's resource\n")
    if case != "unexpected_existing":
        payload["before_sha256"] = "0" * 64
    with pytest.raises(resource.ResourceError):
        resource.install_resource(payload, config)
    if target.exists():
        assert target.read_bytes() == b"// someone else's resource\n"
    assert not receipts(config)


@pytest.mark.parametrize(
    "location", ["www", "target", "backups", "configuration", "config"]
)
def test_symlinks_rejected(
    installation: tuple[dict, Path], location: str, tmp_path: Path
) -> None:
    payload, config = installation
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "configuration.yaml").write_bytes(b"outside configuration\n")
    outside_file = outside / "untouched.js"
    outside_file.write_bytes(b"outside data\n")
    if location == "www":
        (config / "www").symlink_to(outside, target_is_directory=True)
    elif location == "target":
        (config / "www").mkdir()
        (config / resource.RESOURCE_PATH).symlink_to(outside_file)
    elif location == "backups":
        (config / ".simplisafe-streaming-backups").symlink_to(
            outside, target_is_directory=True
        )
    elif location == "configuration":
        (config / "configuration.yaml").unlink()
        (config / "configuration.yaml").symlink_to(outside / "configuration.yaml")
    else:
        alias = tmp_path / "config-alias"
        alias.symlink_to(config, target_is_directory=True)
        config = alias
    with pytest.raises(resource.ResourceError):
        resource.install_resource(payload, config)
    assert outside_file.read_bytes() == b"outside data\n"
    assert (outside / "configuration.yaml").read_bytes() == b"outside configuration\n"


def test_replacement_failure_keeps_original(
    installation: tuple[dict, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    payload, config = installation
    target = config / resource.RESOURCE_PATH
    target.parent.mkdir()
    target.write_bytes(b"// retained\n")
    payload["before_sha256"] = resource._digest(target.read_bytes())
    replace = os.replace

    def fail_resource(source: Path, destination: Path) -> None:
        if destination == target:
            raise OSError("simulated install failure")
        replace(source, destination)

    monkeypatch.setattr(resource.os, "replace", fail_resource)
    with pytest.raises(resource.ResourceError, match="original state restored"):
        resource.install_resource(payload, config)
    assert target.read_bytes() == b"// retained\n"
    assert json.loads(receipts(config)[0].read_text())["status"] == "rolled_back"


@pytest.mark.parametrize("existing", [False, True])
def test_failure_after_replacement_rolls_back(
    installation: tuple[dict, Path], existing: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload, config = installation
    target = config / resource.RESOURCE_PATH
    if existing:
        target.parent.mkdir()
        target.write_bytes(b"// original\n")
        target.chmod(0o640)
        payload["before_sha256"] = resource._digest(target.read_bytes())
    save_receipt = resource._receipt

    def fail_completion(path: Path, value: dict) -> None:
        if value["status"] == "complete":
            raise OSError("simulated receipt failure")
        save_receipt(path, value)

    monkeypatch.setattr(resource, "_receipt", fail_completion)
    with pytest.raises(resource.ResourceError, match="original state restored"):
        resource.install_resource(payload, config)
    if existing:
        assert target.read_bytes() == b"// original\n"
        assert stat.S_IMODE(target.stat().st_mode) == 0o640
    else:
        assert not target.exists()
        assert not target.parent.exists()
    assert json.loads(receipts(config)[0].read_text())["status"] == "rolled_back"
    assert not (config / ".simplisafe-streaming-install.lock").exists()


def test_concurrent_change_is_preserved(
    installation: tuple[dict, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    payload, config = installation
    target = config / resource.RESOURCE_PATH
    target.parent.mkdir()
    save_receipt = resource._receipt

    def introduce_edit(path: Path, value: dict) -> None:
        save_receipt(path, value)
        if value["status"] == "prepared":
            target.write_bytes(b"// concurrent resource\n")

    monkeypatch.setattr(resource, "_receipt", introduce_edit)
    with pytest.raises(resource.ResourceError, match="original state restored"):
        resource.install_resource(payload, config)
    assert target.read_bytes() == b"// concurrent resource\n"


def test_failed_rollback_retains_backup_and_marks_manual_recovery(
    installation: tuple[dict, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    payload, config = installation
    target = config / resource.RESOURCE_PATH
    target.parent.mkdir()
    target.write_bytes(b"// original\n")
    payload["before_sha256"] = resource._digest(target.read_bytes())
    save_receipt = resource._receipt
    replace = os.replace

    def fail_completion(path: Path, value: dict) -> None:
        if value["status"] == "complete":
            raise OSError("simulated receipt failure")
        save_receipt(path, value)

    def fail_restore(source: Path, destination: Path) -> None:
        if source.name == "restore.js":
            raise OSError("simulated rollback failure")
        replace(source, destination)

    monkeypatch.setattr(resource, "_receipt", fail_completion)
    monkeypatch.setattr(resource.os, "replace", fail_restore)
    with pytest.raises(resource.ResourceError, match="manual recovery required"):
        resource.install_resource(payload, config)
    receipt = json.loads(receipts(config)[0].read_text())
    assert receipt["status"] == "rollback_failed"
    assert receipt["rollback_errors"] == [resource.RESOURCE_PATH]
    assert Path(receipt["original_path"]).read_bytes() == b"// original\n"
    assert resource._digest(target.read_bytes()) == payload["after_sha256"]
    assert not (config / ".simplisafe-streaming-install.lock").exists()


def test_existing_lock_is_preserved(installation: tuple[dict, Path]) -> None:
    payload, config = installation
    lock = config / ".simplisafe-streaming-install.lock"
    lock.write_bytes(b"another installation\n")
    with pytest.raises(resource.ResourceError, match="Another installation"):
        resource.install_resource(payload, config)
    assert lock.read_bytes() == b"another installation\n"
    assert not receipts(config)


def test_standalone_cli_build_and_dry_run(
    installation: tuple[dict, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, config = installation
    payload_path = tmp_path / "payload.json"
    monkeypatch.setattr(
        "sys.argv",
        ["installer", "build", str(tmp_path / "reviewed.js"), str(payload_path)],
    )
    assert resource.main() == 0
    monkeypatch.setattr(
        "sys.argv",
        [
            "installer",
            "apply",
            str(payload_path),
            "--config-dir",
            str(config),
            "--dry-run",
        ],
    )
    assert resource.main() == 0
    assert not (config / "www").exists()
