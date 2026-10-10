const test = require("node:test");
const assert = require("node:assert/strict");
const { fixture } = require("./camera-fixture.cjs");

test("document.createElement accepts a constructor without forbidden host attributes", async () => {
  const f = fixture(); const wrapper = f.document.createElement("simplisafe-camera-live");
  assert.equal(wrapper.attributes.size, 0); assert.equal(wrapper.children.length, 0);
  assert.ok(wrapper.shadowRoot); assert.equal(wrapper.isConnected, false);
  const connected = await f.mount(); assert.equal(connected.getAttribute("data-state"), "starting");
  assert.ok(f.native(connected));
});

test("uses the native camera card with preserved config and latest hass", async () => {
  const f = fixture(); const action = Object.freeze({ action: "more-info" });
  const config = Object.freeze({ type: "custom:simplisafe-camera-live", entity: "camera.outdoor",
    name: "Back yard", tap_action: action, camera_view: "auto", show_state: true });
  const wrapper = await f.mount(config); const native = f.native(wrapper);
  assert.equal(native.config.type, "picture-entity"); assert.equal(native.config.camera_view, "live");
  assert.equal(native.config.aspect_ratio, "16:9"); assert.equal(native.config.show_state, false);
  assert.equal(native.config.tap_action, action); assert.equal(native.config.name, "Back yard");
  assert.equal(config.camera_view, "auto"); assert.equal(wrapper.getCardSize(), 4);
  const latest = f.hass("recording"); wrapper.hass = latest;
  assert.equal(native.hass, latest); assert.equal(wrapper.hass, latest); assert.equal(f.cards.length, 1);
});

test("does not create streams for missing, unknown or unavailable entities", async () => {
  const f = fixture(); const wrapper = await f.mount(undefined, { states: {} });
  assert.equal(f.cards.length, 0); assert.equal(f.clock.timers.size, 0);
  assert.equal(wrapper.getAttribute("data-state"), "unavailable");
  for (const state of ["unknown", "unavailable", ""]) {
    wrapper.hass = f.hass(state); await f.clock.advance(120000);
    assert.equal(f.cards.length, 0); assert.equal(f.clock.timers.size, 0);
  }
  wrapper.hass = f.hass(); await f.flush(); assert.equal(f.cards.length, 1);
});

test("cold starts receive sixty seconds before a bounded retry", async () => {
  const f = fixture(); const wrapper = await f.mount(); const first = f.native(wrapper);
  first.video.videoWidth = 2; first.video.videoHeight = 2; first.video.readyState = 4;
  await f.clock.advance(59999); assert.equal(f.cards.length, 1); assert.equal(first.closed, 0);
  await f.clock.advance(1); assert.equal(first.closed, 1); assert.equal(f.native(wrapper), undefined);
  await f.clock.advance(999); assert.equal(f.cards.length, 1);
  await f.clock.advance(1); assert.equal(f.cards.length, 2);
  assert.equal(wrapper.getAttribute("data-reconnects"), "1");
});

test("real frames followed by the cloud's 2x2 placeholder close and reopen the native session", async () => {
  const f = fixture(); const wrapper = await f.mount(); await f.play(wrapper);
  const first = f.native(wrapper); assert.equal(wrapper.getAttribute("data-state"), "playing");
  first.video.present({ width: 2, height: 2 }); await f.clock.advance(1000);
  assert.equal(first.closed, 1); assert.equal(first.video.callbacks.size, 0);
  assert.equal(wrapper.getAttribute("data-state"), "reconnecting");
  await f.clock.advance(1000); assert.notEqual(f.native(wrapper), first); assert.equal(f.cards.length, 2);
});

test("normal native WebRTC error markup in nested shadow roots triggers recovery", async () => {
  const f = fixture(); const wrapper = await f.mount(); await f.play(wrapper);
  const first = f.native(wrapper); first.showError(); await f.clock.advance(2000);
  assert.equal(first.closed, 1); await f.clock.advance(1000); assert.equal(f.cards.length, 2);
});

