"""Executes one job payload inside an ephemeral workspace and returns a structured result."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from awf.agent_runtime import AgentContract, AgentRuntime
from awf.agent_runtime.adapters import ModelAdapter, ModelError, get_adapter
from awf.agent_runtime.runtime import AgentResult
from awf.agent_runtime.definitions import submit_tool_schema
from awf.agent_runtime.validation import has_section, validate_submission
from awf.policy import PolicyEngine
from awf.security import redact, redact_obj
from awf.tool_sdk import ToolContext, ToolRegistry
from awf.tool_sdk.build import configured_command
from awf.tool_sdk.command import GitError
from awf.worker_sdk import claude_code, cli_engines, codex
from awf.worker_sdk.sandbox import CommitInfo, IntegrationConflict, Workspace

# Permissions a standalone tool job needs; tool jobs are platform actions, not agent actions.
TOOL_JOB_PERMISSIONS = {"filesystem": {"read": True}, "git": {"read": True}, "build": True, "test": True}

LogFn = Callable[[str, str], None]


class JobExecutor:
    def __init__(self, workspace_root: Path, policy: PolicyEngine, registry: ToolRegistry | None = None,
                 adapter_factory: Callable[[str], ModelAdapter] = get_adapter, keep_workspaces: bool = False,
                 claude_code_bin: list[str] | None = None, codex_bin: list[str] | None = None,
                 engine_bins: dict[str, list[str]] | None = None):
        self.workspace_root = workspace_root
        self.claude_code_bin = claude_code_bin
        self.codex_bin = codex_bin
        self.engine_bins = engine_bins or {}  # tests: engine -> argv of a stand-in CLI
        self.policy = policy
        self.registry = registry or ToolRegistry()
        self.runtime = AgentRuntime(self.registry)
        self.adapter_factory = adapter_factory
        self.keep_workspaces = keep_workspaces

    def execute(self, job_id: str, payload: dict[str, Any], log: LogFn | None = None,
                on_state: Callable[[str], None] | None = None) -> dict[str, Any]:
        started = time.monotonic()
        log = log or (lambda line, stream="stdout": None)
        notify = on_state or (lambda state: None)
        ws = Workspace(self.workspace_root, job_id, git_env=payload.get("_git_env"))
        try:
            notify("PREPARING")
            repo = payload.get("repository") or {}
            wsp = payload.get("workspace") or {}
            if repo.get("url"):
                resolve = wsp.get("resolve")
                ws.prepare(repo["url"], repo.get("base_branch") or "main", wsp.get("integration_branch"),
                           wsp.get("task_branch") if wsp.get("write") and not resolve else None)
                if resolve:
                    files = ws.start_resolve(resolve["kind"], resolve["source"], resolve["target"])
                    log(f"conflict workspace: {resolve['source']} onto {resolve['target']}, "
                        f"{len(files)} conflicted file(s): {', '.join(files)}", "system")
                else:
                    log(f"workspace ready at {ws.repo} (branch {wsp.get('task_branch') or wsp.get('integration_branch')})",
                        "system")
            elif payload.get("job_type") == "agent" and not wsp.get("write"):
                ws.prepare_scratch()  # content/research runs without a repository: an empty working directory
                log(f"empty workspace at {ws.repo} (no repository)", "system")
            else:
                raise ValueError("job payload has no repository url")
            notify("RUNNING")
            if payload.get("job_type") == "tool":
                result = self._run_tool_job(payload, ws, log)
            else:
                result = self._run_agent_job(payload, ws, log)
            notify("UPLOADING_RESULT")
        except IntegrationConflict as exc:
            result = {"status": "failed", "error": str(exc), "retryable": False, "error_code": "INTEGRATION_CONFLICT",
                      "conflict": exc.as_dict()}
        except (GitError, ValueError, OSError) as exc:
            result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "retryable": True}
        finally:
            if not self.keep_workspaces:
                try:
                    ws.cleanup()
                except OSError as exc:
                    log(f"workspace cleanup failed: {exc}", "stderr")
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
        return redact_obj(result)

    # ------------------------------------------------------------------ agent jobs
    def _run_agent_job(self, payload: dict[str, Any], ws: Workspace, log: LogFn) -> dict[str, Any]:
        agent = payload["agent"]
        contract = AgentContract.model_validate(agent["contract"])
        route = agent["route"]
        task = payload["task"]
        writes = bool(payload.get("workspace", {}).get("write"))
        ctx = ToolContext(workspace=ws.repo, permissions=contract.permissions, policy=self.policy,
                          repository=payload.get("repository") or {}, base_ref=ws.base_ref, log=log)
        engine = agent.get("engine") or claude_code.NATIVE
        adapter = None
        if engine == claude_code.NATIVE:
            try:
                adapter = self.adapter_factory(route["provider"])
            except ModelError as exc:
                return {"status": "failed", "error": str(exc), "retryable": exc.retryable,
                        "error_code": exc.code or "MODEL_REJECTED"}
        meta = {
            "role": agent["role"],
            "task": task,
            "objective": payload.get("context", {}).get("objective", ""),
            "contract": agent["contract"],
        }
        log(f"agent {agent['role']} starting on {route['provider']}/{route['model']}"
            + (f" with engine {engine}" if engine != claude_code.NATIVE else ""), "system")
        if engine in (claude_code.ENGINE, codex.ENGINE, *cli_engines.ENGINES):
            res, engine_usage = self._run_engine(engine, payload, contract, route, ws, ctx, log)
        elif adapter is not None:
            res = self.runtime.run(contract=contract, system_prompt=agent["system_prompt"],
                                   user_message=payload["context"]["user_message"], adapter=adapter,
                                   model=route["model"], params=route.get("params") or {}, tool_ctx=ctx, meta=meta)
            engine_usage = {}
        else:
            return {"status": "failed", "error": f"coding engine '{engine}' is not supported by this worker",
                    "retryable": False}
        result: dict[str, Any] = {
            "status": res.status,
            "summary": res.summary,
            "outputs": res.outputs,
            "decision": res.decision,
            "findings": res.findings,
            "evidence": _public_evidence(ctx.evidence),
            "tool_calls": ctx.calls,
            "usage": {"input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
                      "turns": res.turns, "provider": route["provider"], "model": route["model"], **engine_usage},
            "error": res.error,
            "retryable": res.retryable,
        }
        if res.error_code:
            result["error_code"] = res.error_code
        if res.status != "succeeded":
            return result

        commit = None
        if writes and "git.branch_write" in _allowed_actions(self.policy, contract.permissions):
            message = f"awf({task.get('stage_id')}): {task.get('title')}\n\nTask: {task.get('id')}\nRole: {agent['role']}"
            try:
                info = ws.commit_and_integrate(message[:2000])
            except IntegrationConflict as exc:
                # The work is committed on its task branch; a "resolve conflict" task brings it into the run.
                result["integration_conflict"] = exc.as_dict()
                log(f"[platform] {exc}; a conflict-resolution task will integrate this work", "system")
                info = CommitInfo(has_changes=True, task_branch=ws.task_branch, integration_branch=ws.integration_branch)
            commit = info
            result["commit"] = info.as_dict()
            log(f"commit: changes={info.has_changes} sha={info.sha} integrated={info.integrated}", "system")
        else:
            dirty = _dirty_files(ws)
            if dirty:
                result["findings"] = list(result["findings"]) + [{
                    "severity": "INFO", "message": f"read-only role left {len(dirty)} uncommitted file(s); discarded"}]
        result["evidence"]["has_changes"] = bool(commit and commit.has_changes)

        for out in contract.worker_outputs():
            if out.type == "source_diff":
                result["outputs"][out.name] = commit.diff if commit else ""
            elif out.type == "test_evidence":
                result["outputs"][out.name] = json.dumps(_public_evidence(ctx.evidence), indent=2)
        return result

    # ------------------------------------------------------------------ engines (ADR-011, -012, -013, -014)
    def _run_engine(self, engine: str, payload: dict[str, Any], contract: AgentContract, route: dict[str, Any],
                    ws: Workspace, ctx: ToolContext, log: LogFn) -> tuple[AgentResult, dict[str, Any]]:
        """An engine does the role's work in the workspace; the platform then verifies, commits and reports.
        Coding roles edit files; read-only roles (analyst, architect, reviewer, QA: engine scope `all`) may only
        read. A contract the final Markdown message cannot carry (a decision, several or JSON outputs) is answered
        with an `awf-result` JSON block, validated like a native submission (one corrected attempt)."""
        usage: dict[str, Any] = {"engine": engine, "cost_usd": None}  # None: estimated from tokens
        auth = None
        if engine == claude_code.ENGINE:
            base, hint = self.claude_code_bin or claude_code.claude_code_bin(), "Claude Code or set AWF_CLAUDE_CODE_BIN"
            auth = claude_code.auth_mode()
        elif engine == codex.ENGINE:
            base, hint = self.codex_bin or codex.codex_bin(), "Codex (npm i -g @openai/codex) or set AWF_CODEX_BIN"
            auth = codex.auth_mode()
        else:
            spec = cli_engines.SPECS[engine]
            base = self.engine_bins.get(engine) or cli_engines.engine_bin(engine)
            hint = f"{spec['install']}, or set {spec['bin_env']}"
        if not base:
            return AgentResult(status="failed", error=f"{engine} is not installed on this worker (install {hint})",
                               retryable=False, error_code="ENGINE_MISSING"), usage
        writes = bool(payload.get("workspace", {}).get("write"))
        structured = needs_result_block(contract)

        commands = []
        for key, tool in (("build_command", "build.run"), ("test_command", "test.run"), ("lint_command", "lint.run")):
            argv = configured_command(ctx, key)
            if (argv and (writes or tool in contract.allowed_tools) and self.policy.command_allowed(argv)
                    and "," not in " ".join(argv)):
                commands.append(argv)
        template = {claude_code.ENGINE: claude_code.ENGINE_RULES, codex.ENGINE: codex.ENGINE_RULES}.get(
            engine, cli_engines.ENGINE_RULES)
        rules = template.format(
            task_file=cli_engines.TASK_FILE,
            commands=", ".join(f"`{claude_code.command_text(c)}`" for c in commands) or "none",
            sections=", ".join(f"`## {s}`" for s in (s for o in contract.agent_outputs() for s in o.required_sections))
            or "a short summary")
        if not writes:
            rules += READ_ONLY_RULES
        if structured:
            rules += RESULT_RULES.format(schema=json.dumps(submit_tool_schema(contract), separators=(",", ":")))
        head_before = _head(ws)
        params = route.get("params") or {}
        timeout = contract.timeout_policy.seconds
        model = claude_code.login_model(route["model"]) if engine == claude_code.ENGINE and auth == claude_code.LOGIN \
            else route["model"]

        def invoke(task_text: str) -> claude_code.EngineResult:
            if engine == claude_code.ENGINE:
                prompt_file = ws.root / "claude-code-system.md"
                prompt_file.write_text(payload["agent"]["system_prompt"] + rules
                                       + claude_code.repository_instructions(ws.repo), encoding="utf-8")
                if ws.is_git:  # local engine state must never end up in the commit
                    with (ws.repo / ".git" / "info" / "exclude").open("a", encoding="utf-8") as fh:
                        fh.write("\n.claude/settings.local.json\n")
                argv = claude_code.build_argv(
                    base, model=model, effort=params.get("effort"), max_turns=contract.timeout_policy.max_turns,
                    allowed=claude_code.bash_rules(commands) + claude_code.READ_ONLY_RULES,
                    system_prompt_file=prompt_file, max_budget_usd=params.get("max_budget_usd"), auth=auth,
                    read_only=not writes)
                return claude_code.run_engine(argv, task_text, ws.repo, timeout, log, env=claude_code.engine_env(auth))
            if engine in cli_engines.ENGINES:
                # The prompt goes in a task file the CLI is pointed at (removed before the platform commits).
                text = f"{payload['agent']['system_prompt']}{rules}\n\n# Your task\n\n{task_text}\n"
                stdin = cli_engines.uses_stdin(engine)
                task = None if stdin else cli_engines.write_task(ws.repo, text, ws.is_git)
                argv = cli_engines.build_argv(engine, base, model=route["model"],
                                              max_turns=contract.timeout_policy.max_turns, timeout=timeout)
                try:
                    return claude_code.run_engine(argv, text if stdin else "", ws.repo, timeout + 60, log,
                                                  env=cli_engines.engine_env(engine, route["model"]),
                                                  parser=cli_engines.make_parser(engine, log), name=engine)
                finally:
                    if task:
                        task.unlink(missing_ok=True)
            # Codex reads the repository's AGENTS.md itself; the role prompt and rules lead the stdin prompt.
            prompt = f"{payload['agent']['system_prompt']}{rules}\n\n# Your task\n\n{task_text}"
            argv = codex.build_argv(base, model=route["model"], effort=params.get("effort"))
            return claude_code.run_engine(argv, prompt, ws.repo, timeout, log,
                                          env=codex.engine_env(ws.root / "codex-home", auth or codex.API_KEY),
                                          parser=codex.CodexParser(log), name=codex.ENGINE)

        task_text = payload["context"]["user_message"]
        submission: dict[str, Any] | None = None
        res = AgentResult(status="failed")
        for attempt in (1, 2):
            er = invoke(task_text)
            ctx.calls.extend(er.tool_calls)
            res.turns += er.turns
            res.input_tokens += er.input_tokens
            res.output_tokens += er.output_tokens
            if er.cost_usd is not None:
                usage["cost_usd"] = (usage["cost_usd"] or 0) + er.cost_usd
            usage["session_id"] = er.session_id
            res.status, res.error, res.retryable = er.status, er.error, er.retryable
            if er.status != "succeeded" or not structured:
                break
            submission, problems = parse_result_block(contract, er.text)
            if not problems:
                break
            res.status, res.retryable = "failed", True
            res.error = "engine result rejected: " + "; ".join(problems)
            log(f"[platform] {res.error}", "system")
            task_text = (f"{payload['context']['user_message']}\n\n# Your previous answer was rejected\n\n"
                         + "\n".join(f"- {p}" for p in problems)
                         + "\n\nDo the task again and end with one complete, corrected `awf-result` block.")
        if engine in SIGNED_IN_ENGINES or auth == "login":  # Claude Code or Codex on a plan
            usage.update({"billing": "subscription", "cost_usd": 0.0})  # paid by the plan, not per token
        if head_before and _head(ws) != head_before:
            res.status, res.retryable = "failed", False
            res.error = "ENGINE_TOUCHED_GIT: the engine changed git history; only the platform may commit"
            return res, usage
        if res.status != "succeeded":
            return res, usage

        # Evidence comes from the platform, never from the engine's own runs: build and test again here.
        log("[platform] verifying the engine's work", "system")
        for tool in ("build.run", "test.run", "lint.run"):
            if tool in contract.allowed_tools:
                self.registry.execute(tool, {}, ctx, allowed=contract.allowed_tools)
        if submission is not None:
            res.summary = str(submission.get("summary", ""))[:300]
            res.outputs = submission.get("outputs", {})
            res.decision = submission.get("decision")
            res.findings = list(submission.get("findings") or [])
        else:
            res.outputs = engine_outputs(contract, er.text)
            res.summary = next((ln.strip("# ").strip() for ln in er.text.splitlines() if ln.strip()), "")[:300]
        if er.denials:
            res.findings.append({"severity": "INFO", "code": "ENGINE_DENIED",
                                 "message": f"{len(er.denials)} engine tool call(s) were denied "
                                            "(commands outside the configured build/test/lint commands)"})
        return res, usage

    # ------------------------------------------------------------------ base branch (ADR-016)
    def _sync_and_verify(self, payload: dict[str, Any], ws: Workspace, log: LogFn) -> dict[str, Any]:
        """Rebase the run onto the latest base branch; when that changed anything, build and test it again."""
        if not ws.integration_branch:
            return {}
        synced = ws.sync_with_base()
        if not synced["updated"]:
            return synced
        log(f"[platform] {ws.integration_branch} rebased onto the latest {ws.base_branch} "
            f"({synced['base'][:10]}); building and testing again", "system")
        ctx = ToolContext(workspace=ws.repo, permissions=TOOL_JOB_PERMISSIONS, policy=self.policy,
                          repository=payload.get("repository") or {}, base_ref=ws.base_ref, log=log)
        for tool in ("build.run", "test.run"):
            self.registry.execute(tool, {}, ctx)
            ev = _public_evidence(ctx.evidence).get(tool.split(".")[0], {})
            if ev.get("status") == "fail":
                return {"status": "failed", "retryable": False, "evidence": _public_evidence(ctx.evidence),
                        "tool_calls": ctx.calls, "error": f"after rebasing onto the latest {ws.base_branch} "
                        f"({synced['base'][:10]}), {tool} fails: someone's change on {ws.base_branch} breaks this run"}
        return synced

    def _merge_to_base(self, payload: dict[str, Any], ws: Workspace, log: LogFn) -> dict[str, Any]:
        """After approve_merge: fast-forward the base branch of a hosted repository to the run's work. Other
        repositories (GitHub...) are left to their own review: the result says so."""
        if not (payload.get("repository") or {}).get("hosted"):
            return {"status": "succeeded", "summary": f"not hosted on the control plane: merge "
                    f"{ws.integration_branch} into {ws.base_branch} yourself (pull request)", "outputs": {},
                    "evidence": {}, "tool_calls": [{"tool": "git.merge_to_base", "status": "ok"}]}
        synced = self._sync_and_verify(payload, ws, log)
        if synced.get("status") == "failed":
            return synced
        merged = ws.merge_to_base()
        log(f"[platform] {ws.base_branch} fast-forwarded to {merged['merged'][:10]}", "system")
        return {"status": "succeeded", "summary": f"{ws.base_branch} is now {merged['merged'][:10]}"
                + (" (rebased onto the latest base first)" if merged["rebased"] else ""),
                "outputs": {}, "evidence": {"merge": merged}, "tool_calls": [{"tool": "git.merge_to_base", "status": "ok"}]}

    # ------------------------------------------------------------------ tool jobs
    def _run_tool_job(self, payload: dict[str, Any], ws: Workspace, log: LogFn) -> dict[str, Any]:
        tool = payload["tool"]
        name = tool["name"]
        if name == "git.merge_to_base":
            return self._merge_to_base(payload, ws, log)
        if name == "git.export_patch":
            synced = self._sync_and_verify(payload, ws, log)
            if synced.get("status") == "failed":
                return synced
            exported = ws.export_patch()
            pr = _pull_request_markdown(payload, ws, exported)
            return {
                "status": "succeeded",
                "summary": f"{exported['files_changed']} files changed, {exported['insertions']} insertions, "
                           f"{exported['deletions']} deletions on {exported['head']}",
                "outputs": {"patch": exported["diff"], "pull_request": pr},
                "evidence": {"export": {k: v for k, v in exported.items() if k != "diff"}},
                "tool_calls": [{"tool": name, "status": "ok"}],
            }
        ctx = ToolContext(workspace=ws.repo, permissions=TOOL_JOB_PERMISSIONS, policy=self.policy,
                          repository=payload.get("repository") or {}, base_ref=ws.base_ref, log=log)
        res = self.registry.execute(name, tool.get("input") or {}, ctx)
        kind = name.split(".")[0]
        evidence = _public_evidence(ctx.evidence)
        status = evidence.get(kind, {}).get("status")
        report = {"tool": name, "result": res.data, "message": res.message}
        return {
            "status": "succeeded" if res.status == "ok" else "failed",
            "summary": f"{name}: {status or res.status}",
            "outputs": {"report": json.dumps(report, indent=2, default=str)},
            "evidence": evidence,
            "tool_calls": ctx.calls,
            "error": None if res.status == "ok" else res.message,
            "retryable": False,
        }


# Engines that sign in with the user's own account: their work is paid by the plan, not billed per token here.
SIGNED_IN_ENGINES = {"antigravity", "kimi-code", "minimax-code"}

READ_ONLY_RULES = """- This role is read-only: do not create, edit or delete any file. Read the code, run the allowed commands,
  and put everything you produce in your final message.
