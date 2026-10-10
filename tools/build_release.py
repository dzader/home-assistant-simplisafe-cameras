#!/usr/bin/env python3
"""Build the reviewable Home Assistant install ZIP using only the standard library.

Build from a standalone source clone without Git, network access or sibling
repositories. Only explicit runtime, documentation, tooling and committed
source patches are packaged; private diagnostics are never discovered.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, overload

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "simplirtc"
IGNORED_PARTS = {
	".git",
	"__pycache__",
	".pytest_cache",
	".ruff_cache",
	".mypy_cache",
	"tests",
	"test",
	"node_modules",
}
RUNTIME_SUFFIXES = {".py", ".json", ".png", ".svg"}
SOURCE_PATCHES = ("simplisafe-python", "hass-simplirtc")
OPTIONAL_NOTICES = (
	"LICENSE",
	"LICENSE.contributions",
	"THIRD_PARTY_NOTICES.md",
	"UPSTREAM_README.md",
	"licenses/simplisafe-python-LICENSE",
	"licenses/README.txt",
	"docs/DEVELOPMENT.md",
)


def sha256(data: bytes) -> str:
	return hashlib.sha256(data).hexdigest()


@overload
def public_file(path: Path, *, required: Literal[True] = True) -> bytes: ...


@overload
def public_file(path: Path, *, required: Literal[False]) -> bytes | None: ...


def public_file(path: Path, *, required: bool = True) -> bytes | None:
	"""Read an explicitly selected file without following source-tree symlinks."""
	relative = path.relative_to(ROOT)
	if any(
		(ROOT / Path(*relative.parts[:index])).is_symlink()
		for index in range(1, len(relative.parts) + 1)
	):
		raise RuntimeError(f"Refusing a public source symlink: {relative}")
	if not path.exists() and not required:
		return None
	if not path.is_file():
		raise RuntimeError(f"Required public source file is missing: {relative}")
	return path.read_bytes()


def source_baselines(data: bytes) -> dict[str, dict[str, str]]:
	"""Use saved upstream identities, excluding stale patch and worktree metadata."""
	value = json.loads(data)
	if not isinstance(value, dict):
		raise RuntimeError("SOURCE_BASELINES.json must contain a repositories mapping")
	repositories = value.get("repositories", value)
	if not isinstance(repositories, dict) or not repositories:
		raise RuntimeError("SOURCE_BASELINES.json must contain a repositories mapping")
	result = {}
	for name, baseline in repositories.items():
		if (
			not isinstance(name, str)
			or not isinstance(baseline, dict)
			or not isinstance(baseline.get("url"), str)
			or not isinstance(baseline.get("baseline_commit"), str)
		):
			raise RuntimeError("SOURCE_BASELINES.json contains an invalid source identity")
		result[name] = {key: baseline[key] for key in ("url", "baseline_commit")}
	if not set(SOURCE_PATCHES).issubset(result):
		raise RuntimeError("SOURCE_BASELINES.json is missing a patched source identity")
	return result


def runtime_files() -> list[Path]:
	"""Select runtime files and bundled upstream notices, excluding bytecode."""
	if COMPONENT.is_symlink():
		raise RuntimeError("Refusing a runtime directory symlink")
	result: list[Path] = []
	for path in sorted(COMPONENT.rglob("*")):
		relative = path.relative_to(COMPONENT)
		if any(part in IGNORED_PARTS or part.startswith(".") for part in relative.parts):
			continue
		if path.is_symlink():
			raise RuntimeError(f"Refusing a runtime symlink: {relative}")
		if not path.is_file():
			continue
		if path.suffix in RUNTIME_SUFFIXES or path.name in {
			"LICENSE",
			"NOTICE",
			"README.md",
			"py.typed",
		}:
			result.append(path)
	if not result or not (COMPONENT / "manifest.json").is_file():
		raise RuntimeError("The SimpliRTC runtime folder is missing")
	manifest = json.loads(public_file(COMPONENT / "manifest.json"))
	if not isinstance(manifest, dict) or manifest.get("domain") != "simplirtc":
		raise RuntimeError("Unexpected custom integration domain")
	return result


def build(output: Path) -> dict[str, object]:
	files: dict[str, bytes] = {}
	source_files: dict[str, str] = {}

	runtime = runtime_files()
	for path in runtime:
		archive_path = "custom_components/simplirtc/" + path.relative_to(COMPONENT).as_posix()
		data = public_file(path)
		files[archive_path] = data
		source_files[path.relative_to(ROOT).as_posix()] = sha256(data)

	documents = [
		ROOT / "README.md",
		ROOT / "docs" / "API_NOTES.md",
		ROOT / "docs" / "VALIDATION.md",
		ROOT / "docs" / "SOURCE_BASELINES.json",
		ROOT / "dashboards" / "simplisafe-cameras.yaml",
	]
	for path in documents:
		data = public_file(path)
		files[path.relative_to(ROOT).as_posix()] = data
		source_files[path.relative_to(ROOT).as_posix()] = sha256(data)

	frontend = ROOT / "frontend" / "simplisafe-camera-live.js"
	if frontend.is_symlink() or not frontend.is_file():
		raise RuntimeError("The reviewed dashboard resource is missing or is a symlink")
	files["frontend/simplisafe-camera-live.js"] = public_file(frontend)
	source_files[frontend.relative_to(ROOT).as_posix()] = sha256(
		files["frontend/simplisafe-camera-live.js"]
	)

	resource_installer = ROOT / "tools" / "install_dashboard_resource.py"
	if resource_installer.is_symlink() or not resource_installer.is_file():
		raise RuntimeError("The reviewed dashboard resource installer is missing or is a symlink")
	for name in (
		"build_release.py",
		"inspect_diagnostics.py",
		"apply_runtime_delta.py",
		"install_dashboard_resource.py",
	):
		path = ROOT / "tools" / name
		files[f"tools/{name}"] = public_file(path)
		source_files[f"tools/{name}"] = sha256(files[f"tools/{name}"])

	installer = ROOT / "tools" / "install_release.py"
	if not installer.is_file():
		raise RuntimeError("The reviewed release installer is missing")
	files["INSTALL.py"] = public_file(installer)
	source_files["tools/install_release.py"] = sha256(files["INSTALL.py"])

	repositories = source_baselines(files["docs/SOURCE_BASELINES.json"])
	for name in SOURCE_PATCHES:
		patch_path = f"patches/{name}.patch"
		patch = public_file(ROOT / patch_path)
		files[patch_path] = patch
		source_files[patch_path] = sha256(patch)
		repositories[name]["source_patch_sha256"] = sha256(patch)
	for name in OPTIONAL_NOTICES:
		notice_data = public_file(ROOT / name, required=False)
		if notice_data is not None:
			files[name] = notice_data
			source_files[name] = sha256(notice_data)

	provenance = {
		"format_version": 1,
		"built_at_utc": datetime.now(timezone.utc).isoformat(),
		"installation_payload": "custom_components/simplirtc",
		"dashboard_resource": {
			"archive_path": "frontend/simplisafe-camera-live.js",
			"install_path": "www/simplisafe-camera-live.js",
			"sha256": sha256(files["frontend/simplisafe-camera-live.js"]),
		},
		"hardware_playback_verified_by_build": False,
		"repositories": repositories,
		"source_file_sha256": source_files,
		"payload_sha256": {name: sha256(data) for name, data in sorted(files.items())},
	}
	files["PROVENANCE.json"] = (json.dumps(provenance, indent=2, sort_keys=True) + "\n").encode()
	files["SHA256SUMS"] = "".join(
		f"{sha256(data)}  {name}\n" for name, data in sorted(files.items())
	).encode()

	output.parent.mkdir(parents=True, exist_ok=True)
	with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".zip.tmp", delete=False) as temp:
		temp_path = Path(temp.name)
	try:
		with zipfile.ZipFile(
			temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
		) as archive:
			for name, data in sorted(files.items()):
				entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
				entry.compress_type = zipfile.ZIP_DEFLATED
				entry.external_attr = 0o100644 << 16
				archive.writestr(entry, data)
		with zipfile.ZipFile(temp_path) as archive:
			if archive.testzip() is not None:
				raise RuntimeError("ZIP integrity check failed")
			if set(archive.namelist()) != set(files):
				raise RuntimeError("ZIP content check failed")
			for name, data in files.items():
				if archive.read(name) != data:
					raise RuntimeError(f"ZIP payload verification failed: {name}")
		temp_path.replace(output)
	finally:
		temp_path.unlink(missing_ok=True)
	return {
		"path": str(output),
		"files": len(files),
		"bytes": output.stat().st_size,
		"sha256": sha256(output.read_bytes()),
		"runtime_files": len(runtime),
	}


def main() -> None:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument(
		"--output",
		type=Path,
		default=ROOT / "dist" / "simplisafe-camera-support.zip",
		help="Output ZIP path (default: dist/simplisafe-camera-support.zip)",
	)
	args = parser.parse_args()
	print(json.dumps(build(args.output.resolve()), indent=2))


if __name__ == "__main__":
	main()
