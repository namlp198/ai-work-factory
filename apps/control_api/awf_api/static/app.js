// AI Work Factory dashboard (Plan §19 Dashboard MVP). Plain JS, no build step.
// Every text the user reads comes from i18n.js through t(): English and Vietnamese, chosen in the header.
"use strict";

const API = "/api/v1";
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const short = (id) => (id || "").slice(0, 8);
const money = (v) => `$${Number(v || 0).toFixed(4)}`;

// ------------------------------------------------------------------------------------ language
// The choice is kept in this browser (localStorage) and used on every later visit; the first visit follows the
// browser's language. Strings in i18n.js may contain markup; values put into them with {name} are escaped here
// unless the key's caller passes markup on purpose (raw: true).
const LANGS = { en: "English", vi: "Tiếng Việt" };
const LANG_KEY = "awf.lang";
function initialLang() {
  try { const saved = localStorage.getItem(LANG_KEY); if (LANGS[saved]) return saved; } catch { /* storage blocked */ }
  return (navigator.language || "").toLowerCase().startsWith("vi") ? "vi" : "en";
}
let lang = initialLang();
const locale = () => (lang === "vi" ? "vi-VN" : "en-GB");
function t(key, vars = {}, raw = false) {
  const dict = window.AWF_I18N || {};
  const text = dict[lang]?.[key] ?? dict.en?.[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (_, name) => (raw ? String(vars[name] ?? "") : esc(vars[name])));
}
// Count-dependent text: <key>.one for 1, <key>.other otherwise ({n} is the number).
const tn = (key, n, vars = {}) => t(`${key}.${n === 1 ? "one" : "other"}`, { ...vars, n });
const when = (ts) => (ts ? new Date(ts).toLocaleString(locale()) : "");
const pill = (s) => `<span class="pill s-${esc(s)}">${esc(t(`status.${s}`) === `status.${s}` ? s : t(`status.${s}`))}</span>`;

function setLang(next) {
  if (!LANGS[next]) return;
  lang = next;
  try { localStorage.setItem(LANG_KEY, next); } catch { /* storage blocked: the choice lasts until reload */ }
  applyStatic();
  render();
}

// Text in index.html (header, banner, drawer) carries data-i18n / data-i18n-title.
function applyStatic() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-title]").forEach((el) => { el.title = t(el.dataset.i18nTitle); });
  const select = $("#lang-select");
  if (select) {
    select.innerHTML = Object.entries(LANGS).map(([v, name]) => `<option value="${v}" ${v === lang ? "selected" : ""}>${esc(name)}</option>`).join("");
    select.title = t("header.language");
  }
}

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
  if (!res.ok) {  // show FastAPI's {"detail": "..."} as a sentence, not raw JSON
    const text = await res.text();
    let message = `${res.status} ${text}`;
    try { const detail = JSON.parse(text).detail; if (typeof detail === "string") message = detail; } catch { /* not JSON */ }
    throw new Error(message);
  }
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
    <h1>${t("login.title")}</h1>
    <p class="muted">${t("login.help")}</p>
    <form id="login-form" class="row"><input type="password" name="token" autocomplete="current-password" required
      placeholder="${t("login.placeholder")}"><button>${t("login.submit")}</button></form>
    <p id="login-error" class="error">${esc(message)}</p></div>`;
}

function bindLogin() {
  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/session", { method: "POST", body: JSON.stringify({ token: new FormData(e.target).get("token") }) });
      render();
    } catch (err) {
      $("#login-error").textContent = err instanceof AuthRequired ? t("login.invalid") : err.message;
    }
  };
}

$("#logout-btn").onclick = async () => { await api("/session", { method: "DELETE" }); render(); };
$("#drawer-close").onclick = () => $("#drawer").classList.add("hidden");
$("#drawer").onclick = (e) => { if (e.target.id === "drawer") $("#drawer").classList.add("hidden"); };
$("#lang-select").onchange = (e) => { e.target.blur(); setLang(e.target.value); };

function drawer(html) { $("#drawer-body").innerHTML = html; $("#drawer").classList.remove("hidden"); }

// ------------------------------------------------------------------------------------ views
// Stroke icons (24px grid) for the overview cards.
const ICONS = {
  runs: '<path d="M5 3l14 9-14 9V3z"/>',
  approvals: '<path d="M9 12l2 2 4-4"/><circle cx="12" cy="12" r="9"/>',
  workers: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
  cost: '<circle cx="12" cy="12" r="9"/><path d="M15 9.5c-.5-1-1.6-1.5-3-1.5-1.7 0-3 .8-3 2s1.3 1.7 3 2 3 .8 3 2-1.3 2-3 2c-1.4 0-2.5-.5-3-1.5M12 6v2m0 8v2"/>',
  tasks: '<path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/>',
  failed: '<path d="M12 3l10 18H2L12 3z"/><path d="M12 10v4m0 3h.01"/>',
  projects: '<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"/>',
};
const icon = (name) => `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;
const ago = (ts) => {
  if (!ts) return "";
  const s = Math.max(0, (Date.now() - new Date(ts).getTime()) / 1000);
  if (s < 60) return t("time.just_now");
  if (s < 3600) return t("time.minutes_ago", { n: Math.floor(s / 60) });
  if (s < 86400) return t("time.hours_ago", { n: Math.floor(s / 3600) });
  return t("time.days_ago", { n: Math.floor(s / 86400) });
};

// One KPI card: tone colours the icon and, when `alert`, the border (something needs you).
function kpi({ label, value, sub = "", tone = "info", ico, href, alert = false, meter = null, title = "" }) {
  const tag = (href ? `a href="${href}"` : "div") + (title ? ` title="${esc(title)}"` : "");
  const bar = meter == null ? "" : `<div class="meter"><span style="width:${Math.round(meter * 100)}%"></span></div>`;
  return `<${tag} class="kpi tone-${tone}${alert ? " alert" : ""}">
    <div class="kpi-ico">${icon(ico)}</div>
    <div class="kpi-body"><div class="kpi-label">${label}</div><div class="kpi-value">${value}</div>
      ${bar}<div class="kpi-sub">${sub}</div></div></${href ? "a" : "div"}>`;
}

