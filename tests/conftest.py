"""Suite-wide isolation: every test gets its own scratch SQLite file.

Starting the app (`with TestClient(app)`) runs its startup hook, which
resumes any render job or autopilot episode it finds in the database. Without
this fixture a test that doesn't set up its own DB would run that hook
against the real data/podcast_foundry.db — and could start a real render
thread mid-test (the POD-35 corruption). Tests that build their own DB still
override this; this is the floor, not a replacement.
"""
from __future__ import annotations

import pytest

from app import db
from app.config import config


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    path = tmp_path / "isolated.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db(path)
    # The machine's .env may point COMFYUI_URL at the live ComfyUI (8189 here).
    # Tests assume the documented default and must never reach a real one.
    monkeypatch.setitem(config.values, "COMFYUI_URL", "http://127.0.0.1:8188")
    yield
