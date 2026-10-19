// AI Work Factory dashboard (Plan §19 Dashboard MVP). Plain JS, no build step.
"use strict";

const API = "/api/v1";
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pill = (s) => `<span class="pill s-${esc(s)}">${esc(s)}</span>`;
const short = (id) => (id || "").slice(0, 8);
const when = (ts) => (ts ? new Date(ts).toLocaleString() : "");
const money = (v) => `$${Number(v || 0).toFixed(4)}`;

// Auth: an HttpOnly session cookie set by POST /api/v1/session (30 days). The admin token is typed once
// per browser, or never when the dashboard is opened with `awf open` (one-time login link).
class AuthRequired extends Error {}
// The control plane cannot be reached (no network, Tailscale off, server restarting): keep showing what we have.
class Offline extends Error {}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", "X-AWF-Client": "dashboard", ...(opts.headers || {}) };
  let res;
  try { res = await fetch(API + path, { ...opts, headers, credentials: "same-origin" }); }
  catch { throw new Offline(); }
  if ([502, 503, 504].includes(res.status)) throw new Offline();  // proxy (tailscale serve/Caddy) up, awf down
  if (res.status === 401) throw new AuthRequired();
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  const type = res.headers.get("content-type") || "";
  return type.includes("json") && !path.endsWith("/content") ? res.json() : res.text();
}

async function consumeLoginLink() {
  const m = location.hash.match(/^#login=([\w.\-]+)/);
  if (!m) return;
  history.replaceState(null, "", location.pathname + "#/");  // never leave the ticket in the address bar
  try { await api("/session", { method: "POST", body: JSON.stringify({ ticket: m[1] }) }); }
  catch { /* expired or already used: fall back to the login form */ }
}

function viewLogin(message = "") {
  return `<div class="card" style="max-width:440px;margin:40px auto">
    <h1>Sign in</h1>
    <p class="muted">Paste <code>AWF_API_TOKEN</code> from the control plane's <code>.env</code>. This browser stays
      signed in for 30 days. Tip: run <code>awf open</code> on the control-plane machine to sign in without typing.</p>
    <form id="login-form" class="row"><input type="password" name="token" autocomplete="current-password" required
      placeholder="admin token"><button>Sign in</button></form>
    <p id="login-error" class="error">${esc(message)}</p></div>`;
}

function bindLogin() {
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/session", { method: "POST", body: JSON.stringify({ token: new FormData(e.target).get("token") }) });
      render();
    } catch (err) {
      $("#login-error").textContent = err instanceof AuthRequired ? "Invalid token." : err.message;
    }
  };
}

$("#logout-btn").onclick = async () => { await api("/session", { method: "DELETE" }); render(); };
$("#drawer-close").onclick = () => $("#drawer").classList.add("hidden");
$("#drawer").onclick = (e) => { if (e.target.id === "drawer") $("#drawer").classList.add("hidden"); };

function drawer(html) { $("#drawer-body").innerHTML = html; $("#drawer").classList.remove("hidden"); }

// ------------------------------------------------------------------------------------ views
async function viewOverview() {
  const [o, approvals] = await Promise.all([api("/overview"), api("/approvals?state=pending")]);
  const card = (k, v, href) => `<a class="card" ${href ? `href="${href}"` : ""}><div class="k">${k}</div><div class="v">${v}</div></a>`;
  const waiting = approvals.slice(0, 5).map((a) => `<a class="item warn" href="#/approvals/${a.id}"><b>${esc(a.action)}</b>
      <span class="muted">run <code>${short(a.workflow_run_id)}</code> · ${when(a.created_at)}</span></a>`).join("");
  const runs = (o.runs || []).map((r) => {
    const pct = r.stages_total ? Math.round((100 * r.stages_done) / r.stages_total) : 0;
    const where = r.worker ? `on ${esc(r.worker)}` : (r.waiting_for ? `<span class="error">${esc(r.waiting_for)}</span>` : "");
    return `<a class="item" href="#/runs/${r.id}">
      <div class="row"><b>${esc(r.project || "")}</b><span class="muted">${esc(r.workflow_id)}</span></div>
      <div class="obj">${esc(r.objective)}</div>
      <div class="progress"><span style="width:${pct}%"></span></div>
      <div class="muted">${r.stages_done}/${r.stages_total} · ${r.current_gate ? "⏳ " : ""}${esc(r.current_stage || "")} ${where}</div></a>`;
  }).join("");
  return `<h1>Overview</h1>${installHint()}
    <div class="cards">${card("Active runs", o.active_runs)}${card("Waiting approvals", o.waiting_approvals, "#/approvals")}
    ${card("Workers online", `${o.workers_online}/${o.workers_total}`, "#/workers")}${card("Failed tasks", o.failed_tasks)}
    ${card("Active tasks", o.active_tasks)}${card("Projects", o.projects, "#/projects")}${card("LLM cost", money(o.total_cost_usd))}</div>
    ${approvals.length ? `<h2>Waiting for your approval</h2><div class="list">${waiting}
      ${approvals.length > 5 ? `<a href="#/approvals">See all ${approvals.length} →</a>` : ""}</div>` : ""}
    <h2>Running</h2><div class="list">${runs || '<p class="muted">Nothing running.</p>'}</div>`;
}

