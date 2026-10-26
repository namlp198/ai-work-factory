"""Stand-in for the `codex` CLI in tests: speaks `codex exec --json` (JSON lines) and edits the workspace.

FAKE_CODEX_MODE picks the behaviour; FAKE_CODEX_RECORD names a file that receives the argv, the stdin prompt and
what the environment looked like, so tests can assert what the worker asked for.
"""

import json
import os
import sys
from pathlib import Path


def emit(event):
    print(json.dumps(event), flush=True)


def main() -> int:
    argv = sys.argv[1:]
    prompt = sys.stdin.read()
    mode = os.environ.get("FAKE_CODEX_MODE", "ok")
    record = os.environ.get("FAKE_CODEX_RECORD")
    if record:
        Path(record).write_text(json.dumps({
            "argv": argv, "prompt": prompt, "cwd": os.getcwd(),
            "codex_api_key": os.environ.get("CODEX_API_KEY", ""), "codex_home": os.environ.get("CODEX_HOME", ""),
            "env_has_openai_key": bool(os.environ.get("OPENAI_API_KEY")),
            "env_has_awf_token": bool(os.environ.get("AWF_API_TOKEN"))}), encoding="utf-8")
    emit({"type": "thread.started", "thread_id": "th-1"})
    emit({"type": "turn.started"})
    if mode == "auth":
        emit({"type": "error", "message": "401 Unauthorized: Incorrect API key provided"})
        emit({"type": "turn.failed", "error": {"message": "401 Unauthorized: Incorrect API key provided"}})
        return 1
    target = Path("perception_service") / "engine_health.py"
    target.write_text("def engine_health():\n    return 'ok'\n" if mode != "broken" else "def engine_health(:\n",
                      encoding="utf-8")
    emit({"type": "item.completed", "item": {"id": "item_1", "type": "file_change", "status": "completed",
                                              "changes": [{"path": str(target), "kind": "add"}]}})
    emit({"type": "item.completed", "item": {"id": "item_2", "type": "command_execution", "command": "pytest -q",
                                              "aggregated_output": "1 passed", "exit_code": 0, "status": "completed"}})
    text = "## Changes\n\nAdded `engine_health`.\n\n## Tests\n\nRan pytest.\n\n## Risks\n\nNone."
    emit({"type": "item.completed", "item": {"id": "item_3", "type": "agent_message", "text": text}})
    emit({"type": "turn.completed", "usage": {"input_tokens": 3000, "cached_input_tokens": 1000, "output_tokens": 600,
                                              "reasoning_output_tokens": 200}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
