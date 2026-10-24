/**
 * Proctor module — candidate camera + live connection to the exam server.
 *
 *  - The camera stream opened in the System Check is handed over and kept alive.
 *  - Frames are sent as small JPEGs at the rate the SERVER asks for:
 *        idle 1 frame / 5 s  ·  grid 1 fps (320x240)  ·  focus ~5 fps (640x480)
 *    so the admin sees smooth video when watching, without wasting bandwidth.
 *  - If the WebSocket is blocked (proxy/firewall), frames fall back to HTTP POST.
 *  - A covered / pitch-black camera is detected and shown to the candidate.
 *  - If the camera track dies (USB unplugged, another app grabs it) it is
 *    re-opened automatically and the event is logged for the admin.
 *  - Server commands (force end, disqualify, phase ended…) are delivered to every
 *    listener registered with onMessage(), also after reconnects.
 */
const Proctor = (() => {
  const DEFAULT_RATE = { mode: 'idle', interval_ms: 5000, width: 320, height: 240, quality: 0.6 };

  let videoEl = null;
  let statusEl = null;
  let stream = null;
  let ws = null;
  let wsReconnectHandle = null;
  let wsAttempts = 0;
  let stopped = true;
  let rate = { ...DEFAULT_RATE };
  let captureTimer = null;
  let lastHttpFrameAt = 0;
  let darkCount = 0;
  let reacquiring = false;
  const listeners = new Set();

  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d', { willReadFrequently: false });
  const probe = document.createElement('canvas');
  probe.width = 32; probe.height = 24;
  const probeCtx = probe.getContext('2d', { willReadFrequently: true });

  function token() { return localStorage.getItem('access_token') || ''; }

  function setStatus(text, isAlert = false) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.classList.toggle('alert', !!isAlert);
  }

  function emit(msg) {
    listeners.forEach(fn => { try { fn(msg); } catch (_) {} });
  }

  // ── WebSocket ───────────────────────────────────────────────────────────
  function connectWebSocket() {
    if (stopped) return;
    if (wsReconnectHandle) { clearTimeout(wsReconnectHandle); wsReconnectHandle = null; }
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    let sock;
    try {
      sock = new WebSocket(`${protocol}//${location.host}/exam/ws/candidate?token=${encodeURIComponent(token())}`);
    } catch (_) { scheduleReconnect(); return; }
    ws = sock;
    sock.onopen = () => { wsAttempts = 0; emit({ type: 'ws_open' }); };
    sock.onmessage = (evt) => {
      let msg; try { msg = JSON.parse(evt.data); } catch (_) { return; }
      if (msg.type === 'proctor_rate') { applyRate(msg); return; }
      emit(msg);
    };
    sock.onerror = () => {};
    sock.onclose = (evt) => {
      if (ws === sock) ws = null;
      emit({ type: 'ws_closed', code: evt.code });
      if (evt.code === 4001) { emit({ type: 'auth_lost' }); return; } // token revoked → no reconnect loop
      scheduleReconnect();
    };
  }

  function scheduleReconnect() {
    if (stopped) return;
    const delay = Math.min(1000 * Math.pow(2, wsAttempts), 15000);
    wsAttempts++;
    wsReconnectHandle = setTimeout(connectWebSocket, delay);
  }

  function wsOpen() { return ws && ws.readyState === WebSocket.OPEN; }

  // ── Rate control ────────────────────────────────────────────────────────
  function applyRate(r) {
    if (!r || !r.interval_ms) return;
    const changed = r.interval_ms !== rate.interval_ms || r.width !== rate.width;
    rate = { mode: r.mode, interval_ms: r.interval_ms, width: r.width, height: r.height, quality: r.quality };
    if (changed) scheduleCapture(50);
  }

  function scheduleCapture(delay) {
    if (captureTimer) clearTimeout(captureTimer);
    if (stopped) return;
    captureTimer = setTimeout(captureLoop, delay == null ? rate.interval_ms : delay);
  }

  async function captureLoop() {
    captureTimer = null;
    try { await captureAndSend(); } catch (_) {}
    scheduleCapture();
  }

  function isDark() {
    try {
      probeCtx.drawImage(videoEl, 0, 0, probe.width, probe.height);
      const d = probeCtx.getImageData(0, 0, probe.width, probe.height).data;
      let sum = 0;
      for (let i = 0; i < d.length; i += 4) sum += d[i] * 0.299 + d[i + 1] * 0.587 + d[i + 2] * 0.114;
      return sum / (d.length / 4) < 12;
    } catch (_) { return false; }
  }

  async function captureAndSend() {
    if (!videoEl || !stream) return;
    if (videoEl.paused) videoEl.play().catch(() => {});
    if (videoEl.readyState < 2 || !videoEl.videoWidth) return;

    // keep the camera's aspect ratio (phones in portrait give tall video)
    const vw = videoEl.videoWidth, vh = videoEl.videoHeight;
    const scale = Math.min(rate.width / vw, rate.height / vh, 1);
    const w = Math.max(2, Math.round(vw * scale)), h = Math.max(2, Math.round(vh * scale));
    if (canvas.width !== w) canvas.width = w;
    if (canvas.height !== h) canvas.height = h;
    ctx.drawImage(videoEl, 0, 0, w, h);

    const dark = isDark();
    darkCount = dark ? darkCount + 1 : 0;
    if (darkCount === 3) setStatus('Camera image is black — uncover the camera / add light', true);
    else if (darkCount === 0) setStatus('Camera active — monitoring');

    const frame = canvas.toDataURL('image/jpeg', rate.quality);
    if (wsOpen()) {
      if (ws.bufferedAmount > 256 * 1024) return; // slow uplink: skip instead of piling up
      ws.send(JSON.stringify({ type: 'proctor_frame', frame, dark }));
    } else {
      // HTTP fallback, at most every 5 s
      const now = Date.now();
      if (now - lastHttpFrameAt < 5000) return;
      lastHttpFrameAt = now;
      try {
        const res = await fetch('/exam/frame', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token()}` },
          body: JSON.stringify({ frame, dark }),
        });
        if (res.ok) { const d = await res.json(); if (d && d.rate) applyRate({ ...d.rate, interval_ms: Math.max(d.rate.interval_ms, 1000) }); }
      } catch (_) {}
    }
  }

  // ── Camera ──────────────────────────────────────────────────────────────
  const CONSTRAINTS = {
    video: { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 15, max: 30 }, facingMode: 'user' },
    audio: false,
  };

  async function getCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      const e = new Error('Camera needs a secure (https) connection and a modern browser.');
      e.name = 'InsecureContext';
      throw e;
    }
    try {
      return await navigator.mediaDevices.getUserMedia(CONSTRAINTS);
    } catch (err) {
      if (err && (err.name === 'OverconstrainedError' || err.name === 'ConstraintNotSatisfiedError')) {
        return await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      }
      throw err;
    }
  }

  function attach(el, s) {
    el.muted = true;
    el.autoplay = true;
    el.playsInline = true;
    el.setAttribute('playsinline', '');
    el.setAttribute('muted', '');
    el.srcObject = s;
    const p = el.play();
    if (p && p.catch) p.catch(() => {});
  }

  function watchTracks() {
    if (!stream) return;
    stream.getVideoTracks().forEach(track => {
      track.addEventListener('ended', onTrackEnded, { once: true });
    });
  }

  async function onTrackEnded() {
    if (stopped || reacquiring) return;
    reacquiring = true;
    setStatus('Camera disconnected — reconnecting…', true);
    emit({ type: 'camera_lost' });
    for (let i = 0; i < 5 && !stopped; i++) {
      try {
        const s = await getCamera();
        stream = s;
        attach(videoEl, s);
        watchTracks();
        setStatus('Camera active — monitoring');
        emit({ type: 'camera_restored' });
        reacquiring = false;
        return;
      } catch (_) {
        await new Promise(r => setTimeout(r, 2000));
      }
    }
    reacquiring = false;
    setStatus('Camera unavailable — please reconnect your camera', true);
  }

  /** Open the camera for the System Check preview. Resolves with the stream. */
  async function openForPreview(videoElement) {
    const s = await getCamera();
    attach(videoElement, s);
    await new Promise((resolve, reject) => {
      if (videoElement.readyState >= 2 && videoElement.videoWidth) return resolve();
      const t = setTimeout(() => reject(Object.assign(new Error('The camera did not deliver a picture. Close other apps using the camera and retry.'), { name: 'Timeout' })), 8000);
      const ok = () => { clearTimeout(t); resolve(); };
      videoElement.addEventListener('loadeddata', ok, { once: true });
      videoElement.addEventListener('playing', ok, { once: true });
    });
    return s;
  }

  /**
   * Begin monitoring. `existingStream` is the stream from the System Check (kept alive).
   * Returns true when the camera is running.
   */
  async function start(videoElement, statusElement, existingStream = null) {
    videoEl = videoElement;
    statusEl = statusElement;
    stopped = false;
    connectWebSocket();
    try {
      stream = existingStream && existingStream.active ? existingStream : await getCamera();
      attach(videoEl, stream);
      watchTracks();
      setStatus('Camera active — monitoring');
      scheduleCapture(1500);
      return true;
    } catch (err) {
      setStatus(cameraErrorMessage(err), true);
      return false;
    }
  }

  function stop() {
    stopped = true;
    if (captureTimer) { clearTimeout(captureTimer); captureTimer = null; }
    if (wsReconnectHandle) { clearTimeout(wsReconnectHandle); wsReconnectHandle = null; }
    if (stream) { stream.getTracks().forEach(t => { try { t.stop(); } catch (_) {} }); stream = null; }
    if (ws) { const s = ws; ws = null; s.onclose = null; try { s.close(); } catch (_) {} }
    if (videoEl) videoEl.srcObject = null;
    setStatus('Camera stopped');
  }

  function cameraErrorMessage(err) {
    if (!err) return 'Camera access failed.';
    const n = err.name || '';
    if (n === 'NotAllowedError' || n === 'PermissionDeniedError' || n === 'SecurityError')
      return 'Camera permission was blocked. Tap the lock/camera icon next to the address bar, choose "Allow" for Camera, then press Retry.';
    if (n === 'NotFoundError' || n === 'DevicesNotFoundError')
      return 'No camera was found. Connect a webcam (or use a phone/laptop with a front camera) and press Retry.';
    if (n === 'NotReadableError' || n === 'TrackStartError' || n === 'AbortError')
      return 'The camera is being used by another app (Zoom, Meet, WhatsApp, Camera app…). Close it and press Retry.';
    if (n === 'InsecureContext' || n === 'TypeError')
      return 'The camera only works on the secure https:// address of the exam site. Please open the https link.';
    return err.message || 'Camera access failed.';
  }

  function onMessage(fn) { listeners.add(fn); return () => listeners.delete(fn); }
  function isConnected() { return wsOpen(); }

  return { openForPreview, start, stop, onMessage, isConnected, cameraErrorMessage };
})();