test("a later hidden HLS alternate cannot mask the active WebRTC video", async () => {
  const f = fixture(); const wrapper = await f.mount(); const native = f.native(wrapper);
  const alternate = native.alternative(); await f.play(wrapper, 80);
  assert.equal(alternate.video.videoWidth, 0); assert.equal(alternate.video.paused, true);
  assert.equal(wrapper.getAttribute("data-state"), "playing");
  assert.equal(wrapper.getAttribute("data-reconnects"), null); assert.equal(f.cards.length, 1);
});

for (const hidden of ["class", "attribute", "display", "visibility"]) {
  test(`an alternative's shadow-root error is ignored when its host is hidden by ${hidden}`, async () => {
    const f = fixture(); const wrapper = await f.mount(); const native = f.native(wrapper);
    native.alternative({ hidden, error: true }); await f.play(wrapper, 80);
    assert.equal(wrapper.getAttribute("data-state"), "playing"); assert.equal(f.cards.length, 1);
    native.showError(); await f.clock.advance(2000); assert.equal(native.closed, 1);
    await f.clock.advance(1000); assert.equal(f.cards.length, 2);
  });
}

test("when both players are visible, active real video wins over a not-yet-loaded alternate", async () => {
  const f = fixture(); const wrapper = await f.mount(); const native = f.native(wrapper);
  native.alternative({ hidden: false }); await f.play(wrapper, 80);
  assert.equal(wrapper.getAttribute("data-state"), "playing"); assert.equal(f.cards.length, 1);
  native.video.present({ width: 2, height: 2 }); await f.clock.advance(2000);
  assert.equal(native.closed, 1);
});

test("an initial WebRTC error respects the startup grace", async () => {
  const f = fixture(); const wrapper = await f.mount(); f.native(wrapper).showError();
  await f.clock.advance(58000); assert.equal(f.cards.length, 1); assert.equal(f.cards[0].closed, 0);
  await f.clock.advance(3000); assert.equal(f.cards.length, 2);
});

test("advancing media time prevents false retries when frame APIs stop reporting", async () => {
  const f = fixture(); const wrapper = await f.mount(); await f.play(wrapper);
  const first = f.native(wrapper); const frames = first.video.frames;
  assert.equal(typeof first.video.requestVideoFrameCallback, "function");
  for (let second = 0; second < 90; second++) {
    first.video.currentTime++; await f.clock.advance(1000);
  }
  assert.equal(first.video.frames, frames); assert.equal(first.video.videoWidth, 1920);
  assert.equal(first.video.paused, false); assert.equal(first.closed, 0); assert.equal(f.cards.length, 1);
  assert.equal(wrapper.getAttribute("data-state"), "playing");
});

test("actual media time and frames both stalled for thirty seconds trigger recovery", async () => {
  const f = fixture(); const wrapper = await f.mount(); await f.play(wrapper);
  const first = f.native(wrapper); await f.clock.advance(28000);
  assert.equal(first.closed, 0); await f.clock.advance(4000);
  assert.equal(first.closed, 1); assert.equal(f.cards.length, 2);
});

test("media time alone can establish playback when frame signals never arrive", async () => {
  const f = fixture(); const wrapper = await f.mount(); const first = f.native(wrapper);
  first.video.videoWidth = 1920; first.video.videoHeight = 1080; first.video.readyState = 4;
  for (let second = 0; second < 80; second++) {
    first.video.currentTime++; await f.clock.advance(1000);
  }
  assert.equal(first.video.frames, 0); assert.equal(first.closed, 0); assert.equal(f.cards.length, 1);
  assert.equal(wrapper.getAttribute("data-state"), "playing");
});

test("a deliberately paused native player is preserved", async () => {
  const f = fixture(); const wrapper = await f.mount(); await f.play(wrapper);
  f.native(wrapper).video.paused = true; await f.clock.advance(120000);
  assert.equal(f.cards.length, 1); assert.equal(f.cards[0].closed, 0);
});

