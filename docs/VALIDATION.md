# Validation — October 9, 2026

The custom integration was installed on a Home Assistant installation, restarted successfully, and played actual live video from all four cameras. Version **1.1.3** and its recovery resource then passed a **649.55-second run (10 minutes 49 seconds)** in a desktop browser: **both outdoor cameras automatically reopened twice and returned to 1920×1080**, while both legacy feeds continued at 1280×720 with no automatic restart. A later full close/reopen also returned all four after one automatic startup retry per card. These checks establish viewing and repeated automatic recovery on the tested account and installation. They do not establish indefinite playback or physical-phone compatibility.

A subsequent longer 1.1.0 run exposed a limit: both LiveKit outdoor feeds became black at 2×2 pixels around 351 seconds while the legacy feeds continued playing. Version 1.1.1's live-view GET polling every 10 seconds **failed to resolve that limit**. Later telemetry captured the camera publisher disconnecting, and the official web player also failed during a comparison check. Version 1.1.3 replaces ineffective polling with native-player reopening after video loss. The initial playback passes remain valid; the new automatic recovery is a separate hardware check.

## Installed environment

- Home Assistant Core **2026.6.4**, Home Assistant OS **18.0**, x86_64, Python **3.14.5**.
- Native SimpliSafe authentication was repaired through Home Assistant and confirmed healthy.
- Initial custom integration **SimpliSafe Camera Streaming 1.1.0** loaded **4 devices / 8 entities**; the currently installed recovery version is **1.1.3**.
- Home Assistant configuration check passed after restart, with no warnings reported by that check.
- The final post-restart configuration check also passed in the UI: **“Configuration will not prevent Home Assistant from starting!”**
- The native SimpliSafe integration and its installed Python library were retained.
- The installer created a configuration/component backup before installation; its local path is kept private.

## Actual camera playback

Each camera was opened and actual video was observed. The times below are sampled browser `<video>.currentTime` values in seconds, not measurements of startup latency or proof of an uninterrupted long-duration session.

| Anonymized camera | Provider | Video size | Sampled playback time |
| --- | --- | --- | ---: |
| Legacy camera B | `simplisafe` → FLV relay | 1280×720 | 29.901 s |
| Legacy camera A | `simplisafe` → FLV relay | 1280×720 | 19.896 s |
| Outdoor camera A | `mist` → LiveKit WebRTC | 1920×1080 | 19.431 s |
| Outdoor camera B | `mist` → LiveKit WebRTC | 1920×1080 | 16.157 s |

The Outdoor camera A video sample reported `paused=false` and `readyState=4` while actual video was visible. Private screenshots for the four playback checks are retained outside the public repository and are excluded from the release package. They are not embedded here.

## Dashboard and concurrent playback

The tested SimpliSafe Cameras dashboard provides **On demand** and **Live view** tabs. **On demand** is the default. The dashboard source is [simplisafe-cameras.yaml](../dashboards/simplisafe-cameras.yaml).

All four cameras also played concurrently in the Live view dashboard. Each video reported `paused=false` and `readyState=4` at the sampled check:

| Camera | Concurrent video size | Sampled playback time |
| --- | --- | ---: |
| Legacy camera A | 1280×720 | 27.275870 s |
| Legacy camera B | 1280×720 | 27.476220 s |
| Outdoor camera A | 1920×1080 | 21.193347 s |
| Outdoor camera B | 1920×1080 | 21.220158 s |

This establishes concurrent playback of four different cameras in one desktop browser. Multiple clients viewing the same camera and prolonged concurrent sessions were not tested.

While the live dashboard was being edited, both LiveKit players transiently reported **“SimpliSafe camera signaling closed; reopen the live view.”** Switching to On demand and back to Live view restored all four feeds. The cause of the transient failure was not captured; the runtime was left unchanged after ordinary reopening succeeded.

A subsequent normal tab-switch check spent approximately one second on On demand, then returned to Live view. All four players were playing at the later sample, with `paused=false` and `readyState=4`: Legacy camera A 25.674332 s / 1280×720; Legacy camera B 25.761897 s / 1280×720; Outdoor camera A 25.306177 s / 1920×1080; Outdoor camera B 25.504388 s / 1920×1080. This is evidence of recovery and normal reopening, not a guarantee against future cloud/signaling interruptions.

During the initial dashboard check, debug logging was temporarily enabled and then disabled again; the UI returned to **Enable debug logging**. No debug-log download was produced at that stage. Later duration investigation retained private signaling telemetry, summarized below without identifiers or credentials.

First-frame startup can take approximately 30 seconds in the observed viewing flow. This is an observed expectation, not a guaranteed upper bound. Outdoor-camera deep-sleep behavior, wake-cycle timing, and battery consumption were not separately measured.