async function viewOverview() {
  const [o, approvals] = await Promise.all([api("/overview"), api("/approvals?state=pending")]);
  const online = o.workers_total ? o.workers_online / o.workers_total : 0;
  const cards = [
    kpi({ label: t("overview.active_runs"), value: o.active_runs, ico: "runs", tone: "info",
          sub: tn("overview.tasks_in_progress", o.active_tasks) }),
    kpi({ label: t("overview.waiting_for_you"), value: o.waiting_approvals, ico: "approvals", href: "#/approvals",
          tone: o.waiting_approvals ? "warn" : "ok", alert: o.waiting_approvals > 0,
          sub: o.waiting_approvals ? t("overview.approvals_pending") : t("overview.nothing_to_approve") }),
    kpi({ label: t("overview.workers_online"), value: `${o.workers_online}<span class="of">/${o.workers_total}</span>`, ico: "workers",
          href: "#/workers", tone: !o.workers_total ? "idle" : o.workers_online ? "ok" : "bad",
          alert: o.workers_total > 0 && !o.workers_online, meter: o.workers_total ? online : null,
          sub: !o.workers_total ? t("overview.no_machine") : o.workers_online ? t("overview.ready_for_jobs") : t("overview.no_machine_online") }),
    kpi({ label: t("overview.llm_spend"), value: money(o.total_cost_usd).replace(/(\.\d{2})\d+$/, "$1"), ico: "cost", tone: "info",
          sub: t("overview.spend_sub"), title: t("overview.spend_title", { amount: money(o.total_cost_usd) }) }),
  ].join("");
  const minor = [
    kpi({ label: t("overview.failed_tasks"), value: o.failed_tasks, ico: "failed", tone: o.failed_tasks ? "bad" : "idle",
          sub: o.failed_tasks ? t("overview.see_runs_below") : t("common.none") }),
    kpi({ label: t("overview.projects"), value: o.projects, ico: "projects", href: "#/projects", tone: "info",
          sub: o.projects ? t("overview.open_projects") : t("overview.create_first") }),
  ].join("");

  const waiting = approvals.slice(0, 5).map((a) => `<a class="row-card warn" href="#/approvals/${a.id}">
      <div class="rc-main"><b>${esc(a.action)}</b><span class="muted">${t("common.run")} <code>${short(a.workflow_run_id)}</code></span></div>
      <span class="muted">${ago(a.created_at)}</span><span class="go">${t("overview.review")}</span></a>`).join("");
  const running = (o.runs || []).map((r) => {
    const pct = r.stages_total ? Math.round((100 * r.stages_done) / r.stages_total) : 0;
    const where = r.worker ? `<span>${t("overview.on_machine", { name: r.worker })}</span>` : (r.waiting_for ? `<span class="error">${esc(r.waiting_for)}</span>` : "");
    return `<a class="run-card${r.current_gate ? " gate" : ""}" href="#/runs/${r.id}">
      <div class="rc-head"><b>${esc(r.project || "")}</b><span class="tag">${esc(r.workflow_id)}</span>
        ${r.current_gate ? `<span class="pill s-APPROVAL_REQUIRED">${t("overview.waiting_approval")}</span>` : pill("RUNNING")}</div>
      <div class="obj">${esc(r.objective)}</div>
      <div class="rc-progress"><div class="progress"><span style="width:${pct}%"></span></div><span class="pct">${pct}%</span></div>
      <div class="rc-meta"><span>${t("overview.stages", { done: r.stages_done, total: r.stages_total })}</span>${r.current_stage ? `<span>· ${esc(r.current_stage)}</span>` : ""}
        ${where ? `<span>·</span>${where}` : ""}<span class="muted">· ${t("overview.started", { ago: ago(r.created_at) })}</span></div></a>`;
  }).join("");
  const recent = (o.recent_runs || []).map((r) => `<a class="row-card" href="#/runs/${r.id}">
      <div class="rc-main"><b>${esc(r.project || "")}</b><span class="obj1">${esc(r.objective)}</span>
        ${r.status === "FAILED" && r.error ? `<span class="error obj1">${esc(r.error)}</span>` : ""}</div>
      ${pill(r.status)}<span class="muted">${ago(r.finished_at || r.created_at)}</span></a>`).join("");
  const empty = !o.projects ? `<div class="empty"><b>${t("overview.empty_title")}</b><p class="muted">${t("overview.empty_text")}</p>
      <a class="btn" href="#/projects">${t("overview.empty_button")}</a></div>` : "";

  return `<div class="page-head"><h1>${t("overview.title")}</h1><span class="muted">${t("overview.updated", { time: new Date().toLocaleTimeString(locale()) })}</span></div>
    ${installHint()}${empty}
    <div class="kpis">${cards}</div><div class="kpis minor">${minor}</div>
    ${approvals.length ? `<section class="panel"><div class="panel-head"><h2>${t("overview.waiting_title")}</h2><span class="count warn">${approvals.length}</span>
      ${approvals.length > 5 ? `<a href="#/approvals">${t("overview.see_all")}</a>` : ""}</div><div class="list">${waiting}</div></section>` : ""}
    <section class="panel"><div class="panel-head"><h2>${t("overview.running_title")}</h2><span class="count">${(o.runs || []).length}</span></div>
      <div class="list">${running || `<p class="muted pad">${t("overview.nothing_running")}</p>`}</div></section>
    ${recent ? `<section class="panel"><div class="panel-head"><h2>${t("overview.recent_title")}</h2></div><div class="list">${recent}</div></section>` : ""}`;
}

// ---------------------------------------------------------------- installable app (PWA)
let installPrompt = null;
window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installPrompt = e; });
const standalone = () => window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

