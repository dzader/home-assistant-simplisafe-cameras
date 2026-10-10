# SimpliSafe camera streaming for Home Assistant

This integration adds camera streaming beside Home Assistant's existing SimpliSafe integration. It includes native WebRTC for cameras using AWS Kinesis (`kvs`) or LiveKit (`mist`), and authenticated FLV streaming for older cameras using `simplisafe` or no provider setting. Camera transport is discovered from the API rather than guessed from the product name.

The install package contains a custom integration named `simplirtc`. It reuses the native SimpliSafe integration's authenticated connection; no additional username, password, API key, or refresh token belongs in your YAML. The accompanying [simplisafe-python source patch](patches/simplisafe-python.patch) provides typed live-view discovery for future library use. Installing this custom integration does not require replacing Home Assistant's installed Python library.

All four cameras played actual video on the connected Home Assistant Core 2026.6.4 / Home Assistant OS installation: two legacy cameras through authenticated FLV at 1280×720; two outdoor cameras through LiveKit at 1920×1080. Configuration checks passed after the initial restart, and the integration loaded four devices and eight entities. Startup often took approximately 30 seconds; a later close/reopen check needed one automatic startup retry and about 86 seconds for all four feeds to return. See [VALIDATION.md](docs/VALIDATION.md) for playback evidence and test results, and [API_NOTES.md](docs/API_NOTES.md) for the observed protocols.

Longer viewing exposed outdoor-camera sessions ending while the two legacy feeds continued. Version 1.1.1's repeated live-view GET did not prevent this. Later signaling evidence captured the camera publisher leaving, and the official SimpliSafe web player also stopped during a comparison check. These observations do not establish a published five-minute policy.

Installed version **1.1.3** passed a desktop-browser check lasting **10 minutes 49 seconds**: both outdoor cameras automatically reopened twice and returned to 1920×1080 video, while the two legacy cameras continued at 1280×720 without automatic restarts. Expect a reconnect and camera-startup gap when the cloud ends a session. The card watches only while connected and visible, gives each startup 60 seconds, uses increasing retry delays, and stops after six consecutive failed attempts with a **Retry** button. Backend tests pass on both tested Home Assistant versions, and 32 frontend lifecycle tests pass. This verifies automatic reopening on this installation, with the detailed bounds recorded in [VALIDATION.md](docs/VALIDATION.md).

## Install on Home Assistant

