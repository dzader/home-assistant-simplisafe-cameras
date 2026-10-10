"""Verify explicit integration requirements against the tested runtime."""

import json
from importlib.metadata import version
from pathlib import Path

from packaging.requirements import Requirement


def test_manifest_declares_response_and_protobuf_runtimes():
	manifest = json.loads(
		(Path(__file__).parents[1] / "custom_components/simplirtc/manifest.json").read_text()
	)
	requirements = {item.name.lower(): item for item in map(Requirement, manifest["requirements"])}
	assert {"pydantic", "protobuf"} <= requirements.keys()
	for requirement in requirements.values():
		assert version(requirement.name) in requirement.specifier