function installHint() {
  if (standalone()) return "";
  if (installPrompt) return `<div class="hint row"><span>${t("pwa.install_text")}</span><button id="install-btn">${t("pwa.install_button")}</button></div>`;
  if (/iPhone|iPad/.test(navigator.userAgent) && window.isSecureContext) return `<div class="hint">${t("pwa.ios")}</div>`;
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
const PLATFORMS = ["windows", "linux", "linux-amd64", "linux-arm64", "any"];
const platformOptions = (selected = "") => `<option value="" ${selected ? "" : "selected"} disabled>${t("common.choose")}</option>` +
  PLATFORMS.map((v) => `<option value="${v}" ${v === selected ? "selected" : ""}>${t(`platform.${v}`)} — ${v}</option>`).join("") +
  (selected && !PLATFORMS.includes(selected) ? `<option value="${esc(selected)}" selected>${esc(selected)}</option>` : "");
const LLM_NAMES = { anthropic: "Claude", google: "Gemini", openai: "ChatGPT", qwen: "Qwen", kimi: "Kimi", deepseek: "DeepSeek", minimax: "MiniMax" };
const llmName = (p) => (LLM_NAMES[p] ? `${LLM_NAMES[p]} (${p})` : p);
const llmOptions = (models, selected = "", auto = "") =>
  `<option value="">${auto || t("common.default_value", { value: llmName(models.default_provider) })}</option>` +
  models.providers.map((p) => `<option value="${esc(p)}" ${p === selected ? "selected" : ""}>${esc(llmName(p))}</option>`).join("");
const ENGINES = ["native", "claude-code", "codex", "antigravity", "qwen-code", "kimi-code", "deepseek-harness", "minimax-code"];
const engineName = (v) => (ENGINES.includes(v) ? t(`engine.${v}`) : esc(v));
const engineOptions = (selected = "", auto = "") => `<option value="">${auto || t("common.default_value", { value: t("engine.native") })}</option>` +
  ENGINES.map((v) => `<option value="${v}" ${v === selected ? "selected" : ""}>${engineName(v)}</option>`).join("");
// Commands are edited as text and sent as argv arrays: quotes group words, backslashes stay as typed (Windows paths).
const argvParse = (text) => (String(text || "").match(/"[^"]*"|'[^']*'|\S+/g) || []).map((w) => w.replace(/^(["'])(.*)\1$/, "$2"));
const argvText = (v) => (Array.isArray(v) ? v.map((a) => (/\s/.test(a) ? `"${a}"` : a)).join(" ") : (v || ""));
const capList = (raw) => String(raw || "").split(",").map((c) => c.trim()).filter(Boolean);
const confidentialityOptions = (selected = "internal") => ["internal", "public", "restricted"]
  .map((c) => `<option value="${c}" ${c === selected ? "selected" : ""}>${t(`confidentiality.${c}`)}</option>`).join("");

async function viewProjects() {
  const [projects, archived] = await Promise.all([api("/projects"), api("/projects?archived=true")]);
  const archivedRows = archived.map((p) => `<tr class="click" data-href="#/projects/${p.id}"><td>${esc(p.name)}</td>
    <td><code>${esc(p.slug)}</code></td><td>${esc(p.repository?.url || "")}</td><td>${when(p.updated_at)}</td></tr>`).join("");
  const rows = projects.map((p) => `<tr class="click" data-href="#/projects/${p.id}"><td>${esc(p.name)}</td><td><code>${esc(p.slug)}</code></td>
    <td>${p.labels?.platform ? `<code>${esc(p.labels.platform)}</code>` : `<span class="error">${t("projects.not_set")}</span>`}</td>
    <td>${esc(p.repository?.url || "")}</td><td>${t(`confidentiality.${p.confidentiality}`)}</td><td>${when(p.created_at)}</td></tr>`).join("");
  return `<h1>${t("projects.title")}</h1>
    <div class="table-wrap"><table><tr><th>${t("field.name")}</th><th>${t("field.slug")}</th><th>${t("field.platform")}</th><th>${t("field.repository")}</th><th>${t("field.confidentiality")}</th><th>${t("field.created")}</th></tr>${rows || `<tr><td colspan="6" class="muted">${t("projects.none")}</td></tr>`}</table></div>
    ${archived.length ? `<details class="archived"><summary class="muted">${t("projects.archived_list", { n: archived.length })}</summary>
      <div class="table-wrap"><table><tr><th>${t("field.name")}</th><th>${t("field.slug")}</th><th>${t("field.repository")}</th><th>${t("projects.archived_at")}</th></tr>${archivedRows}</table></div></details>` : ""}
    <h2>${t("projects.new")}</h2>
    <form class="grid" id="project-form">
      <label>${t("field.name")}<input name="name" required></label>
      <label>${t("field.repository_url")}<input name="url" placeholder="${t("projects.url_placeholder")}"></label>
      <label>${t("field.base_branch")}<input name="base_branch" value="main"></label>
      <label>${t("field.platform")}<select name="platform" required>${platformOptions()}</select></label>
      <label>${t("field.required_capabilities")}<input name="required_capabilities" placeholder="dotnet-windowsdesktop, msbuild"></label>
      <label>${t("field.build_command")}<input name="build_command" placeholder="python -m compileall -q src"></label>
      <label>${t("field.test_command")}<input name="test_command" placeholder="python -m pytest -q"></label>
      <label>${t("field.lint_command")}<input name="lint_command" placeholder="ruff check ."></label>
      <label>${t("field.confidentiality")}<select name="confidentiality">${confidentialityOptions()}</select></label>
      <label>${t("field.budget")}<input name="budget_usd" type="number" step="0.01"></label>
      <div class="full row"><button>${t("projects.create")}</button><span id="project-error" class="error"></span></div>
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
      for (const k of ["build_command", "test_command", "lint_command"]) if (f.get(k)) repo[k] = argvParse(f.get(k));
      repo.required_capabilities = capList(f.get("required_capabilities"));
      body.repository = repo;
    }
    if (f.get("budget_usd")) body.budget_usd = Number(f.get("budget_usd"));
    try { const p = await api("/projects", { method: "POST", body: JSON.stringify(body) }); location.hash = `#/projects/${p.id}`; }
    catch (err) { $("#project-error").textContent = err.message; }
  };
}

// Machine picker for run placement: "" lets the orchestrator choose the least loaded matching machine.
function machineSelect(workers, selected = "", auto = "") {
  const opts = workers.filter((w) => w.status !== "revoked").map((w) =>
    `<option value="${esc(w.name)}" ${w.name === selected ? "selected" : ""}>${esc(w.name)} · ${esc(w.platform)} · ${esc(t(`status.${w.status}`))}</option>`).join("");
  return `<select name="worker"><option value="">${auto || t("run.machine_auto")}</option>${opts}</select>`;
}

async function viewProject(id) {
  const [p, runs, workflows, workers, models] = await Promise.all([api(`/projects/${id}`), api(`/projects/${id}/workflow-runs`), api("/workflows"), api("/workers"), api("/models")]);
  const rows = runs.map((r) => `<tr class="click" data-href="#/runs/${r.id}"><td><code>${short(r.id)}</code></td><td>${esc(r.workflow_id)}</td>
    <td>${pill(r.status)}</td><td>${esc(r.input?.objective || "")}</td><td>${when(r.created_at)}</td></tr>`).join("");
  const options = workflows.filter((w) => w.status === "active").map((w) => `<option value="${esc(w.id)}">${esc(w.name || w.id)} v${esc(w.version)}</option>`).join("");
  const archived = p.status === "ARCHIVED";
  const repo = p.repository || {};
  return `<div class="page-head"><h1>${esc(p.name)}</h1><span class="muted"><code>${esc(p.slug)}</code> · ${tn("project.run_count", p.run_count)}</span></div>
    ${archived ? `<div class="archived-banner"><span>${t("project.archived_banner")}</span>
      <button class="ghost" id="restore-project">${t("project.restore")}</button></div>` : ""}
    <div class="kv"><div>${t("field.repository")}</div><div>${repo.url ? `<code>${esc(repo.url)}</code> (${esc(repo.base_branch)})` : `<span class="muted">${t("project.no_repository")}</span>`}</div>
    <div>${t("project.commands")}</div><div>build: <code>${esc(argvText(repo.build_command) || "—")}</code> test: <code>${esc(argvText(repo.test_command) || "—")}</code> lint: <code>${esc(argvText(repo.lint_command) || "—")}</code></div>
    <div>${t("project.spend_budget")}</div><div>${money(p.spend_usd)} / ${p.budget_usd == null ? "∞" : money(p.budget_usd)}</div></div>
    ${projectSettings(p)}
    ${projectMachines(p, models)}
    ${archived ? "" : `<h2>${t("run.start_title")}</h2>
    <form class="grid" id="run-form"><label>${t("field.workflow")}<select name="workflow_id">${options}</select></label>
      <label>${t("field.machine")}${machineSelect(workers)}</label>
      <label>${t("field.model")}<select name="llm">${llmOptions(models, "", t("common.project_default", { value: llmName(p.labels?.llm || models.default_provider) }))}</select></label>
      <label>${t("field.coding_engine")}<select name="coding_engine">${engineOptions("", t("common.project_default", { value: engineName(p.labels?.coding_engine || "native") }, true))}</select></label>
      <label class="full">${t("field.objective")}<textarea name="objective" rows="3" required></textarea></label>
      <div class="full row"><button>${t("run.start")}</button></div><div class="full" id="run-error"></div></form>`}
    <h2>${t("project.runs")}</h2><div class="table-wrap"><table><tr><th>${t("common.run")}</th><th>${t("field.workflow")}</th><th>${t("field.status")}</th><th>${t("field.objective")}</th><th>${t("field.created")}</th></tr>${rows || `<tr><td colspan="5" class="muted">${t("project.no_runs")}</td></tr>`}</table></div>
    ${projectDangerZone(p)}`;
}

function projectSettings(p) {
  const r = p.repository || {};
  return `<h2>${t("settings.title")}</h2>
    <form class="grid" id="project-settings-form">
      <label>${t("field.name")}<input name="name" value="${esc(p.name)}" required></label>
      <label>${t("field.repository_url")}<input name="url" value="${esc(r.url || "")}" placeholder="https://… , ssh://… , D:/src/repo"></label>
      <label>${t("field.base_branch")}<input name="base_branch" value="${esc(r.base_branch || "main")}"></label>
      <label>${t("field.build_command")}<input name="build_command" value="${esc(argvText(r.build_command))}" placeholder="dotnet build App.csproj -nologo"></label>
      <label>${t("field.test_command")}<input name="test_command" value="${esc(argvText(r.test_command))}" placeholder="dotnet test tests/App.Tests"></label>
      <label>${t("field.lint_command")}<input name="lint_command" value="${esc(argvText(r.lint_command))}" placeholder="${t("common.optional")}"></label>
      <label>${t("field.confidentiality")}<select name="confidentiality">${confidentialityOptions(p.confidentiality)}</select></label>
      <label>${t("settings.budget")}<input name="budget_usd" type="number" step="0.01" min="0" value="${p.budget_usd ?? ""}"></label>
      <label class="full">${t("field.description")}<textarea name="description" rows="2">${esc(p.description || "")}</textarea></label>
      <div class="full row"><button>${t("settings.save")}</button><span id="settings-msg" class="muted"></span><span id="settings-error" class="error"></span>
        <span class="muted">${t("settings.note")}</span></div></form>`;
}

function projectDangerZone(p) {
  if (p.status === "ARCHIVED") return "";
  return `<h2>${t("danger.title")}</h2><div class="danger-zone">
    ${!p.run_count ? `<b>${t("danger.delete_title")}</b><p class="muted">${t("danger.delete_text")}</p>
      <button class="danger" id="delete-project">${t("danger.delete_button")}</button>`
    : `<b>${t("danger.archive_title")}</b><p class="muted">${t("danger.archive_text", { n: p.run_count })}</p>
      <button class="danger" id="archive-project">${t("danger.archive_button")}</button>`}
    <span id="danger-error" class="error"></span></div>`;
}

// Explains why a run was refused when the fix is on a worker machine.
// What a worker needs for each provider key / engine CLI (same table as awf/agent_runtime/providers.py).
const KEY_HELP = { anthropic: ["ANTHROPIC_API_KEY", "platform.claude.com"], google: ["GEMINI_API_KEY", "aistudio.google.com"],
  openai: ["OPENAI_API_KEY", "platform.openai.com"], qwen: ["DASHSCOPE_API_KEY", "modelstudio.console.alibabacloud.com"],
  kimi: ["MOONSHOT_API_KEY", "platform.kimi.ai"], deepseek: ["DEEPSEEK_API_KEY", "platform.deepseek.com"],
  minimax: ["MINIMAX_API_KEY", "platform.minimax.io"] };
const ENGINE_HELP = { "claude-code": ["claude", "code.claude.com"], codex: ["codex", "npm i -g @openai/codex"],
  antigravity: ["agy", "antigravity.google/docs/getting-started"], "qwen-code": ["qwen", "npm i -g @qwen-code/qwen-code"],
  "kimi-code": ["kimi", "code.kimi.com"], "deepseek-harness": ["dsh", "npm i -g @deepseek-ai/dsh"],
  "minimax-code": ["mcode", "npm i -g @minimax-ai/code"] };

// Explains why a run was refused when the fix is on a worker machine.
function runErrorHint(message) {
  const hints = [];
  const missing = new Set([...message.matchAll(/missing ([^;)]+)/g)].flatMap((m) => m[1].split(",").map((x) => x.trim())));
  for (const cap of missing) {
    const provider = cap.startsWith("llm-") ? cap.slice(4) : null;
    if (provider && KEY_HELP[provider]) {
      hints.push(t("hint.llm_missing", { provider: LLM_NAMES[provider] || provider, env: KEY_HELP[provider][0], console: KEY_HELP[provider][1] }));
    } else if (ENGINE_HELP[cap]) {
      hints.push(t("hint.engine_missing", { engine: t(`engine.${cap}`), cmd: ENGINE_HELP[cap][0], install: ENGINE_HELP[cap][1] }));
    }
  }
  if (/platform is /.test(message)) hints.push(t("hint.platform"));
  return `<span class="error">${esc(message)}</span>${[...new Set(hints)].map((h) => `<span class="hint-fix">${h}</span>`).join("")}`;
}

