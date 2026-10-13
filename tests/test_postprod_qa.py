"""Integration tests for app/postprod.py and app/qa.py — M4 (POD-11).

ComfyUI is monkeypatched exactly like tests/test_render.py (submit_prompt /
poll_history never call a real server), but unlike those tests, the chunk
audio files it "returns" must actually exist on disk for ffmpeg to read —
so each fake-success call writes a real short tone WAV at the resolved
output path with real `ffmpeg`/`ffprobe` subprocesses. Everything downstream
of that (concatenation, [PAUSE] silence insertion, speed, two-pass loudnorm,
WAV/MP3 export, per-chunk clip detection, silence-gap detection, speaker-
voice matching, requeue-only-the-failing-chunks) runs for real against
on-disk SQLite + real ffmpeg, never mocked — this is the "explicitly-stubbed
render, real ffmpeg" verification named in the Done bar: ComfyUI itself is
stubbed (unreachable from this WSL session), ffmpeg mastering/QA is not.

M5 (POD-12) change to the on-pass path: a QA pass now hands the episode to
app/delivery.run_delivery_on_qa_pass, which — while EMAIL_METHOD is unset —
records a `paused` delivery row and copies the email MP3 to SHARE_LOCATION,
moving the episode from `ready` to `delivered_paused`. The pass-time
assertions below reflect that; the QA-fail paths are unchanged (no delivery
fire on a failure). Delivery-specific tests live in tests/test_delivery.py.
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
    # M5: qa.run_qa hands off to delivery on pass — delivery needs the same
    # test DB connection, and its SHARE_LOCATION must point at a tmp dir so
    # the paused-handoff copy doesn't touch the Board's real Windows folder.
    monkeypatch.setattr(delivery, "get_connection", fake_get_connection)
    monkeypatch.setitem(config.values, "SHARE_LOCATION", str(tmp_path / "share"))
    (tmp_path / "share").mkdir(parents=True, exist_ok=True)
    monkeypatch.setitem(config.values, "EMAIL_METHOD", None)
    db.init_db(db_path)
    monkeypatch.setattr(render, "_start_thread", lambda job_id: None)
    # Fake chunks here are deliberately longer than Chatterbox's 34s ceiling.
    monkeypatch.setattr(workflow, "max_audio_seconds", lambda wf, roles: None)
    monkeypatch.setitem(config.values, "OUTPUT_FOLDER", str(tmp_path / "comfy_output"))
    with TestClient(app_module.app) as c:
        yield c


def _create_episode_to_script_ready(client, script: str, *, length_minutes_target: int = 5) -> int:
    resp = client.post(
        "/episodes",
        data={
            "title": "M4 Postprod Test Episode!",
            "topic_or_query": "Testing the mastering/QA pipeline",
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
    """Stand-in for a ComfyUI-rendered chunk: a real FLAC tone clip at the
    exact path the fake `poll_history` below reports, so postprod/qa read a
    real file. amplitude > ~1.0 clips on purpose (for the clip-detection test)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"aevalsrc={amplitude}*sin(2*PI*440*t):duration={seconds}:sample_rate=24000",
            "-ac", "1", str(path),
        ],
        check=True, capture_output=True,
    )