test("offscreen cards release streams, timers and frame callbacks while retaining layout", async () => {
  const f = fixture(); const wrapper = await f.mount(); await f.play(wrapper);
  const first = f.native(wrapper); await f.visible(wrapper, false);
  assert.equal(first.closed, 1); assert.equal(first.video.callbacks.size, 0); assert.equal(f.clock.timers.size, 0);
  assert.equal(wrapper.getAttribute("data-state"), "paused");
  assert.equal(f.elements(wrapper).find((node) => node.className === "frame").properties.get("--camera-ratio"), "16 / 9");
  await f.clock.advance(300000); assert.equal(f.cards.length, 1);
  await f.visible(wrapper, true); assert.equal(f.cards.length, 2);
});

test("page visibility and disconnection tear down sessions and restore only when visible", async () => {
  const f = fixture(); const wrapper = await f.mount(); await f.play(wrapper);
  await f.pageVisible(false); assert.equal(f.cards[0].closed, 1); assert.equal(f.clock.timers.size, 0);
  await f.pageVisible(true); assert.equal(f.cards.length, 2);
  wrapper.remove(); assert.equal(f.cards[1].closed, 1); assert.equal(f.clock.timers.size, 0);
  assert.equal(f.document.listeners.get("visibilitychange").size, 0);
  f.document.body.append(wrapper); await f.flush(); assert.equal(f.cards.length, 2);
  await f.visible(wrapper, true); assert.equal(f.cards.length, 3);
});

test("a helper resolving after disconnection cannot create or resurrect a child", async () => {
  const f = fixture({ delayedHelpers: true }); const wrapper = await f.mount();
  assert.equal(f.pending.length, 1); wrapper.remove(); await f.resolveHelper();
  assert.equal(f.cards.length, 0); assert.equal(f.clock.timers.size, 0);
});

test("a hidden async mount cannot overwrite a later visible generation", async () => {
  const f = fixture({ delayedHelpers: true }); const wrapper = await f.mount();
  await f.visible(wrapper, false); await f.visible(wrapper, true); assert.equal(f.pending.length, 2);
  await f.resolveHelper(); assert.equal(f.cards.length, 0);
  await f.resolveHelper(); assert.equal(f.cards.length, 1); assert.ok(f.native(wrapper));
});

test("reconfiguring an async mount uses only the latest config and hass", async () => {
  const f = fixture({ delayedHelpers: true }); const wrapper = await f.mount();
  const latest = f.hass("idle", "camera.door"); wrapper.hass = latest;
  wrapper.setConfig({ entity: "camera.door", name: "Door" });
  await f.resolveHelper(); assert.equal(f.cards.length, 0);
  await f.resolveHelper(); assert.equal(f.cards.length, 1);
  assert.equal(f.native(wrapper).config.entity, "camera.door"); assert.equal(f.native(wrapper).hass, latest);
});

test("hiding during retry cancels the pending remount", async () => {
  const f = fixture(); const wrapper = await f.mount(); await f.play(wrapper);
  f.native(wrapper).showError(); await f.clock.advance(1000);
  assert.equal(wrapper.getAttribute("data-state"), "reconnecting");
  await f.visible(wrapper, false); await f.clock.advance(120000);
  assert.equal(f.cards.length, 1); assert.equal(f.clock.timers.size, 0);
  await f.visible(wrapper, true); assert.equal(f.cards.length, 2);
});

test("six consecutive failed starts stop automatic retries until a visible manual Retry", async () => {
  const f = fixture(); const wrapper = await f.mount();
  await f.clock.advance(600000); assert.equal(f.cards.length, 6); assert.equal(f.clock.timers.size, 0);
  assert.equal(wrapper.getAttribute("data-state"), "failed"); assert.equal(f.native(wrapper), undefined);
  assert.equal(f.button(wrapper).hidden, false);
  await f.clock.advance(600000); assert.equal(f.cards.length, 6);
  await f.visible(wrapper, false); f.button(wrapper).dispatch("click"); await f.flush();
  assert.equal(f.cards.length, 6); await f.visible(wrapper, true);
  assert.equal(f.cards.length, 6); f.button(wrapper).dispatch("click"); await f.flush();
  assert.equal(f.cards.length, 7); await f.play(wrapper); assert.equal(wrapper.getAttribute("data-state"), "playing");
});