// ---------------------------------------------------------------- installable app (PWA)
let installPrompt = null;
window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installPrompt = e; });
const standalone = () => window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

function installHint() {
  if (standalone()) return "";
  if (installPrompt) return `<div class="hint row"><span>Cài dashboard thành ứng dụng trên máy này.</span><button id="install-btn">Install app</button></div>`;
  if (/iPhone|iPad/.test(navigator.userAgent) && window.isSecureContext) {
    return `<div class="hint">iPhone/iPad: bấm <b>Chia sẻ</b> → <b>Thêm vào MH chính</b> để dùng như một ứng dụng.</div>`;
  }
  return "";
}

document.addEventListener("click", async (e) => {
  if (e.target.id !== "install-btn" || !installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice;
  installPrompt = null;
  render();
});

if ("serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}

// Every project says where it runs (no silent default). Free text is accepted too, e.g. linux-amd64.
const PLATFORMS = [["windows", "Windows"], ["linux", "Linux (any)"], ["linux-amd64", "Linux x64"],
  ["linux-arm64", "Linux ARM64 (Jetson)"], ["any", "Any machine"]];
const platformOptions = (selected = "") => `<option value="" ${selected ? "" : "selected"} disabled>Choose…</option>` +
  PLATFORMS.map(([v, t]) => `<option value="${v}" ${v === selected ? "selected" : ""}>${esc(t)} — ${v}</option>`).join("") +
  (selected && !PLATFORMS.some(([v]) => v === selected) ? `<option value="${esc(selected)}" selected>${esc(selected)}</option>` : "");
const LLM_NAMES = { anthropic: "Claude", google: "Gemini" };
const llmName = (p) => (LLM_NAMES[p] ? `${LLM_NAMES[p]} (${p})` : p);
const llmOptions = (models, selected = "", auto = "") =>
  `<option value="">${esc(auto || `Default — ${llmName(models.default_provider)}`)}</option>` +
  models.providers.map((p) => `<option value="${esc(p)}" ${p === selected ? "selected" : ""}>${esc(llmName(p))}</option>`).join("");
const capList = (raw) => String(raw || "").split(",").map((c) => c.trim()).filter(Boolean);

async function viewProjects() {
  const projects = await api("/projects");
  const rows = projects.map((p) => `<tr class="click" data-href="#/projects/${p.id}"><td>${esc(p.name)}</td><td><code>${esc(p.slug)}</code></td>
    <td>${p.labels?.platform ? `<code>${esc(p.labels.platform)}</code>` : '<span class="error">not set</span>'}</td>
    <td>${esc(p.repository?.url || "")}</td><td>${esc(p.confidentiality)}</td><td>${when(p.created_at)}</td></tr>`).join("");
  return `<h1>Projects</h1>
    <div class="table-wrap"><table><tr><th>Name</th><th>Slug</th><th>Platform</th><th>Repository</th><th>Confidentiality</th><th>Created</th></tr>${rows || '<tr><td colspan="6" class="muted">No projects yet</td></tr>'}</table></div>
    <h2>New project</h2>
    <form class="grid" id="project-form">
      <label>Name<input name="name" required></label>
      <label>Repository URL / path<input name="url" placeholder="optional for content: https://… or D:/src/repo"></label>
      <label>Base branch<input name="base_branch" value="main"></label>
      <label>Platform<select name="platform" required>${platformOptions()}</select></label>
      <label>Required capabilities<input name="required_capabilities" placeholder="dotnet-windowsdesktop, msbuild"></label>
      <label>Build command<input name="build_command" placeholder="python -m compileall -q src"></label>
      <label>Test command<input name="test_command" placeholder="python -m pytest -q"></label>
      <label>Lint command<input name="lint_command" placeholder="ruff check ."></label>
      <label>Confidentiality<select name="confidentiality"><option>internal</option><option>public</option><option>restricted</option></select></label>
      <label>Budget (USD)<input name="budget_usd" type="number" step="0.01"></label>
      <div class="full row"><button>Create project</button><span id="project-error" class="error"></span></div>
    </form>`;
}

function bindProjects() {
  const form = $("#project-form");
  if (!form) return;
  form.onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    const body = { name: f.get("name"), confidentiality: f.get("confidentiality"), labels: { platform: f.get("platform") } };
    if (f.get("url")) {  // software projects; content projects may have no repository
      const repo = { url: f.get("url"), base_branch: f.get("base_branch") || "main" };
      for (const k of ["build_command", "test_command", "lint_command"]) if (f.get(k)) repo[k] = f.get(k);
      repo.required_capabilities = capList(f.get("required_capabilities"));
      body.repository = repo;
    }
    if (f.get("budget_usd")) body.budget_usd = Number(f.get("budget_usd"));
    try { const p = await api("/projects", { method: "POST", body: JSON.stringify(body) }); location.hash = `#/projects/${p.id}`; }
    catch (err) { $("#project-error").textContent = err.message; }
  };
}

// Machine picker for run placement: "" lets the orchestrator choose the least loaded matching machine.
function machineSelect(workers, selected = "", auto = "Automatic (least loaded matching machine)") {
  const opts = workers.filter((w) => w.status !== "revoked").map((w) =>
    `<option value="${esc(w.name)}" ${w.name === selected ? "selected" : ""}>${esc(w.name)} · ${esc(w.platform)} · ${esc(w.status)}</option>`).join("");
  return `<select name="worker"><option value="">${esc(auto)}</option>${opts}</select>`;
}

async function viewProject(id) {
  const [p, runs, workflows, workers, models] = await Promise.all([api(`/projects/${id}`), api(`/projects/${id}/workflow-runs`), api("/workflows"), api("/workers"), api("/models")]);
  const rows = runs.map((r) => `<tr class="click" data-href="#/runs/${r.id}"><td><code>${short(r.id)}</code></td><td>${esc(r.workflow_id)}</td>
    <td>${pill(r.status)}</td><td>${esc(r.input?.objective || "")}</td><td>${when(r.created_at)}</td></tr>`).join("");
  const options = workflows.filter((w) => w.status === "active").map((w) => `<option value="${esc(w.id)}">${esc(w.name || w.id)} v${esc(w.version)}</option>`).join("");
  return `<h1>${esc(p.name)}</h1>
    <div class="kv"><div>Repository</div><div><code>${esc(p.repository?.url)}</code> (${esc(p.repository?.base_branch)})</div>
    <div>Commands</div><div>build: <code>${esc(JSON.stringify(p.repository?.build_command ?? null))}</code> test: <code>${esc(JSON.stringify(p.repository?.test_command ?? null))}</code> lint: <code>${esc(JSON.stringify(p.repository?.lint_command ?? null))}</code></div>
    <div>Spend / budget</div><div>${money(p.spend_usd)} / ${p.budget_usd == null ? "∞" : money(p.budget_usd)}</div></div>
    ${projectMachines(p, models)}
    <h2>Start a workflow run</h2>
    <form class="grid" id="run-form"><label>Workflow<select name="workflow_id">${options}</select></label>
      <label>Machine${machineSelect(workers)}</label>
      <label>Model<select name="llm">${llmOptions(models, "", `Project default — ${llmName(p.labels?.llm || models.default_provider)}`)}</select></label>
      <label class="full">Objective<textarea name="objective" rows="3" required></textarea></label>
      <div class="full row"><button>Start</button><span id="run-error" class="error"></span></div></form>
    <h2>Runs</h2><div class="table-wrap"><table><tr><th>Run</th><th>Workflow</th><th>Status</th><th>Objective</th><th>Created</th></tr>${rows || '<tr><td colspan="5" class="muted">No runs</td></tr>'}</table></div>`;
}

function projectMachines(p, models) {
  const req = p.requirements || {};
  const rows = (p.machines || []).map((m) => `<tr><td>${esc(m.name)}</td><td>${pill(m.status)}</td><td><code>${esc(m.platform)}</code></td>
    <td>${m.matches ? '<span class="pill s-PASS">yes</span>' : `<span class="error">${esc(m.reasons.join("; "))}</span>`}</td></tr>`).join("");
  const none = req.platform && (p.machines || []).length && !(p.machines || []).some((m) => m.matches);
  return `<h2>Where it runs</h2>
    ${req.platform ? "" : '<p class="error">This project has no platform yet: runs are refused until you choose one below.</p>'}
    ${none ? '<p class="error">No registered machine can run this project.</p>' : ""}
    <form class="grid" id="project-edit-form">
      <label>Platform<select name="platform" required>${platformOptions(req.platform || "")}</select></label>
      <label>Required capabilities<input name="required_capabilities" value="${esc((p.repository?.required_capabilities || []).join(", "))}"
        placeholder="dotnet-windowsdesktop, msbuild"></label>
      <label>Default model<select name="llm">${llmOptions(models, p.labels?.llm || "")}</select></label>
      <div class="full row"><button class="ghost">Save</button><span id="project-edit-error" class="error"></span>
        <span class="muted">Projects with a repository also need <code>git</code>; workflow stages and the model may add more.</span></div></form>
    <div class="table-wrap"><table><tr><th>Machine</th><th>Status</th><th>Platform</th><th>Can run it</th></tr>
      ${rows || '<tr><td colspan="4" class="muted">No workers registered</td></tr>'}</table></div>`;
}

function bindProject(id) {
  const edit = $("#project-edit-form");
  if (edit) edit.onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(edit);
    try {
      const p = await api(`/projects/${id}`);
      const labels = { ...(p.labels || {}), platform: f.get("platform") };
      if (f.get("llm")) labels.llm = f.get("llm"); else delete labels.llm;
      const body = { labels };
      if (p.repository?.url) body.repository = { ...p.repository, required_capabilities: capList(f.get("required_capabilities")) };
      await api(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) });
      render();
    } catch (err) { $("#project-edit-error").textContent = err.message; }
  };
  const form = $("#run-form");
  if (!form) return;
  form.onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    try {
      const body = { workflow_id: f.get("workflow_id"), inputs: { objective: f.get("objective") } };
      if (f.get("worker")) body.placement = { worker: f.get("worker") };
      if (f.get("llm")) body.llm = f.get("llm");
      const r = await api(`/projects/${id}/workflow-runs`, { method: "POST", body: JSON.stringify(body) });
      location.hash = `#/runs/${r.id}`;
    } catch (err) { $("#run-error").textContent = err.message; }
  };
}

