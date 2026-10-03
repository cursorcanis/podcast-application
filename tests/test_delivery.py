"""Integration tests for app/delivery.py — M5 (POD-12).

Delivery is paused today (EMAIL_METHOD unset), so the tests prove the
paused-handoff path for real (QA pass -> delivery_records row 'paused' ->
email MP3 copied into SHARE_LOCATION -> status 'delivered_paused') with the
same real ffmpeg + on-disk SQLite harness as test_postprod_qa.py, plus the
contract that flips on with a config change and no code change:

  - `resend()` when paused returns the paused state and sends nothing.
  - With EMAIL_METHOD set but SMTP credentials absent, the same delivery
    boundary (reached via QA pass) fails loud naming the missing env names —
    never a real send, never a silent fake success.
  - `resend()` refuses to double-send after a 'sent' record.
  - `resend()` refuses a recipient not in the allowed set.

No real SMTP send happens anywhere in this suite (and none may happen until
POD-7 resolves) — the SMTP path is only reached up to the point it raises for
missing credentials, which is the honest fail-loud behavior this milestone
demands.
"""
from __future__ import annotations

import pathlib
import shutil
import sqlite3
import subprocess

import pytest
from fastapi.testclient import TestClient

import app as app_module
from app import comfyui, db, delivery, episodes, postprod, qa, render, workflow
from app.config import config


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg/ffprobe not on PATH")


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
    monkeypatch.setattr(render, "get_connection", fake_get_connection)
    monkeypatch.setattr(postprod, "get_connection", fake_get_connection)
    monkeypatch.setattr(qa, "get_connection", fake_get_connection)
    monkeypatch.setattr(delivery, "get_connection", fake_get_connection)
    db.init_db(db_path)
    monkeypatch.setattr(render, "_start_thread", lambda job_id: None)
    monkeypatch.setitem(config.values, "OUTPUT_FOLDER", str(tmp_path / "comfy_output"))
    # Point SHARE_LOCATION at a tmp dir so the paused-handoff copy writes
    # there instead of the Board's real Windows folder.
    monkeypatch.setitem(config.values, "SHARE_LOCATION", str(tmp_path / "share"))
    (tmp_path / "share").mkdir(parents=True, exist_ok=True)
    # Force delivery paused for the majority of tests; individual tests flip this.
    monkeypatch.setitem(config.values, "EMAIL_METHOD", None)
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASS", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_PORT", raising=False)
    with TestClient(app_module.app) as c:
        yield c


def _create_episode_to_script_ready(client, script: str, *, length_minutes_target: int = 5) -> int:
    resp = client.post(
        "/episodes",
        data={
            "title": "M5 Delivery Test Episode!",
            "topic_or_query": "Testing the delivery handoff",
            "format": "two_host",
            "speed": "1.0",
            "length_minutes_target": str(length_minutes_target),
            "recipient_emails": [config.default_recipients[0]],
        },
        follow_redirects=False,
    )
    episode_id = int(resp.headers["location"].split("/")[2])
    client.post(f"/episodes/{episode_id}/sources/add", data={
        "title": "Source A", "url": "https://example.com/a", "publication": "Example Press",
    })
    source_id = episodes.list_sources(episode_id)[0]["id"]
    client.post(f"/episodes/{episode_id}/sources/{source_id}/decide", data={"decision": "approved"})
    client.post(f"/episodes/{episode_id}/sources/close")
    client.post(f"/episodes/{episode_id}/script/save", data={"script": script})
    client.post(f"/episodes/{episode_id}/script/ready")
    assert episodes.get_episode(episode_id)["status"] == "script_ready"
    return episode_id


def _save_node_id() -> str:
    wf = workflow.load_workflow(config.path_to_workflow_json)
    return workflow.resolve_roles(wf).save_node_id


def _write_tone_wav(path, *, seconds: float, amplitude: float = 0.5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"aevalsrc={amplitude}*sin(2*PI*440*t):duration={seconds}:sample_rate=24000",
            "-ac", "1", str(path),
        ],
        check=True, capture_output=True,
    )


def _render_and_qa_pass(client, monkeypatch, *, seconds_per_chunk=155.0, length_minutes_target=5) -> int:
    """Walk an episode through render -> master -> QA pass so delivery has
    a real email MP3 and a pass to act on. Returns the episode id."""
    episode_id = _create_episode_to_script_ready(
        client,
        "[HOST_A] First turn. [PAUSE:0.5s] [HOST_B] Second turn.",
        length_minutes_target=length_minutes_target,
    )
    save_node_id = _save_node_id()
    written = {"n": 0}

    def fake_submit(url, prompt, client_id):
        written["n"] += 1
        return f"prompt-{written['n']}"

    def fake_poll(url, prompt_id, *, timeout_s, **kwargs):
        n = written["n"]
        filename = f"chunk_{n:02d}.flac"
        out_path = pathlib.Path(config.output_folder) / "ep_testrun" / filename
        _write_tone_wav(out_path, seconds=seconds_per_chunk)
        return {
            "outcome": "succeeded",
            "outputs": {save_node_id: {"audio": [{"filename": filename, "subfolder": "podcast_foundry/ep_testrun"}]}},
        }

    monkeypatch.setattr(comfyui, "submit_prompt", fake_submit)
    monkeypatch.setattr(comfyui, "poll_history", fake_poll)

    result = render.start_render_job(episode_id)
    assert result["status"] == "running"
    render._run_job(result["job_id"])
    assert episodes.get_episode(episode_id)["status"] == "rendered"

    postprod.run_postproduction(episode_id)
    qa_result = qa.run_qa(episode_id)
    assert qa_result["overall_pass"] is True
    return episode_id


