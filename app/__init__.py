"""Podcast Foundry — FastAPI application entry point (canonical).

The `app/` package is the single canonical implementation of the application
(see app/config.py, app/db.py, app/comfyui.py). The root-level app.py /
config.py / db.py are superseded fail-loud stubs and are never imported by
the runtime: `uvicorn app:app` resolves to this package, because a directory
package shadows a same-named module in the same folder.

M1 scope (POD-8): the Status / Settings screen with an honest, measured
ComfyUI reachability check against the Board's configured COMFYUI_URL, the
configured caps (MAX_RENDER_HOURS, CHUNK_TIMEOUT_MIN, MAX_ATTACHMENT_MB), the
$0/free-only budget banner, and the standing 'email delivery paused — see
POD-7' notice. Fails loud and specific: the check surfaces the exact error
and the documented fallback order, never 'something went wrong'.

Run:  uvicorn app:app --reload        (or start_app.bat / run.bat on Windows)
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import comfyui, db
from .config import config

app = FastAPI(title="Podcast Foundry", version="0.1.0-m1")

BASE_DIR = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Ensure the SQLite state file exists at startup (repo/data/, gitignored) so
# the Status screen's DB-backed checks never crash a fresh checkout. Later
# milestones (M2+) build all episode/render state on this file.
db.init_db()


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@app.get("/", response_class=HTMLResponse)
def status_screen(request: Request):
    """Status / Settings screen. The template reads caps/budget/pause state;
    the live ComfyUI result is rendered by static/status.js polling
    /api/comfyui/status every 10s — measured, never a guessed state."""
    return templates.TemplateResponse(
        request=request,
        name="status.html",
        context={
            "settings": config,
            "settings_dict": config.public_view(),
        },
    )


@app.get("/api/status")
def api_status() -> JSONResponse:
    """Machine view of the Status screen: caps, budget, email-pause state,
    and the live ComfyUI probe result."""
    return JSONResponse(
        {
            "checked_at": utcnow_iso(),
            "comfyui": comfyui.check_reachability(config.comfyui_url),
            "caps": {
                "max_render_hours": config.max_render_hours,
                "chunk_timeout_min": config.chunk_timeout_min,
                "max_attachment_mb": config.max_attachment_mb,
            },
            "budget": {
                "monthly_budget": config.monthly_budget,
                "free_only": config.monthly_budget == 0,
            },
            "email": {
                "paused": config.email_paused,
                "method": config.email_method or "none (paused)",
            },
        }
    )


@app.get("/api/comfyui/status")
def api_comfyui_status() -> JSONResponse:
    """Live probe of {COMFYUI_URL}/system_stats. Honest red UNREACHABLE with
    the exact error and the documented fallback order when there is no
    listener (the state today: ComfyUI not running as of 2026-10-02)."""
    payload = comfyui.check_reachability(config.comfyui_url)
    payload["checked_at"] = utcnow_iso()
    return JSONResponse(payload)


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}