async function viewRun(id) {
  const [run, tasks, approvals, workers] = await Promise.all([api(`/workflow-runs/${id}`), api(`/workflow-runs/${id}/tasks`), api("/approvals?state=pending"), api("/workers")]);
  const pipeline = run.stages.map((s) => `<div class="stage ${esc(s.status)}"><div class="t">${esc(s.title)}</div>
    <div class="m">${esc(s.kind)}${s.gate ? " · gate" : ""} · iter ${s.iteration}</div><div>${pill(s.status)}</div></div>`).join('<div class="arrow">→</div>');
  const rows = tasks.map((t) => `<tr class="click" data-task="${t.id}"><td>${esc(t.stage_id)}</td><td>${esc(t.title)}</td>
    <td>${esc(t.assigned_role || t.task_type)}</td><td>${pill(t.state)}</td><td>${t.iteration}</td><td>${t.attempt}/${t.max_attempts}</td>
    <td>${t.output?.critic ? pill(t.output.critic.decision) : ""} ${t.output?.critic?.verdict ? pill(t.output.critic.verdict) : ""}</td></tr>`).join("");
  const mine = approvals.filter((a) => a.workflow_run_id === id);
  return `<div class="row"><h1>Run <code>${short(run.id)}</code></h1>${pill(run.status)}
      ${run.status === "RUNNING" ? '<button class="danger" id="cancel-run">Cancel run</button>' : ""}</div>
    <div class="kv"><div>Workflow</div><div>${esc(run.workflow_id)} v${esc(run.workflow_version)}</div>
      <div>Objective</div><div>${esc(run.input?.objective)}</div>
      ${placementRows(run, workers)}
      ${run.input?.repository ? `<div>Integration branch</div><div><code>${esc(run.integration_branch)}</code></div>` : ""}
      <div>Fix loops</div><div>${esc(JSON.stringify(run.fix_loops))}</div>
      <div>LLM cost</div><div>${money(run.cost_usd)}</div>
      ${run.error ? `<div>Error</div><div class="error">${esc(run.error)}</div>` : ""}</div>
    ${mine.length ? `<h2>Waiting for your approval</h2>${approvalTable(mine)}` : ""}
    <h2>Workflow</h2><div class="pipeline">${pipeline}</div>
    <h2>Tasks</h2><div class="table-wrap"><table><tr><th>Stage</th><th>Task</th><th>Role</th><th>State</th><th>Iter</th><th>Attempt</th><th>Critic</th></tr>${rows}</table></div>
    <h2>Artifacts</h2><div id="run-artifacts" class="muted">Loading…</div>`;
}

