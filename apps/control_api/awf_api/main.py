"""Control API application factory."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from awf import __version__
from awf.logging import configure_logging, get_logger
from awf.orchestrator.orchestrator import Orchestrator
from awf.platform import Platform
from awf_api.deps import AppState
from awf_api.routes import management, workers

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
        if embedded:
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
        if thread:
            thread.join(timeout=10)

    app = FastAPI(title="AI Work Factory — Control API", version=__version__, lifespan=lifespan)
    app.state.awf = AppState(platform=platform, orchestrator=orchestrator)
    app.include_router(management.router)
    app.include_router(workers.router)

    @app.get("/health")
    @app.get("/api/v1/health")
    def health() -> dict:
        with platform.db.session() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok", "version": __version__, "environment": platform.settings.environment}

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app
