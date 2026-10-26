# AI Work Factory: rules for working on this repository

## Dashboard: always bilingual (English + Vietnamese)

The dashboard (`apps/control_api/awf_api/static/`) ships in **English and Vietnamese**. The user picks the
language in the header, and the browser remembers the choice. Every change that touches the UI must keep both
languages complete:

- Never write user-visible text directly in `app.js` or `index.html`. Add a key to **both** `en` and `vi` in
  `static/i18n.js`, then use `t("key")`. For a count, use `tn("key", n)` with `key.one` and `key.other`. In
  `index.html`, use `data-i18n="key"` (text) or `data-i18n-title="key"` (tooltip).
- Values passed as `{name}` are escaped by `t()`. Markup inside a dictionary string is allowed (`<code>`, `<b>`).
  Pass `raw: true` only for values that are already safe markup, or for plain-text sinks such as `confirm()`.
- Dynamic keys (`status.*`, `platform.*`, `engine.*`, `confidentiality.*`) need an entry in both languages for
  every value the UI can show.
- Not translated: data (project names, objectives, stage titles from workflow YAML, artifact content),
  identifiers (`approve_architecture`, event types, capability names, code) and error messages from the API.
  `runErrorHint()` adds a translated explanation under an API error when it knows the fix.
- `tests/integration/test_i18n.py` fails when a key is missing from one language, when a `t("…")` key has no
  translation, or when the `{placeholders}` differ between languages. Run it after every UI change.
- Vietnamese: keep the technical terms the guide uses (project, run, worker, objective, artifact, stage, task),
  and translate the rest in plain words.

The user guide's section on the dashboard (§7, "Ngôn ngữ giao diện") describes the same rule.

## Model providers and coding engines

- Providers are defined in one place: `packages/awf/agent_runtime/providers.py`. Do not hard-code provider lists or
  key variable names elsewhere. Workers, router and doctor read this table. The dashboard keeps `LLM_NAMES` and
  `KEY_HELP` in `static/app.js` in step with it.
- A new OpenAI-compatible vendor needs a `Provider(..., api="chat_completions")` entry and a `providers.<id>` table in
  `config/model-routing.yaml`, not a new adapter.
- Coding engines are listed in `CODING_ENGINES` (`orchestrator/task_router.py`) and the Developer profile's
  `engines:`. Every engine must leave evidence to the platform: the worker builds and tests after the engine, and
  the worker commits.
- Keep the guide in step with every provider or engine change: §6 (summary table, how to get keys, engine setup),
  §19 (troubleshooting), §21/§22 where relevant, plus an ADR. Before writing that a vendor has no coding engine,
  search for its coding agent **by product name**, not only for a generic "CLI": that check once missed DeepSeek
  Harness and MiniMax Code. The full checklist is in the guide under "Thêm nhà cung cấp hoặc engine mới".
