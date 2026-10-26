"""Stand-in for the agy / qwen / kimi CLIs in tests. FAKE_ENGINE_KIND picks the output format, FAKE_ENGINE_MODE
the behaviour; FAKE_ENGINE_RECORD receives the argv, the task file and the environment the engine saw."""

import json
import os
import sys
from pathlib import Path


def emit(event):
    print(json.dumps(event), flush=True)


def main() -> int:
    argv = sys.argv[1:]
    kind = os.environ["FAKE_ENGINE_KIND"]
    mode = os.environ.get("FAKE_ENGINE_MODE", "ok")
    task = Path(".awf-task.md")
    stdin = sys.stdin.read() if kind in ("dsh", "mcode") else ""
    Path(os.environ["FAKE_ENGINE_RECORD"]).write_text(json.dumps({
        "argv": argv, "task": task.read_text(encoding="utf-8") if task.exists() else stdin, "task_file": task.exists(),
        "env": {k: os.environ.get(k, "") for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "GEMINI_API_KEY",
                                                   "MOONSHOT_API_KEY", "AWF_API_TOKEN", "DEEPSEEK_API_KEY")}}),
        encoding="utf-8")
    if mode == "fail":
        print("authentication required" if kind == "agy" else "quota exhausted", file=sys.stderr)
        if kind == "agy":
            emit({"event": "result", "result": {"status": "ERROR", "error": "authentication required"}})
        if kind == "mcode":
            emit({"schemaVersion": 1, "type": "exec.result", "status": "failed",
                  "error": {"message": "no provider configured"}})
            return 3
        if kind == "dsh":
            emit({"type": "error", "message": "401 Unauthorized: invalid api key"})
        return 1
    Path("perception_service/engine_health.py").write_text("def engine_health():\n    return 'ok'\n", encoding="utf-8")
    summary = "## Changes\n\nAdded `engine_health`.\n\n## Tests\n\nRan the tests.\n\n## Risks\n\nNone."
    if kind == "agy":
        emit({"event": "init", "conversation_id": "conv-1", "init": {"cwd": os.getcwd(), "tools": ["write_to_file"]}})
        emit({"event": "step_update", "step_update": {"conversation_id": "conv-1", "step_index": 1, "state": "DONE",
                                                      "step_type": "tool_call",
                                                      "tool_info": {"name": "write_to_file",
                                                                    "path": "perception_service/engine_health.py"}}})
        emit({"event": "result", "result": {"conversation_id": "conv-1", "status": "SUCCESS", "response": summary,
                                            "num_turns": 3, "usage": {"input_tokens": 5000, "output_tokens": 700,
                                                                      "thinking_tokens": 300}}})
    elif kind == "qwen":
        emit({"type": "system", "subtype": "init", "session_id": "q-1", "model": "qwen3.8-max"})
        emit({"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "write_file",
             "input": {"file_path": "perception_service/engine_health.py"}}]}})
        emit({"type": "result", "is_error": False, "result": summary, "num_turns": 2, "session_id": "q-1",
              "usage": {"input_tokens": 4000, "output_tokens": 500}})
    elif kind == "dsh":
        emit({"type": "session", "sessionId": "session-1"})
        emit({"type": "tool_call", "name": "write_file", "input": {"path": "perception_service/engine_health.py"}})
        emit({"type": "tool_result", "name": "write_file"})
        emit({"type": "tool_call", "name": "shell", "input": {"command": "curl example.com"}})
        emit({"type": "tool_result", "name": "shell", "error": {"name": "shell", "code": "AUTO_REVIEW_DENIED"}})
        emit({"type": "text", "text": summary})
        emit({"type": "final", "sessionId": "session-1", "reason": {"kind": "completed"}, "answer": summary})
    elif kind == "mcode":
        emit({"schemaVersion": 1, "type": "exec.result", "runId": "r1", "sessionId": "s1", "status": "succeeded",
              "output": summary, "model": {"providerId": "minimax", "modelId": "MiniMax-M3"},
              "usage": {"totalTokens": 4200, "inputTokens": 3600, "outputTokens": 600}})
    else:  # kimi
        emit({"role": "assistant", "content": "Writing the helper.", "tool_calls": [
            {"type": "function", "id": "tc_1", "function": {"name": "WriteFile", "arguments": "{\"path\": \"x\"}"}}]})
        emit({"role": "tool", "tool_call_id": "tc_1", "content": "ok"})
        emit({"role": "assistant", "content": summary})
    return 0


if __name__ == "__main__":
    sys.exit(main())
