# Observed SimpliSafe camera APIs

These contracts come from current open-source client implementations. SimpliSafe does not publish these camera API contracts in the cited sources. A source implementation is evidence of protocol use, not proof of playback against the tested account or every camera model. No private account data was used to establish these contracts.

Subsequent authenticated Home Assistant diagnostics confirmed that the tested account has two cameras using `simplisafe` and two using `mist`, on Home Assistant Core 2026.6.4 / Home Assistant OS with Python 3.14.5. This is live discovery evidence only. Raw diagnostic files, device identities, addresses, and credentials are excluded from the public source repository and install package.

## Backend discovery

Camera payloads expose `cameraSettings.admin.webRTCProvider`. `kvs` means AWS Kinesis WebRTC, `mist` means LiveKit, and `simplisafe` or an absent provider setting selects the legacy FLV path in Homebridge. Unknown values should be reported instead of guessed. Account migrations can change the provider without changing camera or base-station firmware. See [SimpliRTC's backend documentation](https://github.com/gilliginsisland/hass-simplirtc#advanced-backend-names) and [Homebridge's provider selection](https://github.com/homebridge-simplisafe3/homebridge-simplisafe3/blob/aec7b6939c45fe2d69223d0e173599a75048226a/src/accessories/camera.js#L91-L104).

Use the camera's API UUID for endpoint paths. Hardware serials may also appear in motion events; they are not guaranteed to be the same identifier. The subscription ID identifies the location/system and belongs in the second path segment of the live-view request.

## Authenticated live-view discovery

```http
GET https://app-hub.prd.aser.simplisafe.com/v2/cameras/{camera_uuid}/{subscription_id}/live-view
Authorization: Bearer {simplisafe_access_token}
```

There is no request body. The bearer credential is the normal SimpliSafe OAuth access token. This host differs from the normal `api.simplisafe.com` API host; an explicit `Host` header must match the destination. [Homebridge's observed request and LiveKit response validation](https://github.com/homebridge-simplisafe3/homebridge-simplisafe3/blob/aec7b6939c45fe2d69223d0e173599a75048226a/src/simplisafe.js#L352-L389).

LiveKit responses contain:

```json
{
  "cameraStatus": "example-status",
  "liveKitDetails": {
    "liveKitURL": "wss://example-livekit-host",
    "userToken": "temporary-participant-jwt"
  }
}
```

Kinesis responses contain:

```json
{
  "signedChannelEndpoint": "wss://example-kinesis-host/temporary-signed-endpoint",
  "clientId": "example-viewer-id",
  "iceServers": [
    {
      "urls": ["turn:example-turn-host:443"],
      "username": "temporary-username",
      "credential": "temporary-credential"
    }
  ]
}
```

These are placeholder values, not actual credentials. Accept optional metadata without requiring it. Validate required transport fields before opening a connection. Source: [Python bridge Kinesis contract](https://github.com/jarvisryan/simplisafe-apple-home-bridge/blob/1d24d00a7b613742fb9ae71f25482b8f2334507f/src/simplisafe_apple_home/client.py), [SimpliRTC camera implementation](https://github.com/gilliginsisland/hass-simplirtc/blob/d6c2ecb12ec3c8a7b44a2d6ea98b85971c56bf39/custom_components/simplirtc/camera.py).

Fetch new metadata for each new viewer session. Do not save room JWTs, signed websocket URLs, or ICE passwords to configuration or log output.

## Kinesis WebRTC

Connect to the returned `signedChannelEndpoint`. The endpoint is already signed by SimpliSafe; the viewer does not need its own AWS account credentials. Kinesis signaling is JSON. `SDP_OFFER` and `ICE_CANDIDATE` requests use `action`, `recipientClientId`, `correlationId`, and `messagePayload`. The payload contains base64-encoded JSON. Responses use `messageType`, with `SDP_ANSWER` and `ICE_CANDIDATE` conveying negotiation data. [SimpliRTC Kinesis adapter](https://github.com/gilliginsisland/hass-simplirtc/blob/d6c2ecb12ec3c8a7b44a2d6ea98b85971c56bf39/custom_components/simplirtc/kinesis.py), [AWS WebRTC SDK](https://github.com/awslabs/amazon-kinesis-video-streams-webrtc-sdk-js).

AWS currently documents a one-minute maximum signaling URL duration, a one-hour websocket connection maximum, a ten-minute websocket idle timeout, and a five-minute TURN credential lifecycle. These are AWS service quotas, not measured SimpliSafe-specific session limits. [AWS WebRTC quotas](https://docs.aws.amazon.com/en_us/kinesisvideostreams-webrtc-dg/latest/devguide/kvswebrtc-limits.html).

## LiveKit WebRTC

The returned LiveKit URL is a signaling service, not a plain media file. Join its `/rtc` websocket with the participant token and exchange binary LiveKit `SignalRequest`/`SignalResponse` protobufs. Homebridge uses the older subscriber negotiation: the server sends SDP offers, the viewer sends SDP answers, and ICE candidates target the subscriber connection. This custom integration instead forwards the Home Assistant browser's offer through LiveKit's publisher connection and returns its answer/candidates. Both styles need to handle tracks appearing or changing after the initial join. Ping timing comes from the server's join response.

Homebridge uses a protocol-15 URL containing `access_token`, `auto_subscribe=1`, `sdk=js`, and `version=2.22.3`. SimpliRTC uses protocol 16 with a compressed protobuf join request and a bearer header. This custom integration retains the SimpliRTC signaling implementation. [Homebridge LiveKit adapter](https://github.com/homebridge-simplisafe3/homebridge-simplisafe3/blob/aec7b6939c45fe2d69223d0e173599a75048226a/src/lib/liveKitSource.js), [SimpliRTC LiveKit adapter](https://github.com/gilliginsisland/hass-simplirtc/blob/d6c2ecb12ec3c8a7b44a2d6ea98b85971c56bf39/custom_components/simplirtc/livekit.py), [LiveKit client protocol](https://docs.livekit.io/reference/internals/client-protocol/).

LiveKit token expiry constrains the initial join rather than terminating an already connected session. LiveKit can refresh participant tokens over signaling. SimpliSafe may apply additional rules; no fixed SimpliSafe stream duration is established here. [LiveKit token lifecycle](https://docs.livekit.io/frontends/reference/tokens-grants/).

## Legacy FLV and snapshots

```http
GET https://media.simplisafe.com/v1/{camera_uuid}/flv?x=1280&audioEncoding=AAC
Authorization: Bearer {simplisafe_access_token}
```

The observed media response is an FLV stream. Authentication is a bearer header; the observed request does not put the access token in a query parameter. The existing Python library's `video_url()` produces the URL only, so adding `token=` to its arbitrary keyword arguments would not establish a supported authentication contract. [Homebridge legacy input](https://github.com/homebridge-simplisafe3/homebridge-simplisafe3/blob/aec7b6939c45fe2d69223d0e173599a75048226a/src/lib/streamingDelegate.js#L318-L333), [upstream Python camera URL](https://github.com/bachya/simplisafe-python/blob/b03f554cdb2c5981841a8aaa3ee7bb17ef6e79ee/simplipy/device/camera.py).

Homebridge also uses the following MJPEG snapshot endpoint with the same bearer header:

```http
GET https://media.simplisafe.com/v1/{camera_uuid}/mjpg?x=1280&fr=1
```

Its implementation resolves the media hostname to an IP address and disables TLS certificate validation for snapshots. This custom integration preserves HTTPS hostname validation and does not copy that workaround. Its thumbnails use event history, so thumbnails do not intentionally open live media or a privacy shutter. [Homebridge snapshot implementation](https://github.com/homebridge-simplisafe3/homebridge-simplisafe3/blob/aec7b6939c45fe2d69223d0e173599a75048226a/src/lib/streamingDelegate.js#L150-L174).

The custom integration supplies FLV to Home Assistant's decoder through a token-protected listener bound to `127.0.0.1` on a random port. This is an in-process adapter, not a LAN RTSP server. OAuth access tokens remain in the upstream HTTP request inside Home Assistant. The decoder-facing URL has a separate local random capability; it is not the SimpliSafe OAuth token. The relay authenticates on connection, retries an upstream 401 after refreshing, and closes its tasks/listener when unloaded.

No working SimpliSafe HLS or RTSP URL was established in this research. Kinesis's general HLS capabilities do not prove that the SimpliSafe account has permission to request those URLs.

## Battery wake, cloud access, and privacy limits

Battery cameras can sleep between events. Homebridge waits up to 30 seconds for its first video packet and explicitly notes that separate wake handling is not implemented there. Another Python bridge retries live-view discovery every two seconds, retrying HTTP 400/409 and responses lacking LiveKit credentials. Those retry policies are implementation evidence rather than a published SimpliSafe wake contract. [Homebridge startup timeout](https://github.com/homebridge-simplisafe3/homebridge-simplisafe3/blob/aec7b6939c45fe2d69223d0e173599a75048226a/src/lib/liveKitSource.js#L52-L60), [Python bridge wake retries](https://github.com/jarvisryan/simplisafe-apple-home-bridge/blob/1d24d00a7b613742fb9ae71f25482b8f2334507f/src/simplisafe_apple_home/livekit_bridge.py).

Further read-only inspection of the official public web app, version 4.13.0, found a `wakeBatteryCameras` API method using:

```http
POST https://app-hub.prd.aser.simplisafe.com/v1/ss3/subscriptions/{subscription_id}/camera-wakeup
Authorization: Bearer {simplisafe_access_token}
Content-Type: application/json

{"cameraSerial":"example-camera-identifier","wakeAll":false}
```

The method also constructs `{"wakeAll":true}` when no camera identifier is supplied. The official app subscribes to that all-camera wake request when subscriptions are loaded or the selected location changes, for eligible battery cameras. The LiveKit connection helper also calls the single-camera method before requesting room details, although that call alone does not prove its returned observable is executed. This establishes methods in shipped official client code, not hardware-verified requests or a proved renewal contract. This custom integration has not deployed or tested that POST. Source: [official app API bundle](https://webapp.simplisafe.com/main.454416466c99e82c.js), [official camera-player bundle](https://webapp.simplisafe.com/945.ec1a49db3c1908ae.js). These public assets were inspected without signing in or executing them.

The current custom integration retries incomplete live-view responses within bounded startup time, and retains SimpliRTC's LiveKit warmup behavior. Camera availability can also be restricted while a monitoring agent has an active feed. [SimpliSafe's agent-viewing documentation](https://support.simplisafe.com/articles/smart-alarm-indoor-camera/how-do-recordings-and-live-streams-work-during-an-intruder-intervention-alarm).

On-demand dashboard viewing is the starting target. Indefinite wall displays, battery longevity under continuous viewing, camera wake from deep sleep, two-way audio, simultaneous viewers, device/browser combinations, and any monitoring-plan restrictions require real account/hardware checks. This workspace does not claim those validations from mocked tests.

## LiveKit duration investigation and recovery

A longer installed playback check on the tested account reached approximately 351 seconds before both outdoor LiveKit video frames became black at 2×2 pixels. The legacy FLV cameras continued playing. No corresponding signaling-close event was captured at that time. This is an observed rendering/media failure; its cause is not yet established and must not be explained as JWT expiry alone.

BookOnTape's MIT-licensed bridge has a `keep_warm()` loop that repeats the same authenticated `/v2/cameras/{camera_uuid}/{subscription_id}/live-view` GET. Its default interval is **10 seconds**, and that loop can run alongside a LiveKit media session. Its documentation describes this as keeping the camera online. It uses the app-hub `Host` header and discards newly returned room credentials during these polls. This is evidence of another client's intended keep-online polling, separate from LiveKit's websocket ping; it does not prove a session-renewal contract. [Pinned bridge implementation](https://github.com/BookOnTape/simplisafe-livekit-homekit/blob/ebb527bb2e596562fecbc80a4d44ee2f7e7ea795/bridge/bridge.py#L199-L242), [bridge documentation](https://github.com/BookOnTape/simplisafe-livekit-homekit/blob/ebb527bb2e596562fecbc80a4d44ee2f7e7ea795/README.md).

The installed 1.1.1 candidate polled every 10 seconds only while a viewer had an active LiveKit session. Its **hardware duration validation failed**: both outdoor feeds became 2×2 black frames by sampled playback times of 300.66 and 301.59 seconds, while the legacy feeds continued beyond 300 seconds. Repeated live-view GET requests alone did not fix the duration issue. Version 1.1.3 removes this ineffective polling. No official fixed five-minute LiveKit stream limit, separate renewal endpoint, or required timer-reset data message was established by this research.

The official camera bundle also contains a `MaxConceivableTimeToLive=300000` constant, but its use is a limit on recorded startup timing metrics in the older player, not a demonstrated LiveKit session shutdown timer. It must not be conflated with the outdoor-camera failure. The LiveKit player listens for participant/track changes, receives data-channel events, and has an initial track-arrival timeout. [Official camera-player bundle](https://webapp.simplisafe.com/945.ec1a49db3c1908ae.js).

In the inspected official bundles, the LiveKit helper fetches room details when connecting; no periodic live-view GET or application call to send a LiveKit data message was identified in that player. Its recurring 100 ms task looks up the camera participant and subscribed tracks locally. It treats a departing camera participant or missing video track as a stream failure. The bundled camera-state protobuf contains audio mode and interaction-result fields, but no timer-reset field was identified. This is a bounded source inspection, not a claim about all mobile app behavior.

The official bundled LiveKit SDK defaults to one peer connection. It handles `mediaSectionsRequirement` by adding the requested audio/video transceivers and renegotiating; retains `refreshToken` for later connection recovery; updates room/participant state on `roomMoved`; and distinguishes `leave` actions requesting disconnect, reconnect, or resume. The installed 1.1.1 adapter cannot add browser transceivers after the original Home Assistant offer, resends that original offer for extra-media requirements, and does not implement all of those continuity paths. These are concrete capability differences worth checking in failure telemetry; they are not established causes of the approximately five-minute outdoor failure. [Official camera-player and bundled SDK](https://webapp.simplisafe.com/945.ec1a49db3c1908ae.js).

The upstream LiveKit SFU defines a blank **2×2 H.264 keyframe** and uses it to flush a viewer's decoder when a subscribed track closes. That makes a track-close/flush a source-supported interpretation of the observed 2×2 frame, while leaving SimpliSafe's reason for closing the camera track unresolved. It does not require the viewer's signaling websocket to close. [LiveKit blank H.264 frame](https://github.com/livekit/livekit/blob/5a897c239cde995e615047be3fd5b27ca1afdad7/pkg/sfu/downtrack.go#L96-L108), [track-close decoder flush](https://github.com/livekit/livekit/blob/5a897c239cde995e615047be3fd5b27ca1afdad7/pkg/sfu/downtrack.go#L1426-L1464), [blank-frame encoding](https://github.com/livekit/livekit/blob/5a897c239cde995e615047be3fd5b27ca1afdad7/pkg/sfu/downtrack.go#L1989-L2008).

An installed 1.1.2 diagnostic check subsequently captured the camera publisher reporting **DISCONNECTED**, reason **1 / CLIENT_INITIATED**, with its video track removed and zero active camera video tracks. This happened about 146.8 seconds into viewing a room that was already warm. Viewer signaling pings remained healthy. Live-view discovery returned the same room with a different token subject (`same_room=true`, `same_subject=false`); the inspected token still had approximately 601 seconds before expiry. A replacement publisher appeared about nine seconds after another live-view request, but the old browser player stayed at 2×2. The trace supports camera-publisher track loss rather than viewer JWT expiry as the immediate failure. It does not establish why the camera publisher left or prove a universal session-duration rule. Raw participant identities, tokens, room identifiers, and traces remain private.

The official SimpliSafe web player also stopped in an actual comparison check: its last working sample was 1920×1080 at 239.61 seconds, followed approximately 30 seconds later by a 0×0 paused player and **“Unable to Stream Video.”** This confirms that a similar viewing failure occurred outside Home Assistant during the investigation. The samples do not pinpoint the closure time or prove an exact five-minute policy.

Version 1.1.3 treats confirmed camera-publisher video-track loss as a normal Home Assistant `stream_closed` WebRTC error and closes that viewer session. Its separate Lovelace resource wraps the native `hui-picture-entity-card` using Home Assistant's public `loadCardHelpers()` / `createCardElement()` contract, passes through `hass` and card configuration, and removes/recreates the native card to request a fresh session. It does not obtain credentials, call SimpliSafe endpoints, or implement signaling in the browser. The public helper is documented in [Home Assistant's custom-card helper announcement](https://developers.home-assistant.io/blog/2020/02/18/106-custom-card-changes/).

The wrapper observes ordinary video dimensions, media time, presented frames, and the native player's error markup across open shadow roots. After valid video playback, it retries a 2×2 placeholder, a native error, or a 30-second stall in both media time and frame progress. Initial starts have a 60-second grace period. Retry delays increase from one second to a maximum of 30 seconds; six consecutive failures stop automatic attempts and expose **Retry**. Thirty seconds of valid media/frame progress resets the failure budget. Hidden, offscreen, or disconnected cards release their child and cancel their timers. The backend and 32 frontend lifecycle checks pass. An installed desktop-browser run lasting **649.55 seconds** also passed **two automatic reopenings for each outdoor camera**, returning both to 1920×1080 while legacy feeds continued at 1280×720 with no automatic restarts. A later full close/reopen needed one automatic startup retry per card and about 86 seconds for all four to return. Reopening has a cloud/camera-startup gap and is distinct from renewing one uninterrupted media session. See [the installed validation record](VALIDATION.md) for timing and bounds.

Home Assistant can mount both HLS and WebRTC players while choosing a transport, with the alternative player hidden by `display:none`. Real-browser validation found that inspecting the first video across shadow roots could select this hidden alternative and falsely time out a working feed. The corrected wrapper checks composed ancestors, including shadow hosts, for `hidden`, `display:none`, and hidden/collapsed visibility; it prefers a visible playing video and ignores errors inside hidden alternatives. This behavior is covered by realistic dual-player fixtures. [Native HA transport/player selection](https://raw.githubusercontent.com/home-assistant/frontend/20260624.5/src/components/ha-camera-stream.ts).

A subsequent automated Chrome viewing check also triggered coordinated false retries while the page reported visible but had not been freshly rendered for about a minute. Frame-presentation signals alone do not reliably distinguish suspended browser composition from lost media. The corrected policy accepts advancing `video.currentTime` even when frame callbacks/counters exist but stop. This deliberately cannot distinguish frozen video under an advancing audio/media clock from suspended composition; such a freeze can require manual reopening. Backend camera-publisher loss, visible native errors, and a 2×2 placeholder still provide separate recovery signals. The lifecycle fixture covers both moving-media/flat-frame continuity and a true media-plus-frame stall. The final browser run used DOM metrics at approximately 20-second intervals, with screenshots only after recovery and at the end; it did not rely on a recurring screenshot/forced-render workaround. It passed the two outdoor recovery cycles described above. Indefinite unattended operation and other devices remain separate validation bounds.

## Authentication reuse and validation boundary

The custom integration uses the native Home Assistant SimpliSafe API object and its existing token-refresh callbacks. The normal SimpliSafe OAuth flow uses browser authorization with PKCE and `POST https://auth.simplisafe.com/oauth/token`. Access tokens authenticate camera requests; refresh tokens renew access and must remain in the native integration's private storage. [Homebridge OAuth implementation](https://github.com/homebridge-simplisafe3/homebridge-simplisafe3/blob/aec7b6939c45fe2d69223d0e173599a75048226a/src/lib/authManager.ts), [Home Assistant SimpliSafe authentication instructions](https://www.home-assistant.io/integrations/simplisafe/).

Local checks cover response parsing, provider selection, credential handling, relay behavior, and compatibility with Home Assistant APIs. Source hashes and repository baselines are captured when the release ZIP is built. A successful check or package build does not confirm real camera playback; that requires opening a dashboard camera entity against the user's account and observing video.