function projectMachines(p, models) {
  const req = p.requirements || {};
  const rows = (p.machines || []).map((m) => `<tr><td>${esc(m.name)}</td><td>${pill(m.status)}</td><td><code>${esc(m.platform)}</code></td>
    <td>${m.matches ? `<span class="pill s-PASS">${t("common.yes")}</span>` : `<span class="error">${esc(m.reasons.join("; "))}</span>`}</td></tr>`).join("");
  const none = req.platform && (p.machines || []).length && !(p.machines || []).some((m) => m.matches);
  return `<h2>${t("where.title")}</h2>
    ${req.platform ? "" : `<p class="error">${t("where.no_platform")}</p>`}
    ${none ? `<p class="error">${t("where.no_machine")}</p>` : ""}
    <form class="grid" id="project-edit-form">
      <label>${t("field.platform")}<select name="platform" required>${platformOptions(req.platform || "")}</select></label>
      <label>${t("field.required_capabilities")}<input name="required_capabilities" value="${esc((p.repository?.required_capabilities || []).join(", "))}"
        placeholder="dotnet-windowsdesktop, msbuild"></label>
      <label>${t("where.default_model")}<select name="llm">${llmOptions(models, p.labels?.llm || "")}</select></label>
      <label>${t("where.default_engine")}<select name="coding_engine">${engineOptions(p.labels?.coding_engine || "")}</select></label>
      <div class="full row"><button class="ghost">${t("common.save")}</button><span id="project-edit-error" class="error"></span>
        <span class="muted">${t("where.note")}</span></div></form>
    <div class="table-wrap"><table><tr><th>${t("field.machine")}</th><th>${t("field.status")}</th><th>${t("field.platform")}</th><th>${t("where.can_run")}</th></tr>
      ${rows || `<tr><td colspan="4" class="muted">${t("workers.none")}</td></tr>`}</table></div>`;
}

