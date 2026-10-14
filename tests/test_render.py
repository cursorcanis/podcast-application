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

import json
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


# --- POD-110: chunk-target guard and frozen voice mapping -------------------------

# The real ceiling lookup, captured before the fixture stubs it out.
_real_max_audio_seconds = workflow.max_audio_seconds


def test_chunk_target_over_safe_margin_is_rejected_before_any_job_exists(client, monkeypatch):
    # Episode 8's setting: 60s against the real workflow's 850-token (34s) ceiling.
    monkeypatch.setattr(workflow, "max_audio_seconds", _real_max_audio_seconds)
    monkeypatch.setitem(config.values, "CHUNK_SECONDS_TARGET_DEFAULT", 60)
    monkeypatch.setattr(comfyui, "submit_prompt", lambda *a, **k: pytest.fail("nothing may be submitted"))
    episode_id = _create_episode_to_script_ready(client, "[HOST_A] Hello there. [HOST_B] Yes indeed.")

    with pytest.raises(render.RenderError, match="exceeds the safe maximum of 26s"):
        render.dry_run_projection(episode_id)
    with pytest.raises(render.RenderError, match="CHUNK_SECONDS_TARGET_DEFAULT is 60s"):
        render.start_render_job(episode_id)
    assert render.get_latest_job_for_episode(episode_id) is None
    assert episodes.get_episode(episode_id)["status"] == "script_ready"


def test_default_chunk_target_passes_the_guard(client, monkeypatch):
    monkeypatch.setattr(workflow, "max_audio_seconds", _real_max_audio_seconds)
    monkeypatch.setitem(config.values, "CHUNK_SECONDS_TARGET_DEFAULT", 20)
    episode_id = _create_episode_to_script_ready(client, "[HOST_A] Hello there. [HOST_B] Yes indeed.")

    assert render.dry_run_projection(episode_id)
    assert render.start_render_job(episode_id)["status"] == "running"


def test_chunk_target_guard_skipped_when_workflow_has_no_max_new_tokens(client, monkeypatch):
    # Never guess a ceiling: a workflow without max_new_tokens isn't checked.
    real_load = workflow.load_workflow

    def load_without_ceiling(path):
        wf = real_load(path)
        tts_id = workflow.resolve_roles(wf).tts_node_id
        wf[tts_id]["inputs"].pop("max_new_tokens", None)
        return wf

    monkeypatch.setattr(workflow, "max_audio_seconds", _real_max_audio_seconds)
    monkeypatch.setattr(workflow, "load_workflow", load_without_ceiling)
    monkeypatch.setitem(config.values, "CHUNK_SECONDS_TARGET_DEFAULT", 60)
    episode_id = _create_episode_to_script_ready(client, "[HOST_A] Hello there. [HOST_B] Yes indeed.")

    assert render.start_render_job(episode_id)["status"] == "running"


def test_voice_mapping_snapshot_written_once_and_used_on_resume(client, monkeypatch):
    original = {"HOST_A": "voice_a.wav", "HOST_B": "voice_b.wav"}
    monkeypatch.setitem(config.values, "VOICE_MAPPING", original)
    episode_id = _create_episode_to_script_ready(client, "[HOST_A] First turn. [HOST_B] Second turn.")

    monkeypatch.setattr(comfyui, "submit_prompt", lambda url, prompt, client_id: "prompt-x")
    calls = {"n": 0}

    def flaky_poll(url, prompt_id, *, timeout_s, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"outcome": "succeeded", "outputs": {_save_node_id(): {"audio": [{"filename": "c1.flac", "subfolder": "podcast_foundry/x"}]}}}
        raise RuntimeError("simulated process crash mid chunk 2")

    monkeypatch.setattr(comfyui, "poll_history", flaky_poll)
    job_id = render.start_render_job(episode_id)["job_id"]
    # Not frozen at job creation — only once the worker actually runs.
    assert render.get_latest_job_for_episode(episode_id)["voice_mapping_snapshot"] is None
    with pytest.raises(RuntimeError, match="simulated process crash"):
        render._run_job(job_id)
    snapshot = render.get_latest_job_for_episode(episode_id)["voice_mapping_snapshot"]
    assert json.loads(snapshot) == original

    # VOICE_MAPPING changes between the crash and the resume.
    monkeypatch.setitem(config.values, "VOICE_MAPPING", {"HOST_A": "voice_c.wav", "HOST_B": "voice_d.wav"})
    monkeypatch.setattr(comfyui, "poll_history", _fake_success("podcast_foundry/x", "c2.flac"))
    render._run_job(job_id)

    job = render.get_latest_job_for_episode(episode_id)
    assert job["status"] == "succeeded"
    assert job["voice_mapping_snapshot"] == snapshot  # never overwritten on resume
    chunks = render.job_progress(job_id)["chunks"]
    # The resumed chunk rendered under the job's frozen mapping, so the
    # episode doesn't switch voices half-way through.
    assert [c["voice_reference_used"] for c in chunks] == ["voice_a.wav", "voice_b.wav"]


