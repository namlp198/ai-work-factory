# ADR-010 — Claude or Gemini per run

- **Status:** Accepted

## Decision
- A run uses **one model provider** for all of its agents: `anthropic` (Claude, the `default_provider`) or
  `google` (Gemini). Chosen at run creation (`llm`), else the project's `labels.llm`, else the default.
- `config/model-routing.yaml` keeps one class table per provider (`classes` for the default, `providers.<name>`
  for the others). Profiles keep asking for a class (BALANCED, REASONING, ...), never a vendor model.
  `LOCAL_PRIVATE` is never remapped: restricted projects stay off hosted models whatever the provider.
- Workers announce `llm-<provider>` for the API keys they hold (always derived from the environment, even with
  `AWF_WORKER_CAPABILITIES`). A run's requirements include the `llm-*` of every provider its agents route to, so
  run placement only picks machines that can call that model, and a run no machine can serve is refused.
- The Gemini adapter calls `generateContent` over REST (httpx, no extra SDK) with the same manual tool loop as
  Claude: every tool call goes through the policy engine, the model's turns are echoed back unchanged (thought
  signatures), tool names are mapped to Gemini-safe names.

## Consequences
- Workers registered before this change must restart to announce `llm-*`.
- Mixing providers inside one run (e.g. Claude architect, Gemini developer) is not supported; it would need
  per-stage provider choice and machines holding both keys.
