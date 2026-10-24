/**
 * Proctor module — camera initialisation, live preview, and periodic frame sending.
 *
 * Key fixes in this version:
 *   1. The stream opened during the system check is KEPT ALIVE and passed directly
 *      to start() so the candidate sees a live preview before entering the exam.
 *   2. Comprehensive readyState / dimension validation before declaring camera OK.
 *   3. Frames are captured at a steady 1-2 FPS as compressed JPEG (quality 0.6-0.75) —
 *      low enough bandwidth/CPU for 100+ concurrent candidates, high enough for a
 *      human proctor to actually see what's going on.
 *   4. Camera disconnection is detected and reported as a violation event; a camera
 *      that never acquires (NotAllowedError/NotFoundError/etc.) is reported as a
 *      non-punitive advisory instead — see _reportCameraStatus() — so the exam is
 *      never blocked or locked by a hardware/permission problem outside the
 *      candidate's control.
 *   5. WebSocket auto-reconnects on drop without losing the exam session.
 *
 * Architecture:
 *   Candidate → Canvas → JPEG → Base64 → WebSocket → Server (throttled relay) → Admin
 *
 * The server-side ConnectionManager throttles relay to admin dashboards
 * independently of this send rate (see PROCTOR_RELAY_INTERVAL_SECONDS in
 * backend/websocket_manager.py) — so even at 1-2 FPS per candidate, 100+
 * concurrent candidates never overwhelm an admin's own socket/bandwidth.
 */