## Initial 1.1.0 automated and package checks

| Check | Result | What it covers |
| --- | --- | --- |
| `simplisafe-python` suite | 133 passed | Existing library behavior, live-view discovery, typed response validation, provider selection, and token handling |
| Custom integration on HA Core 2026.6.4 | 33 passed | Compatibility with the installed HA version; camera/relay/session behavior in mocked tests |
| Custom integration on HA Core 2026.10 | 33 passed | Compatibility against the newer HA API environment |
| Installer suite | 22 passed | Hash validation, path safety, dry run, YAML additions, backup, replacement, and rollback behavior |
| Release verification | Passed | ZIP integrity, complete checksum coverage, exact installer/runtime bytes, provenance, and an installer dry run |

The 2026.6.4 compatibility environment used stock `simplisafe-python` 2024.1.0 and `av` 16.0.1. The 2026.10 environment used stock `simplisafe-python` 2026.6.0 and `av` 19.0.0. The library and installer JUnit reports are retained locally outside the public repository.

Verified initial installed archive, retained privately for historical comparison: **296,259 bytes**, **72 runtime files**, **83 archive entries**. SHA-256:

```text
eb8954e7435458ac2ea0f85b5c3191dac317c57baa1bf48473848a531a8bbc41
```

The initial archive contains 82 checksum entries, 77 source-file hashes, source patches for both modified repositories, and four upstream baseline commits. That versioned ZIP was built before live testing and remains unchanged. Its `hardware_playback_verified_by_build=false` provenance field correctly describes what the build operation itself checked; the subsequent installed-camera playback evidence is recorded in this report.

## Installed 1.1.1 candidate

Version **1.1.1** was installed through a validated runtime delta. The delta changed only `camera.py` and `manifest.json` inside the custom integration. Its sustained outdoor-camera hardware check failed: both outdoor feeds were black at **2×2 pixels** by sampled browser playback times of **300.66 s** and **301.59 s**, despite live-view GET polling every 10 seconds. Both legacy feeds continued playing at 1280×720 beyond 300 seconds. That historical candidate did not establish a duration fix; the later 1.1.3 recovery result is recorded below.

- Integration tests: **39 passed** on HA Core 2026.6.4 and **39 passed** on HA Core 2026.10.
- Delta-tool tests: **12 passed**.
- The runtime delta created a backup before replacement; its local path is kept private.
- Historical 1.1.1 archive at the time of its installed check: **308,874 bytes**, **72 runtime files**, **86 archive entries**, **85 checksum entries**, **80 source-file hashes**, and **5 upstream baseline commits**. Its checksum below identifies that earlier archive, rather than the newer distribution at the unversioned ZIP path.

SHA-256:

```text
f728a5586a0877e338a082d13999d640af892b6349dd309f68b270dbab4c166f
```

That archive included the validation boundary recorded when it was built. The subsequent failed hardware check is recorded here separately.

## Publisher-loss trace and official web comparison

An installed **1.1.2** diagnostic run captured a camera publisher reporting **DISCONNECTED**, reason **1 / CLIENT_INITIATED**, followed by its video track being removed and the active camera-video track count reaching zero. This occurred approximately **146.8 seconds** into a viewer attached to an already warm room; it is not a measured cold-session lifetime. Viewer signaling pings remained healthy. Follow-up discovery reported the **same room** and a **different token subject**, with approximately **601 seconds** remaining before token expiry. A new camera publisher appeared about nine seconds after a later live-view GET, while the old browser video remained 2×2. The immediate failure was loss of the camera publisher/video track; the reason the camera ended publishing is unresolved.

The official SimpliSafe web app also stopped during an actual comparison: the last working video sample was **1920×1080 / 239.61 seconds**, and approximately 30 seconds later the player was **0×0, paused**, with **“Unable to Stream Video.”** This establishes a similar failure outside Home Assistant. Sample timing does not establish an exact five-minute timeout or a published policy.

The trace and screenshots remain private local evidence. This report records no room/participant identities, tokens, network identities, or camera images.

## Installed 1.1.3 automatic recovery — passed

The backend emits one normal `stream_closed` WebRTC error after confirmed camera-publisher video loss and closes the viewer session. Its ineffective 10-second live-view polling has been removed. The JavaScript resource wraps Home Assistant's native camera card and automatically remounts it after video loss, with a 60-second startup grace period, increasing retry delays, and a six-failure ceiling followed by a manual **Retry** button. Hidden/disconnected cards unload their native child and cancel timers; unavailable or missing entities do not start a stream.

