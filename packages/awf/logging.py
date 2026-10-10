"""Structured JSON logging (Technical Design §21)."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from awf.security import redact, redact_obj

CONTEXT_FIELDS = ("project_id", "workflow_run_id", "task_id", "agent_run_id", "worker_id", "job_id", "event")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        for key in CONTEXT_FIELDS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        payload["message"] = redact(record.getMessage())
        extra = getattr(record, "data", None)
        if extra:
            payload["data"] = redact_obj(extra)
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


_configured = False


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("awf")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name if name.startswith("awf") else f"awf.{name}")


def log_event(logger: logging.Logger, event: str, message: str = "", level: int = logging.INFO, **context: Any) -> None:
    extra = {k: v for k, v in context.items() if k in CONTEXT_FIELDS}
    data = {k: v for k, v in context.items() if k not in CONTEXT_FIELDS}
    extra["event"] = event
    if data:
        extra["data"] = data
    logger.log(level, message or event, extra=extra)
