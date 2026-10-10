"""Exercise release installation against real temporary configuration directories."""

from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path

import pytest

from tools import install_release as installer


def make_archive(
    path: Path, *, extra: dict[str, bytes] | None = None, bad_hash: bool = False
) -> Path:
    """Create a complete, hashed release with runtime and non-runtime payloads."""
    payload = {
        "custom_components/simplirtc/__init__.py": b'"""New streaming integration."""\n',
        "custom_components/simplirtc/manifest.json": b'{"domain":"simplirtc","version":"1.0.0"}\n',
        "custom_components/simplirtc/camera.py": b'"""Camera transport."""\n',
        "custom_components/simplirtc/vendor/LICENSE": b"MIT license\n",
        "custom_components/simplirtc/py.typed": b"",
        "INSTALL.py": b"# installer metadata\n",
        "README.md": b"Installation instructions\n",
    }
    payload.update(extra or {})
    manifest = "".join(
        f"{installer.sha256(data)}  {name}\n" for name, data in sorted(payload.items())
    )
    if bad_hash:
        manifest = "0" * 64 + manifest[64:]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in payload.items():
            archive.writestr(name, data)
        archive.writestr("SHA256SUMS", manifest)
    return path


def make_config(
    path: Path, content: bytes = b"default_config:\n# Keep this comment.\n"
) -> Path:
    """Create a real configuration plus an unrelated custom component."""
    path.mkdir()
    (path / "configuration.yaml").write_bytes(content)
    unrelated = path / "custom_components" / "unrelated"
    unrelated.mkdir(parents=True)
    (unrelated / "__init__.py").write_bytes(b"# existing user component\n")
    (path / "automations.yaml").write_bytes(b"# existing automations\n")
    return path


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    """A preview validates the package and leaves every configuration file untouched."""
    archive = make_archive(tmp_path / "release.zip")
    config = make_config(tmp_path / "config")
    original_files = {
        path.relative_to(config): path.read_bytes()
        for path in config.rglob("*")
        if path.is_file()
    }
    plan = installer.install_archive(archive, config, dry_run=True)
    assert plan["status"] == "dry-run"
    assert plan["appended_keys"] == ["simplirtc"]
    assert not plan["will_replace_component"]
    assert not (config / ".simplisafe-streaming-backups").exists()
    assert not (config / "custom_components" / "simplirtc").exists()
    assert {
        path.relative_to(config): path.read_bytes()
        for path in config.rglob("*")
        if path.is_file()
    } == original_files


def test_install_backs_up_and_preserves_unrelated_files(tmp_path: Path) -> None:
    """Install only the component and append one key while preserving original bytes."""
    archive = make_archive(tmp_path / "release.zip")
    original = b"default_config:\r\n# Keep this comment.\r\n"
    config = make_config(tmp_path / "config", original)
    receipt = installer.install_archive(archive, config)
    backup = Path(receipt["backup_dir"])
    assert receipt["status"] == "complete"
    assert (backup / "configuration.yaml").read_bytes() == original
    assert (config / "configuration.yaml").read_bytes() == original + b"simplirtc:\r\n"
    assert (config / "custom_components" / "simplirtc" / "camera.py").is_file()
    assert not (config / "INSTALL.py").exists()
    assert not (config / "README.md").exists()
    assert (
        config / "custom_components" / "unrelated" / "__init__.py"
    ).read_bytes() == b"# existing user component\n"
    assert (config / "automations.yaml").read_bytes() == b"# existing automations\n"
    assert receipt["configuration"]["before_sha256"] == installer.sha256(original)
    assert receipt["component"]["backup_path"] is None
    assert json.loads((backup / "receipt.json").read_text())["status"] == "complete"
    assert not (config / ".simplisafe-streaming-install.lock").exists()