def test_qa_pass_records_paused_and_hands_off_to_share_location(client, monkeypatch):
    episode_id = _render_and_qa_pass(client, monkeypatch)

    episode = episodes.get_episode(episode_id)
    assert episode["status"] == "delivered_paused"
    assert episode["qa_status"] == "pass"

    record = delivery.latest_delivery_record(episode_id)
    assert record is not None
    assert record["status"] == "paused"
    assert record["provider_message_id"] is None
    assert delivery.PAUSE_REASON_NO_METHOD in (record["reason"] or "")

    # The email MP3 must actually exist in SHARE_LOCATION now.
    share_dir = pathlib.Path(config.share_location)
    copied = list(share_dir.glob("*_email_96k.mp3"))
    assert copied, f"no email MP3 copied into SHARE_LOCATION {share_dir}"
    assert copied[0].exists()

    # Resend in the paused state sends nothing and leaves status intact.
    resend = delivery.resend(episode_id)
    assert resend["sent"] is False
    assert resend["status"] == "paused"
    assert episodes.get_episode(episode_id)["status"] == "delivered_paused"


def test_method_set_without_credentials_fails_loud_via_same_boundary(client, monkeypatch):
    """Activate the method BEFORE the QA pass. The QA-driven delivery path is
    the exact same boundary as resend; with EMAIL_METHOD set but no SMTP
    credentials it must fail loud (naming the missing env names), never send,
    and never fake a 'sent' — proving activation is config-only, no code
    change."""
    monkeypatch.setitem(config.values, "EMAIL_METHOD", "smtp")
    episode_id = _create_episode_to_script_ready(
        client,
        "[HOST_A] First turn. [PAUSE:0.5s] [HOST_B] Second turn.",
        length_minutes_target=5,
    )
    save_node_id = _save_node_id()
    written = {"n": 0}

    def fake_submit(url, prompt, client_id):
        written["n"] += 1
        return f"prompt-{written['n']}"

    def fake_poll(url, prompt_id, *, timeout_s, **kwargs):
        n = written["n"]
        filename = f"chunk_{n:02d}.flac"
        out_path = pathlib.Path(config.output_folder) / "ep_testrun" / filename
        _write_tone_wav(out_path, seconds=155.0)
        return {
            "outcome": "succeeded",
            "outputs": {save_node_id: {"audio": [{"filename": filename, "subfolder": "podcast_foundry/ep_testrun"}]}},
        }

    monkeypatch.setattr(comfyui, "submit_prompt", fake_submit)
    monkeypatch.setattr(comfyui, "poll_history", fake_poll)

    result = render.start_render_job(episode_id)
    render._run_job(result["job_id"])
    postprod.run_postproduction(episode_id)

    # QA passes its checks, then hands to delivery, which raises for the
    # missing credentials — fail loud, no send, no 'sent' record.
    with pytest.raises(delivery.DeliveryError) as exc:
        qa.run_qa(episode_id)
    msg = str(exc.value)
    assert "SMTP_USER" in msg or "SMTP_PASS" in msg or "credential" in msg.lower()

    # No delivery record was written with 'sent'.
    record = delivery.latest_delivery_record(episode_id)
    assert record is None or record["status"] != "sent"


def test_resend_refuses_double_send_after_sent_record(client, monkeypatch):
    episode_id = _render_and_qa_pass(client, monkeypatch)
    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO delivery_records (episode_id, status, provider_message_id, recipients_sent_to, attempted_at) "
            "VALUES (?, 'sent', 'mock:server:1', '[]', datetime('now'))",
            (episode_id,),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setitem(config.values, "EMAIL_METHOD", "smtp")
    with pytest.raises(delivery.DeliveryError) as exc:
        delivery.resend(episode_id)
    assert "already delivered" in str(exc.value)


def test_resend_refuses_unapproved_recipient(client, monkeypatch):
    episode_id = _render_and_qa_pass(client, monkeypatch)
    # Inject an unapproved recipient into the episode record directly.
    conn = db.get_connection()
    try:
        conn.execute(
            "UPDATE episodes SET recipient_emails = ? WHERE id = ?",
            ('["intruder@example.com"]', episode_id),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setitem(config.values, "EMAIL_METHOD", "smtp")
    monkeypatch.setenv("SMTP_USER", "test@example.com")
    monkeypatch.setenv("SMTP_PASS", "secret")
    with pytest.raises(delivery.DeliveryError) as exc:
        delivery.resend(episode_id)
    assert "intruder@example.com" in str(exc.value)