def test_progress_metrics_and_stall_flag_for_dashboard(client, monkeypatch):
    episode_id = _create_episode_to_script_ready(client, "[HOST_A] First turn here. [HOST_B] Second turn.")
    monkeypatch.setattr(comfyui, "submit_prompt", lambda url, prompt, client_id: "prompt-x")

    def crash_on_second(url, prompt_id, *, timeout_s, **kwargs):
        if crash_on_second.calls:
            raise RuntimeError("worker died mid chunk 2")
        crash_on_second.calls += 1
        return {"outcome": "succeeded", "outputs": {_save_node_id(): {"audio": [{"filename": "c1.flac", "subfolder": "x"}]}}}
    crash_on_second.calls = 0
    monkeypatch.setattr(comfyui, "poll_history", crash_on_second)

    job_id = render.start_render_job(episode_id)["job_id"]
    with pytest.raises(RuntimeError):
        render._run_job(job_id)

    m = render.job_progress(job_id)["metrics"]
    assert m["percent"] == 50.0
    assert m["words_total"] == 5 and m["words_done"] == 3
    assert m["eta"] is not None
    assert m["current"]["chunk_index"] == 1 and m["current"]["speaker"] == "HOST_B"
    assert m["current"]["stalled"] is False

    # Chunk 2 "started" a day ago: far past the poll give-up point, so the
    # dashboard must say the worker is gone instead of counting down.
    conn = db.get_connection()
    conn.execute("UPDATE render_chunks SET started_at = '2000-01-01T00:00:00+00:00' WHERE job_id = ? AND chunk_index = 1", (job_id,))
    conn.commit()
    conn.close()
    assert render.job_progress(job_id)["metrics"]["current"]["stalled"] is True

    body = client.get(f"/api/episodes/{episode_id}/render/status").json()
    assert body["episode"]["title"] == "Render Test Episode"
    page = client.get(f"/episodes/{episode_id}/render").text
    assert 'id="render-dash"' in page and "/static/render.js" in page


# --- Hold / resume / discard ----------------------------------------------------

@pytest.fixture()
def comfy_calls(monkeypatch):
    calls = {"cancel": [], "free": 0}
    monkeypatch.setattr(comfyui, "cancel_prompt", lambda url, pid: calls["cancel"].append(pid))

    def fake_free(url):
        calls["free"] += 1

    monkeypatch.setattr(comfyui, "free_memory", fake_free)
    monkeypatch.setattr(comfyui, "fetch_finished_result", lambda url, pid: None)
    monkeypatch.setattr(comfyui, "queue_position", lambda url, pid: None)
    return calls


