/* Admin-side live proctoring grid.
 *
 * One WebSocket to /ws/proctor carries signalling for every candidate. For
 * each tile the admin asks the candidate to publish, answers their offer, and
 * attaches the resulting MediaStream to the tile's <video>. Candidates that
 * fall back to 1 fps frames render into an <img> instead, so a tile always
 * shows something.
 */
(function (global) {
  "use strict";

  var Grid = {
    socket: null,
    adminId: null,
    iceServers: [],
    peers: {},        // candidateId -> RTCPeerConnection
    tiles: {},        // candidateId -> tile element
    sessions: [],
    reconnectDelay: 1000,
  };

  var el = function (id) { return document.getElementById(id); };

  Grid.init = function (config) {
    this.iceServers = config.iceServers || [];
    this.connect();
    this.refresh();
    setInterval(this.refresh.bind(this), 5000);
  };

  /* -------------------------------------------------------- signalling */
  Grid.connect = function () {
    var scheme = global.location.protocol === "https:" ? "wss" : "ws";
    var self = this;
    this.socket = new WebSocket(scheme + "://" + global.location.host + "/ws/proctor");

    this.socket.addEventListener("open", function () {
      self.reconnectDelay = 1000;
      self.setLinkState(true);
      // Re-subscribe to everyone already on screen.
      Object.keys(self.tiles).forEach(function (id) {
        self.send({ type: "watch", candidate_id: id });
        self.send({ type: "request-stream", to: id });
      });
    });

    this.socket.addEventListener("message", function (event) {
      var msg;
      try { msg = JSON.parse(event.data); } catch (e) { return; }
      self.handle(msg);
    });

    this.socket.addEventListener("close", function () {
      self.setLinkState(false);
      setTimeout(function () { self.connect(); }, self.reconnectDelay);
      self.reconnectDelay = Math.min(self.reconnectDelay * 2, 15000);
    });
  };

  Grid.send = function (message) {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(message));
    }
  };

  Grid.setLinkState = function (up) {
    var dot = el("wsDot");
    var label = el("wsState");
    if (dot) dot.className = "dot" + (up ? "" : " off");
    if (label) label.textContent = up ? "Signalling connected" : "Signalling reconnecting…";
  };

  Grid.handle = async function (msg) {
    if (msg.type === "welcome") {
      this.adminId = msg.admin_id;
      if (msg.ice_servers && msg.ice_servers.length) this.iceServers = msg.ice_servers;
      return;
    }
    if (msg.type === "event") return this.handleEvent(msg);
    if (msg.type === "offer") return this.acceptOffer(msg.from, msg.payload);
    if (msg.type === "frame") return this.renderFrame(msg.from, msg.payload);
    if (msg.type === "ice-candidate") {
      var pc = this.peers[msg.from];
      if (pc && msg.payload) {
        try { await pc.addIceCandidate(new RTCIceCandidate(msg.payload)); } catch (e) {}
      }
    }
  };

  Grid.handleEvent = function (msg) {
    if (msg.type === "violation") {
      this.logEvent(
        (msg.registration_number || msg.candidate_id) + " — " +
        msg.violation_type.replace(/_/g, " ").toLowerCase() +
        " (#" + msg.count + ")",
        msg.flagged ? "err" : "warn"
      );
      var tile = this.tiles[msg.candidate_id];
      if (tile) {
        var badge = tile.querySelector(".vcount");
        if (badge) {
          badge.textContent = msg.count;
          badge.className = "pill err vcount";
        }
        if (msg.flagged) tile.classList.add("flagged");
      }
    } else if (msg.type === "candidate-online") {
      this.logEvent((msg.registration_number || msg.candidate_id) + " came online", "ok");
      this.send({ type: "request-stream", to: msg.candidate_id });
    } else if (msg.type === "candidate-offline") {
      this.logEvent((msg.candidate_id) + " went offline", "warn");
      this.markOffline(msg.candidate_id);
    }
  };

  /* ------------------------------------------------------------ webrtc */
  Grid.acceptOffer = async function (candidateId, offer) {
    if (!offer) return;
    var self = this;
    var pc = this.peers[candidateId];
    if (pc) { try { pc.close(); } catch (e) {} }

    pc = new RTCPeerConnection({ iceServers: this.iceServers });
    this.peers[candidateId] = pc;

    pc.ontrack = function (event) {
      var tile = self.tiles[candidateId];
      if (!tile) return;
      var video = tile.querySelector("video");
      var img = tile.querySelector("img");
      var placeholder = tile.querySelector(".placeholder");
      if (video) {
        video.srcObject = event.streams[0];
        video.hidden = false;
        video.play().catch(function () {});
      }
      if (img) img.hidden = true;
      if (placeholder) placeholder.hidden = true;
    };

    pc.onicecandidate = function (event) {
      if (event.candidate) {
        self.send({ type: "ice-candidate", to: candidateId, payload: event.candidate });
      }
    };

    try {
      await pc.setRemoteDescription(new RTCSessionDescription(offer));
      var answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      this.send({ type: "answer", to: candidateId, payload: answer });
    } catch (err) {
      this.logEvent("Could not negotiate video for " + candidateId, "err");
    }
  };

  Grid.renderFrame = function (candidateId, dataUrl) {
    var tile = this.tiles[candidateId];
    if (!tile || !dataUrl) return;
    var img = tile.querySelector("img");
    var video = tile.querySelector("video");
    var placeholder = tile.querySelector(".placeholder");
    if (img) { img.src = dataUrl; img.hidden = false; }
    if (video) video.hidden = true;
    if (placeholder) placeholder.hidden = true;
    var mode = tile.querySelector(".mode");
    if (mode) mode.textContent = "1 fps";
  };

  Grid.markOffline = function (candidateId) {
    var tile = this.tiles[candidateId];
    if (!tile) return;
    var placeholder = tile.querySelector(".placeholder");
    var video = tile.querySelector("video");
    var img = tile.querySelector("img");
    if (video) video.hidden = true;
    if (img) img.hidden = true;
    if (placeholder) { placeholder.hidden = false; placeholder.textContent = "Offline"; }
  };

  /* -------------------------------------------------------------- grid */
  Grid.refresh = async function () {
    var self = this;
    try {
      this.sessions = await Portal.api("/api/admin/sessions/live");
    } catch (err) {
      return;
    }
    el("liveCount").textContent = this.sessions.length;
    el("onlineCount").textContent = this.sessions.filter(function (s) { return s.online; }).length;
    el("flaggedCount").textContent =
      this.sessions.filter(function (s) { return s.violation_count >= 5; }).length;

    var container = el("feedGrid");
    var filter = (el("filterBox").value || "").trim().toLowerCase();
    var visible = this.sessions.filter(function (s) {
      if (!filter) return true;
      return (s.full_name + " " + s.registration_number + " " + (s.domain || ""))
        .toLowerCase().indexOf(filter) !== -1;
    });

    if (!visible.length) {
      container.innerHTML = '<p class="loading">No sessions match.</p>';
      this.tiles = {};
      return;
    }

    // Clear the initial "Loading feeds..." placeholder; otherwise it survives
    // as a phantom first cell and shifts every tile in the grid.
    var stale = container.querySelector(".loading");
    if (stale) stale.remove();

    visible.forEach(function (s) {
      var tile = self.tiles[s.candidate_id];
      if (!tile) {
        tile = document.createElement("div");
        tile.className = "feed";
        tile.dataset.candidate = s.candidate_id;
        tile.innerHTML =
          '<div class="screen">' +
            "<video autoplay playsinline muted hidden></video>" +
            '<img alt="" hidden>' +
            '<div class="placeholder">Waiting for camera…</div>' +
          "</div>" +
          '<div class="meta">' +
            '<div class="name"></div>' +
            '<div class="line">' +
              '<span class="mono reg"></span>' +
              '<span class="pill phase"></span>' +
              '<span class="mono tleft"></span>' +
            "</div>" +
            '<div class="line">' +
              '<span class="pill vcount">0</span>' +
              '<span class="ping"></span>' +
              '<span class="mode">live</span>' +
              '<span class="presence"></span>' +
            "</div>" +
          "</div>";
        tile.addEventListener("click", function () { self.openDetail(s); });
        container.appendChild(tile);
        self.tiles[s.candidate_id] = tile;
        self.send({ type: "watch", candidate_id: s.candidate_id });
        self.send({ type: "request-stream", to: s.candidate_id });
      }

      tile.querySelector(".name").textContent = s.full_name;
      tile.querySelector(".reg").textContent = s.registration_number;
      tile.querySelector(".phase").textContent = s.current_phase.replace("_", " ");
      tile.querySelector(".tleft").textContent = Portal.formatDuration(s.seconds_left);
      var v = tile.querySelector(".vcount");
      v.textContent = s.violation_count;
      v.className = "pill vcount " + (s.violation_count >= 5 ? "err" : s.violation_count ? "warn" : "ok");
      tile.querySelector(".ping").textContent = s.ping_ms !== null ? s.ping_ms + " ms" : "";
      tile.querySelector(".presence").textContent = s.online ? "online" : "offline";
      tile.classList.toggle("flagged", s.violation_count >= 5);
      if (!s.online) self.markOffline(s.candidate_id);
    });

    // Drop tiles for sessions that have finished.
    var stillLive = {};
    visible.forEach(function (s) { stillLive[s.candidate_id] = true; });
    Object.keys(this.tiles).forEach(function (id) {
      if (!stillLive[id]) {
        self.send({ type: "unwatch", candidate_id: id });
        self.send({ type: "stop-stream", to: id });
        if (self.peers[id]) { try { self.peers[id].close(); } catch (e) {} delete self.peers[id]; }
        self.tiles[id].remove();
        delete self.tiles[id];
      }
    });
  };

  /* ------------------------------------------------------------ detail */
  Grid.openDetail = async function (session) {
    el("detailTitle").textContent = session.full_name + " — " + session.registration_number;
    el("detailBody").innerHTML = '<p class="loading">Loading violation history…</p>';
    el("detailModal").hidden = false;

    try {
      var rows = await Portal.api("/api/admin/sessions/" + session.session_id + "/violations");
      if (!rows.length) {
        el("detailBody").innerHTML = '<p class="muted">No violations recorded.</p>';
        return;
      }
      el("detailBody").innerHTML =
        '<div class="table-wrap"><table><thead><tr><th>Time</th><th>Type</th>' +
        "<th>Detail</th><th>Snapshot</th></tr></thead><tbody>" +
        rows.map(function (v) {
          return "<tr>" +
            '<td class="mono small">' + new Date(v.timestamp).toLocaleString() + "</td>" +
            '<td><span class="pill warn">' + v.violation_type.replace(/_/g, " ") + "</span></td>" +
            "<td>" + Portal.escapeHtml(v.details || "") + "</td>" +
            "<td>" + (v.snapshot_url
              ? '<a href="' + v.snapshot_url + '" target="_blank" rel="noopener">view</a>'
              : "—") + "</td>" +
            "</tr>";
        }).join("") + "</tbody></table></div>";
    } catch (err) {
      el("detailBody").innerHTML = '<p class="alert err">' + Portal.escapeHtml(err.message) + "</p>";
    }
  };

  Grid.logEvent = function (text, kind) {
    var log = el("eventLog");
    var row = document.createElement("div");
    row.className = "small";
    row.style.padding = "5px 0";
    row.style.borderBottom = "1px solid var(--border)";
    row.innerHTML = '<span class="pill ' + (kind || "") + '">' +
      new Date().toLocaleTimeString() + "</span> " + Portal.escapeHtml(text);
    log.insertBefore(row, log.firstChild);
    while (log.childElementCount > 80) log.removeChild(log.lastChild);
  };

  global.ProctorGrid = Grid;
})(window);
