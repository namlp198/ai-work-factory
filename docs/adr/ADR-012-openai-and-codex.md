# ADR-012 — ChatGPT (OpenAI) as a third provider, Codex as a second coding engine

- **Status:** Accepted

## Decision
- `openai` joins `anthropic` and `google` as a model provider chosen per run (`llm`) or per project
  (`labels.llm`), as in ADR-010. `providers.openai` in `config/model-routing.yaml` maps the classes to
  `gpt-6-luna` (FAST) and `gpt-6.1-sol` (all others). `LOCAL_PRIVATE` is never remapped. Workers announce
  `llm-openai` when `OPENAI_API_KEY` is set.
- The adapter calls the **Responses API** over REST (httpx, no SDK), with the same manual function-calling loop
  as Claude and Gemini, so every tool call goes through the policy engine. Requests use `store: false`, so
  OpenAI holds no conversation state: the adapter resends the whole item list each turn, reasoning items
  (encrypted) included. Malformed or truncated tool calls are answered with an error output instead of being
  executed. `insufficient_quota` is not retried.
- `codex` joins `claude-code` as a coding engine (ADR-011). It applies to roles whose profile lists it (the
  Developer), and it needs the `codex` capability (CLI installed) plus `llm-openai`. The route is resolved with
  provider `openai` whatever the run's provider. Restricted projects and the scripted provider keep the native
  runtime.
- The worker runs `codex exec --json --sandbox workspace-write` with `approval_policy=never`, no web search, no
  network in the sandbox, `--ephemeral --ignore-user-config --ignore-rules`, a fresh `CODEX_HOME` per job, the
  key as `CODEX_API_KEY` only, and `shell_environment_policy.inherit=core` so commands never see it. The prompt
  (role prompt, platform rules, task) goes on stdin: no long values on the command line, which also keeps the
  Windows `codex.cmd` shim out of quoting trouble.
- Evidence, commits and the Critic are exactly as for Claude Code: the worker builds and tests after the engine
  exits, an engine that moves HEAD fails the job, and the final message is mapped onto the contract's Markdown
  outputs.

## Consequences
- Codex contains commands with an OS sandbox (writes confined to the workspace, no network), not with a
  per-command allowlist as Claude Code does. Inside the sandbox it may run any command.
- On Windows the Codex sandbox needs a supported Windows build (Windows 11 recommended). Some managed machines
  block its setup.
- Cost for Codex runs is estimated from tokens with the `pricing` table, since the CLI reports tokens, not
  dollars.
- The `-c` overrides rely on Codex reading a value that is not valid TOML as a plain string (bare words such as
  `never`). If a later CLI version changes that, the worker must quote them.
