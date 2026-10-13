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

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", "X-AWF-Client": "dashboard", ...(opts.headers || {}) };
  const res = await fetch(API + path, { ...opts, headers, credentials: "same-origin" });
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
  const card = (k, v) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`;
  return `<h1>Overview</h1>
    <div class="cards">${card("Projects", o.projects)}${card("Active runs", o.active_runs)}
    ${card("Active tasks", o.active_tasks)}${card("Failed tasks", o.failed_tasks)}
    ${card("Waiting approvals", o.waiting_approvals)}${card("Workers online", `${o.workers_online}/${o.workers_total}`)}
    ${card("LLM cost", money(o.total_cost_usd))}</div>
    <h2>Waiting approvals</h2>${approvalTable(approvals)}`;
}

async function viewProjects() {
  const projects = await api("/projects");
  const rows = projects.map((p) => `<tr class="click" data-href="#/projects/${p.id}"><td>${esc(p.name)}</td><td><code>${esc(p.slug)}</code></td>
    <td>${esc(p.repository?.url || "")}</td><td>${esc(p.confidentiality)}</td><td>${when(p.created_at)}</td></tr>`).join("");
  return `<h1>Projects</h1>
    <div class="table-wrap"><table><tr><th>Name</th><th>Slug</th><th>Repository</th><th>Confidentiality</th><th>Created</th></tr>${rows || '<tr><td colspan="5" class="muted">No projects yet</td></tr>'}</table></div>
    <h2>New project</h2>
    <form class="grid" id="project-form">
      <label>Name<input name="name" required></label>
      <label>Repository URL / path<input name="url" required placeholder="https://… or D:/src/repo"></label>
      <label>Base branch<input name="base_branch" value="main"></label>
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
    const repo = { url: f.get("url"), base_branch: f.get("base_branch") || "main" };
    for (const k of ["build_command", "test_command", "lint_command"]) if (f.get(k)) repo[k] = f.get(k);
    const body = { name: f.get("name"), repository: repo, confidentiality: f.get("confidentiality") };
    if (f.get("budget_usd")) body.budget_usd = Number(f.get("budget_usd"));
    try { const p = await api("/projects", { method: "POST", body: JSON.stringify(body) }); location.hash = `#/projects/${p.id}`; }
    catch (err) { $("#project-error").textContent = err.message; }
  };
}

async function viewProject(id) {
  const [p, runs, workflows] = await Promise.all([api(`/projects/${id}`), api(`/projects/${id}/workflow-runs`), api("/workflows")]);
  const rows = runs.map((r) => `<tr class="click" data-href="#/runs/${r.id}"><td><code>${short(r.id)}</code></td><td>${esc(r.workflow_id)}</td>
    <td>${pill(r.status)}</td><td>${esc(r.input?.objective || "")}</td><td>${when(r.created_at)}</td></tr>`).join("");
  const options = workflows.filter((w) => w.status === "active").map((w) => `<option value="${esc(w.id)}">${esc(w.name || w.id)} v${esc(w.version)}</option>`).join("");
  return `<h1>${esc(p.name)}</h1>
    <div class="kv"><div>Repository</div><div><code>${esc(p.repository?.url)}</code> (${esc(p.repository?.base_branch)})</div>
    <div>Commands</div><div>build: <code>${esc(JSON.stringify(p.repository?.build_command ?? null))}</code> test: <code>${esc(JSON.stringify(p.repository?.test_command ?? null))}</code> lint: <code>${esc(JSON.stringify(p.repository?.lint_command ?? null))}</code></div>
    <div>Spend / budget</div><div>${money(p.spend_usd)} / ${p.budget_usd == null ? "∞" : money(p.budget_usd)}</div></div>
    <h2>Start a workflow run</h2>
    <form class="grid" id="run-form"><label>Workflow<select name="workflow_id">${options}</select></label>
      <label class="full">Objective<textarea name="objective" rows="3" required></textarea></label>
      <div class="full row"><button>Start</button><span id="run-error" class="error"></span></div></form>
    <h2>Runs</h2><div class="table-wrap"><table><tr><th>Run</th><th>Workflow</th><th>Status</th><th>Objective</th><th>Created</th></tr>${rows || '<tr><td colspan="5" class="muted">No runs</td></tr>'}</table></div>`;
}

