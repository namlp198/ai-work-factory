# ADR-015 — Model per role, complexity levels, approval before high effort; Codex on a ChatGPT plan

- **Status:** Accepted

## Context
The first complete run (29 October 2026) put Opus at effort high on the architect, planner, code reviewer and
developer. The owner wants the strong model only where thinking pays (architecture and planning), cheaper models
for code, verification and review, high or extra effort only for genuinely hard problems, a clear view of which
model and effort each task uses, and **no task at effort high or above without the owner's approval**. The owner
also uses ChatGPT, whose Codex CLI can sign in with a ChatGPT plan, while the Codex engine accepted only an API key.

## Decision
- **Classes.** REASONING (architect, planner) = Opus 5.5 at medium; CODE_SPECIALIST (developer) and BALANCED (code
  reviewer, analyst, QA, test engineer) = Sonnet 5.5 at medium. Gemini and ChatGPT tables follow the same rule; no
  class defaults to effort high. DeepSeek keeps thinking on at its default effort.
- **Complexity levels.** A run carries `complexity` (normal, hard, very_hard; project label `complexity`). A task
  sent back for its work (the Critic or a human asking for changes, a fix loop) climbs one level; infrastructure
  failures do not. Hard raises effort one rung on `effort_ladder` (low, medium, high, xhigh); very hard promotes
  BALANCED/CODE_SPECIALIST to REASONING with one rung more, or two rungs for a class not promoted. Effort never
  exceeds the provider's top (`max_effort`, Gemini: high). The route reason records every step.
- **Approval before high effort.** Before dispatching a task whose route has an effort in
  `escalation.approval_required` (high, xhigh; DeepSeek `extra.reasoning_effort` included), the orchestrator moves
  the task to APPROVAL_REQUIRED and asks `approve_effort` with the task, role, model, effort and reason. Approved:
  it runs as routed. Rejected: it runs at the highest effort that needs no approval, and the run goes on. A
  decision is remembered per task and model/effort, so a later climb asks again. Engines that choose their own
  reasoning (Antigravity, Qwen Code, Kimi Code, DeepSeek Harness, MiniMax Code) and the offline provider are not
  gated.
- **Visibility.** Agent runs record the effort and engine; the run's task table shows "Model · effort" with the
  route reason; approvals show what an effort request is about.
- **Codex login mode.** `AWF_CODEX_AUTH=login|api-key` (default api-key when a key is set). Login mode uses the
  ChatGPT sign-in in the worker user's CODEX_HOME with `--ignore-user-config --ignore-rules --ephemeral`, passes no
  key, and records the work as `billing: subscription`. Codex, like Claude Code, no longer requires `llm-openai`.
  Workers advertise `claude-code-login` / `codex-login` when they run on a plan; the dashboard suggests "every role
  on Claude Code" for new projects when a worker has `claude-code-login`.
- **No retry against an account that cannot pay.** MODEL_BILLING, MODEL_REJECTED (bad key, no access to the model)
  and ENGINE_MISSING fail the task at once instead of using its remaining attempts.

## Consequences
- Normal runs cost less and never wait for approval; hard work waits for one click per task and effort.
- A reviewer on Sonnet may miss what Opus would catch; the fix loop and the escalation on rejection compensate,
  and a run can start at hard or very hard.
- The Codex login reads the user's sign-in file; Codex may refresh it, which is the same as using Codex by hand.