function placementRows(run, workers) {
  if (run.placement?.mode !== "run") return `<div>Machine</div><div class="muted">any matching machine per job (placement: stage)</div>`;
  const w = run.assigned_worker;
  const machine = w ? `<b>${esc(w.name || short(w.id))}</b> ${w.status ? pill(w.status) : ""} <span class="muted">since ${when(run.assigned_at)}</span>`
    : `<span class="muted">not assigned yet</span>`;
  const req = run.requirements || {};
  const move = run.status === "RUNNING" ? `<form id="reassign-form" class="row">${machineSelect(workers, run.placement.worker || "", "Let the orchestrator choose")}
      <button class="ghost">Move run</button><span id="reassign-error" class="error"></span></form>` : "";
  return `<div>Machine</div><div>${machine}${run.placement.worker ? ' <span class="muted">(chosen by hand)</span>' : ""}
      ${run.placement.wait ? `<div class="error">${esc(run.placement.wait)}</div>` : ""}${move}</div>
    <div>Requirements</div><div>platform <code>${esc(req.platform || "any")}</code> · capabilities <code>${esc((req.capabilities || []).join(", "))}</code></div>
    ${run.llm ? `<div>Model</div><div>${esc(llmName(run.llm))}</div>` : ""}`;
}

async function bindRun(id) {
  const form = $("#reassign-form");
  if (form) form.onsubmit = async (e) => {
    e.preventDefault();
    const worker = new FormData(form).get("worker") || null;
    try { await api(`/workflow-runs/${id}/reassign`, { method: "POST", body: JSON.stringify({ worker }) }); render(); }
    catch (err) { $("#reassign-error").textContent = err.message; }
  };
  const btn = $("#cancel-run");
  if (btn) btn.onclick = async () => { if (confirm("Cancel this run?")) { await api(`/workflow-runs/${id}/cancel`, { method: "POST" }); render(); } };
  document.querySelectorAll("tr[data-task]").forEach((tr) => (tr.onclick = () => openTask(tr.dataset.task)));
  bindApprovalButtons();
  const run = await api(`/workflow-runs/${id}`);
  const arts = await api(`/projects/${run.project_id}/artifacts?run_id=${id}`);
  $("#run-artifacts").outerHTML = artifactTable(arts);
  bindArtifactLinks();
}

