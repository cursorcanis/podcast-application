"""Podcast Foundry — root-level module is SUPERSEDED.

The canonical settings loader is app/config.py. This stub fails loud on
import so nothing can accidentally read the duplicate root Settings that
drifted from the app/ package during the M1 migration.
"""
raise ImportError(
    "config.py at the repo root is superseded by app/config.py. "
    "Import the canonical loader: from app.config import config. "
    "If a tool insists on this module, delete it — it carries no code anymore."
)