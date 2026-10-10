// A small browser fixture for lifecycle tests. The native card and video are
// external actors; tests drive only Lovelace's public card/lifecycle contract.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

async function flush() {
  for (let index = 0; index < 6; index++) await Promise.resolve();
}

class Clock {
  constructor() { this.now = 0; this.timers = new Map(); this.next = 1; }
  set(callback, delay, repeat = false) {
    const id = this.next++;
    this.timers.set(id, { callback, delay, at: this.now + delay, repeat });
    return id;
  }
  clear(id) { this.timers.delete(id); }
  async advance(milliseconds) {
    const target = this.now + milliseconds;
    for (;;) {
      const next = [...this.timers].filter(([, timer]) => timer.at <= target)
        .sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
      if (!next) break;
      const [id, timer] = next;
      this.now = timer.at;
      if (timer.repeat) timer.at += timer.delay;
      else this.timers.delete(id);
      timer.callback();
      await flush();
    }
    this.now = target;
    await flush();
  }
}

class Element {
  constructor(tag = "DIV") {
    this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null;
    this.isConnected = false; this.hidden = false; this.attributes = new Map();
    this.listeners = new Map(); this.properties = new Map(); this.textContent = "";
    this.style = { setProperty: (name, value) => this.properties.set(name, value) };
  }
  attachShadow() { this.shadowRoot = new Element("SHADOW"); this.shadowRoot.host = this; return this.shadowRoot; }
  get parentElement() {
    return this.parentNode && !["SHADOW", "DOCUMENT"].includes(this.parentNode.tagName) ? this.parentNode : null;
  }
  getRootNode() { let node = this; while (node.parentNode) node = node.parentNode; return node; }
  _connected(value) {
    if (this.isConnected === value) return;
    this.isConnected = value;
    if (value) this.connectedCallback?.(); else this.disconnectedCallback?.();
    this.shadowRoot?._connected(value);
    for (const child of this.children) child._connected(value);
  }
  append(...children) {
    for (const child of children) {
      child.parentNode?.removeChild(child);
      this.children.push(child); child.parentNode = this;
      child._connected(this.isConnected);
    }
  }
  removeChild(child) {
    this.children = this.children.filter((item) => item !== child);
    child.parentNode = null; child._connected(false); return child;
  }
  remove() { this.parentNode?.removeChild(this); }
  replaceChildren(...children) {
    for (const child of [...this.children]) this.removeChild(child);
    this.append(...children);
  }
  querySelectorAll() {
    const result = [];
    for (const child of this.children) result.push(child, ...child.querySelectorAll("*"));
    return result;
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  addEventListener(name, callback) {
    if (!this.listeners.has(name)) this.listeners.set(name, new Set());
    this.listeners.get(name).add(callback);
  }
  removeEventListener(name, callback) { this.listeners.get(name)?.delete(callback); }
  dispatch(name) {
    const event = { type: name, target: this, stopPropagation() {} };
    for (const callback of [...(this.listeners.get(name) ?? [])]) callback(event);
  }
}

class Video extends Element {
  constructor() {
    super("VIDEO"); this.currentTime = 0; this.videoWidth = 0; this.videoHeight = 0;
    this.readyState = 0; this.paused = false; this.error = null;
    this.frames = 0; this.callbacks = new Map(); this.nextCallback = 1; this.cancelled = 0;
  }
  getVideoPlaybackQuality() { return { totalVideoFrames: this.frames }; }
  requestVideoFrameCallback(callback) {
    const id = this.nextCallback++; this.callbacks.set(id, callback); return id;
  }
  cancelVideoFrameCallback(id) { if (this.callbacks.delete(id)) this.cancelled++; }
  present({ width = 1920, height = 1080, seconds = 1 } = {}) {
    this.videoWidth = width; this.videoHeight = height; this.readyState = 4;
    this.currentTime += seconds; this.frames++;
    const callbacks = [...this.callbacks.values()]; this.callbacks.clear();
    for (const callback of callbacks) callback();
  }
}

function fixture({ delayedHelpers = false, helpers = true, fallback = false } = {}) {
  const clock = new Clock(); const cards = []; const observers = []; const pending = [];
  const registry = new Map(); const document = new Element("DOCUMENT");
  document.isConnected = true; document.body = new Element("BODY"); document.append(document.body);
  class NativeCard extends Element {
    constructor() {
      super("HUI-PICTURE-ENTITY-CARD"); this.attachShadow(); this.closed = 0;
      const image = new Element("HUI-IMAGE"); image.attachShadow();
      const camera = new Element("HA-CAMERA-STREAM"); camera.attachShadow(); this.camera = camera;
      this.player = new Element("HA-WEB-RTC-PLAYER"); this.player.attachShadow();
      this.video = new Video(); this.player.shadowRoot.append(this.video);
      camera.shadowRoot.append(this.player); image.shadowRoot.append(camera);
      this.shadowRoot.append(image); cards.push(this);
    }
    setConfig(config) { this.config = config; }
    getCardSize() { return 4; }
    disconnectedCallback() { this.closed++; }
    showError() {
      const alert = new Element("HA-ALERT"); alert.setAttribute("alert-type", "error");
      this.player.shadowRoot.replaceChildren(alert);
    }
    alternative({ hidden = "class", error = false } = {}) {
      const player = new Element("HA-HLS-PLAYER"); player.attachShadow();
      if (hidden === "class") player.className = "hidden";
      else if (hidden === "attribute") player.hidden = true;
      else if (hidden === "display") player.computedStyle = { display: "none" };
      else if (hidden === "visibility") player.computedStyle = { visibility: "hidden" };
      const video = new Video(); video.paused = true; player.shadowRoot.append(video);
      if (error) {
        const alert = new Element("HA-ALERT"); alert.setAttribute("alert-type", "error");
        player.shadowRoot.append(alert);
      }
      // HA renders a hidden alternate after its selected player. A LIFO shadow
      // traversal reaches this alternate's video before the real active one.
      this.camera.shadowRoot.append(player); return { player, video };
    }
    rebuild() { this.dispatch("ll-rebuild"); }
  }
  document.createElement = (tag) => {
    const Type = registry.get(tag);
    if (!Type) return new Element(tag);
    const element = new Type();
    // The custom-element createElement algorithm checks the constructed host.
    // Shadow descendants are allowed; host attributes/children/parents are not.
    if (element.attributes.size || element.children.length || element.parentNode) {
      const error = new Error("Document.createElement result must not have attributes, children or a parent");
      error.name = "NotSupportedError"; throw error;
    }
    element.tagName = tag.toUpperCase(); return element;
  };
  if (fallback) registry.set("hui-picture-entity-card", NativeCard);
  const nativeHelpers = { createCardElement(config) {
    const card = new NativeCard(); card.setConfig(config); return card;
  } };
  const context = {
    HTMLElement: Element, document, performance: { now: () => clock.now },
    getComputedStyle: (element) => ({
      display: element.computedStyle?.display ?? (element.className?.split(" ").includes("hidden") ? "none" : "block"),
      visibility: element.computedStyle?.visibility ?? "visible",
    }),
    customElements: { get: (name) => registry.get(name), define: (name, value) => registry.set(name, value) },
    IntersectionObserver: class {
      constructor(callback) { this.callback = callback; this.active = true; observers.push(this); }
      observe(target) { this.target = target; }
      disconnect() { this.active = false; }
      emit(visible) { if (this.active) this.callback([{ target: this.target, isIntersecting: visible }]); }
    },
    setInterval: (callback, delay) => clock.set(callback, delay, true),
    clearInterval: (id) => clock.clear(id),
    setTimeout: (callback, delay) => clock.set(callback, delay), clearTimeout: (id) => clock.clear(id),
  };
  if (helpers) context.loadCardHelpers = () => delayedHelpers
    ? new Promise((resolve) => pending.push(() => resolve(nativeHelpers))) : Promise.resolve(nativeHelpers);
  vm.createContext(context);
  const source = fs.readFileSync(path.join(__dirname, "..", "simplisafe-camera-live.js"), "utf8");
  const evaluate = () => vm.runInContext(`(() => {\n${source}\n})()`, context);
  evaluate();
  const hass = (state = "idle", entity = "camera.outdoor") => ({ states: {
    [entity]: { state, attributes: { friendly_name: "Outdoor" } },
  } });
  const allElements = (root) => [root, ...root.querySelectorAll("*"),
    ...([root, ...root.querySelectorAll("*")].filter((node) => node.shadowRoot)
      .flatMap((node) => allElements(node.shadowRoot)))];
  return {
    clock, cards, observers, pending, document, hass, evaluate,
    async mount(config = { entity: "camera.outdoor" }, state = hass()) {
      const wrapper = document.createElement("simplisafe-camera-live");
      wrapper.setConfig(config); wrapper.hass = state;
      document.body.append(wrapper); observers.at(-1).emit(true); await flush(); return wrapper;
    },
    native(wrapper) { return cards.filter((card) => card.isConnected).find((card) => allElements(wrapper).includes(card)); },
    elements: allElements,
    button(wrapper) { return allElements(wrapper).find((node) => node.tagName === "BUTTON"); },
    async visible(wrapper, value) {
      observers.findLast((observer) => observer.active && observer.target === wrapper).emit(value); await flush();
    },
    async pageVisible(value) { document.hidden = !value; document.dispatch("visibilitychange"); await flush(); },
    async play(wrapper, seconds = 5, options) {
      for (let index = 0; index < seconds; index++) {
        await clock.advance(1000); this.native(wrapper)?.video.present(options); await flush();
      }
    },
    resolveHelper(index = 0) { pending.splice(index, 1)[0]?.(); return flush(); },
    flush,
  };
}

module.exports = { fixture, flush };