The registered resource is `/local/simplisafe-camera-live.js?v=1.1.3-r3`, resource type **JavaScript module**. All four cards in the Live view dashboard use `custom:simplisafe-camera-live`; the default On demand tab retains its native cards. The integration UI confirms **1.1.3 / 4 devices / 8 entities**.

The first real frontend load exposed `NotSupportedError`: the custom-element constructor added a host attribute, which `document.createElement` forbids during construction. Initial status now modifies only shadow content; host status waits for lifecycle reconciliation. A new fixture enforces that browser construction restriction, reproduced the error before the fix, and passes with the corrected source. This caught a browser behavior the earlier fixtures did not model. The corrected resource was installed and all four wrappers rendered. That resource update created a local backup before replacement.

The first playback run then showed valid 720p/1080p video on all four cameras near 25 seconds, followed by false automatic reopenings by 82 seconds. DOM inspection confirmed that each native card could contain both an active WebRTC video and a hidden 0×0, paused HLS alternative. The original traversal selected that hidden alternative. The wrapper now checks visibility through composed ancestors and prefers the active valid video; it also ignores a hidden alternative's error. A realistic dual-player fixture reproduced the false retry before this correction and passes after it. The new source was installed, the resource URL was updated to `1.1.3-r2`, and the QA page was fully reloaded before the second run began. The resource update created a local backup. Its installed bytes match this SHA-256:

```text
3298b976239eb559d468ffa53a20db122954ff035fc9bba14d564a8d17786085
```

A subsequent r2 run had working video at checks near 48 and 146 seconds, then all four cards reopened together by approximately 210 seconds after about a minute without fresh rendering. The page still reported visible. This coordinated behavior exposed the frame-only stall policy's inability to distinguish interrupted browser composition from interrupted media. The latest frontend also accepts advancing media time with valid video dimensions when frame callbacks/counters stop. A 30-second stall now requires both media time and frame progress to stop. A frozen picture under an advancing audio/media clock cannot be reliably distinguished by that policy and may require manual reopening; confirmed backend publisher loss, visible native errors, and 2×2 video remain separate recovery triggers.

The new regression keeps media time moving for 90 seconds while frame APIs remain present but produce no callbacks or counter progress, and confirms no false restart. Another test confirms recovery when both clocks stop. These checks do not establish long-duration cloud recovery. The latest corrected resource was installed with matching bytes, its `1.1.3-r3` UI resource entry was confirmed saved, and a fresh QA page loaded that asset. The resource update created a local backup. Home Assistant was restarted between investigative attempts. Temporary INFO tracing was reset between attempts and then enabled only for allowlisted publisher-loss diagnostics during the final run. Installed frontend SHA-256:

```text
7c7724363b23a22e4541c9999982739cf97fc78d2d68767202d25739b91fde07
```

The final real-browser run showed all four cameras playing at the 27-second and 48-second checks, with legacy video at 1280×720, outdoor video at 1920×1080, and all four automatic-reconnect counters still zero. All four remained playing with zero reconnects at the approximately 274-second sample.

At approximately 289 seconds, Outdoor camera A had automatically reopened once and was playing 1920×1080; Outdoor camera B also had one automatic reopen and was starting. By approximately 305 seconds, both outdoor feeds were playing 1920×1080 again, while the legacy feeds were still at 1280×720 with zero automatic restarts. At approximately 579 seconds, both outdoor counters had reached two and both feeds were playing 1920×1080. They were still playing at the 599-second sample and at the final **649.55-second** sample. At that final check, all four reported `paused=false` and `readyState=4`:

| Camera | Video size | Current player time | Automatic reopenings |
| --- | --- | ---: | ---: |
| Legacy camera A | 1280×720 | 644.239 s | 0 |
| Legacy camera B | 1280×720 | 644.511 s | 0 |
| Outdoor camera A | 1920×1080 | 76.331 s | 2 |
| Outdoor camera B | 1920×1080 | 70.396 s | 2 |

Player time resets on an automatic reopening; these values are not the test's elapsed time. Allowlisted backend telemetry independently recorded **two Outdoor camera A publisher losses**, each `CLIENT_INITIATED` with one video track removed and zero remaining, followed by the backend closing that viewer. Outdoor camera B's two recoveries are established by the frontend counters and subsequent real 1080p video; no equivalent two-event backend trace is claimed for it. The legacy counters stayed zero throughout this run. No coordinated healthy-feed restarts were observed with the final policy.

