/* Candidate-side proctoring: webcam capture, WebRTC publishing, the security
 * shield and violation reporting.
 *
 * Streaming has two modes. The primary path is a live WebRTC peer connection
 * per watching proctor. If the connection cannot be established or degrades,
 * the client falls back to posting a 1 fps JPEG frame over the signalling
 * socket, which needs a fraction of the bandwidth and still gives the proctor
 * a usable view.
 */
(function (global) {
  "use strict";

  function Proctor(config) {
    this.candidateId = config.candidateId;
    this.sessionId = config.sessionId || null;
    this.iceServers = config.iceServers || [];
    this.onViolation = config.onViolation || function () {};
    this.onStatus = config.onStatus || function () {};

    this.stream = null;
    this.socket = null;
    this.peers = {};           // adminId -> RTCPeerConnection
    this.videoEl = null;
    this.canvas = null;
    this.fallbackTimer = null;
    this.fallbackActive = false;
    this.reconnectDelay = 1000;
    this.closed = false;
    this.lastPingMs = null;
    this.violationCount = 0;
    // Do not spam the server if a flaky camera fires the same event in a loop.
    this._recent = {};
  }

  /* ------------------------------------------------------------ camera */
  Proctor.prototype.startCamera = async function (videoEl) {
    this.videoEl = videoEl;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 15 } },
        audio: false,
      });
    } catch (err) {
      this.onStatus("camera", false);
      throw new Error(
        "Camera access is required to sit this examination. " +
        "Allow camera access in your browser and reload the page."
      );
    }
    if (videoEl) {
      videoEl.srcObject = this.stream;
      await videoEl.play().catch(function () {});
    }
    this.onStatus("camera", true);

    var self = this;
    this.stream.getVideoTracks().forEach(function (track) {
      track.addEventListener("ended", function () {
        self.onStatus("camera", false);
        self.report("CAMERA_DISCONNECTED", "The camera track ended unexpectedly.");
      });
    });

    this.canvas = document.createElement("canvas");
    this.canvas.width = 320;
    this.canvas.height = 240;
    return this.stream;
  };

  /** Grab a still from the live camera, for attaching to a violation. */
  Proctor.prototype.snapshot = function (quality) {
    if (!this.stream || !this.videoEl || !this.canvas) return null;
    if (!this.videoEl.videoWidth) return null;
    try {
      var ctx = this.canvas.getContext("2d");
      ctx.drawImage(this.videoEl, 0, 0, this.canvas.width, this.canvas.height);
      return this.canvas.toDataURL("image/jpeg", quality || 0.5);
    } catch (err) {
      return null;
    }
  };

  /* --------------------------------------------------------- signalling */
  Proctor.prototype.connect = function () {
    if (this.closed) return;
    var scheme = global.location.protocol === "https:" ? "wss" : "ws";
    var url = scheme + "://" + global.location.host + "/ws/signaling/" + this.candidateId;
    var self = this;

    try {
      this.socket = new WebSocket(url);
    } catch (err) {
      this.scheduleReconnect();
      return;
    }

    this.socket.addEventListener("open", function () {
      self.reconnectDelay = 1000;
      self.onStatus("link", true);
    });

    this.socket.addEventListener("message", function (event) {
      var msg;
      try { msg = JSON.parse(event.data); } catch (e) { return; }
      self.handleSignal(msg);
    });

    this.socket.addEventListener("close", function () {
      self.onStatus("link", false);
      self.scheduleReconnect();
    });

    this.socket.addEventListener("error", function () {
      try { self.socket.close(); } catch (e) {}
    });
  };

  Proctor.prototype.scheduleReconnect = function () {
    if (this.closed) return;
    var self = this;
    setTimeout(function () { self.connect(); }, this.reconnectDelay);
    // Exponential backoff, capped, so a long outage does not hammer the server.
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, 15000);
  };

  Proctor.prototype.send = function (message) {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(message));
      return true;
    }
    return false;
  };

  Proctor.prototype.handleSignal = async function (msg) {
    var self = this;
    if (msg.type === "welcome") {
      if (msg.ice_servers && msg.ice_servers.length) this.iceServers = msg.ice_servers;
      return;
    }
    if (msg.type === "request-stream") {
      await this.openPeer(msg.from);
      return;
    }
    if (msg.type === "stop-stream") {
      this.closePeer(msg.from);
      return;
    }
    if (msg.type === "answer") {
      var pc = this.peers[msg.from];
      if (pc && msg.payload) {
        try { await pc.setRemoteDescription(new RTCSessionDescription(msg.payload)); }
        catch (e) { self.enableFallback(); }
      }
      return;
    }
    if (msg.type === "ice-candidate") {
      var peer = this.peers[msg.from];
      if (peer && msg.payload) {
        try { await peer.addIceCandidate(new RTCIceCandidate(msg.payload)); } catch (e) {}
      }
    }
  };

  /* ------------------------------------------------------------ webrtc */
  Proctor.prototype.openPeer = async function (adminId) {
    if (!this.stream || this.peers[adminId]) return;
    var self = this;
    var pc = new RTCPeerConnection({ iceServers: this.iceServers });
    this.peers[adminId] = pc;

    this.stream.getTracks().forEach(function (track) { pc.addTrack(track, self.stream); });

    pc.onicecandidate = function (event) {
      if (event.candidate) {
        self.send({ type: "ice-candidate", to: adminId, payload: event.candidate });
      }
    };

    pc.onconnectionstatechange = function () {
      if (pc.connectionState === "failed" || pc.connectionState === "disconnected") {
        // Media path is down: keep the proctor supplied via the 1 fps path.
        self.enableFallback();
      } else if (pc.connectionState === "connected") {
        self.disableFallbackIfIdle();
      }
    };

    try {
      var offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      this.send({ type: "offer", to: adminId, payload: offer });
    } catch (err) {
      this.enableFallback();
    }
  };

  Proctor.prototype.closePeer = function (adminId) {
    var pc = this.peers[adminId];
    if (pc) {
      try { pc.close(); } catch (e) {}
      delete this.peers[adminId];
    }
    this.disableFallbackIfIdle();
  };

  /* ------------------------------------------------- 1 fps canvas fallback */
  Proctor.prototype.enableFallback = function () {
    if (this.fallbackActive || this.closed) return;
    this.fallbackActive = true;
    this.onStatus("mode", "frames");
    var self = this;
    this.fallbackTimer = setInterval(function () {
      var frame = self.snapshot(0.4);
      if (frame) self.send({ type: "frame", payload: frame });
    }, 1000);
  };

  Proctor.prototype.disableFallbackIfIdle = function () {
    var anyConnected = Object.keys(this.peers).some(function (id) {
      return this.peers[id].connectionState === "connected";
    }, this);
    if (anyConnected && this.fallbackActive) {
      clearInterval(this.fallbackTimer);
      this.fallbackTimer = null;
      this.fallbackActive = false;
      this.onStatus("mode", "live");
    }
  };

  /* -------------------------------------------------------- violations */
  Proctor.prototype.report = async function (type, details, withSnapshot) {
    // Collapse repeats of the same event inside 3 seconds.
    var now = Date.now();
    if (this._recent[type] && now - this._recent[type] < 3000) return;
    this._recent[type] = now;

    this.violationCount += 1;
    var body = { violation_type: type, details: details || null };
    if (withSnapshot !== false) {
      var shot = this.snapshot(0.5);
      if (shot) body.snapshot_data_url = shot;
    }
    try {
      await global.Portal.api("/api/violations", { method: "POST", body: body });
    } catch (err) {
      // Never let a failed violation post interrupt the candidate's paper.
    }
    this.onViolation(type, this.violationCount);
  };

  /* ------------------------------------------------- the security shield */
  Proctor.prototype.installShield = function (options) {
    options = options || {};
    var self = this;
    var body = document.body;
    body.classList.add("no-select");

    function block(event) { event.preventDefault(); return false; }

    document.addEventListener("contextmenu", block);
    document.addEventListener("selectstart", function (e) {
      // Text inputs must still be usable for descriptive answers.
      if (!/^(INPUT|TEXTAREA)$/.test(e.target.tagName)) return block(e);
    });
    document.addEventListener("dragstart", block);

    ["copy", "cut", "paste"].forEach(function (evt) {
      document.addEventListener(evt, function (e) {
        if (/^(INPUT|TEXTAREA)$/.test(e.target.tagName) && evt === "paste") {
          e.preventDefault();
          self.report("COPY_PASTE", "Paste attempted into an answer field.");
          return false;
        }
        e.preventDefault();
        self.report("COPY_PASTE", evt + " attempted");
        return false;
      });
    });

    document.addEventListener("keydown", function (e) {
      var k = (e.key || "").toLowerCase();
      var devtools =
        e.key === "F12" ||
        (e.ctrlKey && e.shiftKey && ["i", "j", "c", "k"].indexOf(k) !== -1) ||
        (e.metaKey && e.altKey && ["i", "j", "c"].indexOf(k) !== -1) ||
        (e.ctrlKey && k === "u");
      if (devtools) {
        e.preventDefault();
        self.report("DEVTOOLS", "Developer-tools shortcut: " + (e.key || "?"));
        return false;
      }
      // Clipboard, save, print and find shortcuts.
      if ((e.ctrlKey || e.metaKey) && ["c", "v", "x", "s", "p", "f", "a"].indexOf(k) !== -1) {
        if (/^(INPUT|TEXTAREA)$/.test(e.target.tagName) && ["a", "c", "x"].indexOf(k) !== -1) return;
        e.preventDefault();
        return false;
      }
    });

    document.addEventListener("visibilitychange", function () {
      if (document.hidden) self.report("TAB_SWITCH", "The exam tab lost visibility.");
    });

    global.addEventListener("blur", function () {
      // A blur while still visible means another window took focus.
      if (!document.hidden) self.report("WINDOW_BLUR", "The exam window lost focus.");
    });

    document.addEventListener("fullscreenchange", function () {
      var full = !!document.fullscreenElement;
      self.onStatus("fullscreen", full);
      if (!full && options.requireFullscreen !== false) {
        self.report("FULLSCREEN_EXIT", "The candidate left full screen.");
        if (options.onFullscreenExit) options.onFullscreenExit();
      }
    });

    global.addEventListener("offline", function () {
      self.onStatus("link", false);
      self.report("NETWORK_LOST", "The browser reported it went offline.", false);
    });

    global.addEventListener("beforeunload", function (e) {
      if (options.warnOnLeave === false) return;
      e.preventDefault();
      e.returnValue = "";
      return "";
    });
  };

  Proctor.prototype.requestFullscreen = async function () {
    try {
      if (!document.fullscreenElement) {
        await document.documentElement.requestFullscreen();
      }
      return true;
    } catch (err) {
      return false;
    }
  };

  Proctor.prototype.destroy = function () {
    this.closed = true;
    if (this.fallbackTimer) clearInterval(this.fallbackTimer);
    Object.keys(this.peers).forEach(this.closePeer, this);
    if (this.socket) { try { this.socket.close(); } catch (e) {} }
    if (this.stream) this.stream.getTracks().forEach(function (t) { t.stop(); });
  };

  global.Proctor = Proctor;
})(window);
