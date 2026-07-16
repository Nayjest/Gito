// ═══════════════════════════════════════════════════════════════════════════
//  Code Doctor  ·  Frontend  ·  Production build
// ═══════════════════════════════════════════════════════════════════════════

// ── App state ────────────────────────────────────────────────────────────────
const state = {
  authRequired: false,
  token: localStorage.getItem("codeDoctorToken") || "",
  health: null,
  overview: null,
  repos: [],
  policies: null,
  reviews: [],
  selectedRunId: null,
  detail: null,
  log: "",
  audit: [],
  preflight: null,
  currentView: "cockpit",
  publishConfig: null,
  publishPreviewed: false,
};

// ── Selector helpers ─────────────────────────────────────────────────────────
const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

// ── XSS escaping ─────────────────────────────────────────────────────────────
function esc(val) {
  return String(val ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function stripAnsi(val) {
  return String(val || "").replace(/\[[0-?]*[ -/]*[@-~]/g, "");
}

// ── Toast system ─────────────────────────────────────────────────────────────
const TOAST_DURATION = 4000;

function toast(message, type = "info") {
  const stack = $("#toastStack");
  const el = document.createElement("div");
  el.className = `toast toast--${type}`;
  el.innerHTML = `
    <span class="toast-dot"></span>
    <span class="toast-msg">${esc(message)}</span>
    <button class="toast-close" type="button" aria-label="Dismiss">×</button>
  `;

  const dismiss = () => {
    el.classList.add("toast--out");
    el.addEventListener("animationend", () => el.remove(), { once: true });
  };

  el.querySelector(".toast-close").addEventListener("click", dismiss);
  stack.appendChild(el);
  setTimeout(dismiss, TOAST_DURATION);
}

const toastSuccess = (msg) => toast(msg, "success");
const toastError   = (msg) => toast(msg, "error");
const toastInfo    = (msg) => toast(msg, "info");

// ── API client ───────────────────────────────────────────────────────────────
async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  if (state.token) headers["Authorization"] = `Bearer ${state.token}`;

  const res = await fetch(path, { ...options, headers });

  if (res.status === 401) {
    showAuthRequired();
  }

  const ct = res.headers.get("content-type") || "";
  const payload = ct.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error(payload?.error || payload || res.statusText);
  return payload;
}

function showAuthRequired() {
  const banner = $("#authBanner");
  banner.classList.remove("hidden");
  $("#authBannerText").textContent = "API access is protected. Enter the workspace token.";
  $("#tokenSection").classList.add("visible");
}

// ── Utility ──────────────────────────────────────────────────────────────────
function setText(sel, val) {
  const node = $(sel);
  if (node) node.textContent = val;
}

function formatTime(val) {
  if (!val) return "—";
  return new Date(val).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function formatDuration(secs) {
  if (!secs) return "—";
  if (secs < 60) return `${Math.round(secs)}s`;
  return `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s`;
}

function basename(path) {
  return String(path || "").split("/").filter(Boolean).pop() || path || "—";
}

function flattenIssues(report) {
  if (!report?.issues) return [];
  return Object.entries(report.issues).flatMap(([file, issues]) =>
    issues.map((issue) => ({ ...issue, file: issue.file || file }))
  );
}

function selectedMeta() {
  return state.detail?.meta || state.reviews.find((r) => r.id === state.selectedRunId) || null;
}

function gateClass(gate) {
  return { pass: "gate--pass", review: "gate--review", block: "gate--block" }[gate] || "gate--muted";
}

function sevClass(sev) {
  return `sev-${Math.min(sev || 5, 5)}`;
}

// ── Scope controls ────────────────────────────────────────────────────────────
function updateScopeControls() {
  const mode = new FormData($("#reviewForm")).get("mode");
  const form = $("#reviewForm");
  if (mode === "refs") {
    form.classList.add("refs-visible");
  } else {
    form.classList.remove("refs-visible");
  }
}

// ── Hydrate form from a run meta object ──────────────────────────────────────
function hydrateFormFromMeta(meta) {
  if (!meta) return;
  if (meta.repo_path) $("#repoPath").value = meta.repo_path;
  if (meta.ollama_base) $("#ollamaBase").value = meta.ollama_base;
  if (meta.model) { $("#model").value = meta.model; setText("#activeModel", meta.model); }
  if (meta.filters) $("#filters").value = meta.filters;
  if (meta.refs)    $("#refs").value = meta.refs;
  if (meta.against) $("#against").value = meta.against;
  $("#mergeBase").checked = meta.merge_base !== false;
  const mode = meta.mode || "working";
  const modeEl = $(`input[name="mode"][value="${mode}"]`);
  if (modeEl) modeEl.checked = true;
  updateScopeControls();
}

// ── View switching ────────────────────────────────────────────────────────────
const VIEW_TITLES = {
  cockpit:    "Cockpit",
  review:     "Review",
  repos:      "Repositories",
  findings:   "Findings",
  reports:    "Reports",
  governance: "Governance",
  audit:      "Audit Log",
};

function showView(view) {
  state.currentView = view;
  $$(".nav-item").forEach((btn) => btn.classList.toggle("active", btn.dataset.view === view));
  $$(".view").forEach((section) => section.classList.remove("active"));
  $(`#view-${view}`)?.classList.add("active");
  setText("#viewTitle", VIEW_TITLES[view] || view);
}

// ── Render: health ────────────────────────────────────────────────────────────
function renderHealth() {
  const h = state.health;
  if (!h) return;

  document.body.classList.toggle("auth-required", h.authRequired);
  state.authRequired = h.authRequired;
  if (h.authRequired) {
    $("#tokenSection").classList.add("visible");
  }

  // Ollama
  const ollamaOk = h.ollama?.ok;
  $("#ollamaStatusDot").dataset.state = ollamaOk ? "ok" : "error";
  setText("#ollamaStatus", ollamaOk ? "Online" : "Offline");

  // Git
  const gitOk = h.git?.ok;
  $("#gitStatusDot").dataset.state = gitOk ? "ok" : "error";
  setText("#gitStatus", gitOk ? (h.git.version?.replace("git version ", "") || "OK") : "Missing");

  // Auth
  setText("#authStatus", h.authRequired ? "Token" : "Local");
  $("#authStatusDot").dataset.state = "ok";

  // Model suggestions
  const models = h.ollama?.models || [];
  $("#modelList").innerHTML = models.map((m) => `<option value="${esc(m)}"></option>`).join("");

  // Defaults
  if (!$("#repoPath").value && h.defaults?.repoPath)   $("#repoPath").value = h.defaults.repoPath;
  if (!$("#newRepoPath").value && h.defaults?.repoPath) $("#newRepoPath").value = h.defaults.repoPath;
  if (!$("#ollamaBase").value) $("#ollamaBase").value = h.defaults?.ollamaBase || "http://localhost:11434";
  if (!$("#model").value) {
    const defaultModel = models[0] || h.defaults?.model || "llama3.1:8b";
    $("#model").value = defaultModel;
    setText("#activeModel", defaultModel);
  }
  if (!$("#filters").value) $("#filters").value = h.defaults?.filters || "*.py,*.js,*.jsx,*.ts,*.tsx";
}

// ── Render: overview / cockpit ────────────────────────────────────────────────
function renderOverview() {
  const ov = state.overview || {};
  const m  = ov.metrics || {};
  const gc = m.gateCounts || {};

  setText("#metricRepos",       m.repos      ?? 0);
  setText("#metricReviews",     m.reviews    ?? 0);
  setText("#metricIssues",      m.issues     ?? 0);
  setText("#metricAvgRisk",     m.avgRisk    ?? 0);
  setText("#metricBlocked",     gc.block     ?? 0);
  setText("#metricReviewGate",  gc.review    ?? 0);

  // Latest review — generation runs never stand in for a review here
  const selected = selectedMeta();
  const latest = ov.latestReview || (selected && !selected.kind ? selected : null);
  const ls = latest?.stats || {};
  const lg = $("#latestGate");
  lg.className = `gate-pill ${gateClass(ls.gate)}`;
  lg.textContent = ls.gate ? ls.gate.toUpperCase() : "No Run";
  setText("#latestReview", ls.summary || "No completed review yet. Start a review or load sample data.");
  $("#latestTags").innerHTML = (ls.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("");

  const issues = flattenIssues(state.detail?.report).slice(0, 4);
  $("#latestFindings").innerHTML = issues.length
    ? issues.map((iss) => `
        <div class="mini-row sev-${esc(iss.severity || 5)}">
          <span class="sev-badge">S${esc(iss.severity || "?")}</span>
          <span class="issue-title">${esc(iss.title)}</span>
          <span class="issue-file">${esc(iss.file)}</span>
        </div>
      `).join("")
    : `<p class="empty-msg" style="margin-top:8px">No findings loaded for the selected run.</p>`;

  // Readiness
  const readiness = ov.readiness || [];
  const readyCount = readiness.filter((r) => r.ready).length;
  setText("#readinessScore", `${readyCount}/${readiness.length}`);
  $("#readinessList").innerHTML = readiness.map((item) => `
    <div class="readiness-row">
      <div>
        <strong>${esc(item.label)}</strong>
        <span>${esc(item.detail)}</span>
      </div>
      <em class="${item.ready ? "ready" : "not-ready"}">${item.ready ? "✓ Ready" : "Setup Needed"}</em>
    </div>
  `).join("");

  // Gate breakdown
  $("#gateBreakdown").innerHTML = ["block", "review", "pass"].map((g) => `
    <div class="gate-card ${`gate--${g}`}">
      <span>${g.toUpperCase()}</span>
      <strong>${esc(gc[g] ?? 0)}</strong>
    </div>
  `).join("");

  // Top tags
  const tags = m.topTags || [];
  $("#topTags").innerHTML = tags.length
    ? tags.map(([tag, count]) => `<span class="tag">${esc(tag)} <strong style="opacity:.7">${esc(count)}</strong></span>`).join("")
    : `<p class="empty-msg">No recurring tags yet. Run a review first.</p>`;
}

// ── Render: run list (sidebar) ────────────────────────────────────────────────
function renderRuns() {
  const list = $("#runList");
  if (!state.reviews.length) {
    list.innerHTML = `<div class="run-item"><strong>No runs yet</strong><span>Start a review or load sample data</span></div>`;
    return;
  }
  list.innerHTML = state.reviews.map((run) => {
    const active = run.id === state.selectedRunId ? " active" : "";
    const stats = run.stats || {};
    const gate = stats.gate ? ` · ${stats.gate.toUpperCase()}` : "";
    const issues = stats.total_issues != null ? ` · ${stats.total_issues} issues` : "";
    const kindTag = { tests: " · TESTGEN", pr: " · PR DRAFT" }[run.kind] || "";
    const line = `${run.status || "unknown"}${kindTag}${issues}${gate}`;
    return `
      <button class="run-item${active}" data-run-id="${esc(run.id)}" type="button">
        <strong>${esc(basename(run.repo_path))}</strong>
        <span>${esc(line)}<br>${esc(formatTime(run.created_at))}</span>
      </button>
    `;
  }).join("");

  $$(".run-item[data-run-id]").forEach((btn) => {
    btn.addEventListener("click", () => selectRun(btn.dataset.runId));
  });
}

// ── Render: selected run (review view) ───────────────────────────────────────
function renderSelectedRun() {
  const meta = selectedMeta();
  const stats = meta?.stats || {};
  const genNoun = { tests: "Unit-test generation", pr: "PR draft" }[meta?.kind];

  setText("#workspaceLabel", meta?.repo_path || $("#repoPath").value || "No repository selected");
  setText("#runState", meta?.status || "Idle");
  if (meta?.status === "running" || meta?.status === "queued") {
    $("#runState").classList.add("running");
  } else {
    $("#runState").classList.remove("running");
  }

  setText("#riskScore",  stats.risk_score    ?? 0);
  setText("#issueCount", stats.total_issues  ?? 0);
  setText("#fileCount",  stats.processed_files ?? 0);
  setText("#duration",   formatDuration(meta?.duration_seconds));
  const genSummary = genNoun
    ? `${genNoun} run — open Reports to view the generated artifacts.`
    : "";
  setText("#summaryBox", genSummary || stats.summary || "No completed review selected.");
  const trustBits = [
    `${stats.total_issues ?? 0} open`,
    ...(stats.verification?.confirmed ? [`${stats.verification.confirmed} verified`] : []),
    ...(stats.rejected_issues ? [`${stats.rejected_issues} auto-rejected`] : []),
    ...(stats.suppressed ? [`${stats.suppressed} dismissed`] : []),
  ];
  setText("#findingMeta", trustBits.join(" · "));

  setText("#reportRunState", meta?.status || "No Run");
  setText("#reportSummary", genSummary || stats.summary || "Select a review run from the sidebar.");

  const g  = stats.gate;
  const gp = $("#gatePill");
  if (gp) {
    gp.className = `gate-pill ${gateClass(g)}`;
    gp.textContent = g ? g.toUpperCase() : (genNoun ? (meta?.status || "gen").toUpperCase() : "No Run");
  }

  $("#tagStrip").innerHTML = (stats.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("");
}

// ── Render: findings ──────────────────────────────────────────────────────────
function renderFindings() {
  const issues = flattenIssues(state.detail?.report);
  const list = $("#findingsList");

  // Update findings badge
  const badge = $("#findingsBadge");
  if (badge) badge.textContent = issues.length > 0 ? String(issues.length) : "";

  if (!issues.length) {
    list.innerHTML = state.detail?.report
      ? `<div class="empty-state"><p>No findings in this review. Great job!</p></div>`
      : `<div class="empty-state"><p>No findings loaded. Select a review run.</p></div>`;
    renderFindingQueue(issues);
    return;
  }

  list.innerHTML = issues.map((issue) => {
    const primary = issue.affected_lines?.[0];
    const loc = primary
      ? `${issue.file}:${primary.start_line}${primary.end_line && primary.end_line !== primary.start_line ? `–${primary.end_line}` : ""}`
      : issue.file;
    const proposal = issue.affected_lines?.find((l) => l.proposal)?.proposal;
    const affected = issue.affected_lines?.find((l) => l.affected_code)?.affected_code;
    const verdictChip = issue.verified === true
      ? `<span class="verdict-chip verdict-confirmed" title="${esc(issue.verifier_reason || "Confirmed by the verification pass")}">✓ verified</span>`
      : issue.verified === false
        ? `<span class="verdict-chip verdict-uncertain" title="${esc(issue.verifier_reason || "The verifier could not confirm this from the diff")}">? unverified</span>`
        : "";

    return `
      <article class="finding-card fade-in${issue.suppressed ? " suppressed" : ""}" id="finding-${esc(issue.id)}">
        <div class="finding-card-header" role="button" tabindex="0" data-finding="${esc(issue.id)}" aria-expanded="false">
          <div class="finding-meta">
            <div class="finding-title-row">
              <span class="sev-pill ${sevClass(issue.severity)}">S${esc(issue.severity || "?")}</span>
              <h3 class="finding-title">#${esc(issue.id)} ${esc(issue.title)}</h3>
              ${verdictChip}
              ${issue.suppressed ? `<span class="verdict-chip verdict-suppressed">dismissed</span>` : ""}
            </div>
            <div class="finding-loc">${esc(loc)}</div>
            <div class="tag-row" style="padding-top:6px">
              ${(issue.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}
            </div>
          </div>
          <button class="btn btn-sm btn-ghost finding-feedback" data-issue="${esc(issue.id)}"
                  data-action="${issue.suppressed ? "restore" : "dismiss"}" type="button"
                  title="${issue.suppressed ? "Count this finding again" : "Suppress this finding from risk scoring in every run"}">
            ${issue.suppressed ? "Restore" : "Dismiss"}
          </button>
        </div>
        <div class="finding-body" id="finding-body-${esc(issue.id)}">
          <p class="finding-details">${esc(issue.details)}</p>
          ${issue.verifier_reason ? `<p class="help-text">Verifier: ${esc(issue.verifier_reason)}</p>` : ""}
          ${affected ? `
            <div class="code-block-label">Affected code</div>
            <pre class="code-block">${esc(affected)}</pre>
          ` : ""}
          ${proposal ? `
            <div class="code-block-label">Proposed fix</div>
            <pre class="code-block proposal">${esc(proposal)}</pre>
          ` : ""}
        </div>
      </article>
    `;
  }).join("");

  $$(".finding-feedback").forEach((btn) => btn.addEventListener("click", (event) => {
    event.stopPropagation();
    sendFindingFeedback(btn.dataset.issue, btn.dataset.action);
  }));

  // Wire expand/collapse
  $$(".finding-card-header").forEach((header) => {
    const toggle = () => {
      const id   = header.dataset.finding;
      const body = $(`#finding-body-${id}`);
      const open = body?.classList.toggle("open");
      header.setAttribute("aria-expanded", String(open));
    };
    header.addEventListener("click", toggle);
    header.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
  });

  renderFindingQueue(issues);
}

// ── Render: generated test cases ────────────────────────────────────────────
function renderTestCases() {
  const plan = state.detail?.test_cases || null;
  const cases = plan?.cases || [];
  const list = $("#testCaseList");
  if (!list) return;

  setText(
    "#testCaseMeta",
    state.selectedRunId ? `${cases.length} cases / ${plan?.ai_app_cases ?? 0} AI app` : "No run"
  );

  if (!cases.length) {
    list.innerHTML = state.detail
      ? `<div class="empty-state"><p>No generated test cases for this review.</p></div>`
      : `<div class="empty-state"><p>No review selected.</p></div>`;
    return;
  }

  list.innerHTML = cases.map((tc) => {
    const priority = String(tc.priority || "P2").toLowerCase().replace(/[^a-z0-9_-]/g, "");
    const steps = (tc.steps || []).slice(0, 5).map((step) => `<li>${esc(step)}</li>`).join("");
    return `
      <article class="test-case-row fade-in">
        <div class="test-case-head">
          <span class="test-priority test-priority-${esc(priority)}">${esc(tc.priority || "P2")}</span>
          <div>
            <h3 class="test-case-title">${esc(tc.title)}</h3>
            <div class="test-case-meta">
              <span>${esc(tc.type || "test")}</span>
              <span>${esc(tc.file || "scope")}</span>
              <span>${esc(tc.source || "generated")}</span>
            </div>
          </div>
        </div>
        <p class="test-case-rationale">${esc(tc.rationale)}</p>
        ${steps ? `<ol class="test-case-steps">${steps}</ol>` : ""}
        <div class="test-case-expected">
          <strong>Expected</strong>
          <span>${esc(tc.expected)}</span>
        </div>
        <div class="test-case-hint">${esc(tc.automation_hint)}</div>
      </article>
    `;
  }).join("");
}

// ── Clipboard ────────────────────────────────────────────────────────────────
async function copyText(text, label) {
  try {
    await navigator.clipboard.writeText(text);
    toastSuccess(`${label} copied to clipboard.`);
  } catch (_) {
    toastError("Copy failed — clipboard unavailable.");
  }
}

// ── Render: generated unit tests + PR draft (reports view) ──────────────────
function generationPlaceholder(kind) {
  const meta = selectedMeta();
  if (meta?.kind === kind && ["queued", "running"].includes(meta.status)) {
    return `<div class="empty-state"><p>Generating… this can take a minute on local models. The panel updates automatically.</p></div>`;
  }
  if (meta?.kind === kind && meta.status === "failed") {
    return `<div class="empty-state"><p>Generation failed — check the execution log in the Review view.</p></div>`;
  }
  const noun = kind === "tests" ? "generated tests" : "PR draft";
  return `<div class="empty-state"><p>No ${noun} for the selected run.</p></div>`;
}

function renderGenerated() {
  const testsBox = $("#generatedTests");
  const prBox = $("#prDraft");
  if (!testsBox || !prBox) return;

  const gt = state.detail?.generated_tests;
  if (gt?.files?.length) {
    testsBox.innerHTML = gt.files.map((file, idx) => `
      <article class="gen-file fade-in">
        <div class="gen-file-head">
          <div>
            <strong class="gen-file-path">${esc(file.path)}</strong>
            <span class="meta-label">${esc(file.framework || "")}${file.covers?.length ? ` · covers ${esc(file.covers.join(", "))}` : ""}</span>
          </div>
          <button class="btn btn-sm btn-ghost copy-test" data-idx="${idx}" type="button">Copy</button>
        </div>
        ${file.rationale ? `<p class="help-text">${esc(file.rationale)}</p>` : ""}
        <pre class="code-block">${esc(file.content)}</pre>
      </article>
    `).join("") + (gt.notes ? `<p class="help-text">${esc(gt.notes)}</p>` : "");
    $$(".copy-test").forEach((btn) => btn.addEventListener("click", () =>
      copyText(gt.files[Number(btn.dataset.idx)]?.content || "", "Test file")));
  } else {
    testsBox.innerHTML = generationPlaceholder("tests");
  }

  const pr = state.detail?.pr_draft;
  if (pr?.title) {
    const checklist = (pr.checklist || []).map((item) => `<li>${esc(item)}</li>`).join("");
    prBox.innerHTML = `
      <article class="gen-file fade-in">
        <div class="gen-file-head">
          <strong class="gen-file-path">${esc(pr.title)}</strong>
          <button id="copyPrDraft" class="btn btn-sm btn-ghost" type="button">Copy Markdown</button>
        </div>
        <div class="tag-row" style="padding-bottom:8px">${(pr.labels || []).map((l) => `<span class="tag">${esc(l)}</span>`).join("")}</div>
        <pre class="code-block pr-draft-body">${esc(pr.body_markdown)}</pre>
        ${checklist ? `<div class="code-block-label">Reviewer checklist</div><ul class="pr-checklist">${checklist}</ul>` : ""}
      </article>
    `;
    $("#copyPrDraft")?.addEventListener("click", () =>
      copyText(`# ${pr.title}\n\n${pr.body_markdown}`, "PR draft"));
  } else {
    prBox.innerHTML = generationPlaceholder("pr");
  }
}

// ── Render: cross-file impact (review view) ─────────────────────────────────
function renderCrossFile() {
  const box = $("#crossFileBox");
  if (!box) return;
  const pack = state.detail?.context_pack;
  const files = pack?.files || {};
  const entries = Object.entries(files);
  const crossFindings = state.detail?.meta?.stats?.cross_file_issues ?? 0;

  setText(
    "#crossFileMeta",
    pack
      ? `${entries.length} changed · ${pack.graph_files ?? 0} files in graph · ${crossFindings} contract findings`
      : "No run"
  );

  if (!entries.length) {
    box.innerHTML = state.detail
      ? `<div class="empty-state"><p>No cross-file context for this run (older run, or no source changes).</p></div>`
      : `<div class="empty-state"><p>No review selected.</p></div>`;
    return;
  }

  box.innerHTML = entries.map(([file, info]) => {
    const symbols = info.symbols || {};
    const removed = symbols.removed || [];
    const changed = (symbols.signature_changed || []).map((c) => c.name);
    const dependents = info.dependents || [];
    const usageRows = Object.entries(info.usages || {}).flatMap(([name, usages]) =>
      usages.slice(0, 3).map((u) => `
        <div class="crossfile-usage">
          <span class="tag">${esc(name)}</span>
          <code>${esc(u.file)}:${esc(u.line)}</code>
          <span class="crossfile-code">${esc(u.code)}</span>
          ${u.break_reason ? `<span class="tag tag-danger" title="Argument-binding check">breaks: ${esc(u.break_reason)}</span>` : ""}
          ${u.verified_ok ? `<span class="tag" title="Call still binds against the new signature">binds ok</span>` : ""}
        </div>
      `)
    ).join("");
    return `
      <article class="crossfile-row fade-in">
        <div class="crossfile-head">
          <strong>${esc(file)}</strong>
          <span class="meta-label">${dependents.length} dependent(s) · ${esc((info.imports || []).length)} import(s)</span>
        </div>
        ${dependents.length ? `<div class="crossfile-deps">Imported by: ${dependents.slice(0, 6).map((d) => `<code>${esc(d)}</code>`).join(" ")}</div>` : ""}
        ${removed.length ? `<div class="crossfile-alert">Removed symbols: ${removed.map((s) => `<code>${esc(s)}</code>`).join(" ")}</div>` : ""}
        ${changed.length ? `<div class="crossfile-alert">Changed signatures: ${changed.map((s) => `<code>${esc(s)}</code>`).join(" ")}</div>` : ""}
        ${usageRows}
      </article>
    `;
  }).join("");
}

// ── Render: publish panel (reports view) ────────────────────────────────────
function renderPublishConfig() {
  const cfg = state.publishConfig;
  if (!cfg) return;
  const ready = [];
  if (cfg.github?.configured) ready.push("GitHub");
  if (cfg.gitlab?.configured) ready.push("GitLab");
  setText("#pubConfigState", ready.length ? `${ready.join(" + ")} configured` : "No tokens configured");
  const published = state.detail?.publish_result;
  if (published?.posted && !state.publishPreviewed) {
    $("#pubResult").innerHTML = `
      <div class="warning-item" style="color:var(--green);background:var(--green-dim);border-color:rgba(63,185,80,0.2)">
        Published to ${esc(published.target)} (${esc(published.posted.mode)})
        ${published.posted.url ? ` — <a href="${esc(published.posted.url)}" target="_blank" rel="noreferrer">view</a>` : ""}
      </div>`;
  }
}
function renderFindingQueue(issues = flattenIssues(state.detail?.report)) {
  setText("#queueMeta", state.selectedRunId ? `${issues.length} findings` : "No run");
  const queue = $("#findingQueue");
  if (!issues.length) {
    queue.innerHTML = `<div class="empty-state"><p>No findings to show.</p></div>`;
    return;
  }
  queue.innerHTML = issues.map((iss) => {
    const line = iss.affected_lines?.[0]?.start_line;
    return `
      <div class="finding-row">
        <span class="sev-pill ${sevClass(iss.severity)}">S${esc(iss.severity || "?")}</span>
        <span>${esc(iss.title)}<br><span style="font-size:11px;color:var(--text-muted)">${esc(iss.file)}${line ? `:${esc(line)}` : ""}</span></span>
        <span>${esc((iss.tags || [])[0] || "—")}</span>
        <span>Conf. ${esc(iss.confidence || "—")}</span>
      </div>
    `;
  }).join("");
}

// ── Render: repos ─────────────────────────────────────────────────────────────
function renderRepos() {
  const repos = state.repos || [];

  // Coverage summary
  const pyCount = repos.filter((r) => r.metadata?.languages?.includes("Python")).length;
  const jsCount = repos.filter((r) => r.metadata?.languages?.includes("Node.js")).length;
  $("#repoCoverage").innerHTML = `
    <div class="cov-card"><span>Registered</span><strong>${esc(repos.length)}</strong></div>
    <div class="cov-card"><span>Python</span><strong>${esc(pyCount)}</strong></div>
    <div class="cov-card"><span>Node.js</span><strong>${esc(jsCount)}</strong></div>
    <div class="cov-card"><span>Tiers</span><strong>${esc(new Set(repos.map((r) => r.tier)).size)}</strong></div>
  `;

  const list = $("#repoList");
  if (!repos.length) {
    list.innerHTML = `<div class="empty-state"><p>No repositories registered. Add one above.</p></div>`;
    return;
  }
  list.innerHTML = repos.map((repo) => {
    const meta = repo.metadata || {};
    const last = repo.lastReview;
    const tier = { production: "🔴", staging: "🟡", development: "🟢" }[repo.tier] || "";
    return `
      <article class="repo-card">
        <div>
          <div class="repo-name">${esc(repo.name)}</div>
          <div class="repo-path">${esc(repo.path)}</div>
          <div class="tag-row" style="padding-top:8px">
            ${(meta.languages || []).map((l) => `<span class="tag">${esc(l)}</span>`).join("")}
            ${meta.branch ? `<span class="tag" style="background:var(--violet-dim);color:var(--violet);border-color:transparent">${esc(meta.branch)}</span>` : ""}
          </div>
        </div>
        <div class="repo-meta">
          <span>${tier} ${esc(repo.owner)} · ${esc(repo.tier)}</span>
          <span>${esc(meta.trackedFiles || 0)} tracked files</span>
          ${meta.commit ? `<span>HEAD: ${esc(meta.commit)}</span>` : ""}
          <span>${last ? `Last: ${esc(last.status)} · ${esc(last.stats?.total_issues ?? 0)} issues` : "Not reviewed"}</span>
        </div>
        <div class="repo-actions">
          <button class="btn btn-sm btn-ghost use-repo"   data-repo-id="${esc(repo.id)}" type="button">Use</button>
          <button class="btn btn-sm btn-secondary review-repo" data-repo-id="${esc(repo.id)}" type="button">Review</button>
          <button class="btn btn-sm btn-ghost remove-repo" data-repo-id="${esc(repo.id)}" type="button" style="color:var(--red)">Remove</button>
        </div>
      </article>
    `;
  }).join("");

  $$(".use-repo").forEach((btn)    => btn.addEventListener("click", () => useRepo(btn.dataset.repoId)));
  $$(".review-repo").forEach((btn) => btn.addEventListener("click", () => reviewRepo(btn.dataset.repoId)));
  $$(".remove-repo").forEach((btn) => btn.addEventListener("click", () => removeRepo(btn.dataset.repoId)));
}

// ── Render: policies / governance ────────────────────────────────────────────
function renderPolicies() {
  const p = state.policies || {};
  const r = p.risk || {};
  if ($("#blockSeverity"))       $("#blockSeverity").value       = r.blockSeverity ?? 1;
  if ($("#reviewSeverity"))      $("#reviewSeverity").value      = r.reviewSeverity ?? 2;
  if ($("#maxRiskScore"))        $("#maxRiskScore").value        = r.maxRiskScore ?? 24;
  if ($("#sensitiveFileReview")) $("#sensitiveFileReview").checked = r.sensitiveFileReview !== false;

  $("#guardrailList").innerHTML = (p.guardrails || []).map((g) => `
    <div class="guardrail-row">
      <strong>${esc(g.name)}</strong>
      <span>${esc(g.evidence)}</span>
      <em class="${g.enabled ? "ready" : "not-ready"}">${g.enabled ? "✓ Enabled" : "Setup Needed"}</em>
    </div>
  `).join("");
}

// ── Render: preflight ─────────────────────────────────────────────────────────
function renderPreflight() {
  const pf = state.preflight;
  if (!pf) return;

  setText("#preflightState", pf.ready ? "Ready" : "Needs Attention");

  const warnings = pf.warnings || [];
  $("#preflightBox").innerHTML = `
    <div class="preflight-stats">
      <div class="preflight-stat">
        <span>Changed Files</span>
        <strong>${esc(pf.changedFiles?.length ?? 0)}</strong>
      </div>
      <div class="preflight-stat">
        <span>Sensitive Files</span>
        <strong style="${pf.sensitiveFiles?.length ? "color:var(--amber)" : ""}">${esc(pf.sensitiveFiles?.length ?? 0)}</strong>
      </div>
      <div class="preflight-stat">
        <span>Test Files</span>
        <strong>${esc(pf.testFiles?.length ?? 0)}</strong>
      </div>
    </div>
    ${warnings.length
      ? `<div class="warning-list">${warnings.map((w) => `<div class="warning-item">${esc(w)}</div>`).join("")}</div>`
      : `<div class="warning-item" style="color:var(--green);background:var(--green-dim);border-color:rgba(63,185,80,0.2)">All preflight checks passed.</div>`
    }
  `;
}

// ── Render: log ───────────────────────────────────────────────────────────────
function renderLog() {
  const log = $("#logOutput");
  const text = stripAnsi(state.log) || "No log output yet.";
  log.textContent = text;
  // Auto-scroll to bottom
  log.scrollTop = log.scrollHeight;
}

// ── Render: audit ─────────────────────────────────────────────────────────────
function renderAudit() {
  const list = $("#auditTrail");
  if (!state.audit.length) {
    list.innerHTML = `<div class="empty-state"><p>No audit events recorded yet.</p></div>`;
    return;
  }
  list.innerHTML = state.audit.map((ev) => `
    <div class="audit-row">
      <strong>${esc(ev.event)}</strong>
      <span>${esc(ev.repo_path || ev.run_id || ev.repo_id || "—")}</span>
      <span class="audit-time">${esc(formatTime(ev.ts))}</span>
    </div>
  `).join("");
}

// ── Render all ────────────────────────────────────────────────────────────────
function renderAll() {
  renderHealth();
  renderOverview();
  renderRuns();
  renderSelectedRun();
  renderFindings();
  renderTestCases();
  renderGenerated();
  renderCrossFile();
  renderPublishConfig();
  renderRepos();
  renderPolicies();
  renderPreflight();
  renderLog();
  renderAudit();
}

// ── Data fetching ─────────────────────────────────────────────────────────────
async function refreshHealth() {
  const base = encodeURIComponent($("#ollamaBase")?.value || "http://localhost:11434");
  state.health = await api(`/api/health?ollamaBase=${base}`);
  renderHealth();
}

async function refreshOverview() {
  state.overview = await api("/api/overview");
  renderOverview();
}

async function refreshRepos() {
  const data = await api("/api/repos");
  state.repos = data.repos || [];
  renderRepos();
}

async function refreshPolicies() {
  state.policies = await api("/api/policies");
  renderPolicies();
}

async function refreshRuns() {
  const data = await api("/api/reviews");
  state.reviews = data.reviews || [];

  // Auto-select latest if nothing selected — prefer review runs over
  // generation runs so the cockpit and review panels stay populated.
  if (!state.selectedRunId && state.reviews.length) {
    const preferred = state.reviews.find((run) => !run.kind || run.kind === "review") || state.reviews[0];
    state.selectedRunId = preferred.id;
    await loadSelected();
    hydrateFormFromMeta(selectedMeta());
  }
  renderRuns();
  renderSelectedRun();
  renderFindings();
  renderTestCases();
  renderGenerated();
  renderCrossFile();
}

async function refreshAudit() {
  const data = await api("/api/audit?limit=150");
  state.audit = data.events || [];
  renderAudit();
}

async function refreshPublishConfig() {
  state.publishConfig = await api("/api/publish/config");
  renderPublishConfig();
}

async function loadSelected() {
  if (!state.selectedRunId) return;
  [state.detail, state.log] = await Promise.all([
    api(`/api/reviews/${encodeURIComponent(state.selectedRunId)}`),
    api(`/api/reviews/${encodeURIComponent(state.selectedRunId)}/log`),
  ]);
}

async function selectRun(runId) {
  state.selectedRunId = runId;
  // A publish preview belongs to one run — never carry it across.
  state.publishPreviewed = false;
  const pubPublish = $("#pubPublish");
  if (pubPublish) pubPublish.disabled = true;
  const pubResult = $("#pubResult");
  if (pubResult) pubResult.innerHTML = "";
  try {
    await loadSelected();
  } catch (err) {
    toastError(`Failed to load run: ${err.message}`);
  }
  hydrateFormFromMeta(selectedMeta());
  renderAll();
}

async function refreshEverything() {
  await Promise.allSettled([
    refreshHealth(),
    refreshOverview(),
    refreshRepos(),
    refreshPolicies(),
    refreshRuns(),
    refreshAudit(),
    refreshPublishConfig(),
  ]);
}

// ── Form helpers ──────────────────────────────────────────────────────────────
function formPayload() {
  const mode = new FormData($("#reviewForm")).get("mode");
  return {
    repoPath:           $("#repoPath").value.trim(),
    ollamaBase:         $("#ollamaBase").value.trim(),
    model:              $("#model").value.trim(),
    mode,
    refs:               mode === "refs" ? $("#refs").value.trim() : "",
    against:            mode === "refs" ? $("#against").value.trim() : "",
    filters:            $("#filters").value.trim(),
    maxConcurrentTasks: Number($("#maxConcurrentTasks").value || 4),
    mergeBase:          $("#mergeBase").checked,
  };
}

// ── Actions ───────────────────────────────────────────────────────────────────
async function startReview(extraPayload = {}) {
  const btn = $("#runReview");
  btn.disabled = true;
  const orig = btn.innerHTML;
  btn.innerHTML = `<svg class="spin" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Starting…`;

  try {
    const meta = await api("/api/reviews", {
      method: "POST",
      body: JSON.stringify({ ...formPayload(), ...extraPayload }),
    });
    state.selectedRunId = meta.id;
    hydrateFormFromMeta(meta);
    await refreshEverything();
    showView("review");
    toastInfo("Review started — watching for results…");
  } catch (err) {
    toastError(`Could not start review: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = orig;
  }
}

async function sendFindingFeedback(issueId, action) {
  if (!state.selectedRunId) return;
  try {
    await api("/api/findings/feedback", {
      method: "POST",
      body: JSON.stringify({ runId: state.selectedRunId, issueId, action }),
    });
    await loadSelected();
    await refreshRuns();
    renderAll();
    toastSuccess(action === "dismiss"
      ? "Finding dismissed — its fingerprint is suppressed from risk scoring in all runs."
      : "Finding restored — it counts toward risk again.");
  } catch (err) {
    toastError(`Feedback failed: ${err.message}`);
  }
}

async function startGeneration(kind) {
  const btn = $(kind === "tests" ? "#generateTests" : "#generatePr");
  btn.disabled = true;
  try {
    const meta = await api("/api/generate", {
      method: "POST",
      body: JSON.stringify({ ...formPayload(), kind }),
    });
    state.selectedRunId = meta.id;
    await refreshEverything();
    showView("reports");
    toastInfo(kind === "tests" ? "Generating unit tests — watching for results…" : "Drafting pull request — watching for results…");
  } catch (err) {
    toastError(`Could not start generation: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
}

function publishPayload(dryRun) {
  return {
    platform: $("#pubPlatform").value,
    repo:     $("#pubRepo").value.trim(),
    pr:       Number($("#pubNumber").value || 0),
    dryRun,
  };
}

async function previewPublish() {
  const meta = selectedMeta();
  if (!meta) { toastError("Select a review run first."); return; }
  const btn = $("#pubPreview");
  btn.disabled = true;
  try {
    const preview = await api(`/api/reviews/${encodeURIComponent(meta.id)}/publish`, {
      method: "POST",
      body: JSON.stringify(publishPayload(true)),
    });
    state.publishPreviewed = true;
    $("#pubPublish").disabled = false;
    $("#pubResult").innerHTML = `
      <div class="code-block-label">Preview — will post to <strong>${esc(preview.target)}</strong> on ${esc(preview.platform)}
        ${preview.line_comments?.length ? ` with ${preview.line_comments.length} inline comment(s)` : ""}</div>
      <pre class="code-block pr-draft-body">${esc(preview.summary_markdown)}</pre>
    `;
    toastInfo("Preview ready. Nothing has been posted yet — click Publish to confirm.");
  } catch (err) {
    state.publishPreviewed = false;
    $("#pubPublish").disabled = true;
    toastError(`Preview failed: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
}

async function doPublish() {
  const meta = selectedMeta();
  if (!meta || !state.publishPreviewed) return;
  const btn = $("#pubPublish");
  btn.disabled = true;
  try {
    const result = await api(`/api/reviews/${encodeURIComponent(meta.id)}/publish`, {
      method: "POST",
      body: JSON.stringify(publishPayload(false)),
    });
    state.publishPreviewed = false;
    $("#pubResult").innerHTML = `
      <div class="warning-item" style="color:var(--green);background:var(--green-dim);border-color:rgba(63,185,80,0.2)">
        Published to ${esc(result.target)} (${esc(result.posted?.mode || "posted")})
        ${result.posted?.url ? ` — <a href="${esc(result.posted.url)}" target="_blank" rel="noreferrer">view</a>` : ""}
      </div>`;
    await loadSelected().catch(() => {});
    toastSuccess(`Review published to ${result.target}.`);
  } catch (err) {
    btn.disabled = false;
    toastError(`Publish failed: ${err.message}`);
  }
}

async function runPreflight() {
  const btn = $("#runPreflight");
  btn.disabled = true;
  try {
    state.preflight = await api("/api/preflight", {
      method: "POST",
      body: JSON.stringify(formPayload()),
    });
    renderPreflight();
    if (!state.preflight.ready) {
      toastError("Preflight detected issues — check the panel.");
    } else {
      toastSuccess("Preflight passed — ready to review.");
    }
  } catch (err) {
    toastError(`Preflight failed: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
}

async function addRepo() {
  const btn = $("#addRepo");
  btn.disabled = true;
  try {
    const repo = await api("/api/repos", {
      method: "POST",
      body: JSON.stringify({
        path:  $("#newRepoPath").value.trim(),
        name:  $("#newRepoName").value.trim(),
        owner: $("#newRepoOwner").value.trim(),
        tier:  $("#newRepoTier").value.trim(),
      }),
    });
    $("#repoPath").value = repo.path;
    await refreshEverything();
    toastSuccess(`Repository "${repo.name}" registered.`);
  } catch (err) {
    toastError(`Failed to add repository: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
}

function useRepo(repoId) {
  const repo = state.repos.find((r) => r.id === repoId);
  if (!repo) return;
  $("#repoPath").value    = repo.path;
  $("#newRepoPath").value = repo.path;
  $("#newRepoName").value = repo.name;
  $("#newRepoOwner").value = repo.owner;
  $("#newRepoTier").value = repo.tier;
  showView("review");
}

async function reviewRepo(repoId) {
  useRepo(repoId);
  await startReview({ repoId });
}

async function removeRepo(repoId) {
  const repo = state.repos.find((r) => r.id === repoId);
  if (!confirm(`Remove repository "${repo?.name || repoId}"? This does not delete any files.`)) return;
  try {
    await api(`/api/repos/${encodeURIComponent(repoId)}`, { method: "DELETE" });
    await refreshRepos();
    toastSuccess("Repository removed.");
  } catch (err) {
    toastError(`Failed to remove repository: ${err.message}`);
  }
}

async function savePolicies() {
  try {
    state.policies = await api("/api/policies", {
      method: "POST",
      body: JSON.stringify({
        risk: {
          blockSeverity:      Number($("#blockSeverity").value  || 1),
          reviewSeverity:     Number($("#reviewSeverity").value || 2),
          maxRiskScore:       Number($("#maxRiskScore").value   || 24),
          sensitiveFileReview: $("#sensitiveFileReview").checked,
        },
      }),
    });
    renderPolicies();
    toastSuccess("Policies saved.");
  } catch (err) {
    toastError(`Failed to save policies: ${err.message}`);
  }
}

async function seedSampleData() {
  const btn = $("#seedSample");
  btn.disabled = true;
  try {
    const meta = await api("/api/sample/seed", { method: "POST" });
    state.selectedRunId = meta.id;
    await refreshEverything();
    await selectRun(meta.id);
    showView("cockpit");
    toastSuccess("Sample data loaded — explore the cockpit!");
  } catch (err) {
    toastError(`Failed to seed sample data: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
}

async function downloadSelected(format) {
  const meta = selectedMeta();
  if (!meta) { toastError("Select a review run first."); return; }
  try {
    const text = await api(`/api/reviews/${encodeURIComponent(meta.id)}/export?format=${encodeURIComponent(format)}`);
    const ext  = format === "md" ? "md" : format;
    const blob = new Blob([typeof text === "string" ? text : JSON.stringify(text, null, 2)], { type: "text/plain" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href     = url;
    a.download = `code-doctor-${meta.id}.${ext}`;
    a.click();
    URL.revokeObjectURL(url);
    toastSuccess(`Downloaded ${ext.toUpperCase()} report.`);
  } catch (err) {
    toastError(`Export failed: ${err.message}`);
  }
}

// ── Event wiring ──────────────────────────────────────────────────────────────
function wireEvents() {
  // Navigation
  $$(".nav-item").forEach((btn) => btn.addEventListener("click", () => showView(btn.dataset.view)));

  // Topbar actions
  $("#runReview").addEventListener("click",  () => startReview());
  $("#seedSample").addEventListener("click", seedSampleData);
  $("#exportJson").addEventListener("click", () => downloadSelected("json"));

  // Token
  $("#saveToken").addEventListener("click", () => {
    state.token = $("#tokenInput").value.trim();
    localStorage.setItem("codeDoctorToken", state.token);
    $("#authBanner").classList.add("hidden");
    refreshEverything().catch((err) => toastError(err.message));
  });

  // Close banner
  $("#closeBanner").addEventListener("click", () => $("#authBanner").classList.add("hidden"));

  // Review form
  $("#runPreflight").addEventListener("click", runPreflight);
  $$('input[name="mode"]').forEach((input) => input.addEventListener("change", updateScopeControls));

  // Log
  $("#refreshLog").addEventListener("click", async () => {
    try {
      await loadSelected();
      renderLog();
    } catch (err) {
      toastError(err.message);
    }
  });

  // Repos
  $("#addRepo").addEventListener("click", addRepo);
  $("#refreshRepos").addEventListener("click", async () => {
    try { await refreshRepos(); toastInfo("Repository list refreshed."); }
    catch (err) { toastError(err.message); }
  });

  // Runs sidebar
  $("#refreshRuns").addEventListener("click", async () => {
    try { await refreshRuns(); }
    catch (err) { toastError(err.message); }
  });

  // Governance
  $("#savePolicies").addEventListener("click", savePolicies);

  // Audit
  $("#refreshAudit").addEventListener("click", async () => {
    try { await refreshAudit(); toastInfo("Audit log refreshed."); }
    catch (err) { toastError(err.message); }
  });

  // Reports
  $("#downloadJson").addEventListener("click",     () => downloadSelected("json"));
  $("#downloadMarkdown").addEventListener("click",  () => downloadSelected("md"));
  $("#downloadCsv").addEventListener("click",       () => downloadSelected("csv"));

  // Generators
  $("#generateTests").addEventListener("click", () => startGeneration("tests"));
  $("#generatePr").addEventListener("click",    () => startGeneration("pr"));

  // Publish to PR/MR — preview first, publish only after an explicit confirm
  $("#pubPreview").addEventListener("click", previewPublish);
  $("#pubPublish").addEventListener("click", doPublish);
  ["#pubPlatform", "#pubRepo", "#pubNumber"].forEach((sel) => {
    $(sel)?.addEventListener("input", () => {
      state.publishPreviewed = false;
      $("#pubPublish").disabled = true;
    });
  });

  // Ollama URL change → re-check health
  $("#ollamaBase").addEventListener("change", () => {
    refreshHealth().catch(() => {});
  });

  // Model input → update sidebar label
  $("#model").addEventListener("input", () => setText("#activeModel", $("#model").value));

  // Token input pre-fill
  $("#tokenInput").value = state.token;
  updateScopeControls();
}

// ── Smart polling ─────────────────────────────────────────────────────────────
let _pollRunning = false;

async function poll() {
  if (_pollRunning) return;
  _pollRunning = true;
  try {
    const prevMeta = selectedMeta();
    await Promise.allSettled([refreshRuns(), refreshOverview()]);
    const newMeta = selectedMeta();
    // If selected run just finished, reload its detail
    if (prevMeta && ["running", "queued", "unknown"].includes(prevMeta.status)) {
      await loadSelected().catch(() => {});
      renderAll();
      const runNoun = { tests: "Unit-test generation", pr: "PR draft" }[newMeta?.kind] || "Review";
      if (newMeta?.status === "completed") {
        toastSuccess(newMeta?.kind
          ? `${runNoun} for "${basename(newMeta.repo_path)}" is ready — see Reports.`
          : `Review of "${basename(newMeta.repo_path)}" completed — ${newMeta.stats?.total_issues ?? 0} issue(s) found.`);
      } else if (newMeta?.status === "failed") {
        toastError(`${runNoun} for "${basename(newMeta.repo_path)}" failed. Check the execution log.`);
      }
    }
  } finally {
    _pollRunning = false;
  }
}

// ── Boot ──────────────────────────────────────────────────────────────────────
async function boot() {
  wireEvents();
  try {
    await refreshEverything();
  } catch (_) {
    // Initial load failure — probably first run; silently continue
  }
  setInterval(poll, 5000);
}

boot();
