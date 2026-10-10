"""Check resource packaging in isolated fixture ZIPs, without rebuilding releases."""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tools import build_release as release


@pytest.fixture
def source_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
	root = tmp_path / "source"
	files = {
		"custom_components/simplirtc/manifest.json": b'{"domain":"simplirtc","version":"1.1.3"}',
		"custom_components/simplirtc/camera.py": b"# runtime\n",
		"custom_components/simplirtc/.cache/private.py": b"# never packaged\n",
		"custom_components/simplirtc/tests/private.py": b"# never packaged\n",
		"frontend/simplisafe-camera-live.js": b"// reviewed frontend\n",
		"frontend/tests/fixture.mjs": b"// tests stay outside payload\n",
		"frontend/private.json": b"private export never packaged\n",
		"README.md": b"instructions\n",
		"docs/API_NOTES.md": b"API notes\n",
		"docs/VALIDATION.md": b"validation boundaries\n",
		"docs/DEVELOPMENT.md": b"standalone clone development guide\n",
		"docs/SOURCE_BASELINES.json": json.dumps(
			{
				"repositories": {
					name: {
						"url": f"https://example.invalid/{name}",
						"baseline_commit": "a" * 40,
						"source_patch_sha256": "obsolete-patch-hash",
						"untracked_source_additions": ["obsolete-worktree-metadata"],
					}
					for name in ("hass-simplirtc", "simplisafe-python")
				}
			}
		).encode(),
		"dashboards/simplisafe-cameras.yaml": b"views: []\n",
		"tools/build_release.py": Path(release.__file__).read_bytes(),
		"tools/inspect_diagnostics.py": b"# inspector\n",
		"tools/apply_runtime_delta.py": b"# runtime updater\n",
		"tools/install_release.py": b"# reviewed installer\n",
		"tools/install_dashboard_resource.py": b"# reviewed resource installer\n",
		"patches/simplisafe-python.patch": b"# stored library source patch\n",
		"patches/hass-simplirtc.patch": b"# stored integration source patch\n",
		"patches/private.patch": b"private patch never packaged\n",
		"licenses/simplisafe-python-LICENSE": b"MIT fixture\n",
		"licenses/README.txt": b"license notices\n",
		"licenses/private.json": b"private export never packaged\n",
		"LICENSE.contributions": b"contribution license\n",
		"THIRD_PARTY_NOTICES.md": b"upstream notices\n",
		"UPSTREAM_README.md": b"upstream guide\n",
		"PROVENANCE.json": b'{"stale":"old archive provenance"}',
		"private-evidence/diagnostics.json": b"private evidence never packaged\n",
	}
	for name, data in files.items():
		path = root / name
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_bytes(data)
	monkeypatch.setattr(release, "ROOT", root)
	monkeypatch.setattr(release, "COMPONENT", root / "custom_components/simplirtc")
	return root


def test_only_reviewed_frontend_and_installer_are_packaged_with_hashes(
	source_tree: Path, tmp_path: Path
) -> None:
	output = tmp_path / "fixture.zip"
	result = release.build(output)
	with zipfile.ZipFile(output) as archive:
		names = archive.namelist()
		assert "frontend/simplisafe-camera-live.js" in names
		assert "tools/install_dashboard_resource.py" in names
		assert not any("fixture.mjs" in name or "private" in name for name in names)
		assert not any(name.startswith("custom_components/simplirtc/frontend/") for name in names)
		expected = {}
		for line in archive.read("SHA256SUMS").decode().splitlines():
			digest, path = line.split("  ", 1)
			expected[path] = digest
		assert expected.keys() == set(names) - {"SHA256SUMS"}
		assert all(
			release.sha256(archive.read(name)) == digest for name, digest in expected.items()
		)
		provenance = json.loads(archive.read("PROVENANCE.json"))
		frontend_digest = release.sha256(archive.read("frontend/simplisafe-camera-live.js"))
		assert (
			provenance["source_file_sha256"]["frontend/simplisafe-camera-live.js"]
			== frontend_digest
		)
		assert provenance["dashboard_resource"] == {
			"archive_path": "frontend/simplisafe-camera-live.js",
			"install_path": "www/simplisafe-camera-live.js",
			"sha256": frontend_digest,
		}
		assert provenance["source_file_sha256"][
			"tools/install_dashboard_resource.py"
		] == release.sha256((source_tree / "tools/install_dashboard_resource.py").read_bytes())
		for name in ("simplisafe-python", "hass-simplirtc"):
			patch_path = f"patches/{name}.patch"
			baseline = provenance["repositories"][name]
			assert archive.read(patch_path) == (source_tree / patch_path).read_bytes()
			assert baseline["baseline_commit"] == "a" * 40
			assert baseline["source_patch_sha256"] == release.sha256(archive.read(patch_path))
			assert "untracked_source_additions" not in baseline
		for name in (
			"LICENSE.contributions",
			"THIRD_PARTY_NOTICES.md",
			"UPSTREAM_README.md",
			"licenses/simplisafe-python-LICENSE",
			"docs/DEVELOPMENT.md",
		):
			assert archive.read(name) == (source_tree / name).read_bytes()
			assert provenance["source_file_sha256"][name] == release.sha256(archive.read(name))
		assert "stale" not in provenance
	assert result["sha256"] == release.sha256(output.read_bytes())