function artifactTable(arts) {
  const rows = arts.map((a) => `<tr class="click" data-artifact="${a.id}"><td>${esc(a.type)}</td><td><code>${esc(a.name)}</code></td>
    <td>v${a.version}</td><td>${esc(a.stage_id || "")}</td><td><code>${esc(a.checksum.slice(0, 19))}</code></td><td>${a.size_bytes}</td></tr>`).join("");
  return `<div class="table-wrap"><table><tr><th>Type</th><th>Name</th><th>Ver</th><th>Stage</th><th>Checksum</th><th>Bytes</th></tr>${rows || '<tr><td colspan="6" class="muted">None</td></tr>'}</table></div>`;
}

function bindArtifactLinks() { document.querySelectorAll("tr[data-artifact]").forEach((tr) => (tr.onclick = () => openArtifact(tr.dataset.artifact))); }

async function openArtifact(id) {
  const [a, content, rel, impact] = await Promise.all([api(`/artifacts/${id}`), api(`/artifacts/${id}/content`), api(`/artifacts/${id}/relations`), api(`/artifacts/${id}/impact`)]);
  const relRows = [...rel.outgoing.map((r) => `<li>${esc(r.relation_type)} → <code>${short(r.to_artifact_id)}</code></li>`),
    ...rel.incoming.map((r) => `<li><code>${short(r.from_artifact_id)}</code> ${esc(r.relation_type)} → this</li>`)].join("");
  drawer(`<h2>${esc(a.name)} <span class="muted">v${a.version}</span></h2>
    <div class="kv"><div>Type</div><div>${esc(a.type)}</div><div>Checksum</div><div><code>${esc(a.checksum)}</code></div>
    <div>Storage</div><div><code>${esc(a.storage_backend)}</code> ${esc(a.uri)}</div><div>Task</div><div><code>${short(a.task_id)}</code></div></div>
    <h3>Traceability</h3><ul>${relRows || "<li class='muted'>none</li>"}</ul>
    <h3>Impact (${impact.impacted_artifacts.length} downstream artifacts)</h3>
    <ul>${impact.impacted_artifacts.map((x) => `<li>${esc(x.type)} <code>${esc(x.name)}</code> (depth ${x.depth}, ${esc(x.via)})</li>`).join("")}</ul>
    <h3>Content</h3><pre>${esc(content)}</pre>`);
}

async function openTask(id) {
  const t = await api(`/tasks/${id}`);
  const runs = t.agent_runs.map((r) => `<h3>Agent run <code>${short(r.id)}</code> ${pill(r.status)}</h3>
    <div class="kv"><div>Model</div><div>${esc(r.model_provider)}/${esc(r.model_name)} (${esc(r.model_class)})</div>
    <div>Route reason</div><div>${esc(r.route_reason)}</div><div>Tokens</div><div>${r.token_input} in / ${r.token_output} out · ${money(r.cost_estimate)} · ${r.duration_ms} ms</div>
    <div>Critic</div><div>${r.critic?.decision ? pill(r.critic.decision) : ""} ${esc((r.critic?.findings || []).map((f) => `[${f.severity}] ${f.message}`).join(" · "))}</div>
    ${r.error ? `<div>Error</div><div class="error">${esc(r.error)}</div>` : ""}</div>
    <details><summary>${r.tool_calls.length} tool calls</summary><pre>${esc(r.tool_calls.map((c) => `${c.status.padEnd(8)} ${c.tool} ${JSON.stringify(c.input || {})} ${c.message || ""}`).join("\n"))}</pre></details>`).join("");
  const jobs = t.jobs.map((j) => `<li><a href="#" data-logs="${j.id}">job ${short(j.id)}</a> ${pill(j.state)} attempt ${j.attempt} ${j.error ? `<span class="error">${esc(j.error)}</span>` : ""}</li>`).join("");
  drawer(`<h2>${esc(t.title)}</h2><div class="kv"><div>State</div><div>${pill(t.state)}</div><div>Role</div><div>${esc(t.assigned_role || t.task_type)}</div>
    <div>Iteration / attempt</div><div>${t.iteration} / ${t.attempt} of ${t.max_attempts}</div>
    <div>Acceptance criteria</div><div>${(t.acceptance_criteria || []).map(esc).join("<br>") || "—"}</div>
    ${t.input?.feedback?.length ? `<div>Feedback</div><div>${t.input.feedback.map(esc).join("<br>")}</div>` : ""}
    ${t.output?.failure_reason ? `<div>Failure</div><div class="error">${esc(t.output.failure_reason)}</div>` : ""}</div>
    <h3>Summary</h3><p>${esc(t.output?.summary || "—")}</p>
    <h3>Jobs</h3><ul>${jobs || "<li class='muted'>none</li>"}</ul><pre id="job-logs" class="hidden"></pre>
    ${runs}<h3>Artifacts</h3>${artifactTable(t.artifacts)}`);
  bindArtifactLinks();
  document.querySelectorAll("[data-logs]").forEach((a) => (a.onclick = async (e) => {
    e.preventDefault();
    const logs = await api(`/jobs/${a.dataset.logs}/logs`);
    const pre = $("#job-logs"); pre.classList.remove("hidden");
    pre.textContent = logs.map((l) => `[${l.stream}] ${l.line}`).join("\n") || "(no log lines)";
  }));
}

