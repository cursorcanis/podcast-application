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

M2 scope (POD-9): episode intake, the Board source-approval gate, and
hand-entered script review (app/episodes.py, app/routes_episodes.py) —
everything hand-entered, no automatic research or script generation
(Risk R5). No rendering, no email.

M3 scope (POD-10): the ComfyUI render pipeline (app/render.py,
app/workflow.py, app/chunking.py, app/routes_render.py) — serial chunked
submission against the configured workflow JSON, crash/reboot-resumable
render_jobs/render_chunks state, the one-job-system-wide constraint, the
10-minute chunk timeout, OOM backoff by halving chunk size, and the
MAX_RENDER_HOURS confirm-to-proceed gate.

M4 scope (POD-11): ffmpeg mastering + export and QA (app/postprod.py,
app/qa.py, app/routes_postprod.py) — concatenate rendered chunks with real
[PAUSE] silence, apply speed, normalize to -16 LUFS, export the WAV master +
192kbps archive MP3 + 96kbps email MP3 with ID3 tags; automated QA (duration
floor, per-chunk clipping, silence gaps over 3s, speaker-voice match) with a
QA report + show notes written as episode documents, and re-render of only
the chunks QA flagged. No email send (M5+).

M5 scope (POD-12): delivery and the share handoff (app/delivery.py,
app/routes_delivery.py) — on QA pass the app records a `delivery_records` row
with `status='paused'` (EMAIL_METHOD unset today), copies the email MP3 to
SHARE_LOCATION, and surfaces a "collect your episode here" notice; the Resend
button stays visible but disabled with the pause reason, wired to the same
delivery boundary so a future EMAIL_METHOD decision activates it with no code
change. Delivery is idempotent and logged — never automatic, never a double-send.

M6 scope (POD-13): the Settings page (app/settings.py, app/routes_settings.py)
— voice profiles tied to Chatterbox reference clips with a 30s sample-render
audition, tone/cadence presets, and saved recipient lists (restricted to
DEFAULT_RECIPIENTS). These are conveniences; the Board still makes the
per-episode choices, and rendering is still driven by the Audio Engineer's
VOICE_MAPPING (read-only here), never by invented linkage.

Run:  uvicorn app:app --reload        (or start_app.bat / run.bat on Windows)
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import comfyui, db, render
from .config import config
from .routes_delivery import router as delivery_router
from .routes_episodes import router as episodes_router
from .routes_postprod import router as postprod_router
from .routes_render import router as render_router
from .routes_settings import router as settings_router
from .web import templates

app = FastAPI(title="Podcast Foundry", version="0.6.0-m6")
app.include_router(episodes_router)
app.include_router(render_router)
app.include_router(postprod_router)
app.include_router(delivery_router)
app.include_router(settings_router)

BASE_DIR = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Ensure the SQLite state file exists at startup (repo/data/, gitignored) so
# the Status screen's DB-backed checks never crash a fresh checkout. Later
# milestones (M2+) build all episode/render state on this file, and M6's
# voice_profiles/presets/recipient_lists tables live here too (created by
# db.SCHEMA).
db.init_db()

# Resumability over speed: any render_job left `running` when the process
# last stopped (crash, reboot, or a plain restart) gets its worker thread
# re-started here, picking up from the first non-succeeded chunk.
render.resume_pending_jobs()


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