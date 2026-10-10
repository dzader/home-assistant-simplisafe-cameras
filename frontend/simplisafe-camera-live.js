/* A visible-only recovery wrapper around Home Assistant's native camera card.
 * It never opens a stream itself: removing/recreating the native card lets HA
 * close the old session and obtain a new one through its normal camera flow.
 */
const CAMERA_CHECK_MS = 2000;
const CAMERA_STARTUP_MS = 60000;
const CAMERA_STALL_MS = 30000;
const CAMERA_STABLE_MS = 30000;
const CAMERA_RETRY_MAX_MS = 30000;
const CAMERA_FAILED_STARTS_MAX = 6;

function cameraElements(root) {
  const result = [];
  const roots = root.shadowRoot ? [root, root.shadowRoot] : [root];
  while (roots.length) {
    for (const element of roots.pop().querySelectorAll("*")) {
      result.push(element);
      if (element.shadowRoot) roots.push(element.shadowRoot);
    }
  }
  return result;
}

function cameraElementVisible(element) {
  // HA may keep an alternative HLS/WebRTC player mounted with display:none.
  // Follow composed ancestors: parentElement stops at a shadow-root boundary.
  for (let current = element; current; current = current.parentElement || current.getRootNode().host) {
    if (current.hidden) return false;
    const style = getComputedStyle(current);
    if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse") return false;
  }
  return true;
}

class SimpliSafeCameraLive extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = `
      :host { display: block; }
      .frame { position: relative; width: 100%; aspect-ratio: var(--camera-ratio, 16 / 9); }
      .card { width: 100%; }
      .status { position: absolute; inset: 0; display: flex; align-items: center;
        justify-content: center; flex-direction: column; gap: 12px;
        padding: 16px; box-sizing: border-box; border-radius: var(--ha-card-border-radius, 12px);
        color: var(--primary-text-color); background: var(--ha-card-background, var(--card-background-color));
        font: inherit; text-align: center; pointer-events: none; }
      .status[hidden] { display: none; }
      button { font: inherit; padding: 8px 16px; border-radius: 8px;
        border: 1px solid var(--divider-color); cursor: pointer; pointer-events: auto;
        color: var(--primary-text-color); background: var(--card-background-color); }
      button[hidden] { display: none; }
    `;
    this._frame = document.createElement("div");
    this._frame.className = "frame";
    this._slot = document.createElement("div");
    this._slot.className = "card";
    this._status = document.createElement("div");
    this._status.className = "status";
    this._status.setAttribute("role", "status");
    this._status.setAttribute("aria-live", "polite");
    this._message = document.createElement("span");
    this._retryButton = document.createElement("button");
    this._retryButton.type = "button";
    this._retryButton.textContent = "Retry";
    this._retryButton.hidden = true;
    this._retryButton.addEventListener("click", () => {
      if (!this._visible() || !this._available()) return;
      this._failures = 0;
      this._retryStopped = false;
      this._pause();
      this._reconcile();
    });
    this._status.append(this._message, this._retryButton);
    this._frame.append(this._slot, this._status);
    this.shadowRoot.append(style, this._frame);
    this._generation = 0;
    this._failures = 0;
    this._reconnects = 0;
    this._intersecting = false;
    this._visibilityChanged = () => this._reconcile();
    // Custom-element construction may initialize shadow content, but adding
    // attributes to the host here would make document.createElement fail.
    // Host status is first set by connectedCallback's reconciliation.
    this._message.textContent = "Connecting…";
  }

  setConfig(config) {
    if (!config || typeof config.entity !== "string" || !/^camera\.[a-zA-Z0-9_]+$/.test(config.entity)) {
      throw new Error("A camera entity is required");
    }
    this._config = {
      ...config,
      type: "picture-entity",
      camera_view: "live",
      aspect_ratio: config.aspect_ratio || "16:9",
      show_state: false,
    };
    const ratio = String(this._config.aspect_ratio).split(/[:/]/).map(Number);
    const width = ratio[0];
    const height = ratio.length === 1 ? 1 : ratio[1];
    this._frame.style.setProperty("--camera-ratio", width > 0 && height > 0 && Number.isFinite(width / height) ? `${width} / ${height}` : "16 / 9");
    this._pause();
    this._failures = 0;
    this._retryStopped = false;
    this._reconcile();
  }

  set hass(value) {
    this._hass = value;
    if (this._card) this._card.hass = value;
    this._reconcile();
  }

  get hass() { return this._hass; }
  getCardSize() { return this._card?.getCardSize?.() ?? 3; }

  connectedCallback() {
    document.addEventListener("visibilitychange", this._visibilityChanged);
    if (typeof IntersectionObserver === "function") {
      this._observer = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          if (entry.target === this) this._intersecting = entry.isIntersecting;
        }
        this._reconcile();
      });
      this._observer.observe(this);
    } else {
      this._intersecting = true;
    }
    this._reconcile();
  }

  disconnectedCallback() {
    document.removeEventListener("visibilitychange", this._visibilityChanged);
    this._observer?.disconnect();
    this._observer = undefined;
    this._intersecting = false;
    this._pause();
  }

  _visible() {
    return this.isConnected && this._intersecting && !this.hidden && !document.hidden;
  }

  _available() {
    const state = this._hass?.states?.[this._config?.entity]?.state;
    return typeof state === "string" && state.length > 0 && state !== "unavailable" && state !== "unknown";
  }

  _reconcile() {
    if (!this._visible()) {
      this._pause();
      this._showStatus("paused", "");
    } else if (!this._available()) {
      this._pause();
      this._showStatus("unavailable", "Camera unavailable");
    } else if (this._retryStopped) {
      this._showStatus("failed", "Live view could not reconnect");
    } else if (this._config && this._hass && !this._card && !this._mounting && !this._retryTimer) {
      void this._mount();
    }
  }

  async _mount() {
    if (!this._visible() || !this._available()) return;
    const generation = ++this._generation;
    this._mounting = true;
    this._startedAt = performance.now();
    this._lastProgressAt = this._startedAt;
    this._stableSince = undefined;
    this._hasValidFrames = false;
    this._showStatus("starting", "Connecting…");
    this._watchTimer = setInterval(() => this._check(), CAMERA_CHECK_MS);
    try {
      let card;
      if (typeof globalThis.loadCardHelpers === "function") {
        const helpers = await globalThis.loadCardHelpers();
        if (generation !== this._generation || !this._visible()) return;
        card = helpers.createCardElement({ ...this._config });
      } else if (customElements.get("hui-picture-entity-card")) {
        card = document.createElement("hui-picture-entity-card");
        card.setConfig({ ...this._config });
      } else {
        throw new Error("Native camera card helpers unavailable");
      }
      if (generation !== this._generation || !this._visible()) return;
      card.hass = this._hass;
      card.addEventListener("ll-rebuild", (event) => {
        event.stopPropagation();
        if (card !== this._card || !this._visible()) return;
        this._unload();
        void this._mount();
      });
      this._card = card;
      this._slot.replaceChildren(card);
    } catch {
      if (generation === this._generation) this._showStatus("starting", "Waiting for the camera…");
    } finally {
      if (generation === this._generation) this._mounting = false;
    }
  }

  _observeVideo(video) {
    if (video === this._video) return;
    this._cancelFrameCallback();
    this._video = video;
    this._lastVideoTime = video?.currentTime;
    this._lastFrameCount = video?.getVideoPlaybackQuality?.().totalVideoFrames;
    if (!video || typeof video.requestVideoFrameCallback !== "function") return;
    const generation = this._generation;
    const onFrame = () => {
      if (generation !== this._generation || video !== this._video || !this._visible()) return;
      this._frameProgress(video);
      this._frameCallback = video.requestVideoFrameCallback(onFrame);
    };
    this._frameCallback = video.requestVideoFrameCallback(onFrame);
  }

  _frameProgress(video) {
    if (video.videoWidth <= 2 || video.videoHeight <= 2 || video.readyState < 2) return;
    const now = performance.now();
    this._hasValidFrames = true;
    this._lastProgressAt = now;
    this._stableSince ??= now;
    if (now - this._stableSince >= CAMERA_STABLE_MS) this._failures = 0;
    this._showStatus("playing", "");
  }

  _check() {
    if (!this._visible() || !this._available()) {
      this._reconcile();
      return;
    }
    const now = performance.now();
    const elements = this._card ? cameraElements(this._card) : [];
    const videos = elements.filter((element) => element.tagName === "VIDEO" && cameraElementVisible(element));
    const video = videos.find((element) => element.videoWidth > 2 && element.videoHeight > 2 && element.readyState >= 2 && !element.paused)
      || (videos.includes(this._video) ? this._video : videos[0]);
    this._observeVideo(video);
    if (video) {
      const frameCount = video.getVideoPlaybackQuality?.().totalVideoFrames;
      // Chrome can suspend presented-frame signals without marking the page
      // hidden. Advancing media time also counts as progress so this does not
      // cause false reconnects. Video frozen under a moving audio clock cannot
      // be distinguished here; native errors/2x2/publisher loss remain handled.
      const progressed = (Number.isFinite(frameCount) && frameCount > (this._lastFrameCount ?? frameCount))
        || video.currentTime > (this._lastVideoTime ?? video.currentTime);
      if (progressed) this._frameProgress(video);
      this._lastFrameCount = frameCount;
      this._lastVideoTime = video.currentTime;
    }
    const nativeError = elements.some((element) => element.tagName === "HA-ALERT" && element.getAttribute("alert-type") === "error" && cameraElementVisible(element));
    const graceOver = now - this._startedAt >= CAMERA_STARTUP_MS;
    if ((this._hasValidFrames || graceOver) && (nativeError || video?.error)) {
      this._retry();
    } else if (this._hasValidFrames && video && video.videoWidth > 0 && video.videoWidth <= 2) {
      this._retry();
    } else if (this._hasValidFrames && now - this._lastProgressAt >= CAMERA_STALL_MS && (!video || !video.paused)) {
      this._retry();
    } else if (!this._hasValidFrames && graceOver) {
      this._retry();
    }
  }

  _retry() {
    if (this._retryTimer || !this._visible()) return;
    const delay = Math.min(1000 * 2 ** Math.min(this._failures++, 5), CAMERA_RETRY_MAX_MS);
    this._unload();
    if (this._failures >= CAMERA_FAILED_STARTS_MAX) {
      this._retryStopped = true;
      this._showStatus("failed", "Live view could not reconnect");
      return;
    }
    this._showStatus("reconnecting", "Reconnecting…");
    this._retryTimer = setTimeout(() => {
      this._retryTimer = undefined;
      if (!this._visible() || !this._available()) return;
      this.setAttribute("data-reconnects", String(++this._reconnects));
      void this._mount();
    }, delay);
  }

  _cancelFrameCallback() {
    if (this._frameCallback !== undefined) this._video?.cancelVideoFrameCallback?.(this._frameCallback);
    this._frameCallback = undefined;
  }

  _unload() {
    ++this._generation;
    clearInterval(this._watchTimer);
    this._watchTimer = undefined;
    this._cancelFrameCallback();
    this._video = undefined;
    this._mounting = false;
    this._card = undefined;
    this._slot.replaceChildren();
  }

  _pause() {
    clearTimeout(this._retryTimer);
    this._retryTimer = undefined;
    this._unload();
  }

  _showStatus(state, message) {
    if (this.getAttribute("data-state") !== state) this.setAttribute("data-state", state);
    if (this._status.hidden !== !message) this._status.hidden = !message;
    if (this._retryButton.hidden !== (state !== "failed")) this._retryButton.hidden = state !== "failed";
    const name = this._config?.name || this._hass?.states?.[this._config?.entity]?.attributes?.friendly_name;
    const text = message ? `${name ? `${name}: ` : ""}${message}` : "";
    if (this._message.textContent !== text) this._message.textContent = text;
  }
}

if (!customElements.get("simplisafe-camera-live")) {
  customElements.define("simplisafe-camera-live", SimpliSafeCameraLive);
}