function bindProject(id) {
  const settings = $("#project-settings-form");
  if (settings) settings.onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(settings);
    try {
      const p = await api(`/projects/${id}`);
      const body = { name: f.get("name"), description: f.get("description") || "", confidentiality: f.get("confidentiality"),
                     budget_usd: f.get("budget_usd") === "" ? null : Number(f.get("budget_usd")) };
      if (f.get("url")) {
        const repo = { ...(p.repository || {}), url: f.get("url"), base_branch: f.get("base_branch") || "main" };
        for (const k of ["build_command", "test_command", "lint_command"]) {
          // An untouched field keeps its stored value exactly; an edited one is parsed into argv.
          if (f.get(k) === argvText(p.repository?.[k])) continue;
          if (f.get(k)) repo[k] = argvParse(f.get(k)); else delete repo[k];
        }
        body.repository = repo;
      }
      await api(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) });
      await render();
      const msg = $("#settings-msg"); if (msg) msg.textContent = t("common.saved");
    } catch (err) { $("#settings-error").textContent = err.message; }
  };
  const del = $("#delete-project");
  if (del) del.onclick = async () => {
    if (!confirm(t("danger.delete_confirm"))) return;
    try { await api(`/projects/${id}`, { method: "DELETE" }); location.hash = "#/projects"; }
    catch (err) { $("#danger-error").textContent = err.message; }
  };
  const arc = $("#archive-project");
  if (arc) arc.onclick = async () => {
    if (!confirm(t("danger.archive_confirm"))) return;
    try { await api(`/projects/${id}/archive`, { method: "POST" }); render(); }
    catch (err) { $("#danger-error").textContent = err.message; }
  };
  const restore = $("#restore-project");
  if (restore) restore.onclick = async () => { await api(`/projects/${id}/restore`, { method: "POST" }); render(); };

  const edit = $("#project-edit-form");
  if (edit) edit.onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(edit);
    try {
      const p = await api(`/projects/${id}`);
      const labels = { ...(p.labels || {}), platform: f.get("platform") };
      if (f.get("llm")) labels.llm = f.get("llm"); else delete labels.llm;
      if (f.get("coding_engine")) labels.coding_engine = f.get("coding_engine"); else delete labels.coding_engine;
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
      if (f.get("coding_engine")) body.coding_engine = f.get("coding_engine");
      const r = await api(`/projects/${id}/workflow-runs`, { method: "POST", body: JSON.stringify(body) });
      location.hash = `#/runs/${r.id}`;
    } catch (err) { $("#run-error").innerHTML = runErrorHint(err.message); }
  };
}

