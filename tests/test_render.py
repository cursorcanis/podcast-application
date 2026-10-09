"""Integration tests for app/render.py — the M3 (POD-10) render pipeline.

ComfyUI itself is never called: app.comfyui.submit_prompt / poll_history are
monkeypatched so these tests run offline and deterministically, but every
other part of the pipeline (chunking, dynamic workflow node discovery,
render_jobs/render_chunks persistence, the one-job-system-wide constraint,
the cap confirmation gate, and crash/resume) runs for real against a fresh
on-disk SQLite file per test, exactly like tests/test_episodes.py.

`_start_thread` is monkeypatched to a no-op so a render job never actually
starts a background thread in these tests; `render._run_job` is called
directly instead, so the worker loop runs synchronously and deterministically
in the test thread.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

import app as app_module
from app import comfyui, db, episodes, render, workflow
from app.config import config


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
    db.init_db(db_path)
    monkeypatch.setattr(render, "_start_thread", lambda job_id: None)
    # Fake chunks here are deliberately longer than Chatterbox's 34s ceiling.
    monkeypatch.setattr(workflow, "max_audio_seconds", lambda wf, roles: None)
    with TestClient(app_module.app) as c:
        yield c


def _create_episode_to_script_ready(client, script: str) -> int:
    resp = client.post(
        "/episodes",
        data={
            "title": "Render Test Episode",
            "topic_or_query": "Testing the render pipeline",
            "format": "two_host",
            "speed": "1.0",
            "length_minutes_target": "10",
            "recipient_emails": [config.default_recipients[0]],
        },
        follow_redirects=False,
    )
    episode_id = int(resp.headers["location"].split("/")[2])
    client.post(f"/episodes/{episode_id}/sources/add", data={"title": "Source A"})
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


def _fake_success(subfolder: str, filename: str):
    save_node_id = _save_node_id()

    def _poll_history(url, prompt_id, *, timeout_s, **kwargs):
        return {
            "outcome": "succeeded",
            "outputs": {save_node_id: {"audio": [{"filename": filename, "subfolder": subfolder}]}},
            "raw": {},
        }

    return _poll_history


def test_full_render_job_succeeds_and_episode_moves_to_rendered(client, monkeypatch):
    episode_id = _create_episode_to_script_ready(client, "[HOST_A] Hello there. [HOST_B] Yes indeed.")

    counter = {"n": 0}

    def fake_submit(url, prompt, client_id):
        counter["n"] += 1
        return f"prompt-{counter['n']}"

    monkeypatch.setattr(comfyui, "submit_prompt", fake_submit)
    monkeypatch.setattr(comfyui, "poll_history", _fake_success("podcast_foundry/sub", "chunk_00001_.flac"))

    result = render.start_render_job(episode_id)
    assert result["status"] == "running"
    render._run_job(result["job_id"])

    job = render.get_latest_job_for_episode(episode_id)
    assert job["status"] == "succeeded"
    assert episodes.get_episode(episode_id)["status"] == "rendered"

    progress = render.job_progress(job["id"])
    assert progress["counts"]["total"] == 2
    assert progress["counts"]["succeeded"] == 2
    assert all(c["status"] == "succeeded" for c in progress["chunks"])
    assert progress["chunks"][0]["output_wav_path"].endswith("chunk_00001_.flac")
    assert progress["chunks"][0]["speaker"] == "HOST_A"
    assert progress["chunks"][1]["speaker"] == "HOST_B"


def test_crash_mid_chunk_resumes_from_first_non_succeeded_chunk(client, monkeypatch):
    episode_id = _create_episode_to_script_ready(client, "[HOST_A] First turn. [HOST_B] Second turn.")

    monkeypatch.setattr(comfyui, "submit_prompt", lambda url, prompt, client_id: "prompt-x")
    calls = {"n": 0}

    def flaky_poll(url, prompt_id, *, timeout_s, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"outcome": "succeeded", "outputs": {_save_node_id(): {"audio": [{"filename": "c1.flac", "subfolder": "podcast_foundry/x"}]}}}
        raise RuntimeError("simulated process crash mid chunk 2")

    monkeypatch.setattr(comfyui, "poll_history", flaky_poll)

    result = render.start_render_job(episode_id)
    job_id = result["job_id"]
    with pytest.raises(RuntimeError, match="simulated process crash"):
        render._run_job(job_id)

    # DB state after the "crash": chunk 1 succeeded and durably recorded,
    # chunk 2 left mid-flight, job still shows running (no clean shutdown).
    progress = render.job_progress(job_id)
    assert progress["chunks"][0]["status"] == "succeeded"
    assert progress["chunks"][1]["status"] == "rendering"
    job_row = render.get_latest_job_for_episode(episode_id)
    assert job_row["status"] == "running"

    # Resume: a fresh app start would call resume_pending_jobs() -> _run_job
    # again for this still-"running" job. It must not re-render chunk 1.
    monkeypatch.setattr(comfyui, "poll_history", _fake_success("podcast_foundry/x", "c2.flac"))
    render._run_job(job_id)

    progress = render.job_progress(job_id)
    assert progress["counts"]["total"] == 2  # no duplicate chunk rows created
    assert progress["counts"]["succeeded"] == 2
    assert render.get_latest_job_for_episode(episode_id)["status"] == "succeeded"


def test_only_one_render_job_system_wide_at_a_time(client, monkeypatch):
    monkeypatch.setattr(comfyui, "submit_prompt", lambda *a, **k: "prompt-x")
    monkeypatch.setattr(comfyui, "poll_history", lambda *a, **k: {"outcome": "timed_out", "error_detail": "stall"})

    ep1 = _create_episode_to_script_ready(client, "[HOST_A] Episode one script.")
    ep2 = _create_episode_to_script_ready(client, "[HOST_A] Episode two script.")

    render.start_render_job(ep1)  # left 'running'; thread start is a no-op in this fixture

    with pytest.raises(render.AlreadyRenderingError):
        render.start_render_job(ep2)


def test_projection_over_cap_requires_explicit_confirmation_before_any_submission(client, monkeypatch):
    submitted = []
    monkeypatch.setattr(comfyui, "submit_prompt", lambda url, prompt, client_id: submitted.append(1) or "p")
    monkeypatch.setattr(comfyui, "poll_history", _fake_success("podcast_foundry/x", "c.flac"))

    # Mutate the frozen Config's underlying dict (not reassigning the
    # attribute) so MAX_RENDER_HOURS is 0 and any non-zero projection is over
    # cap. monkeypatch.setitem restores this automatically even on failure —
    # this is process-global state shared with every other test module.
    monkeypatch.setitem(config.values, "MAX_RENDER_HOURS", 0)

    # Long enough that projected_hours_for's round(..., 2) doesn't round a
    # tiny-but-nonzero projection down to exactly 0.0 (which would tie, not
    # exceed, a zero-hour cap).
    long_line = "This is one sentence of narration text to pad out the script. " * 15
    episode_id = _create_episode_to_script_ready(client, f"[HOST_A] {long_line}")
    result = render.start_render_job(episode_id)
    assert result["status"] == "awaiting_cap_confirmation"
    assert submitted == []  # nothing submitted to ComfyUI before confirmation
    assert episodes.get_episode(episode_id)["status"] == "script_ready"  # not moved to rendering yet

    job = render.get_latest_job_for_episode(episode_id)
    render.confirm_render_job(job["id"])
    assert render.get_latest_job_for_episode(episode_id)["status"] == "running"

    render._run_job(job["id"])
    assert render.get_latest_job_for_episode(episode_id)["status"] == "succeeded"
    assert submitted  # now it actually ran
