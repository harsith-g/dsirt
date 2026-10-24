/* The exam runner: paper rendering, debounced autosave, server-driven timer,
 * question palette and submission.
 *
 * The countdown shown on screen is cosmetic. It ticks locally between polls
 * but is re-synchronised from `seconds_left` on every server response, and
 * the server auto-submits on its own deadline regardless of what the browser
 * believes.
 */
(function (global) {
  "use strict";

  var el = function (id) { return document.getElementById(id); };

  var Exam = {
    state: null,
    questions: [],
    index: 0,
    proctor: null,
    secondsLeft: 0,
    tickTimer: null,
    pollTimer: null,
    heartbeatTimer: null,
    submitting: false,
    dirty: {},
  };

  /* ------------------------------------------------------------- boot */
  Exam.init = async function (config) {
    this.config = config;
    try {
      this.state = await Portal.api("/api/exam/state");
    } catch (err) {
      return this.fatal(err.message);
    }

    if (this.state.status !== "PHASE_1_IN_PROGRESS" &&
        this.state.status !== "PHASE_2_IN_PROGRESS") {
      global.location.href = "/dashboard";
      return;
    }

    try {
      var paper = await Portal.api("/api/exam/paper");
      this.questions = paper.questions || [];
    } catch (err) {
      return this.fatal(err.message);
    }
    if (!this.questions.length) return this.fatal("Your paper contains no questions. Contact the invigilator.");

    el("phaseLabel").textContent =
      this.state.current_phase === "PHASE_1"
        ? "Phase 1 — Research Methodology"
        : "Phase 2 — " + ((this.state.selected_domain && this.state.selected_domain.name) || "Domain paper");

    await this.startProctoring();

    this.secondsLeft = this.state.seconds_left;
    this.renderPalette();
    this.show(0);
    this.startTimers();

    el("examBody").hidden = false;
    el("bootScreen").hidden = true;

    this.syncHeaderHeight();
    var self = this;
    global.addEventListener("resize", function () { self.syncHeaderHeight(); });
    global.addEventListener("orientationchange", function () {
      setTimeout(function () { self.syncHeaderHeight(); }, 250);
    });
  };

  Exam.fatal = function (message) {
    el("bootScreen").innerHTML =
      '<div class="card" style="max-width:520px;margin:80px auto">' +
      '<h2>Cannot start the examination</h2>' +
      '<p class="alert err" style="margin-top:12px">' + Portal.escapeHtml(message) + "</p>" +
      '<a class="btn ghost" href="/dashboard">Back to dashboard</a></div>';
    el("bootScreen").hidden = false;
  };

  /* ------------------------------------------------------- proctoring */
  Exam.startProctoring = async function () {
    var self = this;
    this.proctor = new global.Proctor({
      candidateId: this.config.candidateId,
      sessionId: this.state.session_id,
      iceServers: this.config.iceServers,
      onViolation: function (type, count) {
        el("violationCount").textContent = count;
        self.flash("Recorded: " + type.replace(/_/g, " ").toLowerCase(), "warn");
      },
      onStatus: function (key, value) {
        if (key === "camera") {
          el("camDot").className = "dot" + (value ? "" : " off");
          el("camState").textContent = value ? "Camera on" : "Camera off";
        }
        if (key === "link") {
          el("linkDot").className = "dot" + (value ? "" : " off");
          el("linkState").textContent = value ? "Connected" : "Reconnecting…";
        }
        if (key === "mode") {
          el("modeState").textContent = value === "frames" ? "Low-bandwidth mode" : "Live";
        }
      },
    });

    try {
      await this.proctor.startCamera(el("selfVideo"));
    } catch (err) {
      this.fatal(err.message);
      throw err;
    }

    this.proctor.connect();
    this.proctor.installShield({
      requireFullscreen: true,
      onFullscreenExit: function () { el("fullscreenPrompt").hidden = false; },
    });
    await this.proctor.requestFullscreen();
  };

  /* ---------------------------------------------------------- render */
  Exam.show = function (index) {
    if (index < 0 || index >= this.questions.length) return;
    this.index = index;
    var q = this.questions[index];

    var options = q.options.map(function (opt) {
      var checked = q.selected_option_ids.indexOf(opt.id) !== -1;
      var type = q.question_type === "MULTIPLE_CHOICE" ? "checkbox" : "radio";
      return (
        '<label class="option' + (checked ? " selected" : "") + '" data-option="' + opt.id + '">' +
        '<input type="' + type + '" name="q_' + q.id + '" value="' + opt.id + '"' +
        (checked ? " checked" : "") + ">" +
        '<span class="otext">' + Portal.escapeHtml(opt.text) + "</span></label>"
      );
    }).join("");

    var textArea =
      q.question_type === "TEXT"
        ? '<textarea id="textAnswer" rows="8" placeholder="Type your answer">' +
          Portal.escapeHtml(q.text_answer || "") + "</textarea>"
        : "";

    el("questionArea").innerHTML =
      '<div class="question-card">' +
      '<div class="question-no">Question ' + q.number + " of " + this.questions.length +
      ' &middot; ' + q.marks + " mark" + (q.marks === 1 ? "" : "s") +
      (q.negative_marks ? " &middot; −" + q.negative_marks + " if wrong" : " &middot; no negative marking") +
      "</div>" +
      '<div class="question-text">' + Portal.escapeHtml(q.question_text) + "</div>" +
      (options || textArea) +
      "</div>";

    var self = this;
    Array.prototype.forEach.call(el("questionArea").querySelectorAll(".option"), function (label) {
      label.addEventListener("click", function () {
        setTimeout(function () { self.captureSelection(q); }, 0);
      });
    });
    var ta = el("textAnswer");
    if (ta) {
      ta.addEventListener("input", function () {
        q.text_answer = ta.value;
        self.queueSave(q);
      });
    }

    el("reviewToggle").textContent = q.is_marked_for_review
      ? "Unmark for review"
      : "Mark for review";
    el("prevBtn").disabled = index === 0;
    el("nextBtn").disabled = index === this.questions.length - 1;
    this.renderPalette();
  };

  Exam.captureSelection = function (q) {
    var inputs = el("questionArea").querySelectorAll('input[name="q_' + q.id + '"]');
    var selected = [];
    Array.prototype.forEach.call(inputs, function (input) {
      if (input.checked) selected.push(input.value);
      var label = input.closest(".option");
      if (label) label.classList.toggle("selected", input.checked);
    });
    q.selected_option_ids = selected;
    this.queueSave(q);
    this.renderPalette();
  };

  Exam.renderPalette = function () {
    var self = this;
    el("palette").innerHTML = this.questions.map(function (q, i) {
      var answered = (q.selected_option_ids && q.selected_option_ids.length) ||
                     (q.text_answer && q.text_answer.trim());
      var cls = [];
      if (answered) cls.push("answered");
      if (q.is_marked_for_review) cls.push("review");
      if (i === self.index) cls.push("current");
      return '<button type="button" data-index="' + i + '" class="' + cls.join(" ") + '">' +
             q.number + "</button>";
    }).join("");

    Array.prototype.forEach.call(el("palette").querySelectorAll("button"), function (b) {
      b.addEventListener("click", function () { self.show(parseInt(b.dataset.index, 10)); });
    });

    var answered = this.questions.filter(function (q) {
      return (q.selected_option_ids && q.selected_option_ids.length) ||
             (q.text_answer && q.text_answer.trim());
    }).length;
    el("answeredCount").textContent = answered;
    el("totalCount").textContent = this.questions.length;
    el("reviewCount").textContent = this.questions.filter(function (q) {
      return q.is_marked_for_review;
    }).length;
  };

  /* --------------------------------------------------------- autosave */
  Exam.queueSave = function (q) {
    this.dirty[q.id] = q;
    el("saveState").textContent = "Saving…";
    this.flushSave();
  };

  Exam._save = async function () {
    var pending = Object.keys(this.dirty);
    if (!pending.length) return;
    var self = this;
    var batch = pending.map(function (id) { return self.dirty[id]; });
    this.dirty = {};

    for (var i = 0; i < batch.length; i++) {
      var q = batch[i];
      try {
        var res = await Portal.api("/api/sessions/" + this.state.session_id + "/answer", {
          method: "POST",
          body: {
            question_id: q.id,
            selected_option_ids: q.selected_option_ids || [],
            text_answer: q.question_type === "TEXT" ? (q.text_answer || null) : null,
            is_marked_for_review: !!q.is_marked_for_review,
          },
        });
        this.syncClock(res.seconds_left);
        if (res.status === "PHASE_1_COMPLETED" || res.status === "COMPLETED") {
          return this.onAutoSubmitted(res.status);
        }
        el("saveState").textContent = "All answers saved";
      } catch (err) {
        // Put it back so the next flush retries it.
        this.dirty[q.id] = q;
        el("saveState").textContent = "Save failed — retrying";
      }
    }
  };

  /* ----------------------------------------------------------- timers */
  Exam.syncClock = function (seconds) {
    if (typeof seconds === "number") this.secondsLeft = seconds;
  };

  Exam.startTimers = function () {
    var self = this;

    this.tickTimer = setInterval(function () {
      self.secondsLeft = Math.max(0, self.secondsLeft - 1);
      var t = el("timer");
      t.textContent = Portal.formatDuration(self.secondsLeft);
      t.className = "timer" + (self.secondsLeft <= 60 ? " crit" : self.secondsLeft <= 300 ? " warn" : "");
      if (self.secondsLeft === 300) self.flash("Five minutes remaining.", "warn");
      if (self.secondsLeft === 0 && !self.submitting) self.submit(true);
    }, 1000);

    // Authoritative re-sync plus presence, every 15 seconds.
    this.heartbeatTimer = setInterval(function () { self.heartbeat(); }, 15000);
    this.heartbeat();
  };

  Exam.heartbeat = async function () {
    if (this.submitting) return;
    var started = performance.now();
    try {
      var state = await Portal.api("/api/sessions/" + this.state.session_id + "/heartbeat", {
        method: "POST",
        body: {
          ping_ms: this.lastPing || null,
          camera_active: !!(this.proctor && this.proctor.stream),
          fullscreen: !!document.fullscreenElement,
        },
      });
      this.lastPing = Math.round(performance.now() - started);
      this.syncClock(state.seconds_left);
      // Only write a real number; a malformed response must not paint
      // "undefined" into the candidate's header mid-exam.
      if (typeof state.violation_count === "number") {
        el("violationCount").textContent = state.violation_count;
      }
      if (state.status === "PHASE_1_COMPLETED" || state.status === "COMPLETED") {
        this.onAutoSubmitted(state.status);
      }
    } catch (err) {
      /* transient: the next beat will resync */
    }
  };

  /* ------------------------------------------------------- submission */
  Exam.onAutoSubmitted = function (status) {
    if (this.submitting) return;
    this.submitting = true;
    clearInterval(this.tickTimer);
    clearInterval(this.heartbeatTimer);
    if (this.proctor) this.proctor.destroy();
    alert("Time is up. Your paper has been submitted automatically.");
    global.location.href = status === "COMPLETED" ? "/completed" : "/dashboard";
  };

  Exam.submit = async function (automatic) {
    if (this.submitting) return;
    var unanswered = this.questions.length -
      this.questions.filter(function (q) {
        return (q.selected_option_ids && q.selected_option_ids.length) ||
               (q.text_answer && q.text_answer.trim());
      }).length;

    if (!automatic) {
      var message = "Submit this paper?\n\n" +
        "Answered: " + (this.questions.length - unanswered) + " of " + this.questions.length + "\n" +
        (unanswered ? "Unanswered: " + unanswered + "\n" : "") +
        "\nThis cannot be undone.";
      if (!confirm(message)) return;
    }

    this.submitting = true;
    el("submitBtn").disabled = true;
    el("submitBtn").textContent = "Submitting…";

    // Make sure nothing is still sitting in the debounce window.
    this.flushSave.flush();
    await this._save();

    var endpoint = this.state.current_phase === "PHASE_1"
      ? "/api/exam/phase1/submit"
      : "/api/exam/phase2/submit";

    try {
      var res = await Portal.api(endpoint, { method: "POST" });
      clearInterval(this.tickTimer);
      clearInterval(this.heartbeatTimer);
      if (this.proctor) this.proctor.destroy();
      if (document.fullscreenElement) document.exitFullscreen().catch(function () {});
      global.location.href = res.next_step || "/dashboard";
    } catch (err) {
      this.submitting = false;
      el("submitBtn").disabled = false;
      el("submitBtn").textContent = "Submit paper";
      this.flash(err.message, "err");
    }
  };

  /* ----------------------------------------------------------- misc */
  /** Publish the real header height so the floating self-view can sit just
   *  below it on phones. The header wraps differently by width and by how
   *  long the domain name is, so measuring beats any hard-coded guess. */
  Exam.syncHeaderHeight = function () {
    var header = document.querySelector(".exam-header");
    if (!header) return;
    var h = Math.round(header.getBoundingClientRect().height);
    if (h > 0) document.documentElement.style.setProperty("--header-h", h + "px");
  };

  Exam.flash = function (message, kind) {
    var box = el("flash");
    box.textContent = message;
    box.className = "alert " + (kind || "info");
    box.hidden = false;
    clearTimeout(this._flashTimer);
    this._flashTimer = setTimeout(function () { box.hidden = true; }, 4000);
  };

  Exam.bindControls = function () {
    var self = this;
    el("prevBtn").addEventListener("click", function () { self.show(self.index - 1); });
    el("nextBtn").addEventListener("click", function () { self.show(self.index + 1); });
    el("submitBtn").addEventListener("click", function () { self.submit(false); });
    el("reviewToggle").addEventListener("click", function () {
      var q = self.questions[self.index];
      q.is_marked_for_review = !q.is_marked_for_review;
      el("reviewToggle").textContent = q.is_marked_for_review ? "Unmark for review" : "Mark for review";
      self.queueSave(q);
      self.renderPalette();
    });
    el("resumeFullscreen").addEventListener("click", async function () {
      await self.proctor.requestFullscreen();
      el("fullscreenPrompt").hidden = true;
    });
    document.addEventListener("keydown", function (e) {
      if (/^(INPUT|TEXTAREA)$/.test(e.target.tagName)) return;
      if (e.key === "ArrowRight") self.show(self.index + 1);
      if (e.key === "ArrowLeft") self.show(self.index - 1);
    });
  };

  // 800 ms after the last change: responsive without a request per keystroke.
  Exam.flushSave = Portal.debounce(function () { Exam._save(); }, 800);

  global.Exam = Exam;
})(window);
