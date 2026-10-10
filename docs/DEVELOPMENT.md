# Developing and rebuilding

The integration uses Home Assistant's installed `simplipy` package. Its companion library patch is included for separate upstream review and is not required to install the integration.

## Integration and dashboard checks

Use Python 3.14 for the two tested Home Assistant releases. Create a disposable environment, install `requirements-test.txt`, the selected `homeassistant` version, and the dependencies listed in `custom_components/simplirtc/manifest.json`. The complete commands are in [CI](../.github/workflows/ci.yml).

Run from the repository root:

```sh
python -m pytest tests
node --test frontend/tests/simplisafe-camera-live.test.cjs
python tools/build_release.py
```

The tools-only suite can run without Home Assistant:

```sh
python -m pytest tests/tools --confcutdir=tests/tools
```

The builder reads committed source patches and baseline metadata, and includes only explicit runtime, documentation, notice, frontend, and tool files. It requires no account credentials or additional reference checkouts. Hardware video validation is a separate step.

## Companion Python library patch

Clone the upstream library and apply the patch at the pinned baseline:

```sh
git clone https://github.com/bachya/simplisafe-python.git
cd simplisafe-python
git checkout b03f554cdb2c5981841a8aaa3ee7bb17ef6e79ee
git apply /path/to/home-assistant-simplisafe-cameras/patches/simplisafe-python.patch
```

Use the library's own development dependencies and pytest configuration to run its tests. The archived integration patch records the initial implementation against its upstream baseline; this fork's current files and Git history are authoritative for subsequent development.

Keep camera photos, credentials, raw network captures, diagnostic exports, private signaling logs, and installation backups outside the repository. Use fake credentials and fixture identifiers in tests.