async function viewRun(id) {
  const [run, tasks, approvals, workers] = await Promise.all([api(`/workflow-runs/${id}`), api(`/workflow-runs/${id}/tasks`), api("/approvals?state=pending"), api("/workers")]);
  const pipeline = run.stages.map((s) => `<div class="stage ${esc(s.status)}"><div class="t">${esc(s.title)}</div>
    <div class="m">${esc(s.kind)}${s.gate ? ` · ${t("run.gate")}` : ""} · ${t("run.iteration", { n: s.iteration })}</div><div>${pill(s.status)}</div></div>`).join('<div class="arrow">→</div>');
  const rows = tasks.map((task) => `<tr class="click" data-task="${task.id}"><td>${esc(task.stage_id)}</td><td>${esc(task.title)}</td>
    <td>${esc(task.assigned_role || task.task_type)}</td><td>${pill(task.state)}</td><td>${task.iteration}</td><td>${task.attempt}/${task.max_attempts}</td>
    <td>${task.output?.critic ? pill(task.output.critic.decision) : ""} ${task.output?.critic?.verdict ? pill(task.output.critic.verdict) : ""}</td></tr>`).join("");
  const mine = approvals.filter((a) => a.workflow_run_id === id);
  return `<div class="row"><h1>${t("common.run")} <code>${short(run.id)}</code></h1>${pill(run.status)}
      ${run.status === "RUNNING" ? `<button class="danger" id="cancel-run">${t("run.cancel")}</button>` : ""}</div>
    <div class="kv"><div>${t("field.workflow")}</div><div>${esc(run.workflow_id)} v${esc(run.workflow_version)}</div>
      <div>${t("field.objective")}</div><div>${esc(run.input?.objective)}</div>
      ${placementRows(run, workers)}
      ${run.input?.repository ? `<div>${t("run.integration_branch")}</div><div><code>${esc(run.integration_branch)}</code></div>` : ""}
      <div>${t("run.fix_loops")}</div><div>${esc(JSON.stringify(run.fix_loops))}</div>
      <div>${t("run.llm_cost")}</div><div>${money(run.cost_usd)}</div>
      ${run.error ? `<div>${t("common.error")}</div><div class="error">${esc(run.error)}</div>` : ""}</div>
    ${mine.length ? `<h2>${t("overview.waiting_title")}</h2>${approvalTable(mine)}` : ""}
    <h2>${t("field.workflow")}</h2><div class="pipeline">${pipeline}</div>
    <h2>${t("run.tasks")}</h2><div class="table-wrap"><table><tr><th>${t("run.stage")}</th><th>${t("run.task")}</th><th>${t("run.role")}</th><th>${t("run.state")}</th><th>${t("run.iter")}</th><th>${t("run.attempt")}</th><th>${t("run.critic")}</th></tr>${rows}</table></div>
    <h2>${t("run.artifacts")}</h2><div id="run-artifacts" class="muted">${t("common.loading")}</div>`;
}

function placementRows(run, workers) {
  if (run.placement?.mode !== "run") return `<div>${t("field.machine")}</div><div class="muted">${t("run.placement_stage")}</div>`;
  const w = run.assigned_worker;
  const machine = w ? `<b>${esc(w.name || short(w.id))}</b> ${w.status ? pill(w.status) : ""} <span class="muted">${t("run.since", { time: when(run.assigned_at) })}</span>`
    : `<span class="muted">${t("run.not_assigned")}</span>`;
  const req = run.requirements || {};
  const move = run.status === "RUNNING" ? `<form id="reassign-form" class="row">${machineSelect(workers, run.placement.worker || "", t("run.machine_choose"))}
      <button class="ghost">${t("run.move")}</button><span id="reassign-error" class="error"></span></form>` : "";
  return `<div>${t("field.machine")}</div><div>${machine}${run.placement.worker ? ` <span class="muted">${t("run.chosen_by_hand")}</span>` : ""}
      ${run.placement.wait ? `<div class="error">${esc(run.placement.wait)}</div>` : ""}${move}</div>
    <div>${t("run.requirements")}</div><div>${t("field.platform")} <code>${esc(req.platform || "any")}</code> · ${t("run.capabilities")} <code>${esc((req.capabilities || []).join(", "))}</code></div>
    ${run.llm ? `<div>${t("field.model")}</div><div>${esc(llmName(run.llm))}</div>` : ""}
    ${run.coding_engine && run.coding_engine !== "native" ? `<div>${t("field.coding_engine")}</div><div>${engineName(run.coding_engine)}</div>` : ""}`;
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
  if (btn) btn.onclick = async () => { if (confirm(t("run.cancel_confirm"))) { await api(`/workflow-runs/${id}/cancel`, { method: "POST" }); render(); } };
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
  return `<div class="table-wrap"><table><tr><th>${t("artifact.type")}</th><th>${t("field.name")}</th><th>${t("artifact.version")}</th><th>${t("run.stage")}</th><th>${t("artifact.checksum")}</th><th>${t("artifact.bytes")}</th></tr>${rows || `<tr><td colspan="6" class="muted">${t("common.none")}</td></tr>`}</table></div>`;
}

function bindArtifactLinks() { document.querySelectorAll("tr[data-artifact]").forEach((tr) => (tr.onclick = () => openArtifact(tr.dataset.artifact))); }

async function openArtifact(id) {
  const [a, content, rel, impact] = await Promise.all([api(`/artifacts/${id}`), api(`/artifacts/${id}/content`), api(`/artifacts/${id}/relations`), api(`/artifacts/${id}/impact`)]);
  const relRows = [...rel.outgoing.map((r) => `<li>${esc(r.relation_type)} → <code>${short(r.to_artifact_id)}</code></li>`),
    ...rel.incoming.map((r) => `<li><code>${short(r.from_artifact_id)}</code> ${esc(r.relation_type)} → ${t("artifact.this")}</li>`)].join("");
  drawer(`<h2>${esc(a.name)} <span class="muted">v${a.version}</span></h2>
    <div class="kv"><div>${t("artifact.type")}</div><div>${esc(a.type)}</div><div>${t("artifact.checksum")}</div><div><code>${esc(a.checksum)}</code></div>
    <div>${t("artifact.storage")}</div><div><code>${esc(a.storage_backend)}</code> ${esc(a.uri)}</div><div>${t("run.task")}</div><div><code>${short(a.task_id)}</code></div></div>
    <h3>${t("artifact.traceability")}</h3><ul>${relRows || `<li class='muted'>${t("common.none")}</li>`}</ul>
    <h3>${t("artifact.impact", { n: impact.impacted_artifacts.length })}</h3>
    <ul>${impact.impacted_artifacts.map((x) => `<li>${esc(x.type)} <code>${esc(x.name)}</code> (${t("artifact.depth", { n: x.depth })}, ${esc(x.via)})</li>`).join("")}</ul>
    <h3>${t("artifact.content")}</h3><pre>${esc(content)}</pre>`);
}