function bindProject(id) {
  const form = $("#run-form");
  if (!form) return;
  form.onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    try {
      const r = await api(`/projects/${id}/workflow-runs`, { method: "POST", body: JSON.stringify({ workflow_id: f.get("workflow_id"), inputs: { objective: f.get("objective") } }) });
      location.hash = `#/runs/${r.id}`;
    } catch (err) { $("#run-error").textContent = err.message; }
  };
}

async function viewRun(id) {
  const [run, tasks, approvals] = await Promise.all([api(`/workflow-runs/${id}`), api(`/workflow-runs/${id}/tasks`), api("/approvals?state=pending")]);
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
      <div>Integration branch</div><div><code>${esc(run.integration_branch)}</code></div>
      <div>Fix loops</div><div>${esc(JSON.stringify(run.fix_loops))}</div>
      <div>LLM cost</div><div>${money(run.cost_usd)}</div>
      ${run.error ? `<div>Error</div><div class="error">${esc(run.error)}</div>` : ""}</div>
    ${mine.length ? `<h2>Waiting for your approval</h2>${approvalTable(mine)}` : ""}
    <h2>Workflow</h2><div class="pipeline">${pipeline}</div>
    <h2>Tasks</h2><div class="table-wrap"><table><tr><th>Stage</th><th>Task</th><th>Role</th><th>State</th><th>Iter</th><th>Attempt</th><th>Critic</th></tr>${rows}</table></div>
    <h2>Artifacts</h2><div id="run-artifacts" class="muted">Loading…</div>`;
}

async function bindRun(id) {
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
    <td>${esc(JSON.stringify(w.load))}</td><td>${when(w.last_heartbeat)}</td><td>${action(w)}</td></tr>`).join("");
  return `<h1>Workers</h1><div class="table-wrap"><table><tr><th>Name</th><th>Status</th><th>Platform</th><th>Capabilities</th><th>Labels</th><th>Load</th><th>Last heartbeat</th><th></th></tr>${rows || '<tr><td colspan="8" class="muted">No workers registered</td></tr>'}</table></div>
    <p class="muted">Revoke immediately invalidates a worker's token, requeues its jobs and blocks it from re-registering.
      If the machine was compromised, also rotate <code>AWF_WORKER_REGISTRATION_TOKEN</code> on the control plane.</p>`;
}

function bindWorkers() {
  document.querySelectorAll("[data-revoke-worker]").forEach((b) => (b.onclick = async () => {
    if (!confirm(`Revoke worker ${b.dataset.name}? Its running jobs are handed to other workers.`)) return;
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
  [/^#\/workers$/, "workers", viewWorkers, bindWorkers],
  [/^#\/agents$/, "agents", viewAgents],
  [/^#\/audit$/, "audit", viewAudit],
];

let rendering = false;
let signedOut = false;
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
      if (!focused || signedOut) { $("#view").innerHTML = html; document.querySelectorAll("tr[data-href]").forEach((tr) => (tr.onclick = () => (location.hash = tr.dataset.href))); if (bind) await bind(...m.slice(1)); }
      break;
    }
    signedOut = false;
    const pending = await api("/approvals?state=pending");
    const badge = $("#approval-badge");
    badge.textContent = pending.length; badge.classList.toggle("hidden", !pending.length);
    const session = await api("/session");
    $("#logout-btn").classList.toggle("hidden", !session.auth_required);
  } catch (err) {
    if (err instanceof AuthRequired) {
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
setInterval(() => { if ($("#drawer").classList.contains("hidden") && !signedOut) render(); }, 4000);
consumeLoginLink().then(render);
