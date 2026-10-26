"""Executes one job payload inside an ephemeral workspace and returns a structured result."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from awf.agent_runtime import AgentContract, AgentRuntime
from awf.agent_runtime.adapters import ModelAdapter, ModelError, get_adapter
from awf.agent_runtime.runtime import AgentResult
from awf.agent_runtime.validation import has_section
from awf.policy import PolicyEngine
from awf.security import redact, redact_obj
from awf.tool_sdk import ToolContext, ToolRegistry
from awf.tool_sdk.build import configured_command
from awf.tool_sdk.command import GitError
from awf.worker_sdk import claude_code
from awf.worker_sdk.sandbox import IntegrationConflict, Workspace

# Permissions a standalone tool job needs; tool jobs are platform actions, not agent actions.
TOOL_JOB_PERMISSIONS = {"filesystem": {"read": True}, "git": {"read": True}, "build": True, "test": True}

LogFn = Callable[[str, str], None]


class JobExecutor:
    def __init__(self, workspace_root: Path, policy: PolicyEngine, registry: ToolRegistry | None = None,
                 adapter_factory: Callable[[str], ModelAdapter] = get_adapter, keep_workspaces: bool = False,
                 claude_code_bin: list[str] | None = None):
        self.workspace_root = workspace_root
        self.claude_code_bin = claude_code_bin
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
        ws = Workspace(self.workspace_root, job_id)
        try:
            notify("PREPARING")
            repo = payload.get("repository") or {}
            wsp = payload.get("workspace") or {}
            if repo.get("url"):
                ws.prepare(repo["url"], repo.get("base_branch") or "main", wsp.get("integration_branch"),
                           wsp.get("task_branch") if wsp.get("write") else None)
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
            result = {"status": "failed", "error": str(exc), "retryable": False, "error_code": "INTEGRATION_CONFLICT"}
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
                return {"status": "failed", "error": str(exc), "retryable": exc.retryable}
        meta = {
            "role": agent["role"],
            "task": task,
            "objective": payload.get("context", {}).get("objective", ""),
            "contract": agent["contract"],
        }
        log(f"agent {agent['role']} starting on {route['provider']}/{route['model']}"
            + (f" with engine {engine}" if engine != claude_code.NATIVE else ""), "system")
        if engine == claude_code.ENGINE:
            res, engine_usage = self._run_claude_code(payload, contract, route, ws, ctx, log)
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
        if res.status != "succeeded":
            return result

        commit = None
        if writes and "git.branch_write" in _allowed_actions(self.policy, contract.permissions):
            message = f"awf({task.get('stage_id')}): {task.get('title')}\n\nTask: {task.get('id')}\nRole: {agent['role']}"
            info = ws.commit_and_integrate(message[:2000])
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

    # ------------------------------------------------------------------ claude-code engine (ADR-011)
    def _run_claude_code(self, payload: dict[str, Any], contract: AgentContract, route: dict[str, Any],
                         ws: Workspace, ctx: ToolContext, log: LogFn) -> tuple[AgentResult, dict[str, Any]]:
        usage: dict[str, Any] = {"engine": claude_code.ENGINE}
        base = self.claude_code_bin or claude_code.claude_code_bin()
        if not base:
            return AgentResult(status="failed", error="claude-code is not installed on this worker "
                               "(install Claude Code or set AWF_CLAUDE_CODE_BIN)", retryable=False), usage
        if contract.decision or any(o.type != "markdown" for o in contract.agent_outputs()):
            return AgentResult(status="failed", error=f"engine claude-code cannot produce the outputs of "
                               f"{contract.id}; use the native engine", retryable=False), usage

        commands = []
        for key in ("build_command", "test_command", "lint_command"):
            argv = configured_command(ctx, key)
            if argv and self.policy.command_allowed(argv) and "," not in " ".join(argv):
                commands.append(argv)
        sections = [s for o in contract.agent_outputs() for s in o.required_sections]
        system = (payload["agent"]["system_prompt"]
                  + claude_code.ENGINE_RULES.format(
                      commands=", ".join(f"`{claude_code.command_text(c)}`" for c in commands) or "none",
                      sections=", ".join(f"`## {s}`" for s in sections) or "a short summary")
                  + claude_code.repository_instructions(ws.repo))
        prompt_file = ws.root / "claude-code-system.md"
        prompt_file.write_text(system, encoding="utf-8")
        if ws.is_git:  # local engine state must never end up in the commit
            with (ws.repo / ".git" / "info" / "exclude").open("a", encoding="utf-8") as fh:
                fh.write("\n.claude/settings.local.json\n")
        head_before = _head(ws)
        params = route.get("params") or {}
        argv = claude_code.build_argv(
            base, model=route["model"], effort=params.get("effort"), max_turns=contract.timeout_policy.max_turns,
            allowed=claude_code.bash_rules(commands) + claude_code.READ_ONLY_RULES, system_prompt_file=prompt_file,
            max_budget_usd=params.get("max_budget_usd"))
        er = claude_code.run_engine(argv, payload["context"]["user_message"], ws.repo,
                                    contract.timeout_policy.seconds, log)
        usage.update({"cost_usd": er.cost_usd, "session_id": er.session_id})
        ctx.calls.extend(er.tool_calls)
        res = AgentResult(status=er.status, turns=er.turns, input_tokens=er.input_tokens,
                          output_tokens=er.output_tokens, error=er.error, retryable=er.retryable)
        if head_before and _head(ws) != head_before:
            res.status, res.retryable = "failed", False
            res.error = "ENGINE_TOUCHED_GIT: the engine changed git history; only the platform may commit"
            return res, usage
        if er.status != "succeeded":
            return res, usage

        # Evidence comes from the platform, never from the engine's own runs: build and test again here.
        log("[platform] verifying the engine's changes", "system")
        for tool in ("build.run", "test.run", "lint.run"):
            if tool in contract.allowed_tools:
                self.registry.execute(tool, {}, ctx, allowed=contract.allowed_tools)
        res.outputs = engine_outputs(contract, er.text)
        res.summary = next((ln.strip("# ").strip() for ln in er.text.splitlines() if ln.strip()), "")[:300]
        if er.denials:
            res.findings.append({"severity": "INFO", "code": "ENGINE_DENIED",
                                 "message": f"{len(er.denials)} engine tool call(s) were denied "
                                            "(commands outside the configured build/test/lint commands)"})
        return res, usage

    # ------------------------------------------------------------------ tool jobs
    def _run_tool_job(self, payload: dict[str, Any], ws: Workspace, log: LogFn) -> dict[str, Any]:
        tool = payload["tool"]
        name = tool["name"]
        if name == "git.export_patch":
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
