"""System configuration: environment variables + config/*.yaml with ${VAR:-default} expansion."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _default_home() -> Path:
    env = os.environ.get("AWF_HOME")
    if env:
        return Path(env).resolve()
    # packages/awf/settings.py -> repository root
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "config").is_dir():
        return candidate
    return Path.cwd()


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return expand_env(yaml.safe_load(fh) or {})


@dataclass
class Settings:
    home: Path
    environment: str
    database_url: str
    object_store: str
    artifact_root: Path
    api_token: str
    worker_registration_token: str
    model_provider_override: str
    embedded_orchestrator: bool
    tick_interval_seconds: float
    worker_heartbeat_timeout_seconds: int
    run_reassign_after_seconds: int
    job_max_attempts: int
    context_max_chars_per_artifact: int
    default_worker_platform: str
    git_root: Path = Path("var/git")  # hosted project repositories (ADR-016)
    mirror_token: str = ""  # GitHub token the control plane pushes mirrors with (ADR-017)
    stale_branch_days: int = 14  # awf/run-* branches of failed or cancelled runs are kept this long
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def config_dir(self) -> Path:
        return self.home / "config"

    @property
    def workflows_dir(self) -> Path:
        return self.home / "workflows"

    @property
    def agents_dir(self) -> Path:
        return self.home / "agents"

    @property
    def knowledge_dir(self) -> Path:
        return self.home / "knowledge"

    def config_file(self, name: str) -> dict[str, Any]:
        return load_yaml(self.config_dir / name)


def _resolve_path(home: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (home / path).resolve()


def _resolve_db_url(home: Path, url: str) -> str:
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.startswith(prefix + "/") and ":memory:" not in url:
        rel = url[len(prefix):]
        if not Path(rel).is_absolute():
            path = (home / rel).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            return prefix + path.as_posix()
    return url


def load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines; real environment variables always win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_settings(home: Path | None = None) -> Settings:
    home = home or _default_home()
    load_dotenv(home / ".env")
    raw = load_yaml(home / "config" / "system.yaml")
    storage = raw.get("storage", {})
    orch = raw.get("orchestrator", {})
    env = os.environ
    return Settings(
        home=home,
        environment=raw.get("system", {}).get("environment", "development"),
        database_url=_resolve_db_url(home, storage.get("database_url") or "sqlite:///./var/awf.db"),
        object_store=storage.get("object_store") or "local",
        artifact_root=_resolve_path(home, storage.get("local_artifact_root") or "./var/artifacts"),
        api_token=env.get("AWF_API_TOKEN", ""),
        worker_registration_token=env.get("AWF_WORKER_REGISTRATION_TOKEN", ""),
        model_provider_override=env.get("AWF_MODEL_PROVIDER", ""),
        embedded_orchestrator=env.get("AWF_EMBEDDED_ORCHESTRATOR", "false").lower() in ("1", "true", "yes"),
        tick_interval_seconds=float(orch.get("tick_interval_seconds", 2)),
        worker_heartbeat_timeout_seconds=int(orch.get("worker_heartbeat_timeout_seconds", 90)),
        run_reassign_after_seconds=int(orch.get("run_reassign_after_seconds", 600)),
        job_max_attempts=int(orch.get("job_max_attempts", 3)),
        context_max_chars_per_artifact=int(orch.get("context_max_chars_per_artifact", 60000)),
        default_worker_platform=raw.get("routing", {}).get("default_worker", "linux"),
        git_root=_resolve_path(home, env.get("AWF_GIT_ROOT") or "./var/git"),
        mirror_token=env.get("AWF_MIRROR_TOKEN", ""),
        stale_branch_days=int(env.get("AWF_STALE_BRANCH_DAYS") or 14),
        raw=raw,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return build_settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