async function openTask(id) {
  const task = await api(`/tasks/${id}`);
  const runs = task.agent_runs.map((r) => `<h3>${t("task.agent_run")} <code>${short(r.id)}</code> ${pill(r.status)}</h3>
    <div class="kv"><div>${t("field.model")}</div><div>${esc(r.model_provider)}/${esc(r.model_name)} (${esc(r.model_class)})</div>
    <div>${t("task.route_reason")}</div><div>${esc(r.route_reason)}</div><div>${t("task.tokens")}</div><div>${t("task.tokens_value", { in: r.token_input, out: r.token_output, cost: money(r.cost_estimate), ms: r.duration_ms })}</div>
    <div>${t("run.critic")}</div><div>${r.critic?.decision ? pill(r.critic.decision) : ""} ${esc((r.critic?.findings || []).map((f) => `[${f.severity}] ${f.message}`).join(" · "))}</div>
    ${r.error ? `<div>${t("common.error")}</div><div class="error">${esc(r.error)}</div>` : ""}</div>
    <details><summary>${tn("task.tool_calls", r.tool_calls.length)}</summary><pre>${esc(r.tool_calls.map((c) => `${c.status.padEnd(8)} ${c.tool} ${JSON.stringify(c.input || {})} ${c.message || ""}`).join("\n"))}</pre></details>`).join("");
  const jobs = task.jobs.map((j) => `<li><a href="#" data-logs="${j.id}">${t("task.job", { id: short(j.id) })}</a> ${pill(j.state)} ${t("task.attempt_n", { n: j.attempt })} ${j.error ? `<span class="error">${esc(j.error)}</span>` : ""}</li>`).join("");
  drawer(`<h2>${esc(task.title)}</h2><div class="kv"><div>${t("run.state")}</div><div>${pill(task.state)}</div><div>${t("run.role")}</div><div>${esc(task.assigned_role || task.task_type)}</div>
    <div>${t("task.iteration_attempt")}</div><div>${t("task.iteration_attempt_value", { iter: task.iteration, attempt: task.attempt, max: task.max_attempts })}</div>
    <div>${t("task.acceptance")}</div><div>${(task.acceptance_criteria || []).map(esc).join("<br>") || "—"}</div>
    ${task.input?.feedback?.length ? `<div>${t("task.feedback")}</div><div>${task.input.feedback.map(esc).join("<br>")}</div>` : ""}
    ${task.output?.failure_reason ? `<div>${t("task.failure")}</div><div class="error">${esc(task.output.failure_reason)}</div>` : ""}</div>
    <h3>${t("task.summary")}</h3><p>${esc(task.output?.summary || "—")}</p>
    <h3>${t("task.jobs")}</h3><ul>${jobs || `<li class='muted'>${t("common.none")}</li>`}</ul><pre id="job-logs" class="hidden"></pre>
    ${runs}<h3>${t("run.artifacts")}</h3>${artifactTable(task.artifacts)}`);
  bindArtifactLinks();
  document.querySelectorAll("[data-logs]").forEach((a) => (a.onclick = async (e) => {
    e.preventDefault();
    const logs = await api(`/jobs/${a.dataset.logs}/logs`);
    const pre = $("#job-logs"); pre.classList.remove("hidden");
    pre.textContent = logs.map((l) => `[${l.stream}] ${l.line}`).join("\n") || t("task.no_logs");
  }));
}