function approvalTable(list) {
  const rows = list.map((a) => `<tr><td>${esc(a.action)}</td><td>${a.risk_level}</td><td><a href="#/runs/${a.workflow_run_id}"><code>${short(a.workflow_run_id)}</code></a></td>
    <td>${(a.scope?.artifact_ids || []).map((x) => `<a href="#" data-art="${x}"><code>${short(x)}</code></a>`).join(" ")}</td><td>${pill(a.state)}</td>
    <td>${a.state === "PENDING" ? `<div class="row"><input placeholder="comment" data-comment="${a.id}" style="width:180px">
      <button data-approve="${a.id}">Approve</button><button class="danger" data-reject="${a.id}">Reject</button></div>` : esc(a.approved_by || "")}</td></tr>`).join("");
  return `<div class="table-wrap"><table><tr><th>Action</th><th>Risk</th><th>Run</th><th>Artifacts</th><th>State</th><th></th></tr>${rows || '<tr><td colspan="6" class="muted">Nothing waiting</td></tr>'}</table></div>`;
}

function bindApprovalButtons() {
  const decide = async (id, verb) => {
    const comment = document.querySelector(`[data-comment="${id}"]`)?.value || null;
    await api(`/approvals/${id}/${verb}`, { method: "POST", body: JSON.stringify({ comment }) });
    render();
  };
  document.querySelectorAll("[data-approve]").forEach((b) => (b.onclick = () => decide(b.dataset.approve, "approve")));
  document.querySelectorAll("[data-reject]").forEach((b) => (b.onclick = () => decide(b.dataset.reject, "reject")));
  document.querySelectorAll("[data-art]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); openArtifact(a.dataset.art); }));
}

// One approval, opened from the link in a Telegram/WhatsApp/Zalo message: built for a phone screen.
async function viewApproval(id) {
  const a = await api(`/approvals/${id}`);
  const arts = a.artifacts.map((x) => `<li><a href="#" data-art="${x.id}">${esc(x.type)}</a> <span class="muted">${esc(x.name.split("/").pop())} v${x.version}</span></li>`).join("");
  const pending = a.state === "PENDING";
  return `<div class="card approval-card">
      <div class="row"><h1>${esc(a.action)}</h1>${pill(a.state)}</div>
      <div class="kv">
        <div>Project</div><div>${a.project ? `<a href="#/projects/${a.project.id}">${esc(a.project.name)}</a>` : "—"}</div>
        <div>Run</div><div>${a.run ? `<a href="#/runs/${a.run.id}"><code>${short(a.run.id)}</code></a> ${esc(a.run.workflow_id)}` : "—"}</div>
        <div>Objective</div><div>${esc(a.run?.input?.objective || "")}</div>
        <div>Risk</div><div>${a.risk_level}</div>
        ${a.scope?.description ? `<div>What to check</div><div>${esc(a.scope.description)}</div>` : ""}
        ${a.approved_by ? `<div>Decided by</div><div>${esc(a.approved_by)} ${a.comment ? `— ${esc(a.comment)}` : ""}</div>` : ""}
      </div>
      <h3>Artifacts to review</h3><ul>${arts || "<li class='muted'>none</li>"}</ul>
      ${pending ? `<textarea data-comment="${a.id}" rows="2" placeholder="Comment (optional)"></textarea>
        <div class="row decide"><button data-approve="${a.id}">Approve</button><button class="danger" data-reject="${a.id}">Reject</button></div>` : ""}
    </div>`;
}

async function viewNotifications() {
  const n = await api("/notifications");
  const channels = n.channels.length ? n.channels.map((c) => pill(c)).join(" ") : '<span class="error">none configured</span>';
  const rows = n.items.map((i) => `<tr><td>${when(i.created_at)}</td><td>${esc(i.title)}</td><td>${pill(i.state)}</td>
    <td>${esc(Object.entries(i.deliveries).map(([c, d]) => `${c}: ${d.done} sent${d.failed ? `, ${d.failed} failed` : ""}${d.error ? ` (${d.error})` : ""}`).join("; "))}</td></tr>`).join("");
  return `<h1>Notifications</h1>
    <div class="kv"><div>Channels</div><div>${channels}</div>
      <div>Events</div><div>${esc(n.events.join(", "))}</div>
      <div>Approve in Telegram</div><div>${n.telegram_approvals ? "yes (allow-listed users, two taps)" : "no — messages link here"}</div>
      <div>Links point to</div><div>${n.public_url ? `<code>${esc(n.public_url)}</code>` : '<span class="error">AWF_PUBLIC_URL not set: messages carry no link</span>'}</div></div>
    <div class="row"><button id="notify-test" ${n.channels.length ? "" : "disabled"}>Send a test message</button><span id="notify-msg" class="muted"></span></div>
    <h2>Recent</h2><div class="table-wrap"><table><tr><th>Created</th><th>Message</th><th>State</th><th>Delivery</th></tr>
      ${rows || '<tr><td colspan="4" class="muted">Nothing sent yet</td></tr>'}</table></div>`;
}

function bindNotifications() {
  const btn = $("#notify-test");
  if (btn) btn.onclick = async () => {
    try { await api("/notifications/test", { method: "POST" }); $("#notify-msg").textContent = "Queued; refresh in a few seconds."; }
    catch (err) { $("#notify-msg").textContent = err.message; }
  };
}

async function viewApprovals() {
  const all = await api("/approvals");
  return `<h1>Approval queue</h1>${approvalTable(all.filter((a) => a.state === "PENDING"))}
    <h2>History</h2>${approvalTable(all.filter((a) => a.state !== "PENDING").slice(0, 50))}`;
}

async function viewWorkers() {
  const workers = await api("/workers");
  const action = (w) => w.status === "revoked"
    ? `<button class="ghost" data-delete-worker="${w.id}" title="Remove the record so this machine can join again">Delete</button>`
    : `<button class="danger" data-revoke-worker="${w.id}" data-name="${esc(w.name)}" title="Lost or compromised machine">Revoke</button>`;
  const rows = workers.map((w) => `<tr><td>${esc(w.name)}<div class="muted"><code>${esc(w.machine_id || "")}</code></div></td><td>${pill(w.status)}</td><td><code>${esc(w.platform)}</code></td>
    <td>${(w.capabilities || []).map(esc).join(", ")}</td><td>${esc(Object.entries(w.labels || {}).map(([k, v]) => `${k}=${v}`).join(", "))}</td>
    <td>${w.active_runs ?? 0} <span class="muted">· ${w.max_jobs ?? 1} job slot${(w.max_jobs ?? 1) === 1 ? "" : "s"}</span></td>
    <td>${esc(JSON.stringify(w.load))}</td><td>${when(w.last_heartbeat)}</td><td>${action(w)}</td></tr>`).join("");
  return `<h1>Workers</h1><div class="table-wrap"><table><tr><th>Name</th><th>Status</th><th>Platform</th><th>Capabilities</th><th>Labels</th><th>Runs</th><th>Load</th><th>Last heartbeat</th><th></th></tr>${rows || '<tr><td colspan="9" class="muted">No workers registered</td></tr>'}</table></div>
    <p class="muted">Revoke immediately invalidates a worker's token, requeues its jobs and blocks it from re-registering.
      If the machine was compromised, also rotate <code>AWF_WORKER_REGISTRATION_TOKEN</code> on the control plane.</p>`;
}

function bindWorkers() {
  document.querySelectorAll("[data-revoke-worker]").forEach((b) => (b.onclick = async () => {
    if (!confirm(`Revoke worker ${b.dataset.name}? Its runs move to other matching machines.`)) return;
    await api(`/workers/${b.dataset.revokeWorker}/revoke`, { method: "POST" }); render();
  }));
  document.querySelectorAll("[data-delete-worker]").forEach((b) => (b.onclick = async () => {
    await api(`/workers/${b.dataset.deleteWorker}`, { method: "DELETE" }); render();
  }));
}

async function viewAgents() {
  const [agents, stats] = await Promise.all([api("/agents"), api("/stats/agents")]);
  const byRole = Object.fromEntries(stats.map((s) => [s.role, s]));
  const rows = agents.map((a) => { const s = byRole[a.role] || {};
    return `<tr><td><code>${esc(a.role)}</code><div class="muted">${esc(a.objective)}</div></td><td>${esc(a.model_class)}</td>
      <td>${s.runs ?? 0}</td><td>${s.success_rate ?? "—"}</td><td>${s.retry_rate ?? "—"}</td><td>${s.critic_rejection_rate ?? "—"}</td>
      <td>${s.avg_latency_ms ?? "—"}</td><td>${s.avg_cost_usd != null ? money(s.avg_cost_usd) : "—"}</td><td>${a.allowed_tools.map(esc).join(", ")}</td></tr>`; }).join("");
  return `<h1>Agents</h1><div class="table-wrap"><table><tr><th>Role</th><th>Model class</th><th>Runs</th><th>Success</th><th>Retry</th><th>Critic rejection</th><th>Avg ms</th><th>Avg cost</th><th>Tools</th></tr>${rows}</table></div>`;
}

async function viewAudit() {
  const events = await api("/audit?limit=300");
  const rows = events.map((e) => `<tr><td>${when(e.created_at)}</td><td>${esc(e.event_type)}</td><td>${esc(e.actor_type)}:${esc(e.actor_id)}</td>
    <td>${e.workflow_run_id ? `<a href="#/runs/${e.workflow_run_id}"><code>${short(e.workflow_run_id)}</code></a>` : ""}</td><td><code>${esc(JSON.stringify(e.payload)).slice(0, 300)}</code></td></tr>`).join("");
  return `<h1>Audit trail</h1><div class="table-wrap"><table><tr><th>Time</th><th>Event</th><th>Actor</th><th>Run</th><th>Payload</th></tr>${rows}</table></div>`;
}

// ------------------------------------------------------------------------------------ router
const routes = [
  [/^#?\/?$/, "overview", viewOverview, bindApprovalButtons],
  [/^#\/projects$/, "projects", viewProjects, bindProjects],
  [/^#\/projects\/([\w-]+)$/, "projects", viewProject, bindProject],
  [/^#\/runs\/([\w-]+)$/, "projects", viewRun, bindRun],
  [/^#\/approvals$/, "approvals", viewApprovals, bindApprovalButtons],
  [/^#\/approvals\/([\w-]+)$/, "approvals", viewApproval, bindApprovalButtons],
  [/^#\/notifications$/, "notifications", viewNotifications, bindNotifications],
  [/^#\/workers$/, "workers", viewWorkers, bindWorkers],
  [/^#\/agents$/, "agents", viewAgents],
  [/^#\/audit$/, "audit", viewAudit],
];

let rendering = false;
let signedOut = false;
let shown = false;  // some live data is on screen (an offline banner can say "previous data")
async function render() {
  if (rendering) return;
  rendering = true;
  const hash = location.hash || "#/";
  try {
    for (const [re, nav, view, bind] of routes) {
      const m = hash.match(re);
      if (!m) continue;
      document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === nav));
      const html = await view(...m.slice(1));
      const focused = document.activeElement && ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
      if (!focused || signedOut) { $("#view").innerHTML = html; shown = true; document.querySelectorAll("tr[data-href]").forEach((tr) => (tr.onclick = () => (location.hash = tr.dataset.href))); if (bind) await bind(...m.slice(1)); }
      break;
    }
    signedOut = false;
    $("#offline").classList.add("hidden");
    const pending = await api("/approvals?state=pending");
    const badge = $("#approval-badge");
    badge.textContent = pending.length; badge.classList.toggle("hidden", !pending.length);
    // Number on the installed app's icon (Android, Windows, iOS 16.4+ home-screen apps).
    if (navigator.setAppBadge) (pending.length ? navigator.setAppBadge(pending.length) : navigator.clearAppBadge()).catch(() => {});
    const session = await api("/session");
    $("#logout-btn").classList.toggle("hidden", !session.auth_required);
  } catch (err) {
    if (err instanceof Offline) {
      $("#offline").classList.toggle("hidden", !shown);  // banner over the last view; nothing yet: the card says it
      if (!shown) {
        $("#view").innerHTML = `<div class="card"><h1>Không kết nối được máy chủ</h1><p class="muted">Kiểm tra mạng, bật
          Tailscale trên điện thoại, hoặc máy chủ đang khởi động lại. Trang tự thử lại mỗi vài giây.</p></div>`;
      }
    } else if (err instanceof AuthRequired) {
      $("#logout-btn").classList.add("hidden");
      if (!signedOut) { $("#view").innerHTML = viewLogin(); bindLogin(); signedOut = true; }
    } else {
      $("#view").innerHTML = `<p class="error">${esc(err.message)}</p>`;
    }
  } finally { rendering = false; }
}

window.addEventListener("hashchange", () => {
  if (location.hash.startsWith("#login=")) { signedOut = false; consumeLoginLink().then(render); } else { render(); }
});
// Live refresh while the page is on screen; none in the background (phone battery), catch up when it comes back.
setInterval(() => {
  if (document.visibilityState === "visible" && $("#drawer").classList.contains("hidden") && !signedOut) render();
}, 4000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && !signedOut) render(); });
consumeLoginLink().then(render);
