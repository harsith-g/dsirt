(() => {
  const token = localStorage.getItem('access_token');
  const role  = localStorage.getItem('role');
  if (!token || role !== 'admin') {
    window.location.href = '/login';
    return;
  }

  const authHeaders = { Authorization: `Bearer ${token}` };
  const jsonHeaders = { ...authHeaders, 'Content-Type': 'application/json' };

  // ── Logout ──────────────────────────────────────────────────────────
  document.getElementById('logoutAdminBtn')?.addEventListener('click', () => {
    localStorage.clear(); window.location.href = '/login';
  });

  function escapeHtml(str) {
    const d = document.createElement('div');
    d.textContent = str || '';
    return d.innerHTML;
  }

  async function api(path, options = {}) {
    const res = await fetch(path, options);
    if (res.status === 401) { window.location.href = '/login'; throw new Error('unauthorized'); }
    let data = null;
    if (res.status !== 204) {
      try { data = await res.json(); } catch (_) { data = null; }
    }
    if (!res.ok) throw new Error((data && data.detail) || `Request failed (${res.status})`);
    return data;
  }

  // Authenticated file download. Fetches with the Authorization header (the
  // JWT never goes in the URL), then hands the blob to the browser.
  async function downloadFile(path, fallbackName, btn) {
    const label = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = 'Preparing…'; }
    try {
      const res = await fetch(path, { headers: authHeaders });
      if (res.status === 401) { window.location.href = '/login'; return; }
      if (!res.ok) {
        let detail = `Download failed (${res.status})`;
        try { const d = await res.json(); if (d && d.detail) detail = d.detail; } catch (_) {}
        alert(detail);
        return;
      }
      const cd = res.headers.get('Content-Disposition') || '';
      const m = /filename="?([^";]+)"?/i.exec(cd);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = m ? m[1] : fallbackName;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (err) {
      alert('Download failed: ' + err.message);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = label; }
    }
  }

  function flash(el, msg, ok = false) {
    if (!el) return;
    el.textContent = msg;
    el.style.borderColor = ok ? 'var(--success)' : 'var(--danger)';
    el.style.color = ok ? '#a4e0bf' : '#e6a49c';
    el.style.background = ok ? 'var(--success-soft)' : 'var(--danger-soft)';
    el.classList.add('visible');
    setTimeout(() => el.classList.remove('visible'), 6000);
  }

  function fmtIST(utcStr) {
    if (!utcStr) return '—';
    const d = new Date(utcStr.endsWith('Z') ? utcStr : utcStr + 'Z');
    return d.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit', hour12:true });
  }

  document.getElementById('adminShell').style.display = 'block';

  // ── Tabs ─────────────────────────────────────────────────────────────
  const tabButtons = document.querySelectorAll('.tab-btn');
  const panels = {
    monitor:   document.getElementById('panel-monitor'),
    schedule:  document.getElementById('panel-schedule'),
    users:     document.getElementById('panel-users'),
    questions: document.getElementById('panel-questions'),
  };
  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      tabButtons.forEach(b => b.classList.remove('active'));
      Object.values(panels).forEach(p => p?.classList.remove('active'));
      btn.classList.add('active');
      panels[btn.dataset.tab]?.classList.add('active');
      if (btn.dataset.tab === 'users')     loadUsers();
      if (btn.dataset.tab === 'questions') loadQuestions();
      if (btn.dataset.tab === 'schedule')  loadSchedule();
    });
  });

  // ── Credential modal ─────────────────────────────────────────────────
  const credModal    = document.getElementById('credModal');
  const credModalTitle= document.getElementById('credModalTitle');
  const credModalSubtitle = document.getElementById('credModalSubtitle');
  const credEmail    = document.getElementById('credEmail');
  const credPassword = document.getElementById('credPassword');
  document.getElementById('credModalClose').addEventListener('click', () => credModal.classList.remove('visible'));
  function showCredModal(title, email, password, subtitle = '') {
    credModalTitle.textContent    = title;
    credModalSubtitle.textContent = subtitle || 'Share this information with the user directly.';
    credEmail.textContent    = email;
    credPassword.textContent = password;
    credModal.classList.add('visible');
  }

  // ── Result modal ─────────────────────────────────────────────────────
  const resultModal     = document.getElementById('resultModal');
  const resultModalTitle= document.getElementById('resultModalTitle');
  const resultModalBody = document.getElementById('resultModalBody');
  const resultPdfBtn    = document.getElementById('resultPdfBtn');
  const resultModalClose= document.getElementById('resultModalClose');
  let resultUserId = null;

  resultModalClose.addEventListener('click', () => resultModal.classList.remove('visible'));
  resultPdfBtn.addEventListener('click', async () => {
    if (!resultUserId) return;
    // Opens the printable score card in a new tab (use Print → Save as PDF)
    const w = window.open('', '_blank');
    try {
      const res = await fetch(`/admin/candidates/${resultUserId}/result/pdf`, { headers: authHeaders });
      const html = await res.text();
      if (w) { w.document.open(); w.document.write(html); w.document.close(); }
    } catch (err) { if (w) w.close(); alert('Failed: ' + err.message); }
  });

  async function showResultModal(userId, candidateName) {
    resultUserId = userId;
    resultModalTitle.textContent = `Result — ${candidateName}`;
    resultModalBody.innerHTML = '<div style="padding:2rem; text-align:center; color:var(--text-faint);">Loading…</div>';
    resultModal.classList.add('visible');

    try {
      const r = await api(`/admin/candidates/${userId}/result`, { headers: authHeaders });
      resultModalBody.innerHTML = buildResultHtml(r);
    } catch (err) {
      resultModalBody.innerHTML = `<p style="color:#f87171;">${err.message}</p>`;
    }
  }

  function buildResultHtml(r) {
    function phaseHtml(p, label) {
      if (!p) return '';
      return `
        <div style="margin-bottom:1.5rem;">
          <div style="font-weight:700; font-size:0.9rem; color:#a5b4fc; margin-bottom:8px;">${label}</div>
          <div style="display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-bottom:10px;">
            ${statTile('Attempted', p.attempted)}
            ${statTile('Correct', p.correct, '#4ade80')}
            ${statTile('Incorrect', p.incorrect, '#f87171')}
            ${statTile('Unanswered', p.unanswered)}
          </div>
          <div style="font-size:0.88rem; color:var(--text-faint); margin-bottom:6px;">
            Score: <strong style="color:#e8eaf0;">${p.score} / ${p.max_score}</strong>
            &nbsp;·&nbsp; Accuracy: <strong style="color:#e8eaf0;">${p.accuracy != null ? p.accuracy.toFixed(1) + '%' : '—'}</strong>
            ${p.domain ? `&nbsp;·&nbsp; Domain: <strong style="color:#e8eaf0;">${escapeHtml(p.domain)}</strong>` : ''}
          </div>
          ${buildQuestionTable(p.question_detail)}
        </div>`;
    }

    function statTile(label, val, color = '#e8eaf0') {
      return `<div style="background:var(--bg-panel); border:1px solid var(--border); border-radius:8px; padding:8px; text-align:center;">
        <div style="font-size:1.3rem; font-weight:700; color:${color};">${val}</div>
        <div style="font-size:0.72rem; color:var(--text-faint);">${label}</div>
      </div>`;
    }

    function buildQuestionTable(detail) {
      if (!detail || !detail.length) return '';
      const rows = detail.map((item, i) => {
        const statusIcon = item.is_correct ? '✓' : (item.is_answered ? '✗' : '—');
        const statusColor = item.is_correct ? '#4ade80' : (item.is_answered ? '#f87171' : '#7a7f9a');
        const qText = (item.question_text || '').slice(0, 100) + (item.question_text?.length > 100 ? '…' : '');
        return `<tr>
          <td style="color:var(--text-faint);">${i + 1}</td>
          <td style="font-size:0.82rem; max-width:300px;">${escapeHtml(qText)}</td>
          <td style="font-size:0.82rem; color:#94a3b8;">${escapeHtml(item.candidate_answer_text || '—')}</td>
          <td style="font-size:0.82rem; color:#4ade80;">${escapeHtml(item.correct_answer_text || '—')}</td>
          <td style="color:${statusColor}; font-weight:700;">${statusIcon}</td>
        </tr>`;
      }).join('');

      return `<table style="width:100%; border-collapse:collapse; font-size:0.82rem; margin-top:8px;">
        <thead><tr style="color:var(--text-faint); font-size:0.75rem; text-align:left;">
          <th style="padding:4px 6px; border-bottom:1px solid var(--border);">#</th>
          <th style="padding:4px 6px; border-bottom:1px solid var(--border);">Question</th>
          <th style="padding:4px 6px; border-bottom:1px solid var(--border);">Answer Given</th>
          <th style="padding:4px 6px; border-bottom:1px solid var(--border);">Correct Answer</th>
          <th style="padding:4px 6px; border-bottom:1px solid var(--border);">Result</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    }

    const overall = `
      <div style="display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-bottom:1.5rem; padding-bottom:1rem; border-bottom:1px solid var(--border);">
        <div style="grid-column:1/-1; font-size:0.78rem; text-transform:uppercase; letter-spacing:0.08em; color:var(--text-faint); margin-bottom:4px;">Overall</div>
        <div style="background:var(--bg-panel); border:1px solid var(--border); border-radius:8px; padding:10px; text-align:center;">
          <div style="font-size:1.5rem; font-weight:700;">${r.total_score} / ${r.total_max_score}</div>
          <div style="font-size:0.72rem; color:var(--text-faint);">Total Score</div>
        </div>
        <div style="background:var(--bg-panel); border:1px solid var(--border); border-radius:8px; padding:10px; text-align:center;">
          <div style="font-size:1.5rem; font-weight:700; color:#4ade80;">${r.total_correct}</div>
          <div style="font-size:0.72rem; color:var(--text-faint);">Correct</div>
        </div>
        <div style="background:var(--bg-panel); border:1px solid var(--border); border-radius:8px; padding:10px; text-align:center;">
          <div style="font-size:1.5rem; font-weight:700; color:#f87171;">${r.total_incorrect}</div>
          <div style="font-size:0.72rem; color:var(--text-faint);">Incorrect</div>
        </div>
        <div style="background:var(--bg-panel); border:1px solid var(--border); border-radius:8px; padding:10px; text-align:center;">
          <div style="font-size:1.5rem; font-weight:700;">${r.overall_accuracy != null ? r.overall_accuracy.toFixed(1) + '%' : '—'}</div>
          <div style="font-size:0.72rem; color:var(--text-faint);">Accuracy</div>
        </div>
      </div>`;

    const meta = `<div style="font-size:0.82rem; color:var(--text-faint); margin-bottom:1rem;">
      Status: <strong style="color:#e8eaf0;">${r.session_status?.replace('_',' ') || '—'}</strong>
      &nbsp;·&nbsp; Started: <strong style="color:#e8eaf0;">${fmtIST(r.started_at)}</strong>
      &nbsp;·&nbsp; Completed: <strong style="color:#e8eaf0;">${fmtIST(r.submitted_at)}</strong>
      ${r.domain ? `&nbsp;·&nbsp; Domain: <strong style="color:#e8eaf0;">${escapeHtml(r.domain)}</strong>` : ''}
    </div>`;

    return meta + overall +
      phaseHtml(r.phase1, 'Paper 1 — Research Methodology') +
      phaseHtml(r.phase2, `Paper 2 — ${r.phase2?.domain || 'Domain Specific'}`);
  }

  // ── Global Exam Control ───────────────────────────────────────────────
  const globalExamBtn    = document.getElementById('globalExamBtn');
  const endExamModal     = document.getElementById('endExamModal');
  const endExamCancelBtn = document.getElementById('endExamCancelBtn');
  const endExamConfirmBtn= document.getElementById('endExamConfirmBtn');
  let examIsOpen = false;

  async function fetchExamState() {
    try {
      const state = await api('/admin/exam/state', { headers: authHeaders });
      updateExamControlBtn(state.exam_open);
    } catch (_) {}
  }

  function updateExamControlBtn(isOpen) {
    examIsOpen = isOpen;
    globalExamBtn.dataset.state = isOpen ? 'open' : 'closed';
    if (isOpen) {
      globalExamBtn.textContent = '■ End Exam for All';
      globalExamBtn.classList.add('exam-btn-end');
      globalExamBtn.classList.remove('exam-btn-start');
    } else {
      globalExamBtn.textContent = '▶ Start Exam for All';
      globalExamBtn.classList.add('exam-btn-start');
      globalExamBtn.classList.remove('exam-btn-end');
    }
  }

  globalExamBtn.addEventListener('click', () => {
    if (!examIsOpen) {
      if (!confirm('Start the examination for ALL candidates? Candidates will be allowed to enter their sessions.')) return;
      api('/admin/exam/start', { method: 'POST', headers: authHeaders })
        .then(() => updateExamControlBtn(true))
        .catch(err => alert('Failed to start exam: ' + err.message));
    } else {
      endExamModal.classList.add('visible');
    }
  });
  endExamCancelBtn.addEventListener('click', () => endExamModal.classList.remove('visible'));
  endExamConfirmBtn.addEventListener('click', async () => {
    endExamModal.classList.remove('visible');
    endExamConfirmBtn.disabled = true;
    try {
      const res = await api('/admin/exam/end', { method: 'POST', headers: authHeaders });
      updateExamControlBtn(false);
      alert(`Exam ended. ${res.sessions_force_ended} session(s) were submitted and graded.`);
      fetchSnapshot();
    } catch (err) {
      alert('Failed to end exam: ' + err.message);
    } finally {
      endExamConfirmBtn.disabled = false;
    }
  });

  // ── Hall-wide Paper 1 / Paper 2 controls ──────────────────────────────
  // Bulk versions of the per-candidate buttons on the Live Monitor. Each one
  // reports what it skipped, so "0 moved" never looks like a silent failure.
  const breakAllBtn   = document.getElementById('breakAllBtn');
  const openPaper2Btn = document.getElementById('openPaper2Btn');
  const resetExamBtn  = document.getElementById('resetExamBtn');

  async function bulkAction(btn, url, confirmText, describe) {
    if (!confirm(confirmText)) return;
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = 'Working…';
    try {
      const res = await api(url, { method: 'POST', headers: authHeaders });
      alert(describe(res));
      fetchSnapshot();
    } catch (err) {
      alert('Failed: ' + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  }

  if (breakAllBtn) breakAllBtn.addEventListener('click', () => bulkAction(
    breakAllBtn,
    '/admin/exam/break-all',
    'End Paper 1 for every candidate still writing it and send them into the break?\n\n' +
    'Their Paper 1 answers are submitted and graded. Paper 2 timing is not affected — ' +
    'its clock starts when they open Paper 2, not now.',
    r => {
      const s = r.skipped || {};
      return `${r.message}\n\n` +
        `Left alone: ${s.already_on_break || 0} already on break, ` +
        `${s.in_paper_2 || 0} already in Paper 2, ` +
        `${s.finished || 0} finished, ${s.not_started || 0} not started.`;
    }
  ));

  if (openPaper2Btn) openPaper2Btn.addEventListener('click', () => bulkAction(
    openPaper2Btn,
    '/admin/exam/open-paper2-all',
    'Open Paper 2 now for everyone on the break?\n\n' +
    'They will not have to wait out the rest of the break timer.',
    r => {
      const s = r.skipped || {};
      return `${r.message}\n\nLeft alone: ${s.not_on_break || 0} not on the break.`;
    }
  ));

  // Reset is the one control with no undo, so it asks for the word, not a
  // click. The server refuses it anyway while anyone is still writing.
  if (resetExamBtn) resetExamBtn.addEventListener('click', async () => {
    const typed = prompt(
      'Clear every exam attempt so the hall can be used again?\n\n' +
      'KEPT:     candidate accounts, passwords, the question bank\n' +
      'CLEARED:  every session, answer, violation and result\n\n' +
      'A copy of everything being cleared is saved to data/archive/ first.\n\n' +
      'Type RESET to confirm:'
    );
    if (typed === null) return;
    if (typed.trim().toUpperCase() !== 'RESET') {
      alert('Not confirmed — nothing was cleared.');
      return;
    }
    resetExamBtn.disabled = true;
    try {
      const res = await api('/admin/exam/reset', {
        method: 'POST',
        headers: { ...authHeaders, 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: 'RESET' }),
      });
      alert(res.message);
      updateExamControlBtn(false);
      fetchSnapshot();
    } catch (err) {
      alert('Reset did not run: ' + err.message);
    } finally {
      resetExamBtn.disabled = false;
    }
  });

  // ── Exam Schedule tab ─────────────────────────────────────────────────
  function formatUTCtoISTInput(utcStr) {
    const dt = new Date(utcStr.endsWith('Z') ? utcStr : utcStr + 'Z');
    // Add 5 hours 30 mins to the UTC time so that its UTC output matches IST
    dt.setMinutes(dt.getMinutes() + 330);
    return dt.toISOString().slice(0, 16);
  }

  function parseISTtoUTC(datetimeLocalStr) {
    // e.g. "2026-09-19T13:36"
    const [datePart, timePart] = datetimeLocalStr.split('T');
    const [y, m, d] = datePart.split('-');
    const [hr, min] = timePart.split(':');
    const dt = new Date(Date.UTC(y, m - 1, d, hr, min));
    // Subtract 5 hours 30 mins to get the true UTC time
    dt.setMinutes(dt.getMinutes() - 330);
    return dt.toISOString();
  }

  async function loadSchedule() {
    try {
      const state = await api('/admin/exam/state', { headers: authHeaders });
      const disp  = document.getElementById('currentScheduleDisplay');
      const txt   = document.getElementById('schedDisplayText');
      if (state.scheduled_start_at || state.scheduled_end_at) {
        disp.style.display = 'block';
        txt.textContent    = `${fmtIST(state.scheduled_start_at)} → ${fmtIST(state.scheduled_end_at)} (${state.timezone_label || 'IST'})`;
      }
      // Pre-fill inputs with existing schedule
      if (state.scheduled_start_at) {
        document.getElementById('schedStartInput').value = formatUTCtoISTInput(state.scheduled_start_at);
      }
      if (state.scheduled_end_at) {
        document.getElementById('schedEndInput').value = formatUTCtoISTInput(state.scheduled_end_at);
      }
    } catch (_) {}
  }

  document.getElementById('setScheduleBtn').addEventListener('click', async () => {
    const startVal = document.getElementById('schedStartInput').value;
    const endVal   = document.getElementById('schedEndInput').value;
    const tz       = document.getElementById('schedTzLabel').value.trim() || 'Asia/Kolkata';
    const errEl    = document.getElementById('scheduleError');
    const okEl     = document.getElementById('scheduleSuccess');
    errEl.style.display = 'none'; okEl.style.display = 'none';

    if (!startVal || !endVal) {
      errEl.textContent = 'Both start and end times are required.'; errEl.style.display = 'block'; return;
    }

    try {
      const startUTCStr = parseISTtoUTC(startVal);
      const endUTCStr   = parseISTtoUTC(endVal);

      if (new Date(endUTCStr) <= new Date(startUTCStr)) {
        errEl.textContent = 'End time must be after start time.'; errEl.style.display = 'block'; return;
      }

      await api('/admin/exam/schedule', {
        method: 'POST', headers: jsonHeaders,
        body: JSON.stringify({ scheduled_start_at: startUTCStr, scheduled_end_at: endUTCStr, timezone_label: tz }),
      });
      okEl.textContent = `Schedule saved: ${fmtIST(startUTCStr)} → ${fmtIST(endUTCStr)}`;
      okEl.style.display = 'block';
      loadSchedule();
    } catch (err) {
      errEl.textContent = err.message; errEl.style.display = 'block';
    }
  });

  // ══════════════════════════════════════════════════════════════════════
  // Live monitor + camera
  //   Rows and video tiles are updated IN PLACE (never wiped every second),
  //   so buttons stay clickable and camera pictures never go black.
  //   The server only streams video for the tiles on screen: 1 fps per tile,
  //   ~5 fps in the focus view.
  // ══════════════════════════════════════════════════════════════════════
  const connDot   = document.getElementById('connDot');
  const connLabel = document.getElementById('connLabel');
  const tbody     = document.getElementById('candidateTableBody');
  const tileInProgress   = document.getElementById('tileInProgress');
  const tileCompleted    = document.getElementById('tileCompleted');
  const tileDisqualified = document.getElementById('tileDisqualified');
  const tileTotal        = document.getElementById('tileTotal');
  const tileBreak        = document.getElementById('tileBreak');
  const searchInput  = document.getElementById('candidateSearchInput');
  const statusFilter = document.getElementById('statusFilter');
  const domainFilter = document.getElementById('domainFilter');

  const rows = new Map();          // user_id -> row data
  const rowEls = new Map();        // user_id -> <tr>
  const frames = new Map();        // user_id -> { src, at, dark }
  const deadlines = new Map();     // user_id -> { endMs, breakEndMs }

  function formatTime(seconds) {
    if (seconds == null) return '—';
    seconds = Math.max(0, Math.floor(seconds));
    const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
    return (h ? h + ':' : '') + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
  }
  const STATUS_LABEL = { not_started: 'not started', in_progress: 'in progress', on_break: 'on break', completed: 'completed', expired: 'ended', disqualified: 'disqualified' };

  function upsertRow(r) {
    const prev = rows.get(r.user_id) || {};
    const row = { ...prev, ...r };
    rows.set(r.user_id, row);
    deadlines.set(r.user_id, {
      endMs: Date.now() + (row.time_remaining_seconds || 0) * 1000,
      breakEndMs: Date.now() + (row.break_seconds_left || 0) * 1000,
    });
    return row;
  }

  async function fetchSnapshot() {
    try {
      const list = await api('/admin/sessions', { headers: authHeaders });
      const seen = new Set();
      list.forEach(r => { upsertRow(r); seen.add(r.user_id); });
      [...rows.keys()].forEach(k => { if (!seen.has(k)) rows.delete(k); });
      scheduleRender();
    } catch (_) {}
  }

  function applyWsUpdate(msg) {
    upsertRow(msg);
    scheduleRender();
    const f = focusRow();
    if (f && f.user_id === msg.user_id) fillFocusDetails(rows.get(msg.user_id));
  }

  let renderQueued = false;
  function scheduleRender() {
    if (renderQueued) return;
    renderQueued = true;
    setTimeout(() => { renderQueued = false; renderMonitor(); }, 250);
  }

  function filteredRows() {
    let list = [...rows.values()].sort((a, b) => (a.roll_no || '').localeCompare(b.roll_no || '') || (a.full_name || '').localeCompare(b.full_name || ''));
    const q = (searchInput?.value || '').trim().toLowerCase();
    const st = statusFilter?.value || '';
    const dm = domainFilter?.value || '';
    if (q) list = list.filter(r => (r.full_name || '').toLowerCase().includes(q) || (r.email || '').toLowerCase().includes(q) || (r.roll_no || '').toLowerCase().includes(q));
    if (st) list = list.filter(r => r.status === st);
    if (dm) list = list.filter(r => r.paper2_domain === dm || (dm === 'Agriculture' && (r.paper2_domain || '').startsWith('Agricultural -')));
    return list;
  }

  function setHTML(td, html) { if (td._h !== html) { td.innerHTML = html; td._h = html; } }

  function timeCellText(r) {
    const d = deadlines.get(r.user_id) || {};
    if (r.status === 'in_progress') return formatTime((d.endMs - Date.now()) / 1000);
    if (r.status === 'on_break') { const left = (d.breakEndMs - Date.now()) / 1000; return left > 0 ? `break ${formatTime(left)}` : 'P2 open'; }
    return '—';
  }

  function renderMonitor() {
    // tiles
    let inProg = 0, done = 0, disq = 0, brk = 0;
    rows.forEach(r => {
      if (r.status === 'in_progress') inProg++;
      else if (r.status === 'completed' || r.status === 'expired') done++;
      else if (r.status === 'disqualified') disq++;
      else if (r.status === 'on_break') brk++;
    });
    tileInProgress.textContent = inProg;
    tileCompleted.textContent = done;
    tileDisqualified.textContent = disq;
    tileTotal.textContent = rows.size;
    if (tileBreak) tileBreak.textContent = brk;

    const list = filteredRows();
    const keep = new Set();
    list.forEach((r, i) => {
      keep.add(r.user_id);
      let tr = rowEls.get(r.user_id);
      if (!tr) {
        tr = document.createElement('tr');
        tr.dataset.uid = r.user_id;
        for (let k = 0; k < 8; k++) tr.appendChild(document.createElement('td'));
        tr.children[6].className = 'time-cell';
        tr.children[4].className = 'progress-cell';
        tr.children[7].className = 'actions-td';
        rowEls.set(r.user_id, tr);
      }
      const stale = r.status === 'in_progress' && !r.online;
      tr.className = stale ? 'stale-row' : '';
      const td = tr.children;
      setHTML(td[0], `<div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
          <span class="roll-chip">${escapeHtml(r.roll_no || '')}</span>
          <span style="font-weight:600;">${escapeHtml(r.full_name)}</span>
          ${r.status === 'in_progress' ? `<span class="online-dot ${r.online ? 'on' : ''}" title="${r.online ? 'Connected' : 'Not connected right now'}"></span>` : ''}
        </div>
        <div style="color:var(--text-faint); font-size:12px; margin-top:2px;">${escapeHtml(r.email)}</div>`);
      setHTML(td[1], `<span class="status-pill ${r.status}">${STATUS_LABEL[r.status] || r.status}</span>`);
      setHTML(td[2], `<span style="font-size:12px;">Paper ${r.current_phase || 1}</span>`);
      setHTML(td[3], `<div class="strike-cluster">${[0, 1, 2].map(i => `<span class="dot ${i < r.strikes ? 'on' : ''}"></span>`).join('')} <span style="font-size:11px; color:var(--text-faint);">${r.strikes || 0}</span></div>`);
      setHTML(td[4], `${r.answered_count ?? 0} / ${r.total_questions ?? 0}`);
      setHTML(td[5], r.domain_selected ? `<span class="status-pill in_progress" style="font-size:11px;">${escapeHtml(r.paper2_domain || '—')}</span>` : `<span style="color:var(--text-faint); font-size:12px;">—</span>`);
      td[6].textContent = timeCellText(r);
      const btns = [];
      if (r.status === 'in_progress' || r.status === 'on_break') btns.push(`<button class="btn btn-sm" data-action="disqualify" data-id="${r.user_id}" style="border-color:#ef4444; color:#ef4444;">Disqualify</button>`);
      if (r.status === 'on_break') btns.push(`<button class="btn btn-sm" data-action="skip-break" data-id="${r.user_id}">Open P2 now</button>`);
      if (r.status === 'disqualified' || r.status === 'expired') btns.push(`<button class="btn btn-sm" data-action="grant-retake" data-id="${r.user_id}">Re-open</button>`);
      if (r.status !== 'not_started') btns.push(`<button class="btn btn-sm" data-action="view-result" data-id="${r.user_id}">Result</button>`);
      if (r.status === 'in_progress') btns.push(`<button class="btn btn-sm" data-action="focus" data-id="${r.user_id}">📹 Watch</button>`);
      setHTML(td[7], `<div style="display:flex; gap:4px; flex-wrap:wrap;">${btns.join('')}</div>`);
      if (tbody.children[i] !== tr) tbody.insertBefore(tr, tbody.children[i] || null);
    });
    rowEls.forEach((tr, uid) => { if (!keep.has(uid)) { tr.remove(); if (!rows.has(uid)) rowEls.delete(uid); } });

    renderVideoGrid();
  }

  // per-second ticking of the time column only
  setInterval(() => {
    rowEls.forEach((tr, uid) => {
      const r = rows.get(uid);
      if (r && tr.isConnected) tr.children[6].textContent = timeCellText(r);
    });
    refreshTileStaleness();
    const f = focusRow();
    if (f) document.getElementById('focusTimeLeft') && (document.getElementById('focusTimeLeft').textContent = timeCellText(f));
  }, 1000);

  tbody.addEventListener('click', async e => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const userId = Number(btn.dataset.id);
    const r = rows.get(userId);
    await rowAction(btn.dataset.action, userId, r);
  });

  async function rowAction(action, userId, r) {
    const name = r ? r.full_name : 'this candidate';
    try {
      if (action === 'disqualify') {
        if (!confirm(`Disqualify ${name}? They will be signed out immediately. You can re-open the attempt later.`)) return;
        await api(`/admin/candidates/${userId}/disqualify`, { method: 'POST', headers: jsonHeaders, body: '{}' });
      } else if (action === 'grant-retake') {
        if (!confirm(`Re-open the exam for ${name}? They continue where they stopped with the time they had left (min. 5 minutes).`)) return;
        const res = await api(`/admin/users/${userId}/grant-retake`, { method: 'POST', headers: authHeaders });
        alert(res.message);
      } else if (action === 'skip-break') {
        if (!confirm(`Let ${name} start Paper 2 now (skip the rest of the break)?`)) return;
        await api(`/admin/candidates/${userId}/skip-break`, { method: 'POST', headers: authHeaders });
      } else if (action === 'view-result') {
        showResultModal(userId, name);
        return;
      } else if (action === 'focus') {
        if (r) showFocusModal(r);
        return;
      }
      fetchSnapshot();
    } catch (err) { alert('Failed: ' + err.message); }
  }

  let searchDebounce_;
  searchInput?.addEventListener('input', () => { clearTimeout(searchDebounce_); searchDebounce_ = setTimeout(() => { gridPage = 0; renderMonitor(); sendWatch(); }, 200); });
  statusFilter?.addEventListener('change', () => { gridPage = 0; renderMonitor(); sendWatch(); });
  domainFilter?.addEventListener('change', () => { gridPage = 0; renderMonitor(); sendWatch(); });

  const exportCsvBtn   = document.getElementById('exportCsvBtn');
  const exportExcelBtn = document.getElementById('exportExcelBtn');
  const bulkPdfBtn     = document.getElementById('bulkPdfBtn');
  exportCsvBtn?.addEventListener('click',   () => downloadFile('/admin/results/export/csv',   'dsirt_phd_entrance_results.csv', exportCsvBtn));
  exportExcelBtn?.addEventListener('click', () => downloadFile('/admin/results/export/excel', 'dsirt_phd_entrance_results.csv', exportExcelBtn));
  bulkPdfBtn?.addEventListener('click', async () => {
    // opens the printable report in a new tab (Print → Save as PDF)
    const w = window.open('', '_blank');
    try {
      const res = await fetch('/admin/results/bulk-pdf', { headers: authHeaders });
      const html = await res.text();
      if (w) { w.document.open(); w.document.write(html); w.document.close(); }
    } catch (err) { if (w) w.close(); alert('Failed: ' + err.message); }
  });

  // ── Video grid ────────────────────────────────────────────────────────
  const toggleGridBtn = document.getElementById('toggleGridBtn');
  const tableView = document.getElementById('tableView');
  const gridView  = document.getElementById('gridView');
  const videoGrid = document.getElementById('videoGrid');
  const gridPrev  = document.getElementById('gridPrevPage');
  const gridNext  = document.getElementById('gridNextPage');
  const gridLabel = document.getElementById('gridPageLabel');
  const PAGE_SIZE = 16;
  let gridActive = false;
  let gridPage = 0;
  let gridUids = [];
  const tiles = new Map(); // uid -> tile element

  toggleGridBtn.addEventListener('click', () => {
    gridActive = !gridActive;
    tableView.style.display = gridActive ? 'none' : 'block';
    gridView.style.display = gridActive ? 'block' : 'none';
    toggleGridBtn.textContent = gridActive ? 'View Table' : '📹 View Video Grid';
    renderVideoGrid();
    sendWatch();
  });
  gridPrev.addEventListener('click', () => { gridPage--; renderVideoGrid(); sendWatch(); });
  gridNext.addEventListener('click', () => { gridPage++; renderVideoGrid(); sendWatch(); });

  function makeTile(uid) {
    const tile = document.createElement('div');
    tile.className = 'video-tile';
    tile.innerHTML = `
      <div class="video-tile-media">
        <img alt="" class="video-tile-img" style="display:none;">
        <div class="video-tile-fallback"><div class="initials"></div><div class="nosig">Waiting for camera…</div></div>
        <div class="video-tile-badge" style="display:none;"></div>
      </div>
      <div class="video-tile-caption"><span class="name"></span><span class="meta"></span></div>`;
    tile.addEventListener('click', () => { const r = rows.get(uid); if (r) showFocusModal(r); });
    // Card is built ONCE; keep direct references so frames never need a DOM lookup
    tile._img = tile.querySelector('.video-tile-img');
    tile._fallback = tile.querySelector('.video-tile-fallback');
    tile._badge = tile.querySelector('.video-tile-badge');
    tile._initials = tile.querySelector('.initials');
    tile._name = tile.querySelector('.name');
    tile._meta = tile.querySelector('.meta');
    tile._uid = uid;
    tiles.set(uid, tile);
    const f = frames.get(uid);
    if (f) paintTile(uid, f);
    return tile;
  }

  function renderVideoGrid() {
    const list = filteredRows().filter(r => r.status === 'in_progress');
    const pages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
    gridPage = Math.min(Math.max(0, gridPage), pages - 1);
    const page = list.slice(gridPage * PAGE_SIZE, gridPage * PAGE_SIZE + PAGE_SIZE);
    gridLabel.textContent = `Page ${gridPage + 1} of ${pages} · ${list.length} live`;
    gridPrev.disabled = gridPage === 0;
    gridNext.disabled = gridPage >= pages - 1;
    const newUids = page.map(r => r.user_id);
    const changed = newUids.join(',') !== gridUids.join(',');
    gridUids = newUids;

    page.forEach((r, i) => {
      const tile = tiles.get(r.user_id) || makeTile(r.user_id);
      const border = r.strikes > 0 ? 'var(--danger)' : (r.online ? 'var(--success)' : '#64748b');
      if (tile.style.borderColor !== border) tile.style.borderColor = border;
      setText(tile._initials, (r.full_name || '').split(' ').map(n => n[0]).join('').slice(0, 2));
      setText(tile._name, r.full_name);
      setText(tile._meta, `P${r.current_phase} · ${r.answered_count}/${r.total_questions}${r.strikes ? ' · ⚠' + r.strikes : ''}`);
      if (videoGrid.children[i] !== tile) videoGrid.insertBefore(tile, videoGrid.children[i] || null);
      const f = frames.get(r.user_id);
      if (f) paintTile(r.user_id, f);
    });
    const onPage = new Set(gridUids);
    [...videoGrid.children].forEach(t => { if (!onPage.has(t._uid)) t.remove(); }); // detached, not destroyed — reused later
    if (changed && gridActive) sendWatch();
  }

  function setText(node, text) { if (node.textContent !== text) node.textContent = text; }

  /** Only the <img> source changes per frame — the card itself is never rebuilt. */
  function paintTile(uid, f) {
    const tile = tiles.get(uid);
    if (!tile || !tile.isConnected || tile._painted === f) return;
    tile._painted = f;
    tile._img.src = f.src;
    if (tile._img.style.display !== 'block') { tile._img.style.display = 'block'; tile._fallback.style.display = 'none'; }
    if (f.dark) { setText(tile._badge, 'CAMERA DARK / COVERED'); tile._badge.style.display = 'block'; }
    else if (tile._badge.style.display !== 'none') tile._badge.style.display = 'none';
    tile.classList.remove('stale');
  }

  function refreshTileStaleness() {
    const now = Date.now();
    tiles.forEach((tile, uid) => {
      const f = frames.get(uid);
      // keep the LAST picture (dimmed) instead of going black
      if (!tile.isConnected) return;
      tile.classList.toggle('stale', !f || now - f.at > 15000);
      if (f && now - f.at > 15000) {
        setText(tile._badge, 'NO SIGNAL — last picture ' + Math.round((now - f.at) / 1000) + 's ago');
        tile._badge.style.display = 'block';
      }
    });
  }

  // Frames are stored immediately but painted once per screen refresh (latest frame wins),
  // so a burst of frames never makes the browser decode images it will never show.
  const dirtyFrames = new Set();
  let paintQueued = false;
  function onFrame(uid, src, dark, at) {
    frames.set(uid, { src, dark: !!dark, at: Date.now() });
    dirtyFrames.add(uid);
    if (!paintQueued) { paintQueued = true; requestAnimationFrame(paintFrames); }
  }
  function paintFrames() {
    paintQueued = false;
    const focusOpen = focusModal.classList.contains('visible');
    dirtyFrames.forEach(uid => {
      const f = frames.get(uid);
      if (gridActive) paintTile(uid, f);
      if (focusOpen && focusUserId === uid) {
        if (focusImg.src !== f.src) focusImg.src = f.src;
        focusImg.style.display = 'block';
        focusWaiting.style.display = 'none';
        focusDark.style.display = f.dark ? 'block' : 'none';
      }
    });
    dirtyFrames.clear();
  }

  // ── Focus modal (live, ~5 fps) ─────────────────────────────────────────
  const focusModal   = document.getElementById('focusModal');
  const focusName    = document.getElementById('focusModalName');
  const focusImg     = document.getElementById('focusModalImgEl');
  const focusWaiting = document.getElementById('focusModalWaiting');
  const focusDark    = document.getElementById('focusModalDark');
  const focusDets    = document.getElementById('focusModalDetails');
  const focusDisqBtn = document.getElementById('focusDisqualifyBtn');
  const focusRetakeBtn = document.getElementById('focusGrantRetakeBtn');
  const focusResultBtn = document.getElementById('focusViewResultBtn');
  const focusSkipBtn = document.getElementById('focusSkipBreakBtn');
  let focusUserId = null;
  const focusRow = () => (focusUserId !== null && focusModal.classList.contains('visible')) ? rows.get(focusUserId) : null;

  document.getElementById('focusModalClose').addEventListener('click', closeFocus);
  focusModal.addEventListener('click', e => { if (e.target === focusModal) closeFocus(); });
  function closeFocus() { focusModal.classList.remove('visible'); focusUserId = null; sendWatch(); }

  function fillFocusDetails(r) {
    if (!r) return;
    focusDets.innerHTML = [
      ['Roll no', escapeHtml(r.roll_no || '')],
      ['Status', STATUS_LABEL[r.status] || r.status],
      ['Paper', r.current_phase || 1],
      ['Warnings', r.strikes || 0],
      ['Progress', `${r.answered_count} / ${r.total_questions}`],
      ['Domain', escapeHtml(r.paper2_domain || 'Not selected')],
      ['Time left', `<span id="focusTimeLeft">${timeCellText(r)}</span>`],
      ['Connection', r.online ? 'Connected' : 'Not connected'],
    ].map(([k, v]) => `<div><span style="color:var(--text-faint)">${k}</span><br><strong>${v}</strong></div>`).join('');
    focusDisqBtn.style.display = (r.status === 'in_progress' || r.status === 'on_break') ? '' : 'none';
    focusRetakeBtn.style.display = (r.status === 'disqualified' || r.status === 'expired') ? '' : 'none';
    focusSkipBtn.style.display = r.status === 'on_break' ? '' : 'none';
  }

  function showFocusModal(r) {
    focusUserId = r.user_id;
    focusName.textContent = `${r.full_name} (${r.roll_no || ''})`;
    const f = frames.get(r.user_id);
    if (f) { focusImg.src = f.src; focusImg.style.display = 'block'; focusWaiting.style.display = 'none'; }
    else { focusImg.style.display = 'none'; focusWaiting.style.display = 'flex'; }
    focusDark.style.display = f && f.dark ? 'block' : 'none';
    fillFocusDetails(r);
    focusModal.classList.add('visible');
    sendWatch();
    focusDisqBtn.onclick = async () => { await rowAction('disqualify', r.user_id, rows.get(r.user_id)); fillFocusDetails(rows.get(r.user_id)); };
    focusRetakeBtn.onclick = async () => { await rowAction('grant-retake', r.user_id, rows.get(r.user_id)); };
    focusSkipBtn.onclick = async () => { await rowAction('skip-break', r.user_id, rows.get(r.user_id)); };
    focusResultBtn.onclick = () => { closeFocus(); showResultModal(r.user_id, r.full_name); };
  }

  // ── WebSocket + fallbacks ──────────────────────────────────────────────
  let wsRef = null;
  let wsRetry = 0;

  function sendWatch() {
    const payload = { type: 'watch', user_ids: gridActive ? gridUids : [], focus: focusRow() ? focusUserId : null };
    if (wsRef && wsRef.readyState === WebSocket.OPEN) {
      try { wsRef.send(JSON.stringify(payload)); } catch (_) {}
    }
  }

  function connectWebSocket() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${protocol}//${location.host}/ws/admin?token=${encodeURIComponent(token)}`);
    wsRef = ws;
    ws.onopen = () => { wsRetry = 0; connDot.classList.add('live'); connLabel.textContent = 'Live'; sendWatch(); fetchSnapshot(); };
    ws.onmessage = event => {
      let msg; try { msg = JSON.parse(event.data); } catch (_) { return; }
      if (msg.type === 'candidate_update') applyWsUpdate(msg);
      else if (msg.type === 'proctor_frame') onFrame(msg.user_id, msg.frame, msg.dark, msg.at);
      else if (msg.type === 'exam_state_changed') updateExamControlBtn(msg.exam_open);
      else if (msg.type === 'violation') toast(`⚠ ${msg.full_name}: ${String(msg.violation_type).replace(/_/g, ' ')} (warning ${msg.strikes})`);
    };
    ws.onclose = (evt) => {
      if (wsRef === ws) wsRef = null;
      connDot.classList.remove('live');
      if (evt.code === 4001) { connLabel.textContent = 'Signed out'; window.location.href = '/login'; return; }
      connLabel.textContent = 'Reconnecting…';
      setTimeout(connectWebSocket, Math.min(1000 * Math.pow(2, wsRetry++), 10000));
    };
    ws.onerror = () => { try { ws.close(); } catch (_) {} };
  }

  // If the WebSocket is blocked by a proxy, still show pictures (every 2 s) via HTTP.
  setInterval(async () => {
    if (wsRef && wsRef.readyState === WebSocket.OPEN) return;
    const ids = [...new Set([...(gridActive ? gridUids : []), ...(focusRow() ? [focusUserId] : [])])];
    if (!ids.length) return;
    try {
      const data = await api('/admin/frames?ids=' + ids.join(','), { headers: authHeaders });
      Object.entries(data || {}).forEach(([uid, f]) => {
        const prev = frames.get(Number(uid));
        if (!prev || prev.src !== f.frame) onFrame(Number(uid), f.frame, f.dark, f.at);
      });
    } catch (_) {}
  }, 2000);

  // Small toast for live warnings
  const toastBox = document.createElement('div');
  toastBox.className = 'toast-box';
  document.body.appendChild(toastBox);
  function toast(text) {
    const t = document.createElement('div');
    t.className = 'toast';
    t.textContent = text;
    toastBox.appendChild(t);
    setTimeout(() => t.remove(), 7000);
    while (toastBox.children.length > 5) toastBox.firstChild.remove();
  }

  fetchSnapshot();
  fetchExamState();
  setInterval(fetchSnapshot, 10000);
  connectWebSocket();

  // ── Manage Users tab ──────────────────────────────────────────────────
  const userMsg = document.getElementById('userActionMessage');
  const setterTableBody         = document.getElementById('setterTableBody');
  const candidateUsersTableBody = document.getElementById('candidateUsersTableBody');

  async function loadUsers() {
    try {
      const [setters, candidates] = await Promise.all([
        api('/admin/users?role=setter',    { headers: authHeaders }),
        api('/admin/users?role=candidate', { headers: authHeaders }),
      ]);
      renderSetters(setters);
      renderCandidates(candidates);
    } catch (err) { flash(userMsg, err.message); }
  }

  function renderSetters(rows) {
    if (!rows.length) {
      setterTableBody.innerHTML = `<tr><td colspan="4"><div class="empty-state">No question setters yet.</div></td></tr>`;
      return;
    }
    setterTableBody.innerHTML = rows.map(u => `
      <tr data-id="${u.id}">
        <td>${escapeHtml(u.full_name)}</td>
        <td>${escapeHtml(u.email)}</td>
        <td><span class="status-pill ${u.is_active ? 'active' : 'inactive'}">${u.is_active ? 'active' : 'disabled'}</span></td>
        <td class="action-cell">
          <button class="btn btn-sm" data-action="reset" data-id="${u.id}">Reset password</button>
          <button class="btn btn-sm" data-action="toggle" data-id="${u.id}" data-active="${u.is_active}">${u.is_active ? 'Disable' : 'Enable'}</button>
        </td>
      </tr>`).join('');
  }

  function renderCandidates(rows) {
    if (!rows.length) {
      candidateUsersTableBody.innerHTML = `<tr><td colspan="5"><div class="empty-state">No candidates yet.</div></td></tr>`;
      return;
    }
    candidateUsersTableBody.innerHTML = rows.map(u => {
      const attempt = u.latest_session_status || 'no_attempt';
      const canReactivate = ['disqualified','completed','expired'].includes(attempt);
      const canRetake = attempt === 'disqualified' || attempt === 'expired';
      // NOTE: latest_session_score is NOT shown — admin must use the Result button for that
      return `
        <tr data-id="${u.id}">
          <td>${escapeHtml(u.full_name)}</td>
          <td>${escapeHtml(u.email)}</td>
          <td><span class="status-pill ${u.is_active ? 'active' : 'inactive'}">${u.is_active ? 'active' : 'disabled'}</span></td>
          <td><span class="status-pill ${attempt}">${attempt.replace('_',' ')}</span></td>
          <td class="action-cell">
            <button class="btn btn-sm" data-action="reset" data-id="${u.id}">Reset pwd</button>
            <button class="btn btn-sm" data-action="toggle" data-id="${u.id}" data-active="${u.is_active}">${u.is_active ? 'Disable' : 'Enable'}</button>
            ${canRetake ? `<button class="btn btn-sm" data-action="grant-retake" data-id="${u.id}" style="border-color:var(--warning); color:var(--warning);">Grant Retake</button>` : ''}
            ${canReactivate ? `<button class="btn btn-primary btn-sm" data-action="reactivate" data-id="${u.id}">Clear &amp; restart</button>` : ''}
            ${attempt !== 'no_attempt' ? `<button class="btn btn-sm" data-action="view-result" data-id="${u.id}" data-name="${escapeHtml(u.full_name)}">Result</button>` : ''}
          </td>
        </tr>`;
    }).join('');
  }

  // User form wiring
  function wireSimpleForm(addBtnId, formId, cancelBtnId, nameId, emailId, submitBtnId, role, title, subtitle) {
    const form    = document.getElementById(formId);
    const addBtn  = document.getElementById(addBtnId);
    const cancelBtn = document.getElementById(cancelBtnId);
    const subBtn  = document.getElementById(submitBtnId);
    if (!form || !addBtn) return;

    addBtn.addEventListener('click', () => form.style.display = 'flex');
    cancelBtn?.addEventListener('click', () => {
      form.style.display = 'none';
      if (nameId) document.getElementById(nameId).value = '';
      if (emailId) document.getElementById(emailId).value = '';
    });
    subBtn?.addEventListener('click', async () => {
      const full_name = document.getElementById(nameId)?.value.trim();
      const email     = document.getElementById(emailId)?.value.trim();
      if (!full_name || !email) { flash(userMsg, 'Name and email are required.'); return; }
      try {
        const res = await api('/admin/users', { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ full_name, email, role }) });
        form.style.display = 'none';
        document.getElementById(nameId).value = '';
        document.getElementById(emailId).value = '';
        showCredModal(title, res.user.email, res.temporary_password, subtitle);
        loadUsers();
      } catch (err) { flash(userMsg, err.message); }
    });
  }

  wireSimpleForm('addSetterBtn', 'addSetterForm', 'cancelSetterBtn', 'setterName', 'setterEmail', 'createSetterBtn', 'setter',
    'Question Setter Created', 'Share this temporary password — setter must change it on first login.');
  wireSimpleForm('addCandidateBtn', 'addCandidateForm', 'cancelCandidateBtn', 'candidateName2', 'candidateEmail', 'createCandidateBtn', 'candidate',
    'Candidate Account Created', 'Candidate uses the universal password dsirt@123.');

  // CSV import
  document.getElementById('csvCandidateUpload')?.addEventListener('change', async e => {
    const file = e.target.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    try {
      const res = await api('/admin/users/upload', { method: 'POST', headers: authHeaders, body: formData });
      flash(userMsg, `Imported ${res.inserted} candidate(s). Password: dsirt@123`, true);
      loadUsers();
    } catch (err) { flash(userMsg, err.message); }
    e.target.value = '';
  });

  // Users panel event delegation
  document.getElementById('panel-users').addEventListener('click', async e => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const id = Number(btn.dataset.id);
    const action = btn.dataset.action;
    try {
      if (action === 'reset') {
        if (!confirm('Reset password for this user?')) return;
        const res = await api(`/admin/users/${id}/reset-password`, { method: 'POST', headers: authHeaders });
        showCredModal('Password Reset', res.email, res.temporary_password,
          res.temporary_password === 'dsirt@123' ? 'Candidate reset to universal password.' : 'New temporary password for setter.');
        loadUsers();
      } else if (action === 'toggle') {
        const active = btn.dataset.active === 'true';
        await api(`/admin/users/${id}`, { method: 'PATCH', headers: jsonHeaders, body: JSON.stringify({ is_active: !active }) });
        flash(userMsg, active ? 'Account disabled.' : 'Account re-enabled.', true);
        loadUsers();
      } else if (action === 'grant-retake') {
        if (!confirm('Grant retake to this candidate?')) return;
        const res = await api(`/admin/users/${id}/grant-retake`, { method: 'POST', headers: authHeaders });
        flash(userMsg, res.message, true); loadUsers();
      } else if (action === 'reactivate') {
        if (!confirm('Clear all exam data for this candidate? Irreversible.')) return;
        const res = await api(`/admin/users/${id}/reactivate`, { method: 'POST', headers: authHeaders });
        flash(userMsg, res.message, true); loadUsers();
      } else if (action === 'view-result') {
        const name = btn.dataset.name;
        showResultModal(id, name);
      }
    } catch (err) { flash(userMsg, err.message); }
  });

  // ── Question Bank tab ─────────────────────────────────────────────────
  const questionMsg   = document.getElementById('questionActionMessage');
  const questTableBody = document.getElementById('questionTableBody');
  const qSearchInput  = document.getElementById('questionSearchInput');
  const domainFiltQ   = document.getElementById('domainFilterQuestions');

  let allQCache = [];
  let qPage = 0;
  const Q_PAGE_SIZE = 25;

  async function loadQuestions() {
    try {
      const data = await api('/questions', { headers: authHeaders });
      allQCache = data;
      qPage = 0;
      renderQuestions();
    } catch (err) { flash(questionMsg, err.message); }
  }

  function renderQuestions() {
    let filtered = allQCache;
    const search  = qSearchInput?.value?.trim().toLowerCase() || '';
    const domain  = domainFiltQ?.value || '';
    if (search) filtered = filtered.filter(q => q.question_text.toLowerCase().includes(search) || q.domain.toLowerCase().includes(search));
    if (domain) filtered = filtered.filter(q => q.domain === domain);

    const totalPages = Math.ceil(filtered.length / Q_PAGE_SIZE) || 1;
    qPage = Math.min(qPage, totalPages - 1);
    const page = filtered.slice(qPage * Q_PAGE_SIZE, (qPage + 1) * Q_PAGE_SIZE);

    if (!page.length) {
      questTableBody.innerHTML = `<tr><td colspan="6"><div class="empty-state">No questions match.</div></td></tr>`;
      document.getElementById('questionPaginationBar').innerHTML = '';
      return;
    }

    questTableBody.innerHTML = page.map(q => `
      <tr>
        <td class="progress-cell">${q.order_no}</td>
        <td><span style="font-size:11px; color:#a5b4fc;">${escapeHtml(q.domain)}</span></td>
        <td>${q.phase === 1 ? 'P1' : 'P2'}</td>
        <td><span class="status-pill in_progress" style="font-size:10px;">${q.question_type}</span></td>
        <td style="font-size:0.83rem; max-width:400px;">${escapeHtml((q.question_text||'').slice(0,120))}${(q.question_text||'').length > 120 ? '…' : ''}</td>
        <td class="action-cell">
          <button class="btn btn-danger btn-sm" data-delete="${q.id}" style="font-size:11px;">Delete</button>
        </td>
      </tr>`).join('');

    // Bind delete buttons
    page.forEach(q => {
      questTableBody.querySelector(`[data-delete="${q.id}"]`)?.addEventListener('click', async () => {
        if (!confirm('Delete this question? This cannot be undone.')) return;
        try {
          await api(`/questions/${q.id}`, { method: 'DELETE', headers: authHeaders });
          flash(questionMsg, 'Question deleted.', true);
          loadQuestions();
        } catch (err) { flash(questionMsg, err.message); }
      });
    });

    // Pagination bar
    const pBar = document.getElementById('questionPaginationBar');
    pBar.innerHTML = `
      <button class="btn btn-sm" id="qPrevBtn" ${qPage === 0 ? 'disabled' : ''}>← Prev</button>
      <span>Page ${qPage + 1} of ${totalPages} (${filtered.length} questions)</span>
      <button class="btn btn-sm" id="qNextBtn" ${qPage >= totalPages - 1 ? 'disabled' : ''}>Next →</button>`;
    pBar.querySelector('#qPrevBtn')?.addEventListener('click', () => { qPage--; renderQuestions(); });
    pBar.querySelector('#qNextBtn')?.addEventListener('click', () => { qPage++; renderQuestions(); });
  }

  let qSearchDebounce;
  qSearchInput?.addEventListener('input', () => { clearTimeout(qSearchDebounce); qSearchDebounce = setTimeout(() => { qPage=0; renderQuestions(); }, 200); });
  domainFiltQ?.addEventListener('change', () => { qPage=0; renderQuestions(); });

  // Question file upload
  document.getElementById('questionFileInput')?.addEventListener('change', async e => {
    const file   = e.target.files[0];
    const domain = document.getElementById('uploadDomain')?.value;
    const phase  = document.getElementById('uploadPhase')?.value;
    const status = document.getElementById('questionUploadStatus');

    if (!file || !domain) {
      flash(questionMsg, 'Select a domain and a file.'); return;
    }
    if (status) status.textContent = `Uploading ${file.name}…`;

    const formData = new FormData();
    formData.append('file', file);
    formData.append('domain', domain);
    formData.append('phase', phase || '1');

    try {
      const res = await api('/admin/questions/upload', { method: 'POST', headers: authHeaders, body: formData });
      flash(document.getElementById('questionUploadMessage'), `Imported ${res.inserted} questions successfully.`, true);
      if (status) status.textContent = '';
      loadQuestions();
    } catch (err) {
      flash(document.getElementById('questionUploadMessage'), err.message);
      if (status) status.textContent = '';
    }
    e.target.value = '';
  });

})();