def test_existing_component_and_reinstall_preserve_backups(tmp_path: Path) -> None:
    """Retain the whole old component and avoid duplicate YAML entries on reinstall."""
    archive = make_archive(tmp_path / "release.zip")
    original = b"stream:\nsimplirtc:\n# custom config stays byte-for-byte\n"
    config = make_config(tmp_path / "config", original)
    old = config / "custom_components" / "simplirtc"
    (old / "nested").mkdir(parents=True)
    (old / "__init__.py").write_bytes(b"# previous integration\n")
    (old / "nested" / "local-data.json").write_bytes(b'{"custom":"preserve"}\n')
    first = installer.install_archive(archive, config)
    second = installer.install_archive(archive, config)
    old_backup = Path(first["component"]["backup_path"])
    assert (old_backup / "__init__.py").read_bytes() == b"# previous integration\n"
    assert (
        old_backup / "nested" / "local-data.json"
    ).read_bytes() == b'{"custom":"preserve"}\n'
    assert first["backup_dir"] != second["backup_dir"]
    assert (config / "configuration.yaml").read_bytes() == original
    assert (
        first["configuration"]["appended_keys"]
        == second["configuration"]["appended_keys"]
        == []
    )
    assert first["component"]["before_hashes"][
        "nested/local-data.json"
    ] == installer.sha256(b'{"custom":"preserve"}\n')
    assert not (old / "nested" / "local-data.json").exists()


@pytest.mark.parametrize(
    ("original", "expected", "keys"),
    [
        (
            b"homeassistant:\n  name: Home",
            b"homeassistant:\n  name: Home\nsimplirtc:\nstream:\n",
            ["simplirtc", "stream"],
        ),
        (
            b"'default_config': {}\n",
            b"'default_config': {}\nsimplirtc:\n",
            ["simplirtc"],
        ),
        (b'"stream": {}\nsimplirtc: {}\n', b'"stream": {}\nsimplirtc: {}\n', []),
        (
            b"# simplirtc:\nparent:\n  simplirtc: ignored\n",
            b"# simplirtc:\nparent:\n  simplirtc: ignored\nsimplirtc:\nstream:\n",
            ["simplirtc", "stream"],
        ),
        (b"", b"simplirtc:\nstream:\n", ["simplirtc", "stream"]),
    ],
)
def test_top_level_yaml_detection(
    original: bytes, expected: bytes, keys: list[str]
) -> None:
    """Recognize quoted root keys while ignoring comments and nested occurrences."""
    assert installer.updated_configuration(original) == (expected, keys)


@pytest.mark.parametrize(
    "configuration",
    [
        b"stream:\nstream:\n",
        b"{default_config: {}}\n",
        b"---\nstream:\n---\nother:\n",
        b"stream:\n...\n",
    ],
)
def test_ambiguous_yaml_is_rejected(configuration: bytes) -> None:
    """Refuse configurations that cannot safely accept a top-level append."""
    with pytest.raises(installer.InstallError):
        installer.updated_configuration(configuration)


@pytest.mark.parametrize(
    "name",
    [
        "../escaped.py",
        "/absolute.py",
        "custom_components/simplirtc/../../escaped.py",
        "custom_components\\simplirtc\\camera.py",
        "custom_components/simplirtc/./camera.py",
    ],
)
def test_archive_traversal_is_rejected(tmp_path: Path, name: str) -> None:
    """No unsafe archive member can reach staging or configuration files."""
    archive = make_archive(tmp_path / "release.zip", extra={name: b"bad"})
    config = make_config(tmp_path / "config")
    with pytest.raises(installer.InstallError, match="unsafe path"):
        installer.install_archive(archive, config)
    assert not (config / ".simplisafe-streaming-backups").exists()
    assert not (tmp_path / "escaped.py").exists()


def test_bad_hash_and_uncovered_payload_are_rejected(tmp_path: Path) -> None:
    """Verify digests and require the manifest to cover the entire payload."""
    archive = make_archive(tmp_path / "bad.zip", bad_hash=True)
    with pytest.raises(installer.InstallError, match="mismatch"):
        installer.validate_archive(archive)
    archive = make_archive(tmp_path / "uncovered.zip")
    with zipfile.ZipFile(archive, "a") as output:
        output.writestr("hidden.py", b"uncovered")
    with pytest.raises(installer.InstallError, match="exactly cover"):
        installer.validate_archive(archive)