@pytest.mark.parametrize("case", ["absent", "symlink"])
def test_unreviewed_frontend_source_is_rejected(
	source_tree: Path, tmp_path: Path, case: str
) -> None:
	frontend = source_tree / "frontend/simplisafe-camera-live.js"
	frontend.unlink()
	if case == "symlink":
		frontend.symlink_to(source_tree / "README.md")
	with pytest.raises(RuntimeError, match="reviewed dashboard resource"):
		release.build(tmp_path / "never-created.zip")
	assert not (tmp_path / "never-created.zip").exists()


def test_missing_resource_installer_is_rejected(source_tree: Path, tmp_path: Path) -> None:
	(source_tree / "tools/install_dashboard_resource.py").unlink()
	with pytest.raises(RuntimeError, match="reviewed dashboard resource installer"):
		release.build(tmp_path / "never-created.zip")
	assert not (tmp_path / "never-created.zip").exists()


def test_standalone_cli_build_needs_no_git_or_sibling_clones(
	source_tree: Path, tmp_path: Path
) -> None:
	output = tmp_path / "standalone.zip"
	assert not (source_tree / ".git").exists()
	assert not (source_tree / "hass-simplirtc").exists()
	assert not (source_tree / "simplisafe-python").exists()
	result = subprocess.run(
		[sys.executable, str(source_tree / "tools/build_release.py"), "--output", str(output)],
		cwd=tmp_path,
		capture_output=True,
		text=True,
		check=False,
	)
	assert result.returncode == 0, result.stderr
	assert json.loads(result.stdout)["sha256"] == release.sha256(output.read_bytes())
	with zipfile.ZipFile(output) as archive:
		assert archive.read("INSTALL.py") == (source_tree / "tools/install_release.py").read_bytes()
		assert "docs/SOURCE_BASELINES.json" in archive.namelist()


def test_direct_baselines_mapping_and_optional_notices_are_supported(
	source_tree: Path, tmp_path: Path
) -> None:
	baseline_path = source_tree / "docs/SOURCE_BASELINES.json"
	baseline_path.write_text(json.dumps(json.loads(baseline_path.read_text())["repositories"]))
	for name in release.OPTIONAL_NOTICES:
		(source_tree / name).unlink(missing_ok=True)
	release.build(tmp_path / "minimal-notices.zip")
	with zipfile.ZipFile(tmp_path / "minimal-notices.zip") as archive:
		assert not set(release.OPTIONAL_NOTICES).intersection(archive.namelist())


@pytest.mark.parametrize(
	"case",
	["symlink_patch", "symlink_notices", "missing_baselines", "missing_patch", "invalid_baselines"],
)
def test_invalid_standalone_sources_fail_before_output(
	source_tree: Path, tmp_path: Path, case: str
) -> None:
	if case == "symlink_patch":
		path = source_tree / "patches/simplisafe-python.patch"
		path.unlink()
		path.symlink_to(source_tree / "README.md")
	elif case == "symlink_notices":
		path = source_tree / "THIRD_PARTY_NOTICES.md"
		path.unlink()
		path.symlink_to(source_tree / "README.md")
	elif case == "missing_baselines":
		(source_tree / "docs/SOURCE_BASELINES.json").unlink()
	elif case == "missing_patch":
		(source_tree / "patches/hass-simplirtc.patch").unlink()
	else:
		(source_tree / "docs/SOURCE_BASELINES.json").write_text('{"repositories":{}}')
	output = tmp_path / "never-created.zip"
	with pytest.raises(RuntimeError):
		release.build(output)
	assert not output.exists()
