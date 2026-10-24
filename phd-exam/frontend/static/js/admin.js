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
  resultPdfBtn.addEventListener('click', () => {
    if (!resultUserId) return;
    window.open(`/admin/candidates/${resultUserId}/result/pdf`, '_blank');
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
      phaseHtml(r.phase1, 'Phase 1 — Research Methodology') +
      phaseHtml(r.phase2, `Phase 2 — ${r.phase2?.domain || 'Domain Specific'}`);
  }

  // ── Focus modal ───────────────────────────────────────────────────────
  const focusModal      = document.getElementById('focusModal');
  const focusModalName  = document.getElementById('focusModalName');
  const focusModalImg   = document.getElementById('focusModalImg');
  const focusModalImgEl = document.getElementById('focusModalImgEl');
  const focusModalDets  = document.getElementById('focusModalDetails');
  const focusDisqBtn    = document.getElementById('focusDisqualifyBtn');
  const focusRetakeBtn  = document.getElementById('focusGrantRetakeBtn');
  const focusResultBtn  = document.getElementById('focusViewResultBtn');
  document.getElementById('focusModalClose').addEventListener('click', () => focusModal.classList.remove('visible'));

  let focusUserId = null;
  let focusSessionId = null;

  function showFocusModal(row) {
    focusUserId    = row.user_id;
    focusSessionId = row.session_id;
    focusModalName.textContent = row.full_name;
    focusModal.classList.add('visible');

    // Show latest frame if available
    const img = document.getElementById(`video-img-${row.user_id}`);
    if (img && img.src && img.style.display !== 'none') {
      focusModalImg.style.display = 'flex';
      focusModalImgEl.src = img.src;
    } else {
      focusModalImg.style.display = 'none';
      focusModalImgEl.src = '';
    }

    const online = (row.last_activity_seconds_ago || 0) <= 20;
    const camStatus = cameraStatusMap.get(row.user_id);
    const camLabel = camStatus === 'active' ? 'Active' : camStatus === 'blocked' ? 'Blocked' : 'Unknown';
    const camColor = camStatus === 'active' ? '#4ade80' : camStatus === 'blocked' ? '#f87171' : '#e8eaf0';

    focusModalDets.innerHTML = [
      `<div><span style="color:var(--text-faint)">Status</span><br><strong>${row.status?.replace('_',' ')}</strong></div>`,
      `<div><span style="color:var(--text-faint)">Connection</span><br><strong style="color:${online ? '#4ade80' : '#f87171'};">${online ? 'Online' : 'Offline'}</strong></div>`,
      `<div><span style="color:var(--text-faint)">Camera</span><br><strong style="color:${camColor};">${camLabel}</strong></div>`,
      `<div><span style="color:var(--text-faint)">Phase</span><br><strong>${row.current_phase || 1}</strong></div>`,
      `<div><span style="color:var(--text-faint)">Violation Count</span><br><strong>${row.strikes}</strong></div>`,
      `<div><span style="color:var(--text-faint)">Progress</span><br><strong>${row.answered_count} / ${row.total_questions}</strong></div>`,
      `<div><span style="color:var(--text-faint)">Domain</span><br><strong>${row.paper2_domain || 'Not selected'}</strong></div>`,
      `<div><span style="color:var(--text-faint)">Time Left</span><br><strong>${formatTime(row.time_remaining_seconds)}</strong></div>`,
    ].join('');

    focusDisqBtn.onclick = async () => {
      if (!confirm(`Disqualify ${row.full_name}? Their token will be revoked immediately.`)) return;
      try {
        await api(`/admin/candidates/${focusUserId}/disqualify`, { method: 'POST', headers: jsonHeaders, body: JSON.stringify({}) });
        focusModal.classList.remove('visible');
        fetchSnapshot();
      } catch (e) { alert('Failed: ' + e.message); }
    };
    focusRetakeBtn.onclick = async () => {
      if (!confirm(`Grant retake to ${row.full_name}?`)) return;
      try {
        const res = await api(`/admin/users/${focusUserId}/grant-retake`, { method: 'POST', headers: authHeaders });
        alert(res.message);
        focusModal.classList.remove('visible');
        fetchSnapshot();
      } catch (e) { alert('Failed: ' + e.message); }
    };
    focusResultBtn.onclick = () => {
      focusModal.classList.remove('visible');
      showResultModal(focusUserId, row.full_name);
    };
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

  // ── Live monitor ──────────────────────────────────────────────────────
  const connDot      = document.getElementById('connDot');
  const connLabel    = document.getElementById('connLabel');
  const tbody        = document.getElementById('candidateTableBody');
  const tileInProgress  = document.getElementById('tileInProgress');
  const tileCompleted   = document.getElementById('tileCompleted');
  const tileDisqualified= document.getElementById('tileDisqualified');
  const tileTotal       = document.getElementById('tileTotal');
  const searchInput     = document.getElementById('candidateSearchInput');
  const statusFilter    = document.getElementById('statusFilter');

  const candidateSessions = new Map();
  // user_id -> 'active' | 'blocked' — updated by camera_advisory WS pushes
  // from proctor.js. Purely informational, never affects strikes/violations.
  const cameraStatusMap = new Map();

  async function fetchSnapshot() {
    try {
      const rows = await api('/admin/sessions', { headers: authHeaders });
      candidateSessions.clear();
      rows.forEach(r => candidateSessions.set(r.session_id, r));
      renderMonitor();
    } catch (_) {}
  }

  function applyWsUpdate(msg) {
    const existing = candidateSessions.get(msg.session_id) || {};
    candidateSessions.set(msg.session_id, {
      ...existing,
      session_id: msg.session_id, user_id: msg.user_id,
      full_name: msg.full_name, email: msg.email,
      status: msg.status, strikes: msg.strikes, max_strikes: msg.max_strikes,
      answered_count: msg.answered_count, total_questions: msg.total_questions,
      time_remaining_seconds: msg.time_remaining_seconds,
      last_activity_seconds_ago: 0,
      domain_selected: msg.domain_selected, paper2_domain: msg.paper2_domain,
      current_phase: msg.current_phase,
      phase1_submitted_at: msg.phase1_submitted_at,
      started_at: msg.started_at, submitted_at: msg.submitted_at,
    });
    renderMonitor();
  }

  function formatTime(seconds) {
    if (seconds == null) return '—';
    const m = Math.floor(seconds / 60).toString().padStart(2, '0');
    const s = (seconds % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
  }

  function renderMonitor() {
    let rows = Array.from(candidateSessions.values()).sort((a, b) => (a.full_name||'').localeCompare(b.full_name||''));

    // Apply search filter
    const searchTerm = searchInput?.value?.trim().toLowerCase() || '';
    const statusVal  = statusFilter?.value || '';
    if (searchTerm) rows = rows.filter(r => r.full_name?.toLowerCase().includes(searchTerm) || r.email?.toLowerCase().includes(searchTerm));
    if (statusVal)  rows = rows.filter(r => r.status === statusVal);

    let inProgress = 0, completed = 0, disqualified = 0;
    const allRows = Array.from(candidateSessions.values());
    allRows.forEach(r => {
      if (r.status === 'in_progress') inProgress++;
      else if (r.status === 'completed' || r.status === 'expired') completed++;
      else if (r.status === 'disqualified') disqualified++;
    });
    tileInProgress.textContent   = inProgress;
    tileCompleted.textContent    = completed;
    tileDisqualified.textContent = disqualified;
    tileTotal.textContent        = allRows.length;

    tbody.innerHTML = '';
    rows.forEach(r => {
      const tr = document.createElement('tr');
      const stale = (r.last_activity_seconds_ago || 0) > 20 && r.status === 'in_progress';
      tr.className = stale ? 'stale-row' : '';

      const phaseDisplay = r.phase1_submitted_at
        ? `<span style="font-size:11px; color:#a5b4fc;">Phase ${r.current_phase}</span>`
        : `<span style="font-size:11px; color:#7a7f9a;">Phase 1</span>`;

      const domainCell = r.domain_selected
        ? `<span class="status-pill in_progress" style="font-size:11px;">${escapeHtml(r.paper2_domain || '—')}</span>`
        : `<span style="color:var(--text-faint); font-size:12px;">Not selected</span>`;

      const inProgress_ = r.status === 'in_progress';
      const canDisq = inProgress_;
      const canRetake = r.status === 'in_progress' || r.status === 'disqualified';
      const canResult = r.status !== 'in_progress';

      tr.innerHTML = `
        <td>
          <div>${escapeHtml(r.full_name)}</div>
          <div style="color:var(--text-faint); font-size:12px;">${escapeHtml(r.email)}</div>
        </td>
        <td><span class="status-pill ${r.status}">${r.status.replace('_', ' ')}</span></td>
        <td>${phaseDisplay}</td>
        <td><div class="strike-cluster">${Array.from({length:3},(_,i)=>`<span class="dot ${i<r.strikes?'on':''}"></span>`).join('')} <span style="font-size:11px; color:var(--text-faint);">${r.strikes}</span></div></td>
        <td class="progress-cell">${r.answered_count} / ${r.total_questions}</td>
        <td>${domainCell}</td>
        <td class="time-cell">${formatTime(r.time_remaining_seconds)}</td>
        <td class="action-cell" style="display:flex; gap:4px; flex-wrap:wrap;">
          ${canDisq   ? `<button class="btn btn-sm" data-action="disqualify" data-id="${r.user_id}" style="border-color:#ef4444; color:#ef4444; font-size:11px;">Disqualify</button>` : ''}
          ${canRetake ? `<button class="btn btn-sm" data-action="grant-retake" data-id="${r.user_id}" style="font-size:11px;">Retake</button>` : ''}
          ${canResult ? `<button class="btn btn-sm" data-action="view-result" data-id="${r.user_id}" data-name="${escapeHtml(r.full_name)}" style="font-size:11px;">Result</button>` : ''}
          <button class="btn btn-sm" data-action="focus" data-sid="${r.session_id}" style="font-size:11px;">Focus</button>
        </td>`;
      tbody.appendChild(tr);
    });

    renderVideoGrid(rows.filter(r => r.status === 'in_progress'));
  }

  // Monitor table actions
  tbody.addEventListener('click', async e => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;
    const userId = btn.dataset.id ? Number(btn.dataset.id) : null;
    const sid    = btn.dataset.sid ? Number(btn.dataset.sid) : null;

    if (action === 'disqualify') {
      const row = Array.from(candidateSessions.values()).find(r => r.user_id === userId);
      if (!confirm(`Disqualify ${row?.full_name}? This will revoke their access immediately.`)) return;
      try {
        await api(`/admin/candidates/${userId}/disqualify`, { method: 'POST', headers: jsonHeaders, body: JSON.stringify({}) });
        fetchSnapshot();
      } catch (err) { alert('Failed: ' + err.message); }

    } else if (action === 'grant-retake') {
      if (!confirm('Grant this candidate re-entry permission?')) return;
      try {
        const res = await api(`/admin/users/${userId}/grant-retake`, { method: 'POST', headers: authHeaders });
        alert(res.message); fetchSnapshot();
      } catch (err) { alert('Failed: ' + err.message); }

    } else if (action === 'view-result') {
      const name = btn.dataset.name;
      showResultModal(userId, name);

    } else if (action === 'focus') {
      const row = Array.from(candidateSessions.values()).find(r => r.session_id === sid);
      if (row) showFocusModal(row);
    }
  });

  // Search/filter bindings
  let searchDebounce_;
  searchInput?.addEventListener('input', () => {
    clearTimeout(searchDebounce_);
    searchDebounce_ = setTimeout(renderMonitor, 200);
  });
  statusFilter?.addEventListener('change', renderMonitor);

  // ── Video Grid with pagination ────────────────────────────────────────
  const toggleGridBtn = document.getElementById('toggleGridBtn');
  const tableView  = document.getElementById('tableView');
  const gridView   = document.getElementById('gridView');
  const videoGrid  = document.getElementById('videoGrid');
  const gridPrev   = document.getElementById('gridPrevPage');
  const gridNext   = document.getElementById('gridNextPage');
  const gridLabel  = document.getElementById('gridPageLabel');
  let gridActive   = false;
  let gridPage     = 0;
  const PAGE_SIZE  = 16;
  let gridRows     = [];
  const frameTimestamps = {};

  toggleGridBtn.addEventListener('click', () => {
    gridActive = !gridActive;
    tableView.style.display = gridActive ? 'none' : 'block';
    gridView.style.display  = gridActive ? 'block' : 'none';
    toggleGridBtn.textContent = gridActive ? 'View Table' : 'View Video Grid';
  });

  function renderVideoGrid(rows) {
    gridRows = rows;
    gridPage = Math.min(gridPage, Math.max(0, Math.ceil(rows.length / PAGE_SIZE) - 1));
    const start = gridPage * PAGE_SIZE;
    const pageRows = rows.slice(start, start + PAGE_SIZE);
    const totalPages = Math.ceil(rows.length / PAGE_SIZE) || 1;

    gridLabel.textContent  = `Page ${gridPage + 1} of ${totalPages}`;
    gridPrev.disabled = gridPage === 0;
    gridNext.disabled = gridPage >= totalPages - 1;

    videoGrid.innerHTML = '';
    pageRows.forEach(r => {
      const tile = document.createElement('div');
      tile.id = `video-tile-${r.user_id}`;
      tile.className = 'video-tile';
      tile.style.cssText = `border:2px solid ${r.strikes > 0 ? 'var(--danger)' : 'var(--success)'}; position:relative; background:#111; min-height:160px; display:flex; flex-direction:column; border-radius:10px; overflow:hidden; cursor:pointer;`;

      const initials = (r.full_name || '').split(' ').map(n => n[0]).join('').slice(0, 2);
      const online = (r.last_activity_seconds_ago || 0) <= 20;

      tile.innerHTML = `
        <div style="flex:1; position:relative; display:flex; align-items:center; justify-content:center; overflow:hidden;">
          <img id="video-img-${r.user_id}" loading="lazy" style="width:100%; height:auto; display:none; object-fit:contain;" alt="">
          <div id="video-fallback-${r.user_id}" style="position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; color:#fff;">
            <div style="font-size:28px; font-weight:bold; opacity:0.4;">${initials}</div>
            <div style="margin-top:8px; font-size:11px; background:rgba(220,53,69,0.8); padding:3px 8px; border-radius:4px;">No Video</div>
          </div>
          <div class="video-tile-violations" id="viol-badge-${r.user_id}">${r.strikes} violation${r.strikes === 1 ? '' : 's'}</div>
        </div>
        <div class="video-tile-badges">
          <span class="cam-badge ${online ? 'online' : 'offline'}" id="conn-badge-${r.user_id}">${online ? 'Online' : 'Offline'}</span>
          <span class="cam-badge cam-unknown" id="cam-badge-${r.user_id}">Camera —</span>
        </div>
        <div style="padding:6px 8px; background:var(--bg-panel); font-size:11px; font-weight:600; display:flex; justify-content:space-between; align-items:center;">
          <span>${escapeHtml(r.full_name)}</span>
          <span style="color:#7a7f9a; font-size:10px;">P${r.current_phase}</span>
        </div>`;

      tile.addEventListener('click', () => showFocusModal(r));
      videoGrid.appendChild(tile);
      updateCameraBadge(r.user_id);
    });
  }

  /** Reflect the last-known camera_advisory state onto a tile's badge
   * without a full re-render — called on grid (re)build and on every
   * camera_advisory push so the grid stays live between snapshot polls. */
  function updateCameraBadge(userId) {
    const badge = document.getElementById(`cam-badge-${userId}`);
    if (!badge) return;
    const camStatus = cameraStatusMap.get(userId);
    if (camStatus === 'active') {
      badge.textContent = 'Camera Active';
      badge.className = 'cam-badge cam-active';
    } else if (camStatus === 'blocked') {
      badge.textContent = 'Camera Blocked';
      badge.className = 'cam-badge cam-blocked';
    } else {
      badge.textContent = 'Camera —';
      badge.className = 'cam-badge cam-unknown';
    }
  }

  gridPrev.addEventListener('click', () => { gridPage--; renderVideoGrid(gridRows); });
  gridNext.addEventListener('click', () => { gridPage++; renderVideoGrid(gridRows); });

  // ── Download All Results (single click, CSV or Excel) ──────────────────
  async function downloadAllResults(fmt) {
    const btn = fmt === 'csv' ? document.getElementById('downloadResultsCsvBtn') : document.getElementById('downloadResultsBtn');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = fmt === 'csv' ? '…' : 'Preparing…';
    try {
      const res = await fetch(`/admin/results/export?format=${fmt}`, { headers: authHeaders });
      if (!res.ok) {
        let detail = `Export failed (${res.status})`;
        try { detail = (await res.json()).detail || detail; } catch (_) {}
        throw new Error(detail);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = fmt === 'csv' ? 'all_candidate_results.csv' : 'all_candidate_results.xlsx';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert('Download failed: ' + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  }
  document.getElementById('downloadResultsBtn')?.addEventListener('click', () => downloadAllResults('xlsx'));
  document.getElementById('downloadResultsCsvBtn')?.addEventListener('click', () => downloadAllResults('csv'));

  // ── WebSocket ─────────────────────────────────────────────────────────
  let wsRef = null;

  function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/admin?token=${encodeURIComponent(token)}`);
    ws.onopen  = () => { connDot.classList.add('live'); connLabel.textContent = 'Connected'; };
    ws.onmessage = async event => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'candidate_update') {
          applyWsUpdate(msg);
        } else if (msg.type === 'proctor_frame') {
          const img      = document.getElementById(`video-img-${msg.user_id}`);
          const fallback = document.getElementById(`video-fallback-${msg.user_id}`);
          if (img) {
            img.src = msg.frame;
            img.style.display = 'block';
            if (fallback) fallback.style.display = 'none';
            frameTimestamps[msg.user_id] = Date.now();
            // Update focus modal if it's showing this user
            if (focusUserId === msg.user_id && focusModal.classList.contains('visible')) {
              focusModalImg.style.display = 'flex';
              focusModalImgEl.src = msg.frame;
            }
          }
        } else if (msg.type === 'exam_state_changed') {
          updateExamControlBtn(msg.exam_open);
        } else if (msg.type === 'camera_advisory') {
          cameraStatusMap.set(msg.user_id, msg.camera_status);
          updateCameraBadge(msg.user_id);
          if (focusUserId === msg.user_id && focusModal.classList.contains('visible')) {
            const row = Array.from(candidateSessions.values()).find(r => r.user_id === msg.user_id);
            if (row) showFocusModal(row);
          }
        }
      } catch (_) {}
    };
    ws.onclose = () => {
      connDot.classList.remove('live'); connLabel.textContent = 'Reconnecting…';
      setTimeout(connectWebSocket, 2000);
    };
    ws.onerror = () => ws.close();
    wsRef = ws;
  }

  // Staleness timer
  setInterval(() => {
    let changed = false;
    candidateSessions.forEach(r => {
      if (r.status === 'in_progress') {
        if (r.time_remaining_seconds > 0) { r.time_remaining_seconds--; changed = true; }
        r.last_activity_seconds_ago = (r.last_activity_seconds_ago || 0) + 1;
        const lastTime = frameTimestamps[r.user_id] || 0;
        if (Date.now() - lastTime > 12000) {
          const img = document.getElementById(`video-img-${r.user_id}`);
          const fb  = document.getElementById(`video-fallback-${r.user_id}`);
          if (img && fb && img.style.display !== 'none') {
            img.style.display = 'none'; fb.style.display = 'flex';
          }
        }
      }
    });
    if (changed) renderMonitor();
  }, 1000);

  fetchSnapshot();
  fetchExamState();
  setInterval(fetchSnapshot, 8000);
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
      const canRetake = attempt === 'disqualified';
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
