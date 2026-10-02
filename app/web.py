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
