/**
 * Ph.D Entrance Examination — Exam Interface
 *
 * Flow for Phase 1 entry:
 *   Dashboard → exam.html?phase=1
 *     → Instructions page
 *     → Violation rules page
 *     → System Check (camera live preview)
 *     → Fullscreen + enter exam
 *     → Phase 1 questions (1–50)
 *     → Submit Phase 1 → return to dashboard
 *
 * Flow for Phase 2 entry:
 *   Dashboard → exam.html?phase=2
 *     → System Check (camera live preview)
 *     → Domain selection
 *     → Phase 2 questions (1–50 of domain)
 *     → Submit Phase 2 → completion page
 *
 * VIOLATIONS NEVER AUTO-SUBMIT. Always show warning only.
 * TIMER is authoritative from server. Client ticks locally, syncs every 15s.
 * CANDIDATE NEVER SEES score, correct answers, or result data.
 */
(async () => {
  const token = localStorage.getItem('access_token');
  const role  = localStorage.getItem('role');
  if (!token || role !== 'candidate') {
    window.location.href = '/login';
    return;
  }

  // Determine which phase entry we're doing
  const phaseEntry = sessionStorage.getItem('exam_phase_entry') || 'phase1';

  // ── DOM refs ─────────────────────────────────────────────────────────
  // Screens
  const instructionsScreen  = document.getElementById('instructionsScreen');
  const violationRulesScreen= document.getElementById('violationRulesScreen');
  const systemCheckModal    = document.getElementById('systemCheckModal');
  const domainSelectScreen  = document.getElementById('domainSelectScreen');
  const examScreen          = document.getElementById('examScreen');
  const terminalScreen      = document.getElementById('terminalScreen');

  // Instructions
  const instrAcknowledgeBtn = document.getElementById('instrAcknowledgeBtn');
  const violRulesNextBtn    = document.getElementById('violRulesNextBtn');

  // Syscheck
  const syscheckCameraPreview = document.getElementById('syscheckCameraPreview');
  const syscheckError       = document.getElementById('syscheckError');
  const syschecCTA          = document.getElementById('syschecCTA');
  const syscheckCameraStatus = document.getElementById('syscheckCameraStatus');
  const syscheckRetryBtn    = document.getElementById('syscheckRetryBtn');

  // Exam
  const candidateName       = document.getElementById('candidateName');
  const timerDisplay        = document.getElementById('timerDisplay');
  const timerEndLabel       = document.getElementById('timerEndLabel');
  const questionMeta        = document.getElementById('questionMeta');
  const questionStatusBadge = document.getElementById('questionStatusBadge');
  const questionCard        = document.getElementById('questionCard');
  const questionText        = document.getElementById('questionText');
  const mcqOptions          = document.getElementById('mcqOptions');
  const writtenWrap         = document.getElementById('writtenAnswerWrap');
  const writtenInput        = document.getElementById('writtenAnswerInput');
  const prevBtn             = document.getElementById('prevBtn');
  const nextBtn             = document.getElementById('nextBtn');
  const reviewBtn           = document.getElementById('reviewBtn');
  const submitPhase1Btn     = document.getElementById('submitPhase1Btn');
  const submitPhase2Btn     = document.getElementById('submitPhase2Btn');
  const navGrid1            = document.getElementById('navGrid1');
  const navGrid2            = document.getElementById('navGrid2');
  const navPart2Label       = document.getElementById('navPart2Label');
  const phase1Count         = document.getElementById('phase1Count');
  const phase2Count         = document.getElementById('phase2Count');
  const phaseLabel          = document.getElementById('phaseLabel');
  const strikeDotEls        = document.querySelectorAll('.strike-dot');
  const strikeCountEl       = document.getElementById('strikeCount');

  // Webcam pip
  const webcamPip   = document.getElementById('webcamPip');
  const webcamVideo = document.getElementById('webcamVideo');
  const webcamStatus= document.getElementById('webcamStatus');

  // Domain select
  const domainSelect        = document.getElementById('domainSelect');
  const confirmDomainBtn    = document.getElementById('confirmDomainBtn');
  const domainError         = document.getElementById('domainError');
  const domainConfirmCard   = document.getElementById('domainConfirmCard');
  const domainSelectedName  = document.getElementById('domainSelectedName');
  const confirmDomainFinalBtn = document.getElementById('confirmDomainFinalBtn');
  const cancelDomainBtn     = document.getElementById('cancelDomainBtn');

  // Modals
  const warningModal        = document.getElementById('warningModal');
  const warningModalBody    = document.getElementById('warningModalBody');
  const warningModalOkBtn   = document.getElementById('warningModalOkBtn');
  const submitConfirmModal  = document.getElementById('submitConfirmModal');
  const submitConfirmBody   = document.getElementById('submitConfirmBody');
  const submitConfirmBtn    = document.getElementById('submitConfirmBtn');
  const submitCancelBtn     = document.getElementById('submitCancelBtn');

  // Terminal
  const terminalTitle = document.getElementById('terminalTitle');
  const terminalBody  = document.getElementById('terminalBody');

  // ── State ────────────────────────────────────────────────────────────
  let current          = null;
  let navState         = [];
  let remainingSeconds = 0;
  let scheduledEndAt   = null;   // ISO string from server
  let examActive       = false;
  let tickHandle       = null;
  let syncHandle       = null;
  let writtenSaveTimer = null;
  let lastViolationAt  = 0;
  let phase1Count_     = 50;
  let currentPhase     = 1;
  let previewStream    = null;   // stream kept open from system check

  const VIOLATION_LABELS = {
    tab_switch:          'You switched away from the examination tab.',
    fullscreen_exit:     'You left full-screen mode.',
    copy_attempt:        'Copying examination content is not permitted.',
    right_click:         'The right-click menu is disabled during the examination.',
    devtools_suspected:  'Developer tools access was detected.',
  };

  candidateName.textContent = localStorage.getItem('full_name') || '';

  // ── API helper ────────────────────────────────────────────────────────
  async function api(path, method = 'GET', body = null) {
    try {
      const res = await fetch(path, {
        method,
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (res.status === 401) {
        localStorage.removeItem('access_token');
        localStorage.removeItem('role');
        window.location.href = '/login';
        return { ok: false, status: 401, data: null };
      }
      let data = null;
      try { data = await res.json(); } catch (_) {}
      return { ok: res.ok, status: res.status, data };
    } catch {
      return { ok: false, status: 0, data: { detail: 'Network error' } };
    }
  }

  // ── Show helpers ──────────────────────────────────────────────────────
  function showScreen(el) {
    [instructionsScreen, violationRulesScreen, systemCheckModal,
     domainSelectScreen, examScreen, terminalScreen].forEach(s => {
      if (s) s.style.display = 'none';
    });
    if (el) el.style.display = 'flex';
  }

  // ════════════════════════════════════════════════════════════════════
  // STEP 1 — Instructions
  // ════════════════════════════════════════════════════════════════════

  function showInstructions() {
    showScreen(instructionsScreen);
    instrAcknowledgeBtn?.addEventListener('click', showViolationRules, { once: true });
  }

  // ════════════════════════════════════════════════════════════════════
  // STEP 2 — Violation Rules
  // ════════════════════════════════════════════════════════════════════

  function showViolationRules() {
    showScreen(violationRulesScreen);
    violRulesNextBtn?.addEventListener('click', showSystemCheck, { once: true });
  }

  // ════════════════════════════════════════════════════════════════════
  // STEP 3 — System Check with LIVE CAMERA PREVIEW
  // ════════════════════════════════════════════════════════════════════

  let cameraPassed = false;

  async function showSystemCheck() {
    showScreen(systemCheckModal);
    await runSystemCheck();
  }

  async function runSystemCheck() {
    cameraPassed = false;
    syschecCTA.disabled = true;
    if (syscheckCameraStatus) syscheckCameraStatus.textContent = 'Checking…';
    syscheckError.textContent = '';
    syscheckError.style.display = 'none';
    if (syscheckRetryBtn) syscheckRetryBtn.style.display = 'none';

    try {
      // Open camera and display live preview in the modal
      previewStream = await Proctor.openForPreview(syscheckCameraPreview);
      cameraPassed = true;
      if (syscheckCameraStatus) {
        syscheckCameraStatus.textContent = '✓ Camera active — live preview';
        syscheckCameraStatus.style.color = '#4ade80';
      }
      syschecCTA.disabled = false;
    } catch (err) {
      cameraPassed = false;
      const msg = _cameraErrMsg(err);
      if (syscheckCameraStatus) {
        syscheckCameraStatus.textContent = '✗ ' + msg;
        syscheckCameraStatus.style.color = '#f87171';
      }
      syscheckError.textContent = msg;
      syscheckError.style.display = 'block';
      if (syscheckRetryBtn) syscheckRetryBtn.style.display = 'inline-flex';
    }
  }

  function _cameraErrMsg(err) {
    if (!err) return 'Camera access failed.';
    const n = err.name || '';
    if (n === 'NotAllowedError' || n === 'PermissionDeniedError')
      return 'Camera permission denied. Click the camera icon in your browser address bar and allow access.';
    if (n === 'NotFoundError' || n === 'DevicesNotFoundError')
      return 'No camera device detected. Please connect a camera and try again.';
    if (n === 'NotReadableError' || n === 'TrackStartError')
      return 'Camera is in use by another application. Close other video apps and try again.';
    return `Camera access failed: ${err.message || n}`;
  }

  syscheckRetryBtn?.addEventListener('click', runSystemCheck);

  syschecCTA?.addEventListener('click', async () => {
    if (!cameraPassed) return;
    syschecCTA.disabled = true;
    syschecCTA.textContent = 'Entering fullscreen…';

    try {
      await document.documentElement.requestFullscreen();
    } catch {
      syscheckError.textContent = 'Fullscreen permission is required. Please allow it and try again.';
      syscheckError.style.display = 'block';
      syschecCTA.disabled = false;
      syschecCTA.textContent = 'Enter Exam (Fullscreen)';
      return;
    }

    // Check server exam state before proceeding
    const stateRes = await api('/exam/state');
    if (!stateRes.ok) {
      syscheckError.textContent = 'Could not reach examination server.';
      syscheckError.style.display = 'block';
      syschecCTA.disabled = false;
      syschecCTA.textContent = 'Enter Exam (Fullscreen)';
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      return;
    }

    const state = stateRes.data;

    // If already in terminal state, go there
    if (['completed', 'expired', 'disqualified'].includes(state.status)) {
      endExam(state.status);
      return;
    }

    if (phaseEntry === 'phase2') {
      // Phase 2 entry: check if domain already selected (resume) or needs selection
      if (state.domain_selected && state.total_questions > phase1Count_) {
        await activateExam(state);
      } else if (state.phase1_submitted) {
        showDomainSelection();
      } else {
        syscheckError.textContent = 'Phase 1 must be submitted before starting Phase 2.';
        syscheckError.style.display = 'block';
        syschecCTA.disabled = false;
        syschecCTA.textContent = 'Enter Exam (Fullscreen)';
        if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      }
    } else {
      // Phase 1 entry
      await startOrResumeExam(state);
    }
  });

  // ════════════════════════════════════════════════════════════════════
  // Phase 1 start / resume
  // ════════════════════════════════════════════════════════════════════

  async function startOrResumeExam(state) {
    if (state.phase1_submitted) {
      window.location.href = '/dashboard';
      return;
    }

    if (state.status === 'in_progress' && !state.phase1_submitted) {
      // Resume Phase 1
      await activateExam(state);
    } else if (state.status === 'not_started' || !state.status) {
      // Start new exam
      const res = await api('/exam/start', 'POST');
      if (!res.ok) {
        if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
        showScreen(systemCheckModal);
        syscheckError.textContent = res.data?.detail || 'Could not start the exam. Please wait for the administrator.';
        syscheckError.style.display = 'block';
        syschecCTA.disabled = false;
        syschecCTA.textContent = 'Enter Exam (Fullscreen)';
        return;
      }
      await activateExam(null, res.data);
    } else {
      await activateExam(state);
    }
  }

  // ════════════════════════════════════════════════════════════════════
  // Activate exam UI
  // ════════════════════════════════════════════════════════════════════

  async function activateExam(state, firstQuestion = null) {
    showScreen(examScreen);
    webcamPip.style.display = 'block';
    examActive = true;
    currentPhase = phaseEntry === 'phase2' ? 2 : 1;

    // Init proctor with the stream we already opened in system check
    await Proctor.init(webcamVideo, webcamStatus, previewStream);
    previewStream = null;  // handed off — don't release

    const started = await Proctor.start();
    if (started) {
      const liveDot = document.getElementById('webcamLiveDot');
      if (liveDot) liveDot.classList.add('active');
    }

    // Listen for server push commands
    const ws = Proctor.getWebSocket();
    if (ws) {
      ws.addEventListener('message', (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          handleServerCommand(msg);
        } catch (_) {}
      });
    }

    window.addEventListener('beforeunload', beforeUnloadHandler);
    attachViolationListeners();

    // Refresh full state
    await refreshState();

    // Show first question
    if (firstQuestion) {
      renderQuestion(firstQuestion);
    } else {
      const idx = state?.current_index ?? 0;
      const qRes = await api(`/exam/question/${idx}`);
      if (qRes.ok) renderQuestion(qRes.data);
    }

    startTimer();
    updatePhaseUI();
  }

  function handleServerCommand(msg) {
    if (msg.type === 'exam_force_end') {
      endExam('expired');
    } else if (msg.type === 'exam_disqualified') {
      endExam('disqualified');
    } else if (msg.type === 'retake_granted') {
      // Admin granted retake — exit fullscreen, go back to system check
      examActive = false;
      Proctor.stop();
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      clearInterval(tickHandle);
      clearInterval(syncHandle);
      detachViolationListeners();
      showScreen(systemCheckModal);
      syscheckError.textContent = '';
      syscheckError.style.display = 'none';
      syschecCTA.disabled = !cameraPassed;
      syschecCTA.textContent = 'Re-Enter Exam (Fullscreen)';
      runSystemCheck();
    }
  }

  function updatePhaseUI() {
    if (phaseLabel) {
      phaseLabel.textContent = currentPhase === 1
        ? 'Phase 1 — Research Methodology'
        : 'Phase 2 — Domain Specific';
    }
    if (submitPhase1Btn) submitPhase1Btn.style.display = currentPhase === 1 ? 'inline-flex' : 'none';
    if (submitPhase2Btn) submitPhase2Btn.style.display = currentPhase === 2 ? 'inline-flex' : 'none';
  }

  // ════════════════════════════════════════════════════════════════════
  // Domain Selection (Phase 2)
  // ════════════════════════════════════════════════════════════════════

  function showDomainSelection() {
    showScreen(domainSelectScreen);
    if (domainConfirmCard) domainConfirmCard.style.display = 'none';
  }

  confirmDomainBtn?.addEventListener('click', () => {
    const val = domainSelect?.value;
    if (!val) {
      if (domainError) { domainError.textContent = 'Please select a domain.'; domainError.style.display = 'block'; }
      return;
    }
    if (domainError) domainError.style.display = 'none';
    if (domainSelectedName) domainSelectedName.textContent = val;
    if (domainConfirmCard) domainConfirmCard.style.display = 'block';
    confirmDomainBtn.style.display = 'none';
  });

  cancelDomainBtn?.addEventListener('click', () => {
    if (domainConfirmCard) domainConfirmCard.style.display = 'none';
    if (confirmDomainBtn) confirmDomainBtn.style.display = 'inline-flex';
  });

  confirmDomainFinalBtn?.addEventListener('click', async () => {
    const domain = domainSelect?.value;
    if (!domain) return;
    confirmDomainFinalBtn.disabled = true;
    confirmDomainFinalBtn.textContent = 'Loading Phase 2 questions…';

    const res = await api('/exam/select-domain', 'POST', { domain });
    if (!res.ok) {
      confirmDomainFinalBtn.disabled = false;
      confirmDomainFinalBtn.textContent = 'Confirm Domain & Begin Phase 2';
      if (domainError) { domainError.textContent = res.data?.detail || 'Failed to load domain questions.'; domainError.style.display = 'block'; }
      return;
    }

    currentPhase = 2;
    await activateExam(null);
  });

  // ════════════════════════════════════════════════════════════════════
  // Question rendering
  // ════════════════════════════════════════════════════════════════════

  function renderQuestion(q) {
    current = q;
    questionMeta.textContent = `Question ${q.index + 1} of ${q.total}`;

    const navItem = navState.find(n => n.index === q.index);
    updateQuestionStatusBadge(q, navItem);
    updateQuestionCardClass(q, navItem);

    questionText.textContent = q.question_text;
    prevBtn.disabled = q.index === 0;
    nextBtn.disabled = q.index === q.total - 1;
    reviewBtn.textContent = q.marked_for_review ? 'Unmark Review' : 'Mark for Review';
    reviewBtn.className = 'btn' + (q.marked_for_review ? ' review-active' : '');

    if (q.question_type === 'mcq') {
      writtenWrap.style.display = 'none';
      mcqOptions.style.display = 'flex';
      mcqOptions.innerHTML = '';
      q.options.forEach((opt, idx) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'option-btn' + (q.selected_display_index === idx ? ' selected' : '');
        btn.dataset.idx = idx;
        const letter = String.fromCharCode(65 + idx);
        btn.innerHTML = `<span class="option-letter">${letter}</span><span class="option-text"></span>`;
        btn.querySelector('.option-text').textContent = opt;
        btn.addEventListener('click', () => selectOption(idx));
        mcqOptions.appendChild(btn);
      });
    } else {
      mcqOptions.style.display = 'none';
      writtenWrap.style.display = 'block';
      writtenInput.value = q.written_text || '';
    }

    highlightNavCurrent(q.index);
  }

  function updateQuestionStatusBadge(q, navItem) {
    questionStatusBadge.className = 'question-status-badge';
    if (navItem?.answered && navItem?.marked_for_review) {
      questionStatusBadge.textContent = '🟩 Answered · 🟧 Marked for review';
      questionStatusBadge.classList.add('badge-answered');
    } else if (navItem?.answered) {
      questionStatusBadge.textContent = '🟩 Answered';
      questionStatusBadge.classList.add('badge-answered');
    } else if (navItem?.marked_for_review) {
      questionStatusBadge.textContent = '🟧 Marked for review';
      questionStatusBadge.classList.add('badge-review');
    } else {
      questionStatusBadge.textContent = '⚪ Not answered';
      questionStatusBadge.classList.add('badge-unanswered');
    }
  }

  function updateQuestionCardClass(q, navItem) {
    questionCard.className = 'question-card';
    if (navItem?.answered && navItem?.marked_for_review) questionCard.classList.add('answered', 'marked-for-review');
    else if (navItem?.answered) questionCard.classList.add('answered');
    else if (navItem?.marked_for_review) questionCard.classList.add('marked-for-review');
    else if (navItem?.visited) questionCard.classList.add('visited');
  }

  function highlightNavCurrent(index) {
    document.querySelectorAll('.nav-cell').forEach(cell =>
      cell.classList.toggle('current', Number(cell.dataset.index) === index)
    );
  }

  function buildNavGrids() {
    if (!navGrid1) return;
    navGrid1.innerHTML = '';
    if (navGrid2) navGrid2.innerHTML = '';

    const phase1Items = navState.filter(n => n.phase === 1);
    const phase2Items = navState.filter(n => n.phase === 2);

    if (phase1Count)
      phase1Count.textContent = `${phase1Items.filter(n => n.answered).length} / ${phase1Items.length} answered`;

    phase1Items.forEach(n => navGrid1.appendChild(makeNavCell(n)));

    if (phase2Items.length > 0 && navGrid2) {
      if (navPart2Label) navPart2Label.style.display = 'block';
      navGrid2.style.display = 'grid';
      if (phase2Count)
        phase2Count.textContent = `${phase2Items.filter(n => n.answered).length} / ${phase2Items.length} answered`;
      phase2Items.forEach(n => navGrid2.appendChild(makeNavCell(n)));
    }
  }

  function makeNavCell(n) {
    const cell = document.createElement('div');
    cell.className = 'nav-cell';
    cell.dataset.index = n.index;
    cell.textContent = n.index + 1;
    if (n.answered && n.marked_for_review) cell.classList.add('answered', 'review');
    else if (n.answered) cell.classList.add('answered');
    else if (n.marked_for_review) cell.classList.add('review');
    else if (n.visited) cell.classList.add('visited');
    if (current && current.index === n.index) cell.classList.add('current');
    cell.addEventListener('click', () => jumpTo(n.index));
    return cell;
  }

  // ════════════════════════════════════════════════════════════════════
  // Answer save
  // ════════════════════════════════════════════════════════════════════

  async function selectOption(displayIndex) {
    document.querySelectorAll('.option-btn').forEach((btn, i) =>
      btn.classList.toggle('selected', i === displayIndex)
    );
    current.selected_display_index = displayIndex;
    await api('/exam/answer', 'POST', { index: current.index, selected_display_index: displayIndex });
    await refreshState();
  }

  writtenInput?.addEventListener('input', () => {
    clearTimeout(writtenSaveTimer);
    writtenSaveTimer = setTimeout(async () => {
      await api('/exam/answer', 'POST', { index: current.index, written_text: writtenInput.value });
      await refreshState();
    }, 800);
  });

  reviewBtn?.addEventListener('click', async () => {
    const newVal = !current.marked_for_review;
    current.marked_for_review = newVal;
    reviewBtn.textContent = newVal ? 'Unmark Review' : 'Mark for Review';
    reviewBtn.className = 'btn' + (newVal ? ' review-active' : '');
    await api('/exam/answer', 'POST', { index: current.index, marked_for_review: newVal });
    await refreshState();
  });

  // ════════════════════════════════════════════════════════════════════
  // Navigation
  // ════════════════════════════════════════════════════════════════════

  async function navigate(direction) {
    const res = await api('/exam/navigate', 'POST', { direction });
    if (res.ok) renderQuestion(res.data);
    await refreshState();
  }

  async function jumpTo(index) {
    if (current && index === current.index) return;
    const res = await api('/exam/navigate', 'POST', { jump_to: index });
    if (res.ok) renderQuestion(res.data);
    await refreshState();
  }

  prevBtn?.addEventListener('click', () => navigate('prev'));
  nextBtn?.addEventListener('click', () => navigate('next'));

  // ════════════════════════════════════════════════════════════════════
  // Phase submission
  // ════════════════════════════════════════════════════════════════════

  submitPhase1Btn?.addEventListener('click', () => {
    const answered   = navState.filter(n => n.phase === 1 && n.answered).length;
    const total      = navState.filter(n => n.phase === 1).length;
    const unanswered = total - answered;
    submitConfirmBody.innerHTML = `
      <strong>Phase 1 — Research Methodology</strong><br><br>
      Answered: <strong>${answered}</strong><br>
      Unanswered: <strong>${unanswered}</strong><br>
      Marked for Review: <strong>${navState.filter(n => n.phase === 1 && n.marked_for_review).length}</strong><br><br>
      ${unanswered > 0
        ? `<span style="color:#fbbf24">⚠ You have ${unanswered} unanswered question(s).</span><br><br>`
        : ''}
      Once submitted, Phase 1 cannot be changed. You will return to the dashboard to start Phase 2.`;
    submitConfirmModal.classList.add('visible');
    submitConfirmBtn.onclick = async () => {
      submitConfirmModal.classList.remove('visible');
      const res = await api('/exam/submit-phase1', 'POST');
      if (res.ok) {
        // Phase 1 submitted — exit fullscreen, go to dashboard
        examActive = false;
        clearInterval(tickHandle);
        clearInterval(syncHandle);
        Proctor.stop();
        detachViolationListeners();
        window.removeEventListener('beforeunload', beforeUnloadHandler);
        if (document.fullscreenElement) await document.exitFullscreen().catch(() => {});
        // Show a brief "Phase 1 submitted" page then redirect
        showScreen(terminalScreen);
        terminalTitle.textContent = 'Phase 1 Submitted';
        terminalBody.innerHTML = `
          Your Phase 1 responses have been recorded successfully.<br><br>
          You will be returned to the dashboard where you can begin Phase 2 when ready.<br><br>
          <strong style="color:#7a7f9a; font-size:0.85rem">Results will be processed by the examination administrator.</strong>`;
        setTimeout(() => { window.location.href = '/candidate'; }, 4000);
      }
    };
  });

  submitPhase2Btn?.addEventListener('click', () => {
    const answered   = navState.filter(n => n.phase === 2 && n.answered).length;
    const total      = navState.filter(n => n.phase === 2).length;
    const unanswered = total - answered;
    submitConfirmBody.innerHTML = `
      <strong>Phase 2 — Domain Specific</strong><br><br>
      Answered: <strong>${answered}</strong><br>
      Unanswered: <strong>${unanswered}</strong><br>
      Marked for Review: <strong>${navState.filter(n => n.phase === 2 && n.marked_for_review).length}</strong><br><br>
      ${unanswered > 0
        ? `<span style="color:#fbbf24">⚠ You have ${unanswered} unanswered question(s).</span><br><br>`
        : ''}
      Once submitted, the examination is complete and cannot be reopened.`;
    submitConfirmModal.classList.add('visible');
    submitConfirmBtn.onclick = async () => {
      submitConfirmModal.classList.remove('visible');
      const res = await api('/exam/submit-phase2', 'POST');
      if (res.ok) endExam('completed');
    };
  });

  submitCancelBtn?.addEventListener('click', () => submitConfirmModal.classList.remove('visible'));

  // ════════════════════════════════════════════════════════════════════
  // State sync / timer
  // ════════════════════════════════════════════════════════════════════

  async function refreshState() {
    const res = await api('/exam/state');
    if (!res.ok) return;
    const s = res.data;
    navState     = s.nav;
    phase1Count_ = s.phase1_count || 50;
    currentPhase = s.current_phase || 1;

    // Sync timer from server
    if (s.time_remaining_seconds !== undefined) {
      remainingSeconds = s.time_remaining_seconds;
    }
    if (s.scheduled_end_at) {
      scheduledEndAt = s.scheduled_end_at;
    }

    updateStrikeDots(s.strikes);
    buildNavGrids();
    updateTimerDisplay();
    updatePhaseUI();

    if (current) {
      const navItem = navState.find(n => n.index === current.index);
      updateQuestionStatusBadge(current, navItem);
      updateQuestionCardClass(current, navItem);
    }

    // Terminal checks
    if (s.status === 'expired')      { endExam('expired'); return; }
    if (s.status === 'disqualified') { endExam('disqualified'); return; }
    if (s.status === 'completed')    { endExam('completed'); return; }
  }

  function updateStrikeDots(count) {
    strikeDotEls.forEach((dot, i) => dot.classList.toggle('filled', i < count));
    if (strikeCountEl) strikeCountEl.textContent = count;
  }

  function updateTimerDisplay() {
    // Prefer server-computed remaining seconds (synced every 15s)
    // Between syncs, count down locally
    const m = Math.floor(remainingSeconds / 60).toString().padStart(2, '0');
    const s = (remainingSeconds % 60).toString().padStart(2, '0');
    timerDisplay.textContent = `${m}:${s}`;
    timerDisplay.classList.toggle('low',      remainingSeconds <= 900 && remainingSeconds > 300);
    timerDisplay.classList.toggle('critical', remainingSeconds <= 300);
  }

  function startTimer() {
    if (tickHandle) clearInterval(tickHandle);
    if (syncHandle) clearInterval(syncHandle);

    tickHandle = setInterval(() => {
      if (remainingSeconds > 0) {
        remainingSeconds--;
        updateTimerDisplay();
      }
      if (remainingSeconds === 0 && examActive) {
        endExam('expired');
      }
    }, 1000);

    // Sync with server every 15 seconds to prevent clock drift
    syncHandle = setInterval(refreshState, 15000);
  }

  // ════════════════════════════════════════════════════════════════════
  // Violation handling — ALWAYS warn, NEVER auto-submit
  // ════════════════════════════════════════════════════════════════════

  function attachViolationListeners() {
    document.addEventListener('visibilitychange', onVisibilityChange);
    document.addEventListener('fullscreenchange', onFullscreenChange);
    document.addEventListener('copy',             onCopyAttempt);
    document.addEventListener('contextmenu',      onContextMenu);
  }

  function detachViolationListeners() {
    document.removeEventListener('visibilitychange', onVisibilityChange);
    document.removeEventListener('fullscreenchange', onFullscreenChange);
    document.removeEventListener('copy',             onCopyAttempt);
    document.removeEventListener('contextmenu',      onContextMenu);
    window.removeEventListener('beforeunload',       beforeUnloadHandler);
  }

  function onVisibilityChange() { if (document.hidden && examActive) reportViolation('tab_switch'); }
  function onFullscreenChange() { if (!document.fullscreenElement && examActive) reportViolation('fullscreen_exit'); }
  function onCopyAttempt(e)    { if (examActive) { e.preventDefault(); reportViolation('copy_attempt'); } }
  function onContextMenu(e)    { if (examActive) { e.preventDefault(); reportViolation('right_click'); } }

  async function reportViolation(type) {
    if (!examActive) return;
    const now = Date.now();
    if (now - lastViolationAt < 2500) return;  // debounce
    lastViolationAt = now;

    const res = await api('/exam/violation', 'POST', { violation_type: type });
    if (!res.ok) return;
    const { strikes, action } = res.data;
    updateStrikeDots(strikes);

    // ALWAYS show warning — never auto-disqualify
    showWarningModal(VIOLATION_LABELS[type] || 'An examination monitoring event was recorded.');
  }

  function showWarningModal(message) {
    if (warningModalBody) warningModalBody.textContent = message;
    warningModal?.classList.add('visible');
  }

  warningModalOkBtn?.addEventListener('click', async () => {
    warningModal?.classList.remove('visible');
    // If we exited fullscreen, try to re-enter
    if (!document.fullscreenElement) {
      try { await document.documentElement.requestFullscreen(); } catch (_) {}
    }
  });

  function beforeUnloadHandler(e) {
    if (examActive) { e.preventDefault(); e.returnValue = ''; }
  }

  // ════════════════════════════════════════════════════════════════════
  // Terminal states — no scores shown to candidate
  // ════════════════════════════════════════════════════════════════════

  function endExam(reason) {
    if (!examActive && terminalScreen.style.display === 'flex') return;
    examActive = false;

    clearInterval(tickHandle);
    clearInterval(syncHandle);
    Proctor.stop();
    detachViolationListeners();
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});

    showScreen(terminalScreen);

    const copy = {
      completed: {
        title: 'Examination Submitted',
        body: 'Your responses have been successfully recorded. The examination has ended for you.\n\nResults will be processed by the examination administrator.\n\nThank you for participating.',
      },
      expired: {
        title: 'Examination Ended',
        body: 'The scheduled examination time has ended. Your responses have been recorded automatically.\n\nResults will be processed by the examination administrator.',
      },
      disqualified: {
        title: 'Examination Access Revoked',
        body: 'Your examination access has been revoked by the administrator. Please contact the examination cell for further information.',
      },
    }[reason] || { title: 'Examination Ended', body: 'Your responses have been recorded.' };

    terminalTitle.textContent = copy.title;
    terminalBody.textContent  = copy.body;
    localStorage.removeItem('access_token');
  }

  // ════════════════════════════════════════════════════════════════════
  // ENTRY POINT
  // ════════════════════════════════════════════════════════════════════

  // Check for terminal session first (avoid showing instructions to a completed candidate)
  const initState = await api('/exam/state');
  if (initState.ok) {
    const s = initState.data;
    if (['completed', 'expired', 'disqualified'].includes(s.status)) {
      endExam(s.status);
      return;
    }
    if (s.scheduled_end_at) scheduledEndAt = s.scheduled_end_at;
    if (s.time_remaining_seconds !== undefined) remainingSeconds = s.time_remaining_seconds;
    phase1Count_ = s.phase1_count || 50;

    // Update timer end label
    if (timerEndLabel && s.scheduled_end_at) {
      const d = new Date(s.scheduled_end_at.endsWith('Z') ? s.scheduled_end_at : s.scheduled_end_at + 'Z');
      timerEndLabel.textContent = 'Ends ' + d.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: true });
    }
  }

  // Start the flow depending on phase entry
  if (phaseEntry === 'phase2') {
    // Phase 2: skip instructions, go straight to system check
    showSystemCheck();
  } else {
    // Phase 1: show instructions first
    showInstructions();
  }
})();
