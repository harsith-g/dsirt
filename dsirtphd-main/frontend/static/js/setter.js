(() => {
  const token = localStorage.getItem("access_token");
  const role = localStorage.getItem("role");
  const fullName = localStorage.getItem("full_name");
  if (!token || role !== "setter") {
    window.location.href = "/login";
    return;
  }

  const authHeaders = { Authorization: `Bearer ${token}` };
  const jsonHeaders = { ...authHeaders, "Content-Type": "application/json" };

  document.getElementById("setterShell").style.display = "block";
  document.getElementById("setterName").textContent = fullName ? `Signed in as ${fullName}` : "";

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str || "";
    return div.innerHTML;
  }

  async function api(path, options = {}) {
    const res = await fetch(path, options);
    if (res.status === 401) {
      window.location.href = "/login";
      throw new Error("unauthorized");
    }
    let data = null;
    if (res.status !== 204) {
      try { data = await res.json(); } catch (_) { data = null; }
    }
    if (!res.ok) {
      throw new Error((data && data.detail) || `Request failed (${res.status})`);
    }
    return data;
  }

  function flash(el, msg, ok = false) {
    el.textContent = msg;
    el.style.borderColor = ok ? "var(--success)" : "var(--danger)";
    el.style.color = ok ? "#a4e0bf" : "#e6a49c";
    el.style.background = ok ? "var(--success-soft)" : "var(--danger-soft)";
    el.classList.add("visible");
    setTimeout(() => el.classList.remove("visible"), 5000);
  }

  const questionMsg = document.getElementById("questionActionMessage");
  const questionsTableBody = document.getElementById("questionsTableBody");
  const questionForm = document.getElementById("questionForm");
  const qOptionsList = document.getElementById("qOptionsList");
  const qOptionsWrap = document.getElementById("qOptionsWrap");
  const qType = document.getElementById("qType");

  function addOptionRow(text = "", checked = false) {
    const row = document.createElement("div");
    row.className = "option-input-row";
    row.innerHTML = `
      <input type="radio" name="correctOption">
      <input type="text" placeholder="Option text" value="${escapeHtml(text)}">
      <button type="button" class="btn btn-sm" data-remove-option>✕</button>
    `;
    row.querySelector('input[type="radio"]').checked = checked;
    row.querySelector('[data-remove-option]').addEventListener("click", () => row.remove());
    qOptionsList.appendChild(row);
  }

  document.getElementById("addOptionBtn").addEventListener("click", () => addOptionRow());

  qType.addEventListener("change", () => {
    qOptionsWrap.style.display = qType.value === "mcq" ? "block" : "none";
  });

  let allDomains = [];
  let allQuestionsCache = [];

  async function loadMeta() {
    try {
      const meta = await api("/questions/meta", { headers: authHeaders });
      allDomains = meta.domains;
      const optionsHtml = allDomains.map((d) => `<option value="${escapeHtml(d)}">${escapeHtml(d)}</option>`).join("");
      qDomain.innerHTML = optionsHtml;
      domainFilter.innerHTML = `<option value="">All domains</option>` + optionsHtml;
    } catch (err) {
      flash(questionMsg, err.message);
    }
  }

  function resetQuestionForm() {
    document.getElementById("questionId").value = "";
    document.getElementById("qOrderNo").value = "";
    document.getElementById("qText").value = "";
    if (allDomains.length) qDomain.value = allDomains[0];
    qType.value = "mcq";
    qOptionsWrap.style.display = "block";
    qOptionsList.innerHTML = "";
    addOptionRow();
    addOptionRow();
  }

  document.getElementById("addQuestionBtn").addEventListener("click", () => {
    resetQuestionForm();
    questionForm.classList.add("visible");
    bulkImportForm.classList.remove("visible");
  });
  questionForm.querySelector('[data-cancel]').addEventListener("click", () => {
    questionForm.classList.remove("visible");
  });

  document.getElementById("submitQuestionBtn").addEventListener("click", async () => {
    const id = document.getElementById("questionId").value;
    const order_no = parseInt(document.getElementById("qOrderNo").value, 10);
    const domain = qDomain.value;
    const question_text = document.getElementById("qText").value.trim();
    const question_type = qType.value;

    if (!order_no || !question_text || !domain) {
      flash(questionMsg, "Order number, domain, and question text are required.");
      return;
    }

    let options = null;
    let correct_option = null;
    if (question_type === "mcq") {
      const rows = Array.from(qOptionsList.querySelectorAll(".option-input-row"));
      options = rows.map((r) => r.querySelector('input[type="text"]').value.trim()).filter(Boolean);
      const checkedIdx = rows.findIndex((r) => r.querySelector('input[type="radio"]').checked);
      if (options.length < 2) {
        flash(questionMsg, "Add at least 2 options for a multiple-choice question.");
        return;
      }
      if (checkedIdx === -1) {
        flash(questionMsg, "Select which option is correct.");
        return;
      }
      correct_option = checkedIdx;
    }

    const payload = { order_no, domain, question_type, question_text, options, correct_option };

    try {
      if (id) {
        await api(`/questions/${id}`, { method: "PUT", headers: jsonHeaders, body: JSON.stringify(payload) });
        flash(questionMsg, "Question updated.", true);
      } else {
        await api("/questions", { method: "POST", headers: jsonHeaders, body: JSON.stringify(payload) });
        flash(questionMsg, "Question added.", true);
      }
      questionForm.classList.remove("visible");
      loadQuestions();
    } catch (err) {
      flash(questionMsg, err.message);
    }
  });

  async function loadQuestions() {
    try {
      allQuestionsCache = await api("/questions", { headers: authHeaders });
      applyFilters();
    } catch (err) {
      flash(questionMsg, err.message);
    }
  }

  function applyFilters() {
    const domain = domainFilter.value;
    const search = questionSearch.value.trim().toLowerCase();
    let filtered = allQuestionsCache;
    if (domain) filtered = filtered.filter((q) => q.domain === domain);
    if (search) filtered = filtered.filter((q) => q.question_text.toLowerCase().includes(search));
    renderQuestions(filtered);
  }

  domainFilter.addEventListener("change", applyFilters);
  let searchDebounce;
  questionSearch.addEventListener("input", () => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(applyFilters, 200);
  });

  function renderQuestions(questions) {
    if (!questions.length) {
      questionsTableBody.innerHTML = `<tr><td colspan="5"><div class="empty-state">No questions match.</div></td></tr>`;
      return;
    }
    questionsTableBody.innerHTML = questions.map((q) => `
      <tr>
        <td class="progress-cell">${q.order_no}</td>
        <td>${escapeHtml(q.domain)}</td>
        <td><span class="status-pill in_progress">${q.question_type}</span></td>
        <td>${escapeHtml(q.question_text)}</td>
        <td class="action-cell">
          <button class="btn btn-sm" data-edit="${q.id}">Edit</button>
          <button class="btn btn-danger btn-sm" data-delete="${q.id}">Delete</button>
        </td>
      </tr>
    `).join("");

    questions.forEach((q) => {
      questionsTableBody.querySelector(`[data-edit="${q.id}"]`).addEventListener("click", () => {
        document.getElementById("questionId").value = q.id;
        document.getElementById("qOrderNo").value = q.order_no;
        qDomain.value = q.domain;
        document.getElementById("qText").value = q.question_text;
        qType.value = q.question_type;
        qOptionsWrap.style.display = q.question_type === "mcq" ? "block" : "none";
        qOptionsList.innerHTML = "";
        (q.options || []).forEach((opt, i) => addOptionRow(opt, i === q.correct_option));
        if (q.question_type === "mcq" && (!q.options || q.options.length === 0)) {
          addOptionRow(); addOptionRow();
        }
        questionForm.classList.add("visible");
        bulkImportForm.classList.remove("visible");
        questionForm.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      questionsTableBody.querySelector(`[data-delete="${q.id}"]`).addEventListener("click", async () => {
        if (!confirm("Delete this question? This can't be undone.")) return;
        try {
          await api(`/questions/${q.id}`, { method: "DELETE", headers: authHeaders });
          flash(questionMsg, "Question deleted.", true);
          loadQuestions();
        } catch (err) {
          flash(questionMsg, err.message);
        }
      });
    });
  }

  // ---------------- Bulk import (CSV) ----------------
  document.getElementById("showBulkImportBtn").addEventListener("click", () => {
    bulkImportForm.classList.add("visible");
    questionForm.classList.remove("visible");
  });
  bulkImportForm.querySelector('[data-cancel]').addEventListener("click", () => {
    bulkImportForm.classList.remove("visible");
  });

  document.getElementById("downloadTemplateBtn").addEventListener("click", async () => {
    try {
      const res = await fetch("/questions/bulk-import/template", { headers: authHeaders });
      if (!res.ok) throw new Error("Could not download template");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "question_bank_template.csv";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      flash(questionMsg, err.message);
    }
  });

  document.getElementById("submitBulkImportBtn").addEventListener("click", async () => {
    const file = csvFile.files[0];
    if (!file) {
      flash(questionMsg, "Choose a CSV file first.");
      return;
    }
    const formData = new FormData();
    formData.append("file", file);
    try {
      // No Content-Type header here on purpose — the browser sets the
      // multipart boundary itself; setting it manually breaks the upload.
      const result = await api("/questions/bulk-import", { method: "POST", headers: authHeaders, body: formData });
      let msg = `Imported ${result.created} question(s).`;
      if (result.errors.length) {
        msg += ` ${result.errors.length} row(s) skipped: ` +
          result.errors.slice(0, 3).map((e) => `row ${e.row}: ${e.message}`).join("; ");
        if (result.errors.length > 3) msg += ` (+${result.errors.length - 3} more)`;
      }
      flash(questionMsg, msg, result.errors.length === 0);
      bulkImportForm.classList.remove("visible");
      csvFile.value = "";
      loadQuestions();
    } catch (err) {
      flash(questionMsg, err.message);
    }
  });

  loadMeta().then(loadQuestions);
})();
