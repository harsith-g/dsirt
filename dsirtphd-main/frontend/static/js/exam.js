/**
 * Ph.D Entrance Examination — candidate exam page.
 *
 * Flow
 *   Paper 1 : instructions → rules → camera check → Paper 1 (timer) → submit / time-out
 *             → signed out, 15-minute break screen
 *   Paper 2 : sign in again after the break → camera check → choose domain → Paper 2 (timer)
 *             → submit / time-out → finished
 *
 * Why this version does not "jump" or "stick"
 *   - The whole paper of the running phase is loaded ONCE. Moving between questions is
 *     local (instant, works on slow Wi-Fi) and always uses the ABSOLUTE question number.
 *     The old version asked the server for "next" relative to a position stored on the
 *     server — two tabs / a double tap / a stale tab moved that shared position, which is
 *     how candidates went from question 7 to 32.
 *   - Every answer goes into a save queue that retries until the server confirms it, and is
 *     also backed up on the device, so a network blip or page reload never loses an answer.
 *   - The timer is a deadline from the server, not a counter, so a sleeping phone or
 *     background tab can't make it drift. When it reaches zero the server is asked first;
 *     the page never "auto-submits" on its own guess.
 */
(async () => {
  const token = localStorage.getItem('access_token');
  const role = localStorage.getItem('role');
  if (!token || role !== 'candidate') { location.href = '/login'; return; }

  const $ = (id) => document.getElementById(id);
  const userId = (() => { try { return JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))).user_id; } catch (_) { return 'x'; } })();

  // ── Screens ─────────────────────────────────────────────────────────────
  const screens = {
    loading: $('loadingScreen'), instructions: $('instructionsScreen'), rules: $('violationRulesScreen'),
    syscheck: $('systemCheckModal'), domain: $('domainSelectScreen'), exam: $('examScreen'), terminal: $('terminalScreen'),
  };
  function show(name) {
    Object.entries(screens).forEach(([k, el]) => { if (el) el.style.display = k === name ? 'flex' : 'none'; });
    window.scrollTo(0, 0);
  }

  // ── Elements ────────────────────────────────────────────────────────────
  const el = {
    loadingText: $('loadingText'),
    instrBtn: $('instrAcknowledgeBtn'), rulesBtn: $('violRulesNextBtn'),
    preview: $('syscheckCameraPreview'), sysStatus: $('syscheckCameraStatus'), sysError: $('syscheckError'),
    sysRetry: $('syscheckRetryBtn'), sysCTA: $('syschecCTA'), sysSubtitle: $('syscheckSubtitle'),
    domainSelect: $('domainSelect'), domainError: $('domainError'), domainConfirmCard: $('domainConfirmCard'),
    domainName: $('domainSelectedName'), confirmDomainBtn: $('confirmDomainBtn'), confirmDomainFinalBtn: $('confirmDomainFinalBtn'),
    cancelDomainBtn: $('cancelDomainBtn'), registeredNote: $('registeredDomainNote'),
    candidateName: $('candidateName'), phaseLabel: $('phaseLabel'), phaseBadge: $('phaseBadge'), phaseCount: $('phaseCount'),
    timer: $('timerDisplay'), timerEnd: $('timerEndLabel'), saveStatus: $('saveStatus'), offline: $('offlineBanner'),
    strikeCount: $('strikeCount'), strikeDots: document.querySelectorAll('.strike-dot'),
    qCard: $('questionCard'), qMeta: $('questionMeta'), qBadge: $('questionStatusBadge'), qText: $('questionText'),
    options: $('mcqOptions'), writtenWrap: $('writtenAnswerWrap'), written: $('writtenAnswerInput'),
    prev: $('prevBtn'), next: $('nextBtn'), review: $('reviewBtn'), navGrid: $('navGrid'),
    webcamVideo: $('webcamVideo'), webcamStatus: $('webcamStatus'), liveDot: $('webcamLiveDot'),
    submitTitle: $('submitPanelTitle'), submitSub: $('submitPanelSub'), submitBtn: $('submitPhaseBtn'),
    warnModal: $('warningModal'), warnBody: $('warningModalBody'), warnOk: $('warningModalOkBtn'),
    confirmModal: $('submitConfirmModal'), confirmTitle: $('submitConfirmTitle'), confirmBody: $('submitConfirmBody'),
    confirmCheck: $('submitConfirmCheck'), confirmBtn: $('submitConfirmBtn'), confirmCancel: $('submitCancelBtn'),
    termTitle: $('terminalTitle'), termBody: $('terminalBody'), termCountdown: $('terminalCountdown'), termBtn: $('terminalBtn'),
  };
  el.candidateName.textContent = localStorage.getItem('full_name') || '';

  // ── State ───────────────────────────────────────────────────────────────
  let serverState = null;
  let paper = null;            // { phase, from, to, session_id, questions: [] }
  let curIdx = 0;              // ABSOLUTE question index
  let deadlineMs = 0;
  let examActive = false;
  let fullscreenUsed = false;
  let previewStream = null;
  let proctorStarted = false;
  let tickHandle = null, syncHandle = null;
  let lastViolationAt = 0;
  let endingPhase = false;
  let unsubscribeWs = null;

  // ── API ─────────────────────────────────────────────────────────────────
  async function api(path, method = 'GET', body = null, timeoutMs = 15000) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(path, {
        method,
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: body ? JSON.stringify(body) : undefined,
        signal: ctrl.signal,
        cache: 'no-store',
      });
      let data = null;
      try { data = await res.json(); } catch (_) {}
      if (res.status === 401) { onSignedOut(data && data.detail); return { ok: false, status: 401, data }; }
      return { ok: res.ok, status: res.status, data };
    } catch (_) {
      return { ok: false, status: 0, data: { detail: 'Network error — check your internet connection.' } };
    } finally {
      clearTimeout(t);
    }
  }

  async function apiRetry(path, method = 'GET', body = null, tries = 4) {
    let r;
    for (let i = 0; i < tries; i++) {
      r = await api(path, method, body);
      if (r.ok || (r.status >= 400 && r.status < 500)) return r;
      await sleep(800 * (i + 1));
    }
    return r;
  }

  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  function onSignedOut(detail) {
    if (screens.terminal.style.display === 'flex') return;
    teardown();
    localStorage.removeItem('access_token');
    terminal('Signed out', (detail || 'Your session has ended.') + '\n\nIf this is unexpected, sign in again — your saved answers are kept.', { btn: 'Go to sign-in', href: '/login' });
  }

  // ── Formatting ──────────────────────────────────────────────────────────
  function fmtClock(sec) {
    sec = Math.max(0, Math.floor(sec));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const mm = String(m).padStart(2, '0'), ss = String(s).padStart(2, '0');
    return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
  }
  function fmtTimeIST(ms) {
    return new Date(ms).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: true });
  }
  function fillConfig(s) {
    document.querySelectorAll('.js-p1min').forEach(n => n.textContent = s.phase1_minutes || 60);
    document.querySelectorAll('.js-p2min').forEach(n => n.textContent = s.phase2_minutes || 60);
    document.querySelectorAll('.js-breakmin').forEach(n => n.textContent = s.break_minutes || 15);
    document.querySelectorAll('.js-p1count').forEach(n => n.textContent = s.phase1_count || 50);
  }

  // ════════════════════════════════════════════════════════════════════════
  // Routing
  // ════════════════════════════════════════════════════════════════════════
  async function boot() {
    show('loading');
    const r = await apiRetry('/exam/state');
    if (!r.ok) {
      if (r.status === 401) return;
      terminal('Cannot reach the exam server', (r.data && r.data.detail) || 'Please check your internet connection.', { btn: 'Try again', onClick: () => location.reload() });
      return;
    }
    serverState = r.data;
    fillConfig(serverState);
    route(serverState);
  }

  function route(s) {
    if (s.status === 'disqualified') return finalScreen(s);
    if (s.status === 'completed' || s.status === 'expired') return finalScreen(s);
    if (s.status === 'on_break') {
      if (s.break_seconds_left > 0) return breakScreen(s, false);
      return cameraCheck('phase2');
    }
    if (s.status === 'in_progress') return cameraCheck('resume');
    // not started
    if (!s.exam_open) {
      return terminal('The examination has not started yet',
        'Please wait on the dashboard. The Start button appears as soon as the examiner opens the exam.',
        { btn: 'Back to dashboard', href: '/candidate' });
    }
    showInstructions();
  }

  function showInstructions() {
    show('instructions');
    el.instrBtn.onclick = () => { show('rules'); el.rulesBtn.onclick = () => cameraCheck('start'); };
  }

  // ════════════════════════════════════════════════════════════════════════
  // Camera check
  // ════════════════════════════════════════════════════════════════════════
  let checkMode = 'start';
  async function cameraCheck(mode) {
    checkMode = mode;
    show('syscheck');
    el.sysSubtitle.textContent = mode === 'phase2' ? 'Paper 2 — allow the camera to continue'
      : mode === 'resume' ? 'Resume your exam — allow the camera to continue' : 'Allow camera access to continue';
    el.sysCTA.textContent = mode === 'resume' ? 'Resume Exam' : mode === 'phase2' ? 'Continue to Paper 2' : 'Start Paper 1';
    await runCameraCheck();
  }

  async function runCameraCheck() {
    el.sysCTA.disabled = true;
    el.sysStatus.textContent = 'Requesting access…';
    el.sysStatus.style.color = '';
    el.sysError.style.display = 'none';
    el.sysRetry.style.display = 'none';
    if (previewStream && previewStream.active) {
      el.preview.srcObject = previewStream;
      el.preview.play().catch(() => {});
      cameraOk();
      return;
    }
    try {
      previewStream = await Proctor.openForPreview(el.preview);
      cameraOk();
    } catch (err) {
      const msg = Proctor.cameraErrorMessage(err);
      el.sysStatus.textContent = '✗ Camera not available';
      el.sysStatus.style.color = '#f87171';
      el.sysError.textContent = msg;
      el.sysError.style.display = 'block';
      el.sysRetry.style.display = 'inline-flex';
    }
  }
  function cameraOk() {
    el.sysStatus.textContent = '✓ Camera working';
    el.sysStatus.style.color = '#4ade80';
    el.sysCTA.disabled = false;
  }
  el.sysRetry.addEventListener('click', runCameraCheck);

  const canFullscreen = () => !!(document.documentElement.requestFullscreen && document.fullscreenEnabled);

  async function tryFullscreen() {
    if (!canFullscreen()) { fullscreenUsed = false; return; }
    try {
      await document.documentElement.requestFullscreen({ navigationUI: 'hide' });
      fullscreenUsed = true;
    } catch (_) {
      fullscreenUsed = false; // never block the candidate because of fullscreen
    }
  }

  el.sysCTA.addEventListener('click', async () => {
    if (el.sysCTA.disabled) return;
    el.sysCTA.disabled = true;
    const label = el.sysCTA.textContent;
    el.sysCTA.textContent = 'Please wait…';
    await tryFullscreen();

    if (checkMode === 'phase2') {
      el.sysCTA.textContent = label;
      el.sysCTA.disabled = false;
      showDomainSelection();
      return;
    }
    if (checkMode === 'start') {
      const r = await apiRetry('/exam/start', 'POST');
      if (!r.ok) {
        el.sysError.textContent = (r.data && r.data.detail) || 'Could not start the exam. Please try again.';
        el.sysError.style.display = 'block';
        el.sysCTA.textContent = label;
        el.sysCTA.disabled = false;
        return;
      }
      serverState = r.data.state;
    }
    const ok = await beginPhase();
    if (!ok) { el.sysCTA.textContent = label; el.sysCTA.disabled = false; }
  });

  // ════════════════════════════════════════════════════════════════════════
  // Domain selection (start of Paper 2)
  // ════════════════════════════════════════════════════════════════════════
  function showDomainSelection() {
    show('domain');
    el.domainConfirmCard.style.display = 'none';
    el.confirmDomainBtn.style.display = 'inline-flex';
    const reg = serverState && serverState.registered_domain;
    if (reg && [...el.domainSelect.options].some(o => o.value === reg)) {
      el.domainSelect.value = reg;
      el.registeredNote.textContent = `Your registered domain: ${reg}`;
      el.registeredNote.style.display = 'block';
    }
  }
  el.confirmDomainBtn.addEventListener('click', () => {
    const val = el.domainSelect.value;
    if (!val) { el.domainError.textContent = 'Please select a domain.'; el.domainError.style.display = 'block'; return; }
    el.domainError.style.display = 'none';
    el.domainName.textContent = val;
    el.domainConfirmCard.style.display = 'block';
    el.confirmDomainBtn.style.display = 'none';
  });
  el.cancelDomainBtn.addEventListener('click', () => {
    el.domainConfirmCard.style.display = 'none';
    el.confirmDomainBtn.style.display = 'inline-flex';
  });
  el.confirmDomainFinalBtn.addEventListener('click', async () => {
    const domain = el.domainSelect.value;
    if (!domain || el.confirmDomainFinalBtn.disabled) return;
    el.confirmDomainFinalBtn.disabled = true;
    el.confirmDomainFinalBtn.textContent = 'Loading Paper 2…';
    const r = await apiRetry('/exam/select-domain', 'POST', { domain });
    if (!r.ok) {
      el.confirmDomainFinalBtn.disabled = false;
      el.confirmDomainFinalBtn.textContent = '✓ Confirm & Begin Paper 2';
      el.domainError.textContent = (r.data && r.data.detail) || 'Could not load the domain questions.';
      el.domainError.style.display = 'block';
      return;
    }
    serverState = r.data.state;
    if (!document.fullscreenElement) await tryFullscreen();
    const ok = await beginPhase();
    if (!ok) { el.confirmDomainFinalBtn.disabled = false; el.confirmDomainFinalBtn.textContent = '✓ Confirm & Begin Paper 2'; }
  });

  // ════════════════════════════════════════════════════════════════════════
  // Running a phase
  // ════════════════════════════════════════════════════════════════════════
  const byIndex = new Map();

  async function beginPhase() {
    const r = await apiRetry('/exam/paper');
    if (!r.ok) {
      if (r.status === 409 && r.data && r.data.state) { handlePhaseOver(r.data.state); return true; }
      el.sysError.textContent = (r.data && r.data.detail) || 'Could not load the questions. Please try again.';
      el.sysError.style.display = 'block';
      show('syscheck');
      return false;
    }
    paper = r.data;
    byIndex.clear();
    paper.questions.forEach(q => byIndex.set(q.index, q));
    curIdx = paper.current_index;
    setDeadline(paper.time_remaining_seconds);
    restoreBackup();

    show('exam');
    examActive = true;
    endingPhase = false;

    const isP2 = paper.phase === 2;
    el.phaseLabel.textContent = isP2 ? `Paper 2 — ${paper.domain || 'Domain Specific'}` : 'Paper 1 — Research Methodology';
    el.phaseBadge.textContent = el.phaseLabel.textContent;
    el.phaseBadge.className = 'phase-badge ' + (isP2 ? 'phase-badge-2' : 'phase-badge-1');
    el.submitBtn.textContent = isP2 ? 'Submit Paper 2 (Finish Exam)' : 'Submit Paper 1';

    if (!proctorStarted) {
      proctorStarted = true;
      const camOk = await Proctor.start(el.webcamVideo, el.webcamStatus, previewStream);
      previewStream = null;
      if (camOk) el.liveDot.classList.add('active');
      if (unsubscribeWs) unsubscribeWs();
      unsubscribeWs = Proctor.onMessage(onServerMessage);
    }

    updateStrikes(serverState && serverState.strikes);
    buildNav();
    render();
    attachViolationListeners();
    startClocks();
    flushQueue();
    queueSave(curIdx, { visit: true });
    return true;
  }

  function setDeadline(secondsLeft) {
    deadlineMs = Date.now() + Math.max(0, secondsLeft) * 1000;
    el.timerEnd.textContent = 'Ends ' + fmtTimeIST(deadlineMs);
  }

  function startClocks() {
    clearInterval(tickHandle); clearInterval(syncHandle);
    tickHandle = setInterval(tick, 500);
    syncHandle = setInterval(syncState, 20000);
    tick();
  }

  function tick() {
    const left = Math.max(0, Math.ceil((deadlineMs - Date.now()) / 1000));
    el.timer.textContent = fmtClock(left);
    el.timer.classList.toggle('low', left <= 600 && left > 120);
    el.timer.classList.toggle('critical', left <= 120);
    if (left === 0 && examActive && !endingPhase) timeUp();
  }

  async function timeUp() {
    endingPhase = true;
    el.timer.textContent = '00:00';
    await flushQueue(true, 8000);
    // Ask the server — it decides (it also applies a short grace period for answers in transit).
    for (let i = 0; i < 6; i++) {
      const r = await api('/exam/state');
      if (r.ok) {
        const s = r.data;
        if (s.status === 'in_progress' && s.active_phase === paper.phase && s.time_remaining_seconds > 0) {
          setDeadline(s.time_remaining_seconds); // device clock was off — keep going
          endingPhase = false;
          return;
        }
        handlePhaseOver(s);
        return;
      }
      if (r.status === 401) return;
      await sleep(2000);
    }
    endingPhase = false; // still no server: keep retrying on next tick
  }

  async function syncState() {
    const r = await api('/exam/state');
    if (!r.ok) return;
    const s = r.data;
    serverState = s;
    updateStrikes(s.strikes);
    if (s.status !== 'in_progress' || s.active_phase !== paper.phase) { handlePhaseOver(s); return; }
    if (Math.abs((deadlineMs - Date.now()) / 1000 - s.time_remaining_seconds) > 2) setDeadline(s.time_remaining_seconds);
  }

  function onServerMessage(msg) {
    if (!msg || !msg.type) return;
    if (msg.type === 'exam_force_end' || msg.type === 'exam_disqualified' || msg.type === 'phase_ended') {
      api('/exam/state').then(r => { if (r.ok) handlePhaseOver(r.data); });
    } else if (msg.type === 'retake_granted') {
      location.reload();
    } else if (msg.type === 'auth_lost') {
      api('/exam/state'); // triggers the signed-out screen if the token is really gone
    } else if (msg.type === 'camera_lost') {
      reportViolation('camera_disconnected', true);
    }
  }

  // ── Rendering ───────────────────────────────────────────────────────────
  function isAnswered(q) {
    return q.question_type === 'mcq' ? q.selected_display_index !== null && q.selected_display_index !== undefined : !!(q.written_text && q.written_text.trim());
  }

  function render() {
    const q = byIndex.get(curIdx);
    if (!q) return;
    const pos = curIdx - paper.from + 1, total = paper.to - paper.from;
    el.qMeta.textContent = `Question ${pos} of ${total}`;
    el.qText.textContent = q.question_text;

    el.options.innerHTML = '';
    if (q.question_type === 'mcq') {
      el.writtenWrap.style.display = 'none';
      el.options.style.display = 'flex';
      if (!q.options || q.options.length === 0) {
        const warn = document.createElement('div');
        warn.className = 'form-error';
        warn.style.display = 'block';
        warn.textContent = 'This question could not be displayed. Please inform the invigilator and move to the next question.';
        el.options.appendChild(warn);
      }
      q.options.forEach((opt, i) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'option-btn' + (q.selected_display_index === i ? ' selected' : '');
        b.setAttribute('role', 'radio');
        b.setAttribute('aria-checked', q.selected_display_index === i ? 'true' : 'false');
        b.innerHTML = '<span class="option-letter"></span><span class="option-text"></span>';
        b.querySelector('.option-letter').textContent = String.fromCharCode(65 + i);
        b.querySelector('.option-text').textContent = opt;
        b.addEventListener('click', () => selectOption(q.index, i));
        el.options.appendChild(b);
      });
      if (isAnswered(q)) {
        const clr = document.createElement('button');
        clr.type = 'button';
        clr.className = 'clear-answer-btn';
        clr.textContent = 'Clear my answer';
        clr.addEventListener('click', () => selectOption(q.index, null));
        el.options.appendChild(clr);
      }
    } else {
      el.options.style.display = 'none';
      el.writtenWrap.style.display = 'block';
      el.written.value = q.written_text || '';
      el.written.dataset.index = q.index;
    }

    el.prev.disabled = curIdx <= paper.from;
    const last = curIdx >= paper.to - 1;
    el.next.disabled = last;
    el.next.textContent = last ? 'Last question' : 'Save & Next →';
    el.review.textContent = q.marked_for_review ? '★ Marked for review' : '☆ Mark for review';
    el.review.classList.toggle('review-active', !!q.marked_for_review);

    updateBadge(q);
    updateNavCell(curIdx);
    document.querySelectorAll('.nav-cell.current').forEach(c => { if (Number(c.dataset.index) !== curIdx) c.classList.remove('current'); });
    const cell = el.navGrid.querySelector(`[data-index="${curIdx}"]`);
    if (cell) cell.classList.add('current');
    updateSubmitPanel();
  }

  function updateBadge(q) {
    el.qBadge.className = 'question-status-badge';
    el.qCard.className = 'question-card';
    const a = isAnswered(q), r = q.marked_for_review;
    if (a && r) { el.qBadge.textContent = 'Answered · For review'; el.qBadge.classList.add('badge-answered'); el.qCard.classList.add('answered', 'marked-for-review'); }
    else if (a) { el.qBadge.textContent = 'Answered'; el.qBadge.classList.add('badge-answered'); el.qCard.classList.add('answered'); }
    else if (r) { el.qBadge.textContent = 'Marked for review'; el.qBadge.classList.add('badge-review'); el.qCard.classList.add('marked-for-review'); }
    else { el.qBadge.textContent = 'Not answered'; el.qBadge.classList.add('badge-unanswered'); }
  }

  function buildNav() {
    el.navGrid.innerHTML = '';
    for (let i = paper.from; i < paper.to; i++) {
      const c = document.createElement('button');
      c.type = 'button';
      c.className = 'nav-cell';
      c.dataset.index = i;
      c.textContent = i - paper.from + 1;
      c.addEventListener('click', () => goTo(i));
      el.navGrid.appendChild(c);
      updateNavCell(i);
    }
  }

  function updateNavCell(i) {
    const c = el.navGrid.querySelector(`[data-index="${i}"]`);
    const q = byIndex.get(i);
    if (!c || !q) return;
    c.classList.remove('answered', 'review', 'visited');
    if (isAnswered(q) && q.marked_for_review) c.classList.add('answered', 'review');
    else if (isAnswered(q)) c.classList.add('answered');
    else if (q.marked_for_review) c.classList.add('review');
    else if (q.visited) c.classList.add('visited');
  }

  function counts() {
    let answered = 0, review = 0;
    for (const q of byIndex.values()) { if (isAnswered(q)) answered++; if (q.marked_for_review) review++; }
    return { answered, review, total: paper.to - paper.from };
  }

  function updateSubmitPanel() {
    const c = counts();
    el.phaseCount.textContent = `${c.answered} / ${c.total} answered`;
    el.submitTitle.textContent = paper.phase === 2 ? 'Finished Paper 2?' : 'Finished Paper 1?';
    el.submitSub.textContent = `Answered ${c.answered} of ${c.total}` + (c.review ? ` · ${c.review} marked for review` : '');
  }

  // ── Navigation (always absolute, always inside this phase) ─────────────
  function goTo(i) {
    if (!paper) return;
    const target = Math.min(paper.to - 1, Math.max(paper.from, i));
    if (target === curIdx) return;
    const leaving = byIndex.get(curIdx);
    if (leaving && leaving.question_type === 'written') flushWritten();
    curIdx = target;
    const q = byIndex.get(curIdx);
    if (q && !q.visited) { q.visited = true; }
    render();
    queueSave(curIdx, { visit: true });
    // bring the question back into view only if the candidate had scrolled past it
    if (el.qCard.getBoundingClientRect().top < 0) el.qCard.scrollIntoView({ block: 'start' });
  }
  el.prev.addEventListener('click', () => goTo(curIdx - 1));
  el.next.addEventListener('click', () => goTo(curIdx + 1));

  // ── Answering ───────────────────────────────────────────────────────────
  function selectOption(index, displayIndex) {
    if (!examActive) return;
    const q = byIndex.get(index);
    if (!q) return;
    q.selected_display_index = displayIndex;
    q.visited = true;
    queueSave(index, { selected_display_index: displayIndex });
    if (index === curIdx) render(); else updateNavCell(index);
  }

  el.review.addEventListener('click', () => {
    if (!examActive) return;
    const q = byIndex.get(curIdx);
    if (!q) return;
    q.marked_for_review = !q.marked_for_review;
    queueSave(curIdx, { marked_for_review: q.marked_for_review });
    render();
  });

  let writtenTimer = null;
  el.written.addEventListener('input', () => {
    const q = byIndex.get(Number(el.written.dataset.index));
    if (!q) return;
    q.written_text = el.written.value;
    clearTimeout(writtenTimer);
    writtenTimer = setTimeout(flushWritten, 700);
  });
  function flushWritten() {
    clearTimeout(writtenTimer);
    const q = byIndex.get(Number(el.written.dataset.index));
    if (q) { queueSave(q.index, { written_text: q.written_text || '' }); updateNavCell(q.index); updateSubmitPanel(); }
  }

  // ════════════════════════════════════════════════════════════════════════
  // Save queue — retries until the server confirms, backed up on the device
  // ════════════════════════════════════════════════════════════════════════
  const queue = new Map();       // index -> { payload, version }
  let inflight = false;
  let failures = 0;
  let flushTimer = null;
  let version = 0;

  const backupKey = () => paper ? `exam_pending_${userId}_${paper.session_id}_${paper.phase}` : null;

  function saveBackup() {
    const k = backupKey();
    if (!k) return;
    try {
      const items = [...queue.entries()].filter(([, v]) => Object.keys(v.payload).some(x => x !== 'visit' && x !== 'index')).map(([i, v]) => [i, v.payload]);
      if (items.length) localStorage.setItem(k, JSON.stringify(items)); else localStorage.removeItem(k);
    } catch (_) {}
  }

  function restoreBackup() {
    const k = backupKey();
    if (!k) return;
    try {
      const items = JSON.parse(localStorage.getItem(k) || '[]');
      items.forEach(([i, p]) => {
        const q = byIndex.get(Number(i));
        if (!q) return;
        if ('selected_display_index' in p) q.selected_display_index = p.selected_display_index;
        if ('marked_for_review' in p) q.marked_for_review = p.marked_for_review;
        if ('written_text' in p) q.written_text = p.written_text;
        queueSave(Number(i), p, false);
      });
    } catch (_) {}
  }

  function queueSave(index, patch, kick = true) {
    const cur = queue.get(index);
    const payload = { ...(cur ? cur.payload : {}), ...patch, index };
    queue.set(index, { payload, version: ++version });
    saveBackup();
    setSaveStatus();
    if (kick) flushQueue();
  }

  function setSaveStatus() {
    const pendingReal = [...queue.values()].some(v => Object.keys(v.payload).some(x => x !== 'visit' && x !== 'index'));
    const s = el.saveStatus;
    s.classList.remove('saving', 'offline', 'saved');
    if (failures >= 2) { s.classList.add('offline'); s.querySelector('.save-text').textContent = 'Offline — will retry'; }
    else if (pendingReal) { s.classList.add('saving'); s.querySelector('.save-text').textContent = 'Saving…'; }
    else { s.classList.add('saved'); s.querySelector('.save-text').textContent = 'All answers saved'; }
    el.offline.style.display = failures >= 2 ? 'block' : 'none';
  }

  /** Sends queued saves one at a time. With wait=true resolves when empty (or timeout). */
  async function flushQueue(wait = false, timeoutMs = 10000) {
    if (!wait) { if (!inflight) runQueue(); return true; }
    const until = Date.now() + timeoutMs;
    if (!inflight) runQueue();
    while (Date.now() < until) {
      if (queue.size === 0 && !inflight) return true;
      await sleep(150);
      if (!inflight && queue.size) runQueue();
    }
    return queue.size === 0;
  }

  async function runQueue() {
    if (inflight) return;
    clearTimeout(flushTimer);
    while (queue.size) {
      const [index, entry] = queue.entries().next().value;
      inflight = true;
      const r = await api('/exam/answer', 'POST', entry.payload, 12000);
      inflight = false;
      if (r.ok) {
        failures = 0;
        const now = queue.get(index);
        if (now && now.version === entry.version) queue.delete(index);
        saveBackup();
        setSaveStatus();
        continue;
      }
      if (r.status === 401) return;
      if (r.status === 409 || r.status === 400) {
        const code = r.data && r.data.code;
        queue.delete(index);
        saveBackup();
        setSaveStatus();
        if (code === 'phase_over' || code === 'closed' || code === 'no_session') {
          queue.clear(); saveBackup(); setSaveStatus();
          const s = await api('/exam/state');
          if (s.ok) handlePhaseOver(s.data);
          return;
        }
        continue;
      }
      // network / server error → back off and retry
      failures++;
      setSaveStatus();
      const delay = Math.min(1000 * Math.pow(2, Math.min(failures - 1, 3)), 8000);
      flushTimer = setTimeout(runQueue, delay);
      return;
    }
    setSaveStatus();
  }

  window.addEventListener('online', () => { failures = Math.min(failures, 1); flushQueue(); });

  // ════════════════════════════════════════════════════════════════════════
  // Submit (separate panel + confirmation)
  // ════════════════════════════════════════════════════════════════════════
  el.submitBtn.addEventListener('click', () => {
    if (!examActive) return;
    if (byIndex.get(curIdx)?.question_type === 'written') flushWritten();
    const c = counts();
    const unanswered = c.total - c.answered;
    const p = paper.phase;
    el.confirmTitle.textContent = p === 2 ? 'Submit Paper 2 and finish the exam?' : 'Submit Paper 1?';
    el.confirmBody.innerHTML = '';
    const lines = [
      ['Answered', c.answered], ['Not answered', unanswered], ['Marked for review', c.review],
    ];
    const tbl = document.createElement('div');
    tbl.className = 'confirm-stats';
    lines.forEach(([k, v]) => { const d = document.createElement('div'); d.innerHTML = `<span></span><strong></strong>`; d.firstChild.textContent = k; d.lastChild.textContent = v; tbl.appendChild(d); });
    el.confirmBody.appendChild(tbl);
    const note = document.createElement('p');
    note.textContent = p === 2
      ? 'After submitting, the examination is complete.'
      : `After submitting, you will be signed out for a ${serverState?.break_minutes || 15}-minute break. Then sign in again to start Paper 2.`;
    if (unanswered > 0) note.textContent = `⚠ You still have ${unanswered} unanswered question(s). ` + note.textContent;
    el.confirmBody.appendChild(note);
    el.confirmCheck.checked = false;
    el.confirmBtn.disabled = true;
    el.confirmBtn.textContent = 'Yes, submit';
    el.confirmModal.classList.add('visible');
  });
  el.confirmCancel.addEventListener('click', () => el.confirmModal.classList.remove('visible'));

  el.confirmBtn.addEventListener('click', async () => {
    if (el.confirmBtn.disabled) return;
    el.confirmBtn.disabled = true;
    el.confirmCancel.disabled = true;
    el.confirmBtn.textContent = 'Saving your answers…';
    const flushed = await flushQueue(true, 12000);
    if (!flushed) {
      el.confirmBtn.textContent = 'Retry submit';
      el.confirmBtn.disabled = false;
      el.confirmCancel.disabled = false;
      const n = document.createElement('p');
      n.className = 'form-error';
      n.style.display = 'block';
      n.textContent = 'Some answers could not reach the server yet (connection problem). Check your internet and press Retry — nothing is lost.';
      el.confirmBody.appendChild(n);
      return;
    }
    el.confirmBtn.textContent = 'Submitting…';
    const url = paper.phase === 2 ? '/exam/submit-phase2' : '/exam/submit-phase1';
    const r = await apiRetry(url, 'POST');
    el.confirmCancel.disabled = false;
    if (r.ok) {
      el.confirmModal.classList.remove('visible');
      handlePhaseOver(r.data.state);
    } else {
      el.confirmBtn.textContent = 'Retry submit';
      el.confirmBtn.disabled = false;
      if (r.status === 409) { const s = await api('/exam/state'); if (s.ok) handlePhaseOver(s.data); }
    }
  });

  // ════════════════════════════════════════════════════════════════════════
  // End of a phase / final screens
  // ════════════════════════════════════════════════════════════════════════
  function teardown() {
    examActive = false;
    clearInterval(tickHandle); clearInterval(syncHandle);
    detachViolationListeners();
    Proctor.stop();
    proctorStarted = false;
    el.liveDot.classList.remove('active');
    el.confirmModal.classList.remove('visible');
    el.warnModal.classList.remove('visible');
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  }

  function handlePhaseOver(s) {
    if (!s) return;
    if (s.status === 'in_progress' && paper && s.active_phase === paper.phase && examActive) return; // still running
    teardown();
    queue.clear();
    try { const k = backupKey(); if (k) localStorage.removeItem(k); } catch (_) {}
    serverState = s;
    if (s.status === 'on_break') return breakScreen(s, true);
    if (s.status === 'in_progress') return location.reload(); // e.g. retake granted
    finalScreen(s);
  }

  function finalScreen(s) {
    teardown();
    localStorage.removeItem('access_token');
    if (s.status === 'disqualified') {
      return terminal('Examination access revoked', 'Your examination was stopped by the examiner. Please contact the examination cell.', { btn: 'Close', href: '/login' });
    }
    if (s.status === 'expired') {
      return terminal('Examination ended', 'The examination was closed by the examiner. Your saved answers have been recorded.\n\nThank you.', { btn: 'Close', href: '/login' });
    }
    terminal('Examination submitted ✓', 'Your Paper 1 and Paper 2 answers have been recorded successfully.\n\nResults will be processed by the examination administrator. Thank you for participating.', { btn: 'Close', href: '/login' });
  }

  let breakTimer = null;
  function breakScreen(s, justSubmitted) {
    const auto = s.phase1_auto_submitted;
    const availableAt = s.phase2_available_at ? Date.parse(s.phase2_available_at) : Date.now();
    const endMs = Date.now() + (s.break_seconds_left || 0) * 1000;
    const title = justSubmitted ? (auto ? 'Paper 1 time is over — answers submitted ✓' : 'Paper 1 submitted ✓') : 'Break time';
    const body = `Your Paper 1 answers are saved.\n\nTake a ${s.break_minutes || 15}-minute break. Paper 2 opens at ${fmtTimeIST(availableAt)}.\n\nYou are now signed out. After the break, sign in again with the same email and password to start Paper 2.`;
    if (justSubmitted) {
      // Sign out for the break (as required); the dashboard shows the countdown after signing in again.
      localStorage.removeItem('access_token');
      terminal(title, body, { btn: 'Go to sign-in', href: '/login' });
    } else {
      terminal(title, `Paper 2 opens at ${fmtTimeIST(availableAt)}. Please wait on the dashboard — the Start Paper 2 button unlocks automatically.`, { btn: 'Back to dashboard', href: '/candidate' });
    }
    el.termCountdown.style.display = 'block';
    clearInterval(breakTimer);
    const upd = () => {
      const left = Math.ceil((endMs - Date.now()) / 1000);
      el.termCountdown.textContent = left > 0 ? `Paper 2 opens in ${fmtClock(left)}` : 'Paper 2 is open — sign in again to continue';
      if (left <= 0) clearInterval(breakTimer);
    };
    upd();
    breakTimer = setInterval(upd, 1000);
  }

  function terminal(title, body, opts = {}) {
    show('terminal');
    el.termTitle.textContent = title;
    el.termBody.textContent = body;
    el.termCountdown.style.display = 'none';
    if (opts.btn) {
      el.termBtn.style.display = 'inline-flex';
      el.termBtn.textContent = opts.btn;
      el.termBtn.onclick = opts.onClick || (() => { location.href = opts.href || '/login'; });
    } else el.termBtn.style.display = 'none';
  }

  // ════════════════════════════════════════════════════════════════════════
  // Monitoring events — warnings only, never submit
  // ════════════════════════════════════════════════════════════════════════
  const VIOLATION_LABELS = {
    tab_switch: 'You left the examination page (switched tab or app).',
    fullscreen_exit: 'You left full-screen mode.',
    copy_attempt: 'Copying examination content is not permitted.',
    right_click: 'Right-click is disabled during the examination.',
    camera_disconnected: 'Your camera stopped. It is being reconnected automatically.',
  };
  let listenersOn = false;
  let enteredAt = 0;

  function attachViolationListeners() {
    if (listenersOn) return;
    listenersOn = true;
    enteredAt = Date.now();
    document.addEventListener('visibilitychange', onVisibility);
    document.addEventListener('fullscreenchange', onFullscreen);
    document.addEventListener('copy', onCopy);
    document.addEventListener('cut', onCopy);
    document.addEventListener('contextmenu', onContext);
    window.addEventListener('beforeunload', onBeforeUnload);
  }
  function detachViolationListeners() {
    listenersOn = false;
    document.removeEventListener('visibilitychange', onVisibility);
    document.removeEventListener('fullscreenchange', onFullscreen);
    document.removeEventListener('copy', onCopy);
    document.removeEventListener('cut', onCopy);
    document.removeEventListener('contextmenu', onContext);
    window.removeEventListener('beforeunload', onBeforeUnload);
  }
  function onVisibility() {
    if (document.hidden && examActive) reportViolation('tab_switch');
    if (!document.hidden && examActive) { syncState(); flushQueue(); }
  }
  function onFullscreen() {
    if (fullscreenUsed && !document.fullscreenElement && examActive) reportViolation('fullscreen_exit');
  }
  function onCopy(e) { if (examActive) { e.preventDefault(); reportViolation('copy_attempt'); } }
  function onContext(e) { if (examActive) { e.preventDefault(); reportViolation('right_click'); } }
  function onBeforeUnload(e) { if (examActive) { e.preventDefault(); e.returnValue = ''; } }

  async function reportViolation(type, silent = false) {
    if (!examActive) return;
    const now = Date.now();
    if (now - enteredAt < 3000) return;          // ignore the moment of entering fullscreen / permission prompts
    if (now - lastViolationAt < 3000) return;    // one event per burst
    lastViolationAt = now;
    if (!silent) showWarning(VIOLATION_LABELS[type] || 'A monitoring event was recorded.');
    const r = await api('/exam/violation', 'POST', { violation_type: type });
    if (r.ok && r.data && typeof r.data.strikes === 'number') updateStrikes(r.data.strikes);
  }

  function updateStrikes(n) {
    n = n || 0;
    el.strikeDots.forEach((d, i) => d.classList.toggle('filled', i < n));
    el.strikeCount.textContent = n;
  }

  function showWarning(msg) {
    el.warnBody.textContent = msg;
    el.warnModal.classList.add('visible');
  }
  el.warnOk.addEventListener('click', async () => {
    el.warnModal.classList.remove('visible');
    if (fullscreenUsed && !document.fullscreenElement) { try { await document.documentElement.requestFullscreen(); } catch (_) {} }
  });

  // ── Go ──────────────────────────────────────────────────────────────────
  boot();
})();