def _hold_during_second_chunk(job_ref: dict, output_dir=None):
    """poll_history fake: chunk 1 succeeds (writing its file when
    output_dir is given); during chunk 2 the job is put on hold, and the
    poll reports "stopped" the way the real one does via should_stop."""
    save_node_id = _save_node_id()
    state = {"n": 0}

    def _poll(url, prompt_id, *, timeout_s, should_stop=None, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            sub = f"podcast_foundry/ep{job_ref['episode_id']}_job{job_ref['job_id']}"
            if output_dir is not None:
                d = output_dir / sub.split("/", 1)[1]
                d.mkdir(parents=True, exist_ok=True)
                (d / "c1.flac").write_bytes(b"x" * 1000)
            return {"outcome": "succeeded", "outputs": {save_node_id: {"audio": [{"filename": "c1.flac", "subfolder": sub}]}}}
        render.hold_render_job(job_ref["job_id"])
        assert should_stop is not None and should_stop()
        return {"outcome": "stopped"}

    return _poll


def test_hold_stops_mid_chunk_keeps_progress_and_resume_finishes(client, monkeypatch, comfy_calls):
    episode_id = _create_episode_to_script_ready(client, "[HOST_A] First turn. [HOST_B] Second turn.")
    monkeypatch.setattr(comfyui, "submit_prompt", lambda url, prompt, client_id: "prompt-x")
    result = render.start_render_job(episode_id)
    ref = {"episode_id": episode_id, "job_id": result["job_id"]}
    monkeypatch.setattr(comfyui, "poll_history", _hold_during_second_chunk(ref))

    render._run_job(ref["job_id"])  # returns once the hold is seen

    assert render.get_latest_job_for_episode(episode_id)["status"] == "held"
    chunks = render.job_progress(ref["job_id"])["chunks"]
    assert chunks[0]["status"] == "succeeded"
    assert chunks[1]["status"] == "pending"
    assert chunks[1]["attempt_count"] == 0  # a hold is not a failed attempt
    assert comfy_calls["cancel"] == ["prompt-x"] and comfy_calls["free"] == 1

    render.resume_held_job(ref["job_id"])
    monkeypatch.setattr(comfyui, "poll_history", _fake_success("podcast_foundry/x", "c2.flac"))
    render._run_job(ref["job_id"])
    progress = render.job_progress(ref["job_id"])
    assert progress["counts"]["succeeded"] == 2
    assert render.get_latest_job_for_episode(episode_id)["status"] == "succeeded"


def test_held_job_keeps_the_gpu_slot(client, monkeypatch, comfy_calls):
    first = _create_episode_to_script_ready(client, "[HOST_A] One.")
    second = _create_episode_to_script_ready(client, "[HOST_A] Two.")
    job_id = render.start_render_job(first)["job_id"]
    render.hold_render_job(job_id)
    with pytest.raises(render.AlreadyRenderingError):
        render.start_render_job(second)
    with pytest.raises(render.RenderError, match="not running"):
        render.hold_render_job(job_id)  # already held
    render.resume_held_job(job_id)
    with pytest.raises(render.RenderError, match="not on hold"):
        render.resume_held_job(job_id)


def test_discard_deletes_saved_chunks_and_resets_episode(client, monkeypatch, comfy_calls, tmp_path):
    out = tmp_path / "out" / "podcast_foundry"
    out.mkdir(parents=True)
    monkeypatch.setitem(config.values, "OUTPUT_FOLDER", str(out))
    outside = tmp_path / "out" / "keep_me.flac"
    outside.write_bytes(b"keep")

    episode_id = _create_episode_to_script_ready(client, "[HOST_A] First turn. [HOST_B] Second turn.")
    monkeypatch.setattr(comfyui, "submit_prompt", lambda url, prompt, client_id: "prompt-x")
    result = render.start_render_job(episode_id)
    ref = {"episode_id": episode_id, "job_id": result["job_id"]}
    monkeypatch.setattr(comfyui, "poll_history", _hold_during_second_chunk(ref, out))
    render._run_job(ref["job_id"])
    job_dir = out / f"ep{episode_id}_job{ref['job_id']}"
    (job_dir / "late_take.flac").write_bytes(b"y" * 500)  # saved after the app gave up on it

    resp = client.post(
        f"/episodes/{episode_id}/render/discard?back=/episodes/{episode_id}/render",
        follow_redirects=False,
    )
    assert resp.status_code == 303 and resp.headers["location"] == f"/episodes/{episode_id}/render"

    assert not job_dir.exists()
    assert outside.read_bytes() == b"keep"
    assert render.get_latest_job_for_episode(episode_id)["status"] == "discarded"
    assert render.job_progress(ref["job_id"])["counts"]["total"] == 0
    ep = episodes.get_episode(episode_id)
    assert ep["status"] == "script_ready" and ep["autopilot"] == 0
    assert "2 saved chunk file(s) deleted" in ep["autopilot_note"]
    # The episode can be rendered again from scratch.
    assert "Start render" in client.get(f"/episodes/{episode_id}/render").text
    assert render.start_render_job(episode_id)["status"] == "running"


def test_succeeded_render_cannot_be_discarded(client, monkeypatch, comfy_calls):
    episode_id = _create_episode_to_script_ready(client, "[HOST_A] Hello.")
    monkeypatch.setattr(comfyui, "submit_prompt", lambda url, prompt, client_id: "p")
    monkeypatch.setattr(comfyui, "poll_history", _fake_success("podcast_foundry/x", "c.flac"))
    job_id = render.start_render_job(episode_id)["job_id"]
    render._run_job(job_id)
    with pytest.raises(render.RenderError, match="cannot be discarded"):
        render.discard_render_job(job_id)


def test_queued_episode_page_shows_controls_for_the_active_render(client, monkeypatch, comfy_calls):
    active = _create_episode_to_script_ready(client, "[HOST_A] One.")
    queued = _create_episode_to_script_ready(client, "[HOST_A] Two.")
    render.start_render_job(active)

    page = client.get(f"/episodes/{queued}/render").text
    assert f'action="/episodes/{active}/render/hold?back=/episodes/{queued}/render"' in page.replace("%2F", "/")

    resp = client.post(f"/episodes/{active}/render/hold?back=/episodes/{queued}/render", follow_redirects=False)
    assert resp.headers["location"] == f"/episodes/{queued}/render"
    page = client.get(f"/episodes/{queued}/render").text
    assert "On hold" in page and "/render/resume" in page and "Discard saved render" in page

    # An off-site "back" is ignored.
    resp = client.post(f"/episodes/{active}/render/resume?back=//evil.example", follow_redirects=False)
    assert resp.headers["location"] == f"/episodes/{active}/render"
    assert render.get_latest_job_for_episode(active)["status"] == "running"


def test_rendering_button_leads_to_the_live_render(client, monkeypatch):
    # No render ever: the shortcut falls back to the upload page.
    resp = client.get("/rendering", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/upload"
    assert 'href="/rendering"' in client.get("/").text  # header button on every page

    episode_id = _create_episode_to_script_ready(client, "[HOST_A] One turn. [HOST_B] Another.")
    monkeypatch.setattr(comfyui, "submit_prompt", lambda url, prompt, client_id: "prompt-x")
    render.start_render_job(episode_id)  # running, worker thread stubbed out

    resp = client.get("/rendering", follow_redirects=False)
    assert resp.headers["location"] == f"/episodes/{episode_id}/render"

    status_page = client.get("/").text
    assert "Open rendering screen" in status_page
    assert f'href="/episodes/{episode_id}/render"' in status_page
    assert 'class="live-dot"' in status_page


def test_library_lists_finished_episode_with_player_and_downloads(client, tmp_path):
    episode_id = _create_episode_to_script_ready(client, "[HOST_A] Done already.")
    mp3 = tmp_path / "finished_archive_192k.mp3"
    mp3.write_bytes(b"ID3" + b"\0" * 2000)
    conn = db.get_connection()
    conn.execute(
        "UPDATE episodes SET status = 'delivered', archive_mp3_path = ?, "
        "wav_master_path = ?, measured_duration_seconds = 1560 WHERE id = ?",
        (str(mp3), str(tmp_path / "missing_master.wav"), episode_id),
    )
    conn.commit()
    conn.close()

    page = client.get("/episodes?status=finished").text
    assert f'src="/episodes/{episode_id}/download/archive_mp3?inline=1"' in page
    assert "MP3 (192k)" in page and str(mp3) in page
    assert "WAV master" not in page  # path in DB but file gone: no broken link
    assert "26.0 min" in page
    assert "Render Test Episode" not in client.get("/episodes?status=in_progress").text

    played = client.get(f"/episodes/{episode_id}/download/archive_mp3?inline=1")
    assert played.status_code == 200 and played.headers["content-disposition"].startswith("inline")
    saved = client.get(f"/episodes/{episode_id}/download/archive_mp3")
    assert saved.headers["content-disposition"].startswith("attachment")


def _finished_episode_with_files(client, out_root, share_root, title_script="[HOST_A] Done already."):
    episode_id = _create_episode_to_script_ready(client, title_script)
    out_dir = out_root / f"2026-10-14_render-test-episode"
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for column, name in (("wav_master_path", "ep_master.wav"), ("archive_mp3_path", "ep_archive_192k.mp3"),
                         ("email_mp3_path", "ep_email_96k.mp3")):
        f = out_dir / name
        f.write_bytes(b"\0" * 5000)
        files[column] = str(f)
    share_copy = share_root / f"render-test-episode_ep_email_96k.mp3"
    share_copy.write_bytes(b"\0" * 5000)
    conn = db.get_connection()
    conn.execute(
        "UPDATE episodes SET status='delivered', output_dir=?, wav_master_path=?, archive_mp3_path=?, "
        "email_mp3_path=? WHERE id=?",
        (str(out_dir), files["wav_master_path"], files["archive_mp3_path"], files["email_mp3_path"], episode_id),
    )
    conn.commit()
    conn.close()
    return episode_id, out_dir, share_copy


def test_delete_episode_removes_files_rows_and_share_copy(client, tmp_path, monkeypatch):
    out_root, share_root = tmp_path / "out", tmp_path / "share"
    share_root.mkdir()
    monkeypatch.setitem(config.values, "OUTPUT_FOLDER", str(out_root))
    monkeypatch.setitem(config.values, "SHARE_LOCATION", str(share_root))
    monkeypatch.setattr(comfyui, "free_memory", lambda url: None)
    outside = tmp_path / "not_ours.mp3"
    outside.write_bytes(b"keep me")

    episode_id, out_dir, share_copy = _finished_episode_with_files(client, out_root, share_root)
    keep_id, _, _ = _finished_episode_with_files(client, out_root, share_root)  # same folder
    conn = db.get_connection()
    conn.execute("UPDATE episodes SET wav_master_path = ? WHERE id = ?", (str(outside), episode_id))
    conn.commit()
    conn.close()

    # Another episode still points at the same folder/files: nothing shared goes.
    resp = client.post(f"/episodes/{episode_id}/delete?back=/episodes?status=finished", follow_redirects=False)
    assert resp.status_code == 303 and "status=finished" in resp.headers["location"]
    assert "notice=" in resp.headers["location"]
    assert episodes.get_episode(episode_id) is None
    assert out_dir.is_dir() and share_copy.exists()
    assert outside.exists()  # outside OUTPUT_FOLDER: never deleted

    # Last owner: folder, files and share copy are all removed.
    client.post(f"/episodes/{keep_id}/delete")
    assert episodes.get_episode(keep_id) is None
    assert not out_dir.exists() and not share_copy.exists()
    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM render_jobs WHERE episode_id IN (?, ?)", (episode_id, keep_id)).fetchone()[0] == 0
    conn.close()


def test_delete_stops_a_running_render_and_library_offers_stop(client, monkeypatch):
    episode_id = _create_episode_to_script_ready(client, "[HOST_A] One turn. [HOST_B] Another.")
    monkeypatch.setattr(comfyui, "submit_prompt", lambda url, prompt, client_id: "prompt-x")
    stopped = []
    monkeypatch.setattr(comfyui, "cancel_prompt", lambda url, pid: stopped.append(pid))
    monkeypatch.setattr(comfyui, "free_memory", lambda url: stopped.append("free"))
    render.start_render_job(episode_id)

    page = client.get("/episodes").text
    assert f'action="/episodes/{episode_id}/render/discard?back=' in page
    assert "Stop rendering" in page and "Delete episode" in page

    resp = client.post(f"/episodes/{episode_id}/delete", follow_redirects=False)
    assert "render%20stopped" in resp.headers["location"]
    assert "free" in stopped
    assert episodes.get_episode(episode_id) is None
    assert render.get_system_active_job() is None  # GPU slot released for the queue
