# ADR-014 — Every role on a signed-in engine; bounded model requests and billing errors

- **Status:** Accepted

## Context
The first real runs (28 October 2026) failed for reasons outside the pipeline, and the platform handled them
badly:

- A Claude run failed with HTTP 400 "Your credit balance is too low". The owner pays for Claude Pro and Google AI
  Pro, but a subscription does not pay for API keys: the two are billed separately, even with the same email.
- A Gemini run stayed at 0% for over 30 minutes. `gemini-3.8-flash` was overloaded (503 "high demand", or no
  answer at all). Each request could wait 600 s and was tried 4 times, and the agent's 900 s limit was checked
  only between turns, so one stuck request held the job for about 16 minutes per attempt. The dashboard showed
  nothing, because job logs reached the control plane only in batches of 25 lines.
- The same Gemini key got 429 "limit: 0" for `gemini-4-argon` (free tier), which the adapter treated as busy and
  retried.

Claude Code and Antigravity can sign in with the subscription account, but engines served only the Developer, and
Claude Code ran in `--bare` mode, which reads an API key only.

## Decision
- **Engine scope.** A run (or project label `engine_scope`) chooses `coding` (default: only roles whose profile
  lists the engine) or `all` (every agent role). `all` needs an engine. Placement asks, per role, for the engine's
  capabilities when the role runs on it and `llm-<provider>` otherwise, so a run on a signed-in engine needs no
  API key at all.
- **Claude Code login mode.** `AWF_CLAUDE_CODE_AUTH=login|api-key` on the worker (default: `api-key` when
  `ANTHROPIC_API_KEY` is set). Login mode drops `--bare`, passes no Anthropic credential, and loads neither user
  settings nor MCP servers (`--setting-sources local`, `--strict-mcp-config`), so the user's own permission rules
  cannot widen what the engine may run. `AWF_CLAUDE_CODE_MODEL` pins a model the plan offers. Claude Code no longer
  requires `llm-anthropic` (`key: False`).
- **Read-only roles on an engine.** Without the workspace write permission, Claude Code loses Edit and Write; other
  engines get a read-only rule, and changed files are discarded as before. A contract the final Markdown message
  cannot carry (a decision, several outputs, JSON outputs) is answered with a fenced `awf-result` JSON block that
  matches the `submit_result` schema and is validated by the same code. One corrected attempt is allowed, with the
  problems listed; then the job fails, retryable. Build and test evidence is still produced by the platform.
- **Subscription billing.** Work done on a signed-in engine records `billing: subscription` and cost 0, so it
  does not consume `budget_usd`.
- **Bounded requests.** One shared retry policy for the REST adapters (`ModelSession.post_json`): at most 4
  attempts, each request limited to 240 s or the time left before the agent's deadline, whichever is smaller; no
  backoff wait that would end past the deadline; `Retry-After` and Gemini `RetryInfo` are honoured (cap 60 s). The
  Claude SDK gets the same per-request timeout.
- **Billing errors fail at once.** Claude "credit balance", OpenAI `insufficient_quota`, HTTP 402 and Chat
  Completions balance codes, and Gemini 429 with a daily quota or `limit: 0` raise `MODEL_BILLING`: not retryable,
  with the provider's console and a note that a subscription does not pay for API keys. A per-minute 429 is still
  retried.
- **Visible waiting.** Retries are written to the job log (`[model] ...`, stream `system`), and the worker ships
  `system`/`stderr` lines at once and any line within 10 s. `GET /workflow-runs/{id}` returns `activity`: for each
  unfinished task its latest job, the last platform note and the previous attempt's error. The run page shows it
  as "What it is doing now", with translated hints for billing and overload errors.

## Consequences
- With only a Claude Pro or Google AI Pro plan, a whole run works: slower (one CLI session per role) and bounded
  by the plan's usage windows, which surface as job errors.
- Read-only containment on loose engines remains a prompt rule plus discarding changes, not a hard sandbox.
- The `awf-result` block is a second submission channel; it is validated by the same schema, so contracts stay the
  single definition of what a role must return.
- Codex could get the same login mode (ChatGPT Plus sign-in); not done here.