test("manual Retry cannot start a now unavailable camera", async () => {
  const f = fixture(); const wrapper = await f.mount(); await f.clock.advance(600000);
  wrapper.hass = f.hass("unavailable"); f.button(wrapper).dispatch("click"); await f.flush();
  assert.equal(f.cards.length, 6); assert.equal(f.clock.timers.size, 0); assert.equal(f.button(wrapper).hidden, true);
  wrapper.hass = f.hass(); await f.flush(); assert.equal(f.cards.length, 6);
  assert.equal(f.button(wrapper).hidden, false);
});

test("thirty seconds of actual frame progress resets the failure budget", async () => {
  const f = fixture(); const wrapper = await f.mount(); await f.clock.advance(123000);
  assert.equal(f.cards.length, 3); await f.play(wrapper, 35);
  const healthy = f.native(wrapper); healthy.showError(); await f.clock.advance(2000);
  await f.clock.advance(1000); assert.equal(f.cards.length, 4);
  assert.equal(wrapper.getAttribute("data-state"), "starting");
  await f.clock.advance(600000); assert.equal(f.cards.length, 8);
  assert.equal(wrapper.getAttribute("data-state"), "failed");
});

test("native lazy-loading rebuild events replace only the active native child", async () => {
  const f = fixture(); const wrapper = await f.mount(); const first = f.native(wrapper);
  first.rebuild(); await f.flush(); assert.equal(first.closed, 1); assert.equal(f.cards.length, 2);
  first.rebuild(); await f.flush(); assert.equal(f.cards.length, 2);
  assert.equal(wrapper.getAttribute("data-reconnects"), null);
});

test("registered native cards work before public helpers become available", async () => {
  const f = fixture({ helpers: false, fallback: true }); const wrapper = await f.mount();
  assert.equal(f.cards.length, 1); assert.equal(f.native(wrapper).config.camera_view, "live");
  await f.play(wrapper); assert.equal(wrapper.getAttribute("data-state"), "playing");
});

test("duplicate resource evaluation preserves the existing custom element", async () => {
  const f = fixture(); const wrapper = await f.mount(); const type = wrapper.constructor;
  f.evaluate(); const other = await f.mount(); assert.equal(other.constructor, type);
  await f.play(wrapper); await f.play(other);
  await f.visible(wrapper, false); assert.ok(f.native(other)); assert.equal(f.cards.length, 2);
});

test("older browsers without presented-frame APIs can use progressing video time", async () => {
  const f = fixture(); const wrapper = await f.mount(); const video = f.native(wrapper).video;
  video.getVideoPlaybackQuality = undefined; video.requestVideoFrameCallback = undefined;
  await f.play(wrapper); assert.equal(wrapper.getAttribute("data-state"), "playing");
  video.videoWidth = 2; video.videoHeight = 2; await f.clock.advance(2000);
  assert.equal(f.cards[0].closed, 1);
});

test("missing native helpers fail within the same finite recovery ceiling", async () => {
  const f = fixture({ helpers: false }); const wrapper = await f.mount();
  await f.clock.advance(600000);
  assert.equal(f.cards.length, 0); assert.equal(f.clock.timers.size, 0);
  assert.equal(wrapper.getAttribute("data-state"), "failed"); assert.equal(f.button(wrapper).hidden, false);
});

test("steady video frames do not repeatedly mutate accessible status markup", async () => {
  const f = fixture(); const wrapper = await f.mount(); await f.play(wrapper);
  const status = f.elements(wrapper).find((element) => element.getAttribute("role") === "status");
  const message = status.children.find((element) => element.tagName === "SPAN");
  const button = f.button(wrapper); let writes = 0;
  for (const [element, property] of [[status, "hidden"], [button, "hidden"], [message, "textContent"]]) {
    let value = element[property];
    Object.defineProperty(element, property, { get: () => value, set: (next) => { value = next; writes++; } });
  }
  const original = wrapper.setAttribute;
  wrapper.setAttribute = function (...args) { writes++; original.apply(this, args); };
  for (let index = 0; index < 120; index++) f.native(wrapper).video.present({ seconds: 1 / 30 });
  assert.equal(writes, 0);
});