"""

RESULT_RULES = """- End your final message with your result as JSON in one fenced code block tagged `awf-result`, for example
  ```awf-result
  {{"summary": "...", "outputs": {{"<name>": "<markdown text>"}}}}
  ```
  Each Markdown output is one JSON string containing its required `##` sections. The JSON must match this schema:
  {schema}
"""

_RESULT_BLOCK = re.compile(r"```awf-result[ \t]*\r?\n(.*?)\r?\n```", re.DOTALL)


def needs_result_block(contract: AgentContract) -> bool:
    """True when the engine's final Markdown message cannot carry the contract on its own."""
    outputs = contract.agent_outputs()
    return bool(contract.decision) or len(outputs) > 1 or any(o.type not in ("markdown", "text") for o in outputs)


def parse_result_block(contract: AgentContract, text: str) -> tuple[dict[str, Any] | None, list[str]]:
    """The last `awf-result` block of an engine's final message, validated against the contract."""
    blocks = _RESULT_BLOCK.findall(text or "")
    if not blocks:
        return None, ["the final message has no ```awf-result block"]
    try:
        submission = json.loads(blocks[-1])
    except json.JSONDecodeError as exc:
        return None, [f"the awf-result block is not valid JSON ({exc.msg} at line {exc.lineno})"]
    if not isinstance(submission, dict):
        return None, ["the awf-result block must be a JSON object"]
    problems = validate_submission(contract, submission)
    return submission, [f"[{p.code}] {p.message}" for p in problems]


