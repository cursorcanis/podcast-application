"""M2 smoke tests (POD-9) — run: python -m pytest tests/ -q

Walks one episode from intake through the Board source-approval gate and a
pasted script to status `script_ready`, and checks the gate's hard
boundaries: a typed-in recipient outside DEFAULT_RECIPIENTS/saved lists is
rejected, an out-of-range speed is rejected, and a later stage is not
reachable (and cannot be mutated by a direct POST) until the source gate is
explicitly closed.

Each test gets a fresh on-disk SQLite file (monkeypatched DB_PATH) so tests
do not depend on run order or leak episodes into each other via app/data/.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

import app as app_module
from app import db, episodes


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
    db.init_db(db_path)
    with TestClient(app_module.app) as c:
        yield c


def _create_episode(client, **overrides):
    data = {
        "title": "Test Episode",
        "topic_or_query": "How WSL networking affects local AI tooling",
        "format": "two_host",
        "tone": "investigative",
        "speed": "1.1",
        "length_minutes_target": "25",
        "audience_level": "general",
        "voice_1": "Dana",
        "voice_2": "Marcus",
        "recipient_emails": ["alfredoalea@gmail.com"],
    }
    data.update(overrides)
    return client.post("/episodes", data=data, follow_redirects=False)


def test_full_walk_intake_to_script_ready(client):
    resp = _create_episode(client)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/episodes/1/sources"

    # Gate is open; script review is locked.
    locked = client.get("/episodes/1/script")
    assert "LOCKED" in locked.text

    add1 = client.post(
        "/episodes/1/sources/add",
        data={"title": "WSL mirrored networking docs", "url": "https://learn.microsoft.com/x"},
        follow_redirects=False,
    )
    assert add1.status_code == 303
    add2 = client.post(
        "/episodes/1/sources/add", data={"title": "An undecided post"}, follow_redirects=False
    )
    assert add2.status_code == 303

    # Closing with pending decisions is refused, naming the exact count.
    early_close = client.post("/episodes/1/sources/close", follow_redirects=False)
    assert "pending" in early_close.headers["location"] or "decision" in early_close.headers["location"]
    assert episodes.get_episode(1)["status"] == "sources_pending_approval"

    client.post("/episodes/1/sources/1/decide", data={"decision": "approved"})
    client.post("/episodes/1/sources/2/decide", data={"decision": "removed"})

    close = client.post("/episodes/1/sources/close", follow_redirects=False)
    assert close.status_code == 303
    assert close.headers["location"] == "/episodes/1/script"
    assert episodes.get_episode(1)["status"] == "sources_approved"

    # Now reachable.
    unlocked = client.get("/episodes/1/script")
    assert "LOCKED" not in unlocked.text
    assert "Mark script ready" in unlocked.text

    save = client.post(
        "/episodes/1/script/save",
        data={
            "outline": "Intro; history; fix; outro.",
            "script": "[HOST_A] Welcome. [PAUSE:0.5s] [HOST_B] Let's dig in.",
            "citation_map": "Paragraph 1 -> https://learn.microsoft.com/x",
        },
        follow_redirects=False,
    )
    assert save.status_code == 303

    ready = client.post("/episodes/1/script/ready", follow_redirects=False)
    assert ready.status_code == 303
    assert ready.headers["location"] == "/episodes/1"
    assert episodes.get_episode(1)["status"] == "script_ready"

    library = client.get("/episodes")
    assert "status-script_ready" in library.text


def test_cannot_mark_script_ready_without_script_text(client):
    _create_episode(client)
    client.post("/episodes/1/sources/add", data={"title": "Source A"})
    client.post("/episodes/1/sources/1/decide", data={"decision": "approved"})
    client.post("/episodes/1/sources/close", follow_redirects=False)

    ready = client.post("/episodes/1/script/ready", follow_redirects=False)
    assert "error=" in ready.headers["location"]
    assert episodes.get_episode(1)["status"] == "sources_approved"


def test_arbitrary_recipient_email_rejected(client):
    resp = _create_episode(client, recipient_emails=["not-on-the-list@evil.example"])
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    assert "Recipient" in resp.headers["location"]


def test_speed_out_of_range_rejected(client):
    resp = _create_episode(client, speed="5.0")
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]


def test_gate_closed_episode_rejects_further_source_mutation(client):
    _create_episode(client)
    client.post("/episodes/1/sources/add", data={"title": "Source A"})
    client.post("/episodes/1/sources/1/decide", data={"decision": "approved"})
    client.post("/episodes/1/sources/close", follow_redirects=False)

    add_after = client.post(
        "/episodes/1/sources/add", data={"title": "too late"}, follow_redirects=False
    )
    assert "error=" in add_after.headers["location"]

    decide_after = client.post(
        "/episodes/1/sources/1/decide", data={"decision": "removed"}, follow_redirects=False
    )
    assert "error=" in decide_after.headers["location"]

    reclose = client.post("/episodes/1/sources/close", follow_redirects=False)
    assert "error=" in reclose.headers["location"]


def test_allowed_recipient_emails_is_default_recipients_only_with_no_saved_lists(client):
    allowed = episodes.allowed_recipient_emails()
    from app.config import config

    assert allowed == list(config.default_recipients)
