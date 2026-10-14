"""Shared Jinja2 environment for the app/ package.

A single `templates` instance is imported by app/__init__.py and every
routes module so each page is registered against the exact same Jinja2
environment (filters, globals, autoescape settings) instead of each router
quietly constructing its own.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _render_active() -> bool:
    """True while a render job holds the GPU — lights the dot on the
    header's Rendering button. Imported lazily: render imports config/db
    at module load and must not be pulled in by this shared module."""
    from . import render

    try:
        active = render.get_system_active_job()
    except Exception:
        return False
    return active is not None and active["status"] == "running"


templates.env.globals["render_active"] = _render_active
