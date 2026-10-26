# ADR-013 — Qwen, Kimi, DeepSeek, MiniMax; Antigravity, Qwen Code, Kimi Code, DeepSeek Harness and MiniMax Code engines

- **Status:** Accepted

## Context
Users want every major model family, and a coding engine wherever the vendor ships one. As of October 2026:
Gemini CLI was replaced by **Antigravity CLI** (`agy`) on June 18, 2026; Gemini CLI is not optimized for Gemini 4
Argon, and Google points CLI users to Antigravity for it (the API serves Argon as before). Antigravity signs in with a Google account (Google AI Pro/Ultra quota) or uses an API key.
Alibaba ships **Qwen Code**, whose Qwen OAuth free tier ended on April 15, 2026, so it needs an API key. Moonshot
ships **Kimi Code CLI**. DeepSeek ships **DeepSeek Harness** (`dsh`, open source, 0.1 in August 2026, 0.2 on
October 1, 2026), and MiniMax ships **MiniMax Code**, whose CLI is `mcode` (headless `mcode exec`). All four of Qwen,
Kimi, DeepSeek and
MiniMax offer OpenAI-compatible Chat Completions APIs.

## Decision
- One provider table, `packages/awf/agent_runtime/providers.py`, defines each provider: id, label, API family, key
  variables, base URL (`AWF_<ID>_BASE_URL` overrides it, for example for China regions) and where to get a key.
  Workers derive `llm-<id>` from it, the router derives which providers need a key, and the adapter factory picks
  the adapter from it.
- Qwen, Kimi, DeepSeek and MiniMax share `ChatCompletionsAdapter`, with the same manual tool loop through the
  policy engine. It keeps the assistant message exactly as returned, `reasoning_content` included, because DeepSeek
  and Kimi return 400 when earlier reasoning is dropped in a tool-call conversation. Vendor switches (thinking,
  `reasoning_effort`) live in `extra` in `config/model-routing.yaml` and are merged into the body as is.
- Three more coding engines for the Developer, each bound to its provider: `antigravity` (Gemini), `qwen-code`
  (Qwen) and `kimi-code` (Kimi). Antigravity and Kimi Code authenticate on their own (`key: False`): placement
  requires the CLI capability but no `llm-<provider>`, and the CLI picks its model unless `AWF_ANTIGRAVITY_MODEL` /
  `AWF_KIMI_CODE_MODEL` pins one. Qwen Code gets the DashScope key and endpoint as `OPENAI_API_KEY`,
  `OPENAI_BASE_URL` and `OPENAI_MODEL`.
- These CLIs take a short prompt telling them to read a task file (`.awf-task.md`) in the workspace. The file is
  excluded from git and removed before the commit, so no long text goes on a command line.
- Evidence, commits and the Critic stay as in ADR-011: the worker builds and tests afterwards, an engine that moves
  HEAD fails the job, and the final message is mapped onto the Markdown outputs.
- `deepseek-harness` runs `dsh --profile headless --json --permission-mode auto --approval-policy never -` with the
  prompt on stdin and `DEEPSEEK_API_KEY` in the environment. It succeeds on exit 0 with `final.reason.kind ==
  "completed"`. Auto-review denials are recorded as `engine_denied`.
- `minimax-code` runs `mcode exec --output-format json --permission full --input -` with the prompt on stdin. It
  authenticates with the account's own `mcode login` or saved key (`key: False`) and succeeds on exit 0 with
  `status == "succeeded"`. Exit codes 2 and 3 are not retried.
- An earlier draft of this ADR said DeepSeek and MiniMax had no coding CLI. That was wrong: the search looked for a
  generic "CLI" and missed both products by name.

## Consequences
- Antigravity (`--dangerously-skip-permissions`), Qwen Code (`--approval-mode yolo`), Kimi Code (print mode) and
  MiniMax Code (`--permission full`) approve every tool call in headless mode. DeepSeek Harness leaves each call to
  its automatic reviewer and has no file sandbox. Containment is the throwaway workspace, a credential-free environment except
  the engine's own key, time limits and the platform's verification. Qwen Code can add its Docker/Podman sandbox
  (`AWF_QWEN_CODE_SANDBOX=1`). Use `claude-code` or `codex` when commands must be restricted.
- A Google AI Pro plan powers only the Antigravity engine, not the Gemini API tier, which needs `GEMINI_API_KEY`.
- Cost estimates: Kimi K3 prices are not in `pricing` yet (they show $0). Kimi Code reports no tokens.
  Antigravity on a subscription is estimated at API prices.
- The stream formats of `agy`, `qwen` and `kimi` were implemented from their published docs and tested against
  stand-ins, not against the real CLIs. The parsers are tolerant: success follows the exit code and the final
  result line.
- DeepSeek Harness reports no tokens (cost shows $0). Its model is the Harness' own `agentDefaultModel` setting.
