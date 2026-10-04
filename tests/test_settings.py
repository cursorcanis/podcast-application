"""M6 smoke tests (POD-13) — run: python -m pytest tests/ -q

Voice profiles / presets / saved recipient lists, plus the sample-render
refusals that keep the one-GPU queue honest. The sample render itself hits
ComfyUI, so these tests prove the *guards* (refuse while an episode job is
active; refuse a profile without a reference clip) and the read paths, with
ComfyUI monkeypatched only where a real submit would otherwise occur.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

import app as app_module
from app import db, episodes, settings


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)

    def fake_get_connection(path=db_path):
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    monkeypatch.setattr(db, "get_connection", fake_get_connection)
    monkeypatch.setattr(episodes, "get_connection", fake_get_connection)
    monkeypatch.setattr(settings, "get_connection", fake_get_connection)
    # The sample-render guard checks the system-wide active render job. Stub it
    # so no refusal-path test ever touches the repo's real on-disk SQLite
    # (render.get_connection was bound to the real db module at import time and
    # is intentionally NOT monkeypatched here — a test must never read the
    # production DB).
    monkeypatch.setattr(settings, "get_system_active_job", lambda: None)
    db.init_db(db_path)
    with TestClient(app_module.app) as c:
        yield c


def test_settings_page_renders(client):
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "Voice profiles" in resp.text
    assert "Presets" in resp.text
    assert "Saved recipient lists" in resp.text


def test_create_and_delete_voice_profile(client):
    r = client.post(
        "/settings/voice-profiles",
        data={
            "name": "Dana — warm, measured",
            "comfyui_voice_ref": "voice_a.wav",
            "reference_clip_path": "",
            "notes": "primary host",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    profiles = settings.list_voice_profiles()
    assert [p["name"] for p in profiles] == ["Dana — warm, measured"]
    assert profiles[0]["comfyui_voice_ref"] == "voice_a.wav"

    r = client.post("/settings/voice-profiles/1/delete", follow_redirects=False)
    assert r.status_code == 303
    assert settings.list_voice_profiles() == []


def test_duplicate_voice_profile_rejected(client):
    client.post("/settings/voice-profiles", data={"name": "Dana"})
    r = client.post("/settings/voice-profiles", data={"name": "Dana"}, follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_preset_create_list_delete(client):
    client.post("/settings/presets", data={"kind": "tone", "name": "investigative", "params": "investigative, measured"})
    client.post("/settings/presets", data={"kind": "cadence", "name": "steady", "params": "steady"})
    tones = settings.list_presets("tone")
    cadences = settings.list_presets("cadence")
    assert [(p["name"], p["params"]) for p in tones] == [("investigative", "investigative, measured")]
    assert [(p["name"], p["params"]) for p in cadences] == [("steady", "steady")]

    r = client.post("/settings/presets/1/delete", follow_redirects=False)
    assert r.status_code == 303
    assert settings.list_presets("tone") == []


def test_recipeient_list_restricted_to_default_recipients(client):
    from app.config import config

    r = client.post(
        "/settings/recipient-lists",
        data={"name": "Board", "emails": "intruder@example.com"},
        follow_redirects=False,
    )
    assert "error=" in r.headers["location"]
    # URL-quoted message: "DEFAULT_RECIPIENTS" is a single preserved token.
    assert "DEFAULT_RECIPIENTS" in r.headers["location"]

    ok = config.default_recipients[0]
    r = client.post(
        "/settings/recipient-lists",
        data={"name": "Board", "emails": ok},
        follow_redirects=False,
    )
    assert r.status_code == 303
    lists = settings.list_recipient_lists()
    assert lists[0]["name"] == "Board"
    assert lists[0]["emails"] == [ok]


def test_sample_render_refuses_missing_voice_ref(client):
    client.post("/settings/voice-profiles", data={"name": "NoRef"})
    r = client.post("/settings/voice-profiles/1/sample", follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert "comfyui_voice_ref" in r.headers["location"]


def test_sample_render_refuses_while_episode_job_active(client, monkeypatch):
    client.post("/settings/voice-profiles", data={"name": "Dana", "comfyui_voice_ref": "voice_a.wav"})
    monkeypatch.setattr(
        settings, "get_system_active_job",
        lambda: {"id": 9, "episode_id": 3, "status": "running"},
    )
    r = client.post("/settings/voice-profiles/1/sample", follow_redirects=False)
    assert "error=" in r.headers["location"]
    # URL-quoted message; assert preserved single-word tokens.
    assert "occupy" in r.headers["location"]
    assert "GPU" in r.headers["location"]


def test_allowed_recipient_emails_includes_saved_lists(client):
    from app.config import config

    ok = config.default_recipients[0]
    client.post("/settings/recipient-lists", data={"name": "Board", "emails": ok})
    assert episodes.allowed_recipient_emails() == list(config.default_recipients)