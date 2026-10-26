# ADR-011 — Claude Code as a coding engine, evidence stays with the platform

- **Status:** Accepted

## Context
The native runtime gives the Developer typed tools (`fs.read`, `fs.replace`, `build.run`...) and a manual
model loop. It keeps every action under the policy engine, but it edits code less well than a dedicated coding
agent: `NO_CHANGES` from `fs.replace` mismatches is a common failure. Claude Code is a mature coding agent with a
headless mode (`claude -p`). Using it must not weaken what the factory guarantees: platform-run evidence, policy,
platform-owned commits and the Critic.

## Decision
- A **coding engine** is chosen per run (`coding_engine`), else the project's `labels.coding_engine`, else
  `native`. It applies only to roles whose profile lists it (`engines: [claude-code]`, today only
  `engineering.developer`). Every other role keeps the native runtime.
- The engine talks to Claude. The role's route is resolved with provider `anthropic` even on a Gemini run. A
  restricted project (`LOCAL_PRIVATE`) or the offline `scripted` provider keeps the native runtime: the engine
  never sends restricted code out.
- The worker runs `claude --bare -p --output-format stream-json` in the job's git checkout:
  - the prompt comes from the context builder on stdin, and the role prompt plus platform rules are appended as
    the system prompt; the repository's `CLAUDE.md` / `AGENTS.md` are included as conventions;
  - the model and effort come from the router (`--model`, `--effort`), and the turn limit and timeout come from
    the contract;
  - only the Read/Edit/Write/Glob/Grep/Bash tools are available, with `--permission-mode dontAsk`. Bash may run
    only the project's configured build/test/lint commands and read-only git. Everything else is denied and
    listed as `engine_denied` tool calls with an `ENGINE_DENIED` finding;
  - `--bare` loads no hooks, plugins, MCP servers or settings from the worker's home or the repository. Nothing
    is persisted (`--no-session-persistence`). The process gets the worker environment without credentials,
    plus only `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL`.
- After the engine exits, **the worker runs build/test/lint itself**. That evidence goes to the Critic exactly
  as on the native path: a successful engine whose code does not build is `COMPLETION_RULE_FAILED`.
- The platform commits and integrates. An engine that moved HEAD fails the job (`ENGINE_TOUCHED_GIT`, not
  retried).
- The engine's final message becomes the contract's Markdown outputs. If it lacks a required section, it is
  kept whole under the first section and the others are marked "not stated by the engine". Roles with JSON
  outputs or a decision cannot use the engine.
- A worker announces `claude-code` when the `claude` CLI is on PATH (or `AWF_CLAUDE_CODE_BIN` is set). A run
  with the engine needs `claude-code` + `llm-anthropic`, so placement only picks machines that can run it.
- Cost: the engine's own `total_cost_usd` estimate is recorded on the agent run instead of the token-price
  estimate.

## Consequences
- Coding quality follows Claude Code. Policy granularity is coarser than the typed tools: inside the workspace
  the engine reads and writes freely. Its shell, network and git access are what the permission rules allow.
- `--bare` uses the API key only (never a Claude subscription login), billed to the API account.
- Subsets of the configured commands (for example one test file) are denied when they are not a prefix match of
  a configured command; the platform's own run is the one that counts.
- Not covered: Codex or other engines (the `CODING_ENGINES` table is where they would go), and engines for
  review/QA roles (they produce decisions).