1. In **Settings → System → Backups**, create a backup. Keep a copy of your existing `configuration.yaml`, any existing `/config/custom_components/simplirtc` folder, dashboard YAML, and any existing `www/simplisafe-camera-live.js` outside their active locations.
2. Confirm the native **SimpliSafe** integration is loaded under **Settings → Devices & services**. Complete its **Reconfigure** or authentication repair prompt if necessary. You only sign in through Home Assistant's existing SimpliSafe flow.
3. Choose one installation method:

   - **Manual:** download `simplisafe-camera-support-1.1.3.zip` from the [v1.1.3 release](https://github.com/dzader/home-assistant-simplisafe-cameras/releases/tag/v1.1.3) and extract it on your computer. Copy its entire `custom_components/simplirtc` folder into Home Assistant so `/config/custom_components/simplirtc/manifest.json` exists. Preserve the bundled `vendor`, `protobufs`, and `brand` directories. Use a file transfer method that can copy folders, such as Samba or SSH. On Home Assistant OS, `/config` means Home Assistant's configuration directory. Documentation and patches in the ZIP do not belong in `custom_components`.
   - **HACS:** open HACS → menu → **Custom repositories**, add `https://github.com/dzader/home-assistant-simplisafe-cameras`, choose **Integration**, and install the repository. See the [official HACS instructions](https://www.hacs.dev/docs/faq/custom_repositories/). HACS installs the backend; complete the YAML and frontend steps below as well.

4. Add this **top-level** entry to `/config/configuration.yaml`:

   ```yaml
   simplirtc:
   ```

   If your configuration already includes `default_config:`, streaming is available through it. If you do not use `default_config:`, also add `stream:` as a top-level entry. Keep a single entry for each integration; do not create duplicate YAML keys.
5. Run Home Assistant's configuration check, then restart Home Assistant. A full restart is required when first installing or replacing custom integration Python files. Initial setup may take longer while dependencies install.
6. After restart, look for **SimpliSafe Camera Streaming** under **Settings → Devices & services**, and search **Settings → Devices & services → Entities** for your camera entities. The custom integration discovers the already configured SimpliSafe account automatically; there is no additional integration setup form.
7. Download the [v1.1.3 release](https://github.com/dzader/home-assistant-simplisafe-cameras/releases/tag/v1.1.3) and copy its `frontend/simplisafe-camera-live.js` to `/config/www/simplisafe-camera-live.js`. Create the `www` folder if needed. Under **Settings → Dashboards → Resources**, add `/local/simplisafe-camera-live.js?v=1.1.3-r3` with resource type **JavaScript module**. The Resources entry may be in the dashboard settings menu and require Advanced mode in your profile. Reload the dashboard after registering the resource.

If you already use upstream SimpliRTC, back up its folder before replacing it. Both repositories use the `simplirtc` domain; select one implementation and manage updates through that repository. An update from the upstream repository can replace this fork's changes.

The ZIP also includes `INSTALL.py`, a standard-library installer for environments with access to the Home Assistant configuration directory. It checks the archive hashes, backs up configuration and any prior component, and copies only the runtime folder. Review its dry run first:

```sh
python3 INSTALL.py simplisafe-camera-support-1.1.3.zip --config-dir /config --dry-run
```

Then repeat without `--dry-run` to install. Use the configuration directory exposed by your environment; Home Assistant OS commonly exposes `/config`, while some add-ons expose `/homeassistant`. The installer automatically appends `simplirtc:` when absent and adds `stream:` when neither `stream:` nor `default_config:` is present. After it completes, check the configuration and restart Home Assistant. The installer does not sign in to SimpliSafe or restart Home Assistant.

The separate resource helper verifies and backs up the frontend file. From the extracted ZIP, create a payload and review its dry run:

```sh
python3 tools/install_dashboard_resource.py build frontend/simplisafe-camera-live.js camera-live-resource.json
python3 tools/install_dashboard_resource.py apply camera-live-resource.json --config-dir /config --dry-run
```

Repeat the `apply` command without `--dry-run` to install the file, then register the JavaScript module in the dashboard UI as described above. The initial payload requires that the destination file is absent. For an update, supply `--before-sha256` and the current file's exact SHA-256 to the `build` command. The helper does not register resources or change dashboard/configuration YAML.

## Add a dashboard camera card

The [example dashboard](dashboards/simplisafe-cameras.yaml) has **On demand** and **Live view** tabs. All four cameras were verified playing concurrently in Live view, including two automatic outdoor-camera reopenings during the extended check. The On demand tab keeps its native cards and remains the default so opening the dashboard does not immediately request all four live feeds. Its source is [dashboards/simplisafe-cameras.yaml](dashboards/simplisafe-cameras.yaml).

Replace `camera.your_camera` with the actual entity ID from Home Assistant's entity list. The default card displays the latest available snapshot; tapping it opens the camera dialog for viewing on demand. A snapshot can be absent until an event has provided an image.

The dashboard uses these example entity IDs. Replace every example with your own camera entity:

| Camera | Entity ID |
| --- | --- |
| Legacy camera A | `camera.example_legacy_a` |
| Legacy camera B | `camera.example_legacy_b` |
| Outdoor camera A | `camera.example_outdoor_a` |
| Outdoor camera B | `camera.example_outdoor_b` |

```yaml
type: picture-entity
entity: camera.your_camera
name: Camera
camera_view: auto
show_state: false
tap_action:
  action: more-info
```

After registering the JavaScript module, use this card for visible live viewing with automatic reopening:

```yaml
type: custom:simplisafe-camera-live
entity: camera.your_camera
name: Camera
camera_view: live
aspect_ratio: '16:9'
show_state: false
tap_action:
  action: more-info
```

Start with one camera and the on-demand card. A live card requests a cloud stream while you are viewing the dashboard. Repeated viewing can wake battery cameras and reduce battery life. Avoid enabling camera stream preloading for battery cameras.

## Troubleshooting and useful evidence

- **No camera entities:** confirm the native SimpliSafe integration is loaded, `simplirtc:` is at the top level, and the folder is not accidentally nested twice. V2 systems are skipped; cameras with unknown providers are skipped and reported in diagnostics.
- **Authentication error:** repair the native SimpliSafe integration. This custom integration relies on that connection.
- **Black image or slow start:** startup often took approximately 30 seconds, but the full close/reopen check needed one 60-second startup retry and all four feeds returned by about 86 seconds. Let the custom card finish its startup/retry cycle. If it still does not start, open the same camera in the official app once to verify it is online, then close the app's live view and retry Home Assistant. Playback from both outdoor cameras passed; deep-sleep wake behavior and battery consumption were not separately measured.
- **Missing snapshot:** the thumbnail uses camera event history. Lack of a recent image does not by itself prove live streaming is unavailable.
- **Stream disconnects:** the custom live card reopens the native player after a detected error, a 2×2 placeholder following valid video, or 30 seconds without media-time or frame progress. Two actual automatic reopenings passed for each outdoor camera. A normal native card or camera dialog can still require manual reopening. Cloud sessions can end; this is not an indefinite recording feed.
- **Frozen image while audio/time keeps advancing:** the frontend cannot reliably distinguish this from the browser temporarily stopping video rendering. Reopen the camera manually in that case. Camera-publisher loss and native errors are still handled separately.
- **Outdoor feed goes black after several minutes:** previous versions showed a 2×2 black frame while the legacy cameras kept playing; repeated live-view GET did not fix it. Version 1.1.3 removes that ineffective polling and uses automatic reopening in the custom live card. If it stops after repeated failures, choose **Retry**, or switch to On demand and reopen Live view. Camera startup can create a brief gap.
- **“Custom element doesn't exist: simplisafe-camera-live”:** confirm the file is in `www`, register `/local/simplisafe-camera-live.js?v=1.1.3-r3` as a JavaScript module, and reload the dashboard.
- **“SimpliSafe camera signaling closed; reopen the live view”:** switch to **On demand**, then back to **Live view**. Both outdoor streams showed this transient error during dashboard editing; switching tabs restored all four feeds. A subsequent normal tab switch also passed. The failure cause was not captured.
- **Legacy stream fails:** Home Assistant's stream decoder receives FLV through a private loopback relay. OAuth credentials remain inside Home Assistant. Both tested legacy cameras passed actual playback on this installation; another installation still needs its own media-connectivity and decoder checks.
- **Unsupported provider:** download diagnostics for **SimpliSafe Camera Streaming**. These diagnostics report provider, backend, and camera type without camera names, serials, account IDs, network identities, or authentication tokens. The optional offline tool below extracts the same limited fields from a native SimpliSafe diagnostic export.

Useful next information is your Home Assistant version and installation type, the camera models and whether they are battery powered, the safe provider summary, and any sanitized error message from the camera attempt. Do not share passwords, OAuth callback URLs, refresh/access tokens, signed stream URLs, or raw browser network captures.

To summarize a native diagnostic export locally:

```sh
python3 tools/inspect_diagnostics.py /path/to/downloaded-diagnostics.json
```

The tool reads one local file and prints an allowlisted camera/backend summary. It does not connect to Home Assistant or SimpliSafe. Keep the original diagnostic export private.

## Roll back

Remove the top-level `simplirtc:` entry, restore the backed-up `custom_components/simplirtc` folder or remove the newly installed folder, check the configuration, and restart Home Assistant. Restore any dashboard cards you changed, remove the registered JavaScript resource, and restore or remove `www/simplisafe-camera-live.js`. Keep the existing native SimpliSafe integration configured. If needed, use the Home Assistant backup to restore the prior configuration.

## Source and package provenance

The repository contains the installable integration, frontend resource, tests, and tooling:

- `custom_components/simplirtc/`: the patched [SimpliRTC](https://github.com/gilliginsisland/hass-simplirtc) integration, including legacy FLV support and session handling.
- `frontend/simplisafe-camera-live.js`: the dashboard card that reopens the native player after detected stream loss; `frontend/tests/` contains its lifecycle fixtures.
- `tools/` and `tests/`: release, installer, resource, runtime-delta, and backend checks.
- `patches/simplisafe-python.patch`: library changes for typed live-view discovery and response validation. They are included for review and future upstream use; replacing Home Assistant's installed library is not required.
- `patches/hass-simplirtc.patch`: the archived implementation patch against the original SimpliRTC baseline.

Reference repositories were used for API research and compatibility checks but are not bundled as cloned repositories. These include [Home Assistant Core](https://github.com/home-assistant/core), [Homebridge SimpliSafe 3](https://github.com/homebridge-simplisafe3/homebridge-simplisafe3), and [BookOnTape's LiveKit bridge](https://github.com/BookOnTape/simplisafe-livekit-homekit). Pinned source contracts and validation limits are recorded in [API_NOTES.md](docs/API_NOTES.md) and [VALIDATION.md](docs/VALIDATION.md).

After validation, build a release from this repository:

```sh
python3 tools/build_release.py
```

The builder never stages or commits changes. It records source provenance and file hashes in `PROVENANCE.json`, includes the archived source patches, and writes `SHA256SUMS` for the archive payload. Release ZIPs contain no cloned Git directories, local environments, bytecode, credentials, private camera screenshots, or signaling traces. The backend installer copies `custom_components/simplirtc`; the frontend file requires its separate installation and dashboard-resource registration.

The [v1.1.3 release](https://github.com/dzader/home-assistant-simplisafe-cameras/releases/tag/v1.1.3) contains the installable ZIP and its SHA-256 sidecar. Its streaming implementation and frontend match the successful installed check; repository metadata, dependency notices, documentation, and packaging are prepared for public distribution. Historical investigative versions are described in the validation report rather than recommended for installation.

## Local checks

With the Python dependencies for the selected Home Assistant version installed in a disposable environment, run the backend/tool checks and frontend fixtures from the repository root:

```sh
python3 -m pytest tests
node --test frontend/tests/simplisafe-camera-live.test.cjs
python3 tools/build_release.py
```

Use the dependency setup in the repository's CI workflow for the supported Home Assistant test environments. Automated fixtures do not connect to a real SimpliSafe account; actual camera playback must be tested separately. The accompanying library patch's 133-test result comes from its upstream library test environment, not this integration's pytest suite.