The final run used **DOM metric samples at approximately 20-second intervals**, with several five-second samples near the first drop. Screenshots were taken only after recovery and at the end. It did **not** use recurring screenshots to force video rendering. This validates the final media-time policy through two outdoor session endings in the automation browser, separately from the earlier frame-only failure. Indefinite unattended operation and other browsers/devices remain untested. Full sampled metrics and private screenshots are retained outside the public repository. They are excluded from the distribution and are not embedded here. Reconnect/startup gaps were present, including the Outdoor camera B startup visible at the first recovery sample.

After the sustained run, switching from Live view to On demand left **zero dashboard video elements**. An immediate return to Live view initially showed 0×0 players and one legacy go2rtc error. Each card made one automatic startup retry after its 60-second grace period; all four were playing again by approximately **86 seconds**, at the final reopen sample. The cumulative counters were Legacy camera A/Legacy camera B **1/1** and Outdoor camera A/Outdoor camera B **3/3**, one higher per card than before the close/reopen. Actual video was 1280×720 at player times 28.165/28.655 seconds for the legacy cameras and 1920×1080 at 12.331/17.941 seconds for the outdoor cameras; every player reported `paused=false`, `readyState=4`. This proves eventual reopening with the startup retry, rather than instant startup. Sampled reopen evidence is retained outside the public repository.

The dashboard was finally left on **On demand** with zero video elements. Temporary integration logging was restored to **warning** using a successful `logger.set_level` service action. The final Home Assistant configuration check passed. All **72 installed backend files** match the frozen 1.1.3 source, and the installed frontend matches the SHA-256 above; these bytes remained unchanged during the successful checks.

| Check | Result | Scope |
| --- | --- | --- |
| Custom integration on HA Core 2026.6.4 | 46 passed | Backend camera, relay, session and publisher-loss behavior in mocked checks |
| Custom integration on HA Core 2026.10 | 46 passed | Compatibility with the newer HA API environment |
| Frontend Node lifecycle suite | 32 passed | Browser construction contract, visible/hidden-alternate selection, media-clock continuity, true stalls, startup grace, finite/manual retry, visibility teardown and asynchronous generation races |
| Installer, runtime delta, frontend resource and packaging suites | 64 passed | 22 installer + 13 delta + 29 resource/packaging checks; hash/state validation, backup/rollback, atomic replacement, CLI and package content |
| Frontend syntax and scoped whitespace checks | Passed | JavaScript parse and patch formatting |
| Extended installed recovery run | **Passed** | Two actual automatic reopenings per outdoor camera; both returned to 1080p while legacy feeds continued at 720p without automatic restarts |
| Normal dashboard close/reopen | **Passed with startup retry** | Zero video elements on On demand; all four returned by about 86 seconds after one 60-second startup retry per card |

The frontend suite uses DOM/video fixtures with a simulated clock; it does not render Home Assistant or establish working cloud recovery. That behavior is established separately by the installed run above. Automated reports and private installed-check evidence are retained locally outside the public repository. The [v1.1.3 release](https://github.com/dzader/home-assistant-simplisafe-cameras/releases/tag/v1.1.3) contains the installable ZIP and checksum sidecar, built with the streaming implementation and frontend used in that run. Public repository metadata, dependency notices, documentation, and packaging are prepared separately for distribution. The builder's `hardware_playback_verified_by_build=false` field describes the build operation; this report records the separate installed playback evidence.

## Remaining limits

Kinesis protocol behavior is covered by source inspection and mocked checks; the four tested cameras use FLV or LiveKit. No Kinesis hardware playback was available here. Two automatic outdoor reopenings passed per camera in the desktop check without recurring screenshots. Indefinite sessions, uninterrupted session renewal, indefinite unattended/background operation, multiple clients viewing the same camera, two-way audio, battery longevity, and physical-phone playback were not established. Video frozen under an advancing audio/media clock may require manual reopening. Dashboard startup and reopening depend on cloud reachability and camera availability; the observed full reopen needed one startup retry and about 86 seconds.

Configuration-check success covers the configuration checker at the time of the check; it is not a claim that every Home Assistant log or unrelated integration is warning-free. The four successful camera checks are actual installed playback evidence, separate from automated tests.

## Public repository preparation

The public fork retains the upstream Git history and uses a standalone release builder. Before publication, the backend suite passed again with 46 tests on each of Home Assistant 2026.6.4 and 2026.10.0, the frontend suite passed all 32 tests, and the portable builder/installer suite passed 70 tests. The companion library patch was applied to a fresh checkout of its pinned upstream baseline and passed all 133 library tests.

Public repository metadata, dependency license notices, example dashboard entities, documentation, and package generation were prepared for distribution. The streaming implementation and frontend used for the installed video check were preserved. No additional hardware playback is implied by this publication check. Private account data, photos, logs, and installation backups are excluded.
