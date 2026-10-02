"""Podcast Foundry — root-level module is SUPERSEDED.

The canonical SQLite schema/bootstrap is app/db.py. This stub fails loud on
import so nothing can accidentally use the duplicate root schema that drifted
from the app/ package during the M1 migration.
"""
raise ImportError(
    "db.py at the repo root is superseded by app/db.py. "
    "Import the canonical bootstrap: from app import db. "
    "If a tool insists on this module, delete it — it carries no code anymore."
)