"""Regression: a client must see its own write immediately (found running control plane and worker on
different machines). FastAPI's default `yield` dependency scope committed *after* the response was sent."""

from __future__ import annotations

import socket
import threading
import time

import httpx
import uvicorn


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_post_then_get_sees_committed_data(platform, orchestrator, repository_config):
    from awf_api.main import create_app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(platform, orchestrator, embedded_orchestrator=False),
                                           host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}/api/v1"
    try:
        for _ in range(100):
            try:
                httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.05)
        with httpx.Client(base_url=base, timeout=10) as api:
            for i in range(20):
                project = api.post("/projects", json={"name": f"p{i}", "repository": repository_config}).json()
                assert api.get(f"/projects/{project['id']}").status_code == 200
                run = api.post(f"/projects/{project['id']}/workflow-runs",
                               json={"inputs": {"objective": "x"}}).json()
                assert api.get(f"/workflow-runs/{run['id']}").status_code == 200
    finally:
        server.should_exit = True
        thread.join(timeout=10)
