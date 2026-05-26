"""FastAPI entry point for the webhook listener."""

from __future__ import annotations

import os

from fastapi import FastAPI, Request

from . import __version__
from .handlers import handle_webhook
from .logging_config import configure_logging, stage_log


def create_app() -> FastAPI:
    configure_logging(level=os.environ.get("WEBHOOK_LISTENER_LOG_LEVEL", "INFO"))
    app = FastAPI(title="Infrahub webhook listener", version=__version__)

    if os.environ.get("WEBHOOK_LISTENER_DISABLE_SIG", "0") == "1":
        stage_log(
            "startup_warning",
            request_id="startup",
            error="signature verification disabled (WEBHOOK_LISTENER_DISABLE_SIG=1); demo-only",
            level="WARNING",
        )

    @app.post("/webhook")
    async def webhook(request: Request):
        return await handle_webhook(request)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "version": __version__}

    return app


app = create_app()


def run() -> None:
    import uvicorn

    host = os.environ.get("WEBHOOK_LISTENER_HOST", "0.0.0.0")
    port = int(os.environ.get("WEBHOOK_LISTENER_PORT", "8000"))
    uvicorn.run("webhook_listener.main:app", host=host, port=port, log_config=None)


if __name__ == "__main__":
    run()