def engine_outputs(contract: AgentContract, text: str) -> dict[str, Any]:
    """Map the engine's final message onto the contract's Markdown outputs. A message that lacks a required
    section is kept whole under the first section, with the others marked as not stated."""
    outputs: dict[str, Any] = {}
    for spec in contract.agent_outputs():
        if all(has_section(text, s) for s in spec.required_sections):
            outputs[spec.name] = text
            continue
        parts = []
        for i, section in enumerate(spec.required_sections or ["Summary"]):
            parts.append(f"## {section}\n\n{text.strip() if i == 0 else '_Not stated by the engine._'}")
        outputs[spec.name] = "\n\n".join(parts) + "\n"
    return outputs


def _head(ws: Workspace) -> str | None:
    from awf.tool_sdk.command import git

    if not ws.is_git:
        return None
    return git(["rev-parse", "HEAD"], ws.repo, check=False).stdout.strip() or None


def _allowed_actions(policy: PolicyEngine, permissions: dict[str, Any]) -> set[str]:
    return {a for a in policy.config.actions if policy.evaluate(a, permissions).allowed}


def _dirty_files(ws: Workspace) -> list[str]:
    from awf.tool_sdk.command import git

    if not ws.is_git:
        return []
    return [line for line in git(["status", "--porcelain"], ws.repo).stdout.splitlines() if line.strip()]


def _public_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(evidence, default=str))


def _pull_request_markdown(payload: dict[str, Any], ws: Workspace, exported: dict[str, Any]) -> str:
    ctx = payload.get("context") or {}
    lines = [
        f"# Pull request: {redact(ctx.get('objective', '')[:120]) or 'AI Work Factory change'}",
        "",
        f"- **Source branch:** `{ws.integration_branch}`",
        f"- **Target branch:** `{ws.base_branch}`",
        f"- **Diffstat:** {exported['files_changed']} files, +{exported['insertions']} / -{exported['deletions']}",
        "",
        "## Commits",
        "",
        *[f"- {c}" for c in exported["commits"]],
        "",
        "## Traceability",
        "",
        ctx.get("trace_summary", "_See artifact relations in the control plane._"),
        "",
        "## Merge policy",
        "",
        "Merging into a protected branch is a level-2 action: it requires human approval and is never "
        "performed autonomously by the factory.",
    ]
    return "\n".join(lines)