def test_archive_symlink_and_size_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject links and bounded decompression before any installation writes."""
    archive = make_archive(tmp_path / "symlink.zip")
    info = zipfile.ZipInfo("custom_components/simplirtc/link.py")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "a") as output:
        output.writestr(info, "../../configuration.yaml")
    with pytest.raises(installer.InstallError, match="symlink"):
        installer.validate_archive(archive)
    archive = make_archive(
        tmp_path / "large.zip", extra={"README-large.md": b"x" * 4096}
    )
    monkeypatch.setattr(installer, "MAX_FILE_BYTES", 1024)
    with pytest.raises(installer.InstallError, match="size limit"):
        installer.validate_archive(archive)


def test_configuration_is_required_and_symlinks_are_refused(tmp_path: Path) -> None:
    """Prevent accidentally installing into the wrong directory or through a link."""
    archive = make_archive(tmp_path / "release.zip")
    config = tmp_path / "config"
    config.mkdir()
    with pytest.raises(installer.InstallError, match="configuration.yaml"):
        installer.install_archive(archive, config)
    elsewhere = tmp_path / "outside.yaml"
    elsewhere.write_bytes(b"default_config:\n")
    (config / "configuration.yaml").symlink_to(elsewhere)
    with pytest.raises(installer.InstallError, match="real configuration.yaml"):
        installer.install_archive(archive, config)
    assert elsewhere.read_bytes() == b"default_config:\n"


def test_failed_install_restores_configuration_and_component(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure after both replacements restores the exact original files."""
    archive = make_archive(tmp_path / "release.zip")
    original = b"default_config:\n# original settings\n"
    config = make_config(tmp_path / "config", original)
    target = config / "custom_components" / "simplirtc"
    target.mkdir()
    (target / "__init__.py").write_bytes(b"# original component\n")
    original_write_receipt = installer._write_receipt

    def fail_complete(path: Path, receipt: dict) -> None:
        if receipt["status"] == "complete":
            raise OSError("simulated receipt-write failure")
        original_write_receipt(path, receipt)

    monkeypatch.setattr(installer, "_write_receipt", fail_complete)
    with pytest.raises(installer.InstallError, match="original files restored"):
        installer.install_archive(archive, config)
    assert (config / "configuration.yaml").read_bytes() == original
    assert (target / "__init__.py").read_bytes() == b"# original component\n"
    receipt_path = next(
        (config / ".simplisafe-streaming-backups").glob("*/receipt.json")
    )
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "rolled_back"
    assert Path(receipt["failed_component_path"]).is_dir()
    assert not (config / ".simplisafe-streaming-install.lock").exists()


def test_rollback_failure_retains_recovery_backups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report a failed restoration while retaining the original config backup."""
    archive = make_archive(tmp_path / "release.zip")
    original = b"default_config:\n"
    config = make_config(tmp_path / "config", original)
    original_write = installer._atomic_write
    original_write_receipt = installer._write_receipt
    trigger = {"rollback": False}

    def fail_complete(path: Path, receipt: dict) -> None:
        if receipt["status"] == "complete":
            trigger["rollback"] = True
            raise OSError("simulated install failure")
        original_write_receipt(path, receipt)

    def fail_restore(path: Path, data: bytes, mode: int = 0o600) -> None:
        if trigger["rollback"] and path.name == "configuration.yaml":
            raise OSError("simulated rollback failure")
        original_write(path, data, mode)

    monkeypatch.setattr(installer, "_write_receipt", fail_complete)
    monkeypatch.setattr(installer, "_atomic_write", fail_restore)
    with pytest.raises(installer.InstallError, match="manual repair"):
        installer.install_archive(archive, config)
    receipt_path = next(
        (config / ".simplisafe-streaming-backups").glob("*/receipt.json")
    )
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "rollback_failed"
    assert Path(receipt["configuration"]["backup_path"]).read_bytes() == original
    assert receipt["rollback_errors"]
    assert not (config / "custom_components" / "simplirtc").exists()
