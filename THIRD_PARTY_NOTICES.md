# Source attribution and licenses

This project is a fork of [gilliginsisland/SimpliRTC](https://github.com/gilliginsisland/hass-simplirtc), retaining its Git history. The starting commit is `d6c2ecb12ec3c8a7b44a2d6ea98b85971c56bf39`. Original contributions by this fork are licensed in [LICENSE.contributions](LICENSE.contributions). That license does **not** cover the upstream SimpliRTC portions: the upstream baseline did not contain an integration-wide license. Do not describe the entire integration as MIT-licensed or Apache-licensed.

## Files included in the integration

| Files | Origin | License and notices |
| --- | --- | --- |
| Upstream SimpliRTC integration, branding, and documentation portions | [gilliginsisland/hass-simplirtc](https://github.com/gilliginsisland/hass-simplirtc/tree/d6c2ecb12ec3c8a7b44a2d6ea98b85971c56bf39) | No integration-wide license supplied at the baseline. This fork makes no additional license grant for those portions. |
| `custom_components/simplirtc/vendor/aiortc/` | [aiortc](https://github.com/aiortc/aiortc/tree/1.14.0), version 1.14.0; copyright Jeremy Lainé | BSD-3-Clause; retain the full [bundled license](custom_components/simplirtc/vendor/aiortc/LICENSE). Upstream source imports are namespaced under `custom_components.simplirtc.vendor` to use Home Assistant's compatible media dependencies. |
| `custom_components/simplirtc/protobufs/` generated LiveKit modules | [livekit/protocol](https://github.com/livekit/protocol/tree/ad2cb9af4442a0ee04bb0815ba4b8ee4e2f6ba6f), pinned upstream protocol submodule; copyright 2023 LiveKit, Inc. | Apache-2.0; retain the full upstream license and NOTICE alongside the generated modules. Generated with Protocol Buffers Python 6.32.1 and adapted by upstream SimpliRTC to use relative imports. |

The LiveKit protocol [LICENSE](https://github.com/livekit/protocol/blob/ad2cb9af4442a0ee04bb0815ba4b8ee4e2f6ba6f/LICENSE) and [NOTICE](https://github.com/livekit/protocol/blob/ad2cb9af4442a0ee04bb0815ba4b8ee4e2f6ba6f/NOTICE) remain authoritative. The aiortc BSD license forbids using the aiortc name or its contributors' names to endorse this project without permission.

## Library patches

The companion `simplisafe-python` source patch is based on [bachya/simplisafe-python](https://github.com/bachya/simplisafe-python/tree/b03f554cdb2c5981841a8aaa3ee7bb17ef6e79ee). Its upstream MIT license credits William Scanlon and Aaron Bach and must accompany redistributed copies or substantial portions of that patch. The installed camera integration reuses Home Assistant's native SimpliSafe authentication and library; installing the companion library patch is not required.

## Research references

The following projects informed API and compatibility research. Their reference checkouts are not runtime dependencies and are not bundled as complete projects:

- [Home Assistant Core](https://github.com/home-assistant/core/tree/2427629e2e6b50890a4abbce39f193bfdc7257e4), Apache-2.0: camera, stream, and native SimpliSafe integration interfaces.
- [Homebridge SimpliSafe 3](https://github.com/homebridge-simplisafe3/homebridge-simplisafe3/tree/aec7b6939c45fe2d69223d0e173599a75048226a), MIT; copyright Niccolo Zapponi and Michael Shamoon: legacy FLV and LiveKit API behavior.
- [BookOnTape/simplisafe-livekit-homekit](https://github.com/BookOnTape/simplisafe-livekit-homekit/tree/ebb527bb2e596562fecbc80a4d44ee2f7e7ea795), MIT; copyright 2026 BookOnTape: repeated live-view request investigation.
- [LiveKit Server](https://github.com/livekit/livekit/tree/5a897c239cde995e615047be3fd5b27ca1afdad7), Apache-2.0: signaling and participant lifecycle behavior.

Imports from Home Assistant, Protocol Buffers, and other separately installed dependencies do not change their licenses. Preserve each dependency's license when redistributing its code. SimpliSafe and Home Assistant names identify compatibility; this project is not affiliated with or endorsed by SimpliSafe, Home Assistant, or the credited third-party authors.