function approvalTable(list) {
  const rows = list.map((a) => `<tr><td>${esc(a.action)}</td><td>${a.risk_level}</td><td><a href="#/runs/${a.workflow_run_id}"><code>${short(a.workflow_run_id)}</code></a></td>
    <td>${(a.scope?.artifact_ids || []).map((x) => `<a href="#" data-art="${x}"><code>${short(x)}</code></a>`).join(" ")}</td><td>${pill(a.state)}</td>
    <td>${a.state === "PENDING" ? `<div class="row"><input placeholder="${t("approval.comment")}" data-comment="${a.id}" style="width:180px">
      <button data-approve="${a.id}">${t("approval.approve")}</button><button class="danger" data-reject="${a.id}">${t("approval.reject")}</button></div>` : esc(a.approved_by || "")}</td></tr>`).join("");
  return `<div class="table-wrap"><table><tr><th>${t("approval.action")}</th><th>${t("approval.risk")}</th><th>${t("common.run")}</th><th>${t("run.artifacts")}</th><th>${t("run.state")}</th><th></th></tr>${rows || `<tr><td colspan="6" class="muted">${t("approval.nothing")}</td></tr>`}</table></div>`;
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
        <div>${t("approval.project")}</div><div>${a.project ? `<a href="#/projects/${a.project.id}">${esc(a.project.name)}</a>` : "—"}</div>
        <div>${t("common.run")}</div><div>${a.run ? `<a href="#/runs/${a.run.id}"><code>${short(a.run.id)}</code></a> ${esc(a.run.workflow_id)}` : "—"}</div>
        <div>${t("field.objective")}</div><div>${esc(a.run?.input?.objective || "")}</div>
        <div>${t("approval.risk")}</div><div>${a.risk_level}</div>
        ${a.scope?.description ? `<div>${t("approval.what_to_check")}</div><div>${esc(a.scope.description)}</div>` : ""}
        ${a.approved_by ? `<div>${t("approval.decided_by")}</div><div>${esc(a.approved_by)} ${a.comment ? `— ${esc(a.comment)}` : ""}</div>` : ""}
      </div>
      <h3>${t("approval.artifacts_to_review")}</h3><ul>${arts || `<li class='muted'>${t("common.none")}</li>`}</ul>
      ${pending ? `<textarea data-comment="${a.id}" rows="2" placeholder="${t("approval.comment_optional")}"></textarea>
        <div class="row decide"><button data-approve="${a.id}">${t("approval.approve")}</button><button class="danger" data-reject="${a.id}">${t("approval.reject")}</button></div>` : ""}
    </div>`;
}

async function viewNotifications() {
  const n = await api("/notifications");
  const channels = n.channels.length ? n.channels.map((c) => pill(c)).join(" ") : `<span class="error">${t("notify.none_configured")}</span>`;
  const delivery = (d, c) => `${c}: ${t("notify.sent", { n: d.done })}${d.failed ? `, ${t("notify.failed", { n: d.failed })}` : ""}${d.error ? ` (${esc(d.error)})` : ""}`;
  const rows = n.items.map((i) => `<tr><td>${when(i.created_at)}</td><td>${esc(i.title)}</td><td>${pill(i.state)}</td>
    <td>${Object.entries(i.deliveries).map(([c, d]) => delivery(d, esc(c))).join("; ")}</td></tr>`).join("");
  return `<h1>${t("notify.title")}</h1>
    <div class="kv"><div>${t("notify.channels")}</div><div>${channels}</div>
      <div>${t("notify.events")}</div><div>${esc(n.events.join(", "))}</div>
      <div>${t("notify.telegram")}</div><div>${n.telegram_approvals ? t("notify.telegram_yes") : t("notify.telegram_no")}</div>
      <div>${t("notify.links")}</div><div>${n.public_url ? `<code>${esc(n.public_url)}</code>` : `<span class="error">${t("notify.no_public_url")}</span>`}</div></div>
    <div class="row"><button id="notify-test" ${n.channels.length ? "" : "disabled"}>${t("notify.test")}</button><span id="notify-msg" class="muted"></span></div>
    <h2>${t("notify.recent")}</h2><div class="table-wrap"><table><tr><th>${t("field.created")}</th><th>${t("notify.message")}</th><th>${t("run.state")}</th><th>${t("notify.delivery")}</th></tr>
      ${rows || `<tr><td colspan="4" class="muted">${t("notify.nothing")}</td></tr>`}</table></div>`;
}

function bindNotifications() {
  const btn = $("#notify-test");
  if (btn) btn.onclick = async () => {
    try { await api("/notifications/test", { method: "POST" }); $("#notify-msg").textContent = t("notify.queued"); }
    catch (err) { $("#notify-msg").textContent = err.message; }
  };
}

async function viewApprovals() {
  const all = await api("/approvals");
  return `<h1>${t("approvals.title")}</h1>${approvalTable(all.filter((a) => a.state === "PENDING"))}
    <h2>${t("approvals.history")}</h2>${approvalTable(all.filter((a) => a.state !== "PENDING").slice(0, 50))}`;
}

async function viewWorkers() {
  const workers = await api("/workers");
  const action = (w) => w.status === "revoked"
    ? `<button class="ghost" data-delete-worker="${w.id}" title="${t("workers.delete_title")}">${t("workers.delete")}</button>`
    : `<button class="danger" data-revoke-worker="${w.id}" data-name="${esc(w.name)}" title="${t("workers.revoke_title")}">${t("workers.revoke")}</button>`;
  const rows = workers.map((w) => `<tr><td>${esc(w.name)}<div class="muted"><code>${esc(w.machine_id || "")}</code></div></td><td>${pill(w.status)}</td><td><code>${esc(w.platform)}</code></td>
    <td>${(w.capabilities || []).map(esc).join(", ")}</td><td>${esc(Object.entries(w.labels || {}).map(([k, v]) => `${k}=${v}`).join(", "))}</td>
    <td>${w.active_runs ?? 0} <span class="muted">· ${tn("workers.slots", w.max_jobs ?? 1)}</span></td>
    <td>${esc(JSON.stringify(w.load))}</td><td>${when(w.last_heartbeat)}</td><td>${action(w)}</td></tr>`).join("");
  return `<h1>${t("workers.title")}</h1><div class="table-wrap"><table><tr><th>${t("field.name")}</th><th>${t("field.status")}</th><th>${t("field.platform")}</th><th>${t("run.capabilities")}</th><th>${t("workers.labels")}</th><th>${t("workers.runs")}</th><th>${t("workers.load")}</th><th>${t("workers.heartbeat")}</th><th></th></tr>${rows || `<tr><td colspan="9" class="muted">${t("workers.none")}</td></tr>`}</table></div>
    <p class="muted">${t("workers.revoke_note")}</p>`;
}

function bindWorkers() {
  document.querySelectorAll("[data-revoke-worker]").forEach((b) => (b.onclick = async () => {
    if (!confirm(t("workers.revoke_confirm", { name: b.dataset.name }, true))) return;
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
  return `<h1>${t("agents.title")}</h1><div class="table-wrap"><table><tr><th>${t("run.role")}</th><th>${t("agents.model_class")}</th><th>${t("workers.runs")}</th><th>${t("agents.success")}</th><th>${t("agents.retry")}</th><th>${t("agents.critic_rejection")}</th><th>${t("agents.avg_ms")}</th><th>${t("agents.avg_cost")}</th><th>${t("agents.tools")}</th></tr>${rows}</table></div>`;
}

async function viewAudit() {
  const events = await api("/audit?limit=300");
  const rows = events.map((e) => `<tr><td>${when(e.created_at)}</td><td>${esc(e.event_type)}</td><td>${esc(e.actor_type)}:${esc(e.actor_id)}</td>
    <td>${e.workflow_run_id ? `<a href="#/runs/${e.workflow_run_id}"><code>${short(e.workflow_run_id)}</code></a>` : ""}</td><td><code>${esc(JSON.stringify(e.payload)).slice(0, 300)}</code></td></tr>`).join("");
  return `<h1>${t("audit.title")}</h1><div class="table-wrap"><table><tr><th>${t("audit.time")}</th><th>${t("audit.event")}</th><th>${t("audit.actor")}</th><th>${t("common.run")}</th><th>${t("audit.payload")}</th></tr>${rows}</table></div>`;
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
      if (!shown) $("#view").innerHTML = `<div class="card"><h1>${t("offline.title")}</h1><p class="muted">${t("offline.text")}</p></div>`;
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
applyStatic();
consumeLoginLink().then(render);
