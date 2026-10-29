"""The dashboard as an installable app (PWA): manifest, icons, service worker, phone overview."""

from __future__ import annotations

import struct
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "apps" / "control_api" / "awf_api" / "static"


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_page_links_the_app_metadata(client):
    page = client.get("/")
    assert page.headers["cache-control"] == "no-cache"
    html = page.text
    for needle in ('rel="manifest" href="/manifest.webmanifest"', 'rel="apple-touch-icon"', 'name="theme-color"',
                   "viewport-fit=cover", 'id="offline"'):
        assert needle in html


def test_manifest_and_icons(client):
    resp = client.get("/manifest.webmanifest")
    assert resp.headers["content-type"].startswith("application/manifest+json")
    manifest = resp.json()
    assert manifest["display"] == "standalone" and manifest["start_url"].startswith("/")
    sizes = set()
    for icon in manifest["icons"]:
        data = client.get(icon["src"]).content
        width, height = _png_size(data)
        assert f"{width}x{height}" == icon["sizes"]
        sizes.add((icon["sizes"], icon["purpose"]))
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes  # installable on Android
    assert _png_size(client.get("/static/icons/apple-touch-icon.png").content) == (180, 180)  # iOS home screen


def test_service_worker_caches_the_shell_never_the_api(client):
    resp = client.get("/sw.js")
    assert resp.headers["content-type"].startswith("text/javascript") and resp.headers["cache-control"] == "no-cache"
    sw = resp.text
    assert "__VERSION__" not in sw
    version = sw.split('const VERSION = "')[1].split('"')[0]
    assert f"/static/app.js?v={version}" in client.get("/").text  # same fingerprint as the page
    assert 'url.pathname.startsWith("/api/")' in sw  # API responses are never cached on the device


def test_overview_lists_running_runs_with_progress(client, platform, orchestrator, worker, repository_config):
    project = client.post("/api/v1/projects", json={"name": "Robo", "repository": repository_config,
                                                    "labels": {"platform": "linux"}}).json()
    run_id = client.post(f"/api/v1/projects/{project['id']}/workflow-runs",
                         json={"inputs": {"objective": "Add a health check"}}).json()["id"]
    for _ in range(4):
        orchestrator.run_until_idle()
        worker.run_once()
    orchestrator.run_until_idle()
    runs = client.get("/api/v1/overview").json()["runs"]
    row = next(r for r in runs if r["id"] == run_id)
    assert row["project"] == "Robo" and row["worker"] == "test-linux-01" and row["stages_total"] == 10
    assert row["stages_done"] == 1 and row["current_stage"] == "Architecture" and row["current_gate"] is True
    assert row["objective"] == "Add a health check"


def test_static_files_exist():
    for name in ("manifest.webmanifest", "sw.js", "icons/icon-192.png", "icons/icon-512.png",
                 "icons/icon-maskable-512.png", "icons/apple-touch-icon.png"):
        assert (STATIC / name).is_file(), name