def _render_two_chunk_episode(client, monkeypatch, *, seconds_per_chunk=35.0, clip_chunk2=False, length_minutes_target=5):
    episode_id = _create_episode_to_script_ready(
        client,
        "[HOST_A] First turn of the test episode. [PAUSE:0.5s] [HOST_B] Second turn of the test episode.",
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
        amp = 1.8 if (clip_chunk2 and n == 2) else 0.5
        _write_tone_wav(out_path, seconds=seconds_per_chunk, amplitude=amp)
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
    return episode_id


def test_postprod_and_qa_pass_produce_master_show_notes_and_handoff(client, monkeypatch):
    # 5-minute floor (episodes.MIN_LENGTH_MINUTES) -> need >= 300s of real
    # audio for the duration-floor QA check to pass honestly.
    episode_id = _render_two_chunk_episode(client, monkeypatch, seconds_per_chunk=155.0, length_minutes_target=5)

    postprod.run_postproduction(episode_id)
    episode = episodes.get_episode(episode_id)
    assert episode["status"] == "postproduction"
    assert episode["wav_master_path"]
    assert pathlib.Path(episode["wav_master_path"]).exists()
    assert pathlib.Path(episode["archive_mp3_path"]).exists()
    assert pathlib.Path(episode["email_mp3_path"]).exists()

    # Master duration = 2 x 155s chunks + 0.5s inserted [PAUSE] silence.
    assert episode["measured_duration_seconds"] >= 300.0

    result = qa.run_qa(episode_id)
    assert result["overall_pass"] is True
    episode = episodes.get_episode(episode_id)
    # M5: a QA pass now hands off to delivery (paused -> delivered_paused),
    # not a bare 'ready'.
    assert episode["status"] == "delivered_paused"
    assert episode["qa_status"] == "pass"

    docs = episodes.latest_episode_documents(episode_id)
    assert docs["qa_report"] is not None and "PASS" in docs["qa_report"]["body"]
    assert docs["show_notes"] is not None
    assert "Source A" in docs["show_notes"]["body"]

    # The delivery handoff fired: a paused record + the MP3 in SHARE_LOCATION.
    assert result["delivery"] is not None
    assert result["delivery"]["status"] == "paused"
    assert result["delivery"]["sent"] is False
    copied = list(pathlib.Path(config.share_location).glob("*_email_96k.mp3"))
    assert copied, "no email MP3 copied into SHARE_LOCATION"


def test_qa_fails_on_duration_floor_without_blocking_download(client, monkeypatch):
    # Request 10 minutes but only render ~10s of actual audio.
    episode_id = _render_two_chunk_episode(client, monkeypatch, seconds_per_chunk=5.0, length_minutes_target=10)
    postprod.run_postproduction(episode_id)
    result = qa.run_qa(episode_id)
    assert result["overall_pass"] is False
    assert result["duration_ok"] is False
    episode = episodes.get_episode(episode_id)
    assert episode["status"] == "qa_failed"
    assert episode["qa_status"] == "fail"
    # Mastered files still exist / be downloadable even though QA failed —
    # QA gates the episode's status, not the file's existence.
    assert pathlib.Path(episode["wav_master_path"]).exists()
    docs = episodes.latest_episode_documents(episode_id)
    assert docs["show_notes"] is None  # never written on a QA failure
    # No delivery fired on a QA failure.
    assert result["delivery"] is None
    assert delivery.latest_delivery_record(episode_id) is None


def test_qa_detects_clipping_and_requeues_only_that_chunk(client, monkeypatch):
    episode_id = _render_two_chunk_episode(
        client, monkeypatch, seconds_per_chunk=35.0, clip_chunk2=True, length_minutes_target=5
    )
    postprod.run_postproduction(episode_id)
    result = qa.run_qa(episode_id)
    assert result["overall_pass"] is False
    assert result["clipping_ok"] is False

    job = render.get_latest_job_for_episode(episode_id)
    chunks_before = render.job_progress(job["id"])["chunks"]
    chunk1_id = chunks_before[0]["id"]
    chunk2_id = chunks_before[1]["id"]
    assert chunks_before[1]["qa_clip_detected"] == 1
    assert chunks_before[0]["qa_clip_detected"] == 0
    assert result["failing_chunk_ids"] == [chunk2_id]

    retry = qa.retry_failing_chunks(episode_id)
    assert retry["requeued_chunk_ids"] == [chunk2_id]

    # Only chunk 2 went back to pending; chunk 1's succeeded state (and its
    # audio file) was left completely alone — "re-render only the failing
    # chunks, not the whole episode."
    chunks_after = render.job_progress(job["id"])["chunks"]
    by_id = {c["id"]: c for c in chunks_after}
    assert by_id[chunk1_id]["status"] == "succeeded"
    assert by_id[chunk1_id]["output_wav_path"] == chunks_before[0]["output_wav_path"]
    assert by_id[chunk2_id]["status"] == "pending"
    assert by_id[chunk2_id]["output_wav_path"] is None


def test_requeue_rejects_unknown_chunk_ids(client, monkeypatch):
    episode_id = _render_two_chunk_episode(client, monkeypatch, seconds_per_chunk=5.0, length_minutes_target=5)
    job = render.get_latest_job_for_episode(episode_id)
    with pytest.raises(render.RenderError):
        render.requeue_chunks_for_rerender(job["id"], [999999])


def test_qa_fails_on_clipping_never_fires_delivery(client, monkeypatch):
    episode_id = _render_two_chunk_episode(
        client, monkeypatch, seconds_per_chunk=35.0, clip_chunk2=True, length_minutes_target=5
    )
    postprod.run_postproduction(episode_id)
    result = qa.run_qa(episode_id)
    assert result["overall_pass"] is False
    assert result["delivery"] is None
    assert delivery.latest_delivery_record(episode_id) is None

# --- POD-110: chunk length vs. text, and the frozen voice mapping -----------------

_TEN_WORDS = "This sentence has exactly ten words in it for testing."


def _words(n_tens: int) -> str:
    return " ".join([_TEN_WORDS] * n_tens)


def _render_and_master(client, monkeypatch, turns: list[tuple[str, float]]) -> int:
    """Render one chunk per (text, seconds) turn — alternating HOST_A/HOST_B
    — as a real tone of exactly that length, then master it, so QA measures
    real files."""
    script = " ".join(
        f"[{'HOST_A' if i % 2 == 0 else 'HOST_B'}] {text}" for i, (text, _) in enumerate(turns)
    )
    episode_id = _create_episode_to_script_ready(client, script)
    save_node_id = _save_node_id()
    written = {"n": 0}

    def fake_submit(url, prompt, client_id):
        written["n"] += 1
        return f"prompt-{written['n']}"

    def fake_poll(url, prompt_id, *, timeout_s, **kwargs):
        n = written["n"]
        filename = f"chunk_{n:02d}.flac"
        _write_tone_wav(pathlib.Path(config.output_folder) / "ep_len" / filename, seconds=turns[n - 1][1])
        return {
            "outcome": "succeeded",
            "outputs": {save_node_id: {"audio": [{"filename": filename, "subfolder": "podcast_foundry/ep_len"}]}},
        }

    monkeypatch.setattr(comfyui, "submit_prompt", fake_submit)
    monkeypatch.setattr(comfyui, "poll_history", fake_poll)
    job_id = render.start_render_job(episode_id)["job_id"]
    render._run_job(job_id)
    assert len(render.job_progress(job_id)["chunks"]) == len(turns)  # one chunk per turn
    postprod.run_postproduction(episode_id)
    return episode_id


def _chunks(episode_id: int) -> list[dict]:
    return render.job_progress(render.get_latest_job_for_episode(episode_id)["id"])["chunks"]


def test_qa_flags_truncated_chunk_and_requeues_only_it(client, monkeypatch):
    episode_id = _render_and_master(client, monkeypatch, [
        (_words(4), 16.0),   # 40 words in 16s: 2.5 words/s, normal
        (_words(4), 4.0),    # 40 words in 4s: 10 words/s, cut off
        (_words(4), 14.0),
    ])
    result = qa.run_qa(episode_id)
    assert result["overall_pass"] is False
    assert result["length_ok"] is False

    chunks = _chunks(episode_id)
    assert [c["qa_length_detected"] for c in chunks] == [0, 1, 0]
    assert result["failing_chunk_ids"] == [chunks[1]["id"]]
    report = episodes.latest_episode_documents(episode_id)["qa_report"]["body"]
    assert "audio too short for its text" in report

    retry = qa.retry_failing_chunks(episode_id)
    assert retry["requeued_chunk_ids"] == [chunks[1]["id"]]
    after = {c["id"]: c for c in _chunks(episode_id)}
    assert after[chunks[1]["id"]]["status"] == "pending"
    assert after[chunks[1]["id"]]["qa_length_detected"] == 0  # cleared for the re-render
    assert after[chunks[0]["id"]]["status"] == "succeeded"


def test_qa_flags_identical_duration_cluster_at_the_length_ceiling(client, monkeypatch):
    # Episode 8's signature: every long chunk exactly the same length. Each
    # is a plausible speech rate on its own (40 words in 25s), so only the
    # tie gives them away.
    episode_id = _render_and_master(client, monkeypatch, [
        (_words(4), 25.0), (_words(4), 25.0), (_words(2), 9.0), (_words(4), 25.0),
    ])
    result = qa.run_qa(episode_id)
    assert result["length_ok"] is False

    chunks = _chunks(episode_id)
    assert [c["qa_length_detected"] for c in chunks] == [1, 1, 0, 1]
    assert set(result["failing_chunk_ids"]) == {chunks[0]["id"], chunks[1]["id"], chunks[3]["id"]}
    report = episodes.latest_episode_documents(episode_id)["qa_report"]["body"]
    assert "one of 3 chunks all 25.000s long" in report


def test_qa_length_check_leaves_normal_speech_alone(client, monkeypatch):
    episode_id = _render_and_master(client, monkeypatch, [
        (_words(4), 10.5),   # brisk: 3.8 words/s (~230 wpm), still natural
        (_words(4), 18.0),
        ("Yeah, right, exactly, of course.", 1.0),  # 5 words/s, but too short to judge
        (_words(4), 18.0),   # two chunks tied at the longest is chance, not a ceiling
        (_words(3), 13.0),
    ])
    result = qa.run_qa(episode_id)
    assert result["length_ok"] is True
    assert all(c["qa_length_detected"] == 0 for c in _chunks(episode_id))
    assert result["failing_chunk_ids"] == []


def test_qa_voice_check_uses_the_jobs_snapshot_not_the_current_mapping(client, monkeypatch):
    monkeypatch.setitem(config.values, "VOICE_MAPPING", {"HOST_A": "voice_a.wav", "HOST_B": "voice_b.wav"})
    episode_id = _render_and_master(client, monkeypatch, [(_words(4), 16.0), (_words(4), 15.0)])

    # The Board re-maps the voices after this episode rendered (POD-109).
    monkeypatch.setitem(config.values, "VOICE_MAPPING", {"HOST_A": "voice_c.wav", "HOST_B": "voice_d.wav"})
    result = qa.run_qa(episode_id)
    assert result["voice_ok"] is True
    report = episodes.latest_episode_documents(episode_id)["qa_report"]["body"]
    assert "VOICE_MAPPING snapshot this job rendered under" in report

    # A job from before snapshots existed falls back to the current mapping.
    job_id = render.get_latest_job_for_episode(episode_id)["id"]
    conn = qa.get_connection()
    conn.execute("UPDATE render_jobs SET voice_mapping_snapshot = NULL WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()
    result = qa.run_qa(episode_id)
    assert result["voice_ok"] is False
    assert sorted(result["failing_chunk_ids"]) == sorted(c["id"] for c in _chunks(episode_id))
