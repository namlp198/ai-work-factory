"""Control API application factory."""

from __future__ import annotations

import hashlib
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from awf import __version__
from awf.logging import configure_logging, get_logger
from awf.orchestrator.orchestrator import Orchestrator
from awf.platform import Platform
from awf_api.deps import AppState
from awf_api.routes import management, session, workers

STATIC_DIR = Path(__file__).parent / "static"
logger = get_logger("api")


def create_app(platform: Platform | None = None, orchestrator: Orchestrator | None = None,
               embedded_orchestrator: bool | None = None) -> FastAPI:
    configure_logging()
    platform = platform or Platform.build()
    orchestrator = orchestrator or Orchestrator(platform)
    embedded = platform.settings.embedded_orchestrator if embedded_orchestrator is None else embedded_orchestrator
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not platform.settings.api_token:
            logger.warning("AWF_API_TOKEN is not set: admin API is open (development only, bind to localhost)")
        thread = None
        notifier = None
        if embedded:
            from awf.notify.service import NotificationService

            notifier = NotificationService(platform, orchestrator)
            notifier.start()

            def loop() -> None:
                while not stop.wait(platform.settings.tick_interval_seconds):
                    try:
                        orchestrator.tick()
                    except Exception:
                        logger.exception("orchestrator tick failed")

            thread = threading.Thread(target=loop, name="orchestrator", daemon=True)
            thread.start()
        yield
        stop.set()
        if notifier:
            notifier.stop()
        if thread:
            thread.join(timeout=10)

    app = FastAPI(title="AI Work Factory — Control API", version=__version__, lifespan=lifespan)
    app.state.awf = AppState(platform=platform, orchestrator=orchestrator)
    app.include_router(management.router)
    app.include_router(workers.router)
    app.include_router(session.router)

    @app.get("/health")
    @app.get("/api/v1/health")
    def health() -> dict:
        with platform.db.session() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok", "version": __version__, "environment": platform.settings.environment}

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # Fingerprint app.js/style.css in the page so browsers (phones especially) never keep an old copy after an
    # update; the page itself is never cached.
    digest = hashlib.sha256(b"".join((STATIC_DIR / n).read_bytes() for n in ("app.js", "style.css"))).hexdigest()[:12]
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8") \
        .replace('/static/app.js"', f'/static/app.js?v={digest}"') \
        .replace('/static/style.css"', f'/static/style.css?v={digest}"')

    @app.get("/", include_in_schema=False)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(page, headers={"Cache-Control": "no-cache"})

    # Installable app (PWA). The service worker must be served from the root to control the whole dashboard;
    # it is rendered with the asset fingerprint so every update installs a fresh one.
    manifest = (STATIC_DIR / "manifest.webmanifest").read_text(encoding="utf-8")
    service_worker = (STATIC_DIR / "sw.js").read_text(encoding="utf-8").replace("__VERSION__", digest)

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def web_manifest() -> Response:
        return Response(manifest, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})

    @app.get("/sw.js", include_in_schema=False)
    def sw() -> Response:
        return Response(service_worker, media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    return app
