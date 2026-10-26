"""Stand-in for the `claude` CLI in tests: speaks `--output-format stream-json` and edits the workspace.

FAKE_CLAUDE_MODE picks the behaviour; FAKE_CLAUDE_RECORD names a file that receives the argv, the stdin prompt
and the appended system prompt, so tests can assert what the worker asked for.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


def emit(event):
    print(json.dumps(event), flush=True)


def main() -> int:
    argv = sys.argv[1:]
    prompt = sys.stdin.read()
    system = Path(argv[argv.index("--append-system-prompt-file") + 1]).read_text(encoding="utf-8")
    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    record = os.environ.get("FAKE_CLAUDE_RECORD")
    if record:
        Path(record).write_text(json.dumps({"argv": argv, "prompt": prompt, "system": system,
                                            "env_has_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
                                            "env_has_awf_token": bool(os.environ.get("AWF_API_TOKEN"))}),
                                encoding="utf-8")
    emit({"type": "system", "subtype": "init", "session_id": "s-1", "model": argv[argv.index("--model") + 1]})
    if mode == "crash":
        print("boom: something went wrong", file=sys.stderr)
        return 3
    target = Path("perception_service") / "engine_health.py"
    body = "def engine_health():\n    return 'ok'\n" if mode != "broken" else "def engine_health(:\n"
    emit({"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
        {"type": "text", "text": "I will add the health helper."},
        {"type": "tool_use", "id": "t1", "name": "Write", "input": {"file_path": str(target)}}]}})
    target.write_text(body, encoding="utf-8")
    if mode == "commit":
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-qm", "sneaky"], check=True)
    usage = {"input_tokens": 1200, "cache_creation_input_tokens": 300, "cache_read_input_tokens": 500,
             "output_tokens": 400}
    if mode == "max_turns":
        emit({"type": "result", "subtype": "error_max_turns", "is_error": True, "num_turns": 60,
              "total_cost_usd": 0.5, "usage": usage, "permission_denials": [], "errors": ["max turns reached"],
              "session_id": "s-1"})
        return 1
    summary = ("## Changes\n\nAdded `engine_health`.\n\n## Tests\n\nRan the test suite.\n\n## Risks\n\nNone."
               if mode != "plain" else "Added the helper; all good.")
    emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 4, "result": summary,
          "total_cost_usd": 0.1234, "usage": usage, "session_id": "s-1",
          "permission_denials": [{"tool_name": "Bash", "tool_use_id": "t9", "tool_input": {"command": "curl x"}}]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