const Proctor = (() => {
  // Frame capture interval (ms). 700ms ≈ 1.4 FPS — inside the 1-2 FPS spec.
  const FRAME_INTERVAL_MS = 700;
  // JPEG quality 0.6-0.75 per spec — 0.7 balances clarity vs. payload size.
  const IMAGE_QUALITY     = 0.7;
  const VIDEO_WIDTH       = 640;
  const VIDEO_HEIGHT      = 480;

  let videoEl  = null;
  let statusEl = null;
  let stream   = null;   // may be pre-opened from system check
  let frameIntervalHandle = null;
  let ws       = null;
  let wsReconnectHandle = null;
  let wsReconnectAttempts = 0;

  const canvas = document.createElement('canvas');
  const ctx    = canvas.getContext('2d');

  canvas.width  = VIDEO_WIDTH;
  canvas.height = VIDEO_HEIGHT;

  // ------------------------------------------------------------------ //
  // Status helper
  // ------------------------------------------------------------------ //

  function setStatus(text, isAlert = false) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.classList.toggle('alert', isAlert);
  }

  // ------------------------------------------------------------------ //
  // WebSocket connection
  // ------------------------------------------------------------------ //

  function connectWebSocket() {
    if (wsReconnectHandle) { clearTimeout(wsReconnectHandle); wsReconnectHandle = null; }
    const token    = localStorage.getItem('access_token');
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${window.location.host}/exam/ws/candidate?token=${encodeURIComponent(token)}`);

    ws.onopen = () => {
      wsReconnectAttempts = 0;
    };

    ws.onerror = () => {
      // Close triggers reconnect
    };

    ws.onclose = () => {
      // Exponential back-off reconnect: 2s, 4s, 8s … capped at 30s
      const delay = Math.min(2000 * Math.pow(2, wsReconnectAttempts), 30000);
      wsReconnectAttempts++;
      wsReconnectHandle = setTimeout(connectWebSocket, delay);
    };
  }

  // ------------------------------------------------------------------ //
  // Init — called once at exam entry. Accepts an existing stream
  // (kept alive from system check) or opens a new one.
  // ------------------------------------------------------------------ //

  async function init(videoElement, statusElement, existingStream = null) {
    videoEl  = videoElement;
    statusEl = statusElement;

    if (existingStream) {
      stream = existingStream;
    }

    // Connect WebSocket for frame streaming and server commands
    connectWebSocket();
  }

  // ------------------------------------------------------------------ //
  // Start — activates camera (or attaches existing stream) and begins
  //         frame capture.
  // ------------------------------------------------------------------ //

  async function start() {
    // If a stream was handed to us from the system check, attach it
    if (stream) {
      _attachStream(stream);
      _beginFrameCapture();
      _listenForTrackEnd();
      _reportCameraStatus('active');
      return true;
    }

    // Otherwise open a fresh camera
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: VIDEO_WIDTH },
          height: { ideal: VIDEO_HEIGHT },
          frameRate: { max: 15 },
          facingMode: 'user',
        },
        audio: false,
      });
      _attachStream(stream);
      _beginFrameCapture();
      _listenForTrackEnd();
      _reportCameraStatus('active');
      return true;
    } catch (err) {
      const msg = _cameraErrorMessage(err);
      setStatus(msg, true);
      console.warn('[Proctor] Camera failed:', err);
      // Advisory only — never blocks or ends the exam. The candidate keeps
      // going; a human proctor on the admin dashboard sees "Camera Blocked"
      // and decides whether follow-up is needed.
      _reportCameraStatus('blocked', err && err.name ? err.name : 'unknown');
      return false;
    }
  }

  // ------------------------------------------------------------------ //
  // Camera health advisory — plain HTTP POST (not the WebSocket) so it
  // still gets through even if the proctor socket hasn't connected yet.
  // This is informational only: it never touches strikes/violations.
  // ------------------------------------------------------------------ //

  function _reportCameraStatus(cameraStatus, reason = null) {
    fetch('/exam/camera-status', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${localStorage.getItem('access_token')}`,
      },
      body: JSON.stringify({ camera_status: cameraStatus, reason }),
    }).catch(() => {});
  }

  function _attachStream(s) {
    videoEl.srcObject  = s;
    videoEl.muted      = true;
    videoEl.autoplay   = true;
    videoEl.playsInline = true;

    videoEl.addEventListener('loadedmetadata', () => {
      videoEl.play().catch(() => {});
      if (videoEl.videoWidth > 0 && videoEl.videoHeight > 0) {
        setStatus('Camera active — monitoring');
      }
    }, { once: true });

    // Fallback play attempt after 800ms for browsers that fire loadedmetadata late
    setTimeout(() => {
      if (videoEl.paused) {
        videoEl.play().catch(() => {});
      }
      if (videoEl.videoWidth > 0) {
        setStatus('Camera active — monitoring');
      }
    }, 800);
  }

  function _listenForTrackEnd() {
    if (!stream) return;
    stream.getTracks().forEach(track => {
      track.addEventListener('ended', () => {
        setStatus('Camera disconnected', true);
        // Report to server as a violation event (for admin visibility)
        fetch('/exam/violation', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${localStorage.getItem('access_token')}`,
          },
          body: JSON.stringify({ violation_type: 'camera_disconnected' }),
        }).catch(() => {});
        // Also flip the admin grid's camera indicator to "Blocked" immediately
        // rather than waiting for the frame feed to simply go stale.
        _reportCameraStatus('blocked', 'track_ended');
      });
    });
  }

  function _cameraErrorMessage(err) {
    if (!err) return 'Camera access failed.';
    const name = err.name || '';
    if (name === 'NotAllowedError' || name === 'PermissionDeniedError')
      return 'Camera permission denied. Grant access in browser settings.';
    if (name === 'NotFoundError' || name === 'DevicesNotFoundError')
      return 'No camera device detected.';
    if (name === 'NotReadableError' || name === 'TrackStartError')
      return 'Camera is already in use by another application.';
    if (name === 'OverconstrainedError')
      return 'Camera does not support required resolution.';
    if (name === 'TypeError')
      return 'Camera requires HTTPS. Please use a secure connection.';
    return `Camera access failed: ${err.message || name}`;
  }

  // ------------------------------------------------------------------ //
  // Frame capture loop
  // ------------------------------------------------------------------ //

  function _beginFrameCapture() {
    if (frameIntervalHandle) clearInterval(frameIntervalHandle);
    frameIntervalHandle = setInterval(_captureAndSend, FRAME_INTERVAL_MS);
  }

  function _captureAndSend() {
    if (!videoEl) return;
    if (videoEl.readyState < 2) return;              // HAVE_CURRENT_DATA
    if (videoEl.videoWidth === 0 || videoEl.videoHeight === 0) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;

    try {
      ctx.drawImage(videoEl, 0, 0, canvas.width, canvas.height);
      const frameData = canvas.toDataURL('image/jpeg', IMAGE_QUALITY);
      ws.send(JSON.stringify({ type: 'proctor_frame', frame: frameData }));
    } catch (_) {
      // Transient canvas/encoding error — skip this frame silently
    }
  }

  // ------------------------------------------------------------------ //
  // Stop — clean up everything
  // ------------------------------------------------------------------ //

  function stop() {
    if (frameIntervalHandle) { clearInterval(frameIntervalHandle); frameIntervalHandle = null; }
    if (wsReconnectHandle)   { clearTimeout(wsReconnectHandle);   wsReconnectHandle = null; }
    if (stream) {
      stream.getTracks().forEach(t => t.stop());
      stream = null;
    }
    if (ws) {
      ws.onclose = null;  // suppress reconnect on manual close
      ws.close();
      ws = null;
    }
    if (videoEl) {
      videoEl.srcObject = null;
    }
    setStatus('Camera stopped');
  }

  // ------------------------------------------------------------------ //
  // Public helpers
  // ------------------------------------------------------------------ //

  /** Expose WebSocket so exam.js can listen for server-pushed commands. */
  function getWebSocket() { return ws; }

  /**
   * Open camera stream for the system check preview.
   * Returns the MediaStream so exam.js can hand it to Proctor.init() later.
   * The stream is NOT stopped after this call — caller is responsible.
   */
  async function openForPreview(videoElement) {
    try {
      const s = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: VIDEO_WIDTH },
          height: { ideal: VIDEO_HEIGHT },
          frameRate: { max: 15 },
          facingMode: 'user',
        },
        audio: false,
      });
      videoElement.srcObject   = s;
      videoElement.muted       = true;
      videoElement.autoplay    = true;
      videoElement.playsInline = true;

      await new Promise((resolve, reject) => {
        const t = setTimeout(() => reject(new Error('timeout')), 5000);
        videoElement.addEventListener('loadedmetadata', () => {
          clearTimeout(t);
          videoElement.play().catch(() => {});
          resolve();
        }, { once: true });
        videoElement.addEventListener('error', (e) => { clearTimeout(t); reject(e); }, { once: true });
      });

      // Verify we actually have a real picture
      if (videoElement.videoWidth === 0 || videoElement.videoHeight === 0) {
        throw new Error('Camera stream has no video data (black frame)');
      }

      return s;   // caller receives the stream
    } catch (err) {
      throw err;
    }
  }

  return { init, start, stop, getWebSocket, openForPreview };
})();
