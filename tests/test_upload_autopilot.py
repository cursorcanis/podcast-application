"""Upload Script + autopilot: one uploaded file -> a delivered episode.

ComfyUI is stubbed (each fake chunk is a real FLAC tone written by ffmpeg);
mastering, QA and the share-folder handoff run for real, same approach as
tests/test_postprod_qa.py. Also covers the ComfyUI timeout fix: a chunk still
running at the deadline is waited on, a given-up prompt is cancelled in
ComfyUI, and a retry adopts audio that finished after the app gave up.
"""
from __future__ import annotations

import io
import pathlib
import shutil
import subprocess
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

import app as app_module
from app import autopilot, comfyui, episodes, postprod, render, script_import, workflow
from app.config import config

LONG_SCRIPT = (
    "# Part one\n\n"
    "Welcome to the show. Today we look at **how things work** and why it matters. "
    "[MUSIC IN] This is the first paragraph of a plain prose script with enough words to "
    "count as an episode.\n\n"
    "---\n\n"
    "And this is the second section, with a [link](https://example.com) that should be "
    "read as plain words only. Thanks for listening.\n"
)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setitem(config.values, "SHARE_LOCATION", str(tmp_path / "share"))
    (tmp_path / "share").mkdir()
    monkeypatch.setitem(config.values, "OUTPUT_FOLDER", str(tmp_path / "comfy_output"))
    monkeypatch.setitem(config.values, "EMAIL_METHOD", None)
    monkeypatch.setattr(render, "_start_thread", lambda job_id: None)
    monkeypatch.setattr(autopilot, "start", lambda episode_id: None)
    monkeypatch.setattr(
        comfyui, "check_reachability",
        lambda url, **kw: {"reachable": True, "url": url, "error_detail": None},
    )
    return tmp_path


# --- script_import ------------------------------------------------------------

def test_clean_script_strips_markdown_and_stage_directions():
    out = script_import.prepare_script(LONG_SCRIPT, narrator="HOST_A")
    assert out.text.startswith("[HOST_A]")
    assert "[PAUSE:1.0s] Part one." in out.text
    assert "[PAUSE:1.5s]" in out.text
    assert "MUSIC" not in out.text and "**" not in out.text
    assert "https://" not in out.text and "link that should" in out.text
    assert out.speakers == ["HOST_A"]
    assert 50 < out.word_count < 80


def test_tags_and_speaker_labels_survive_cleanup():
    raw = "HOST_A: Hello there, friend.\n[pause:2s]\n[host_b] Hi back. [NARRATOR] Narrated _aside_ here."
    out = script_import.prepare_script("\n".join([raw] * 3), narrator="HOST_B")
    assert out.text.count("[HOST_A] Hello there") == 3
    assert "[PAUSE:2s]" in out.text
    assert "[HOST_B] Hi back." in out.text
    assert "[NARRATOR]" not in out.text  # mapped to the chosen narrator
    assert "Narrated aside here" in out.text
    assert out.speakers == ["HOST_A", "HOST_B"]


def _docx_bytes(paragraphs: list[tuple[str, str | None]]) -> bytes:
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = ""
    for text, style in paragraphs:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body += f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"
    xml = f'<?xml version="1.0"?><w:document {ns}><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_docx_upload_is_read_with_headings():
    data = _docx_bytes([("Chapter One", "Heading1"), ("The body text of the chapter goes here. " * 3, None)])
    text = script_import.decode_upload("My Script.docx", data)
    out = script_import.prepare_script(text, narrator="HOST_A")
    assert "[PAUSE:1.0s] Chapter One." in out.text
    assert "The body text of the chapter goes here." in out.text


def test_upload_rejections_are_specific():
    with pytest.raises(script_import.ScriptImportError, match="not a supported"):
        script_import.decode_upload("script.pdf", b"%PDF")
    with pytest.raises(script_import.ScriptImportError, match="speakable word"):
        script_import.prepare_script("Too short.", narrator="HOST_A")
    with pytest.raises(script_import.ScriptImportError, match="not a valid Word"):
        script_import.decode_upload("x.docx", b"not a zip")


def test_windows_1252_text_file_is_decoded():
    text = script_import.decode_upload("s.txt", "Caf\xe9 — the end".encode("cp1252"))
    assert text == "Caf\xe9 — the end"


# --- upload route ---------------------------------------------------------------

def test_upload_route_creates_script_ready_autopilot_episode(env):
    with TestClient(app_module.app) as client:
        resp = client.post(
            "/upload",
            files={"script_file": ("my_great_episode.md", LONG_SCRIPT.encode(), "text/markdown")},
            data={"narrator": "HOST_A", "recipient_emails": [config.default_recipients[0]]},
            follow_redirects=False,
        )
        assert resp.status_code == 303, resp.text
        episode_id = int(resp.headers["location"].rsplit("/", 1)[1])
        ep = episodes.get_episode(episode_id)
        assert ep["status"] == "script_ready" and ep["autopilot"] == 1
        assert ep["title"] == "My great episode"
        assert episodes.latest_episode_documents(episode_id)["script"]["body"].startswith("[HOST_A]")
        assert client.get(f"/episodes/{episode_id}").status_code == 200
        assert client.get("/upload").status_code == 200


def test_upload_route_rejects_unmapped_speaker(env):
    with TestClient(app_module.app) as client:
        resp = client.post(
            "/upload",
            files={"script_file": ("s.txt", ("[GUEST] " + "word " * 40).encode(), "text/plain")},
            data={"recipient_emails": [config.default_recipients[0]]},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert "/upload?error=" in resp.headers["location"]
    assert "GUEST" in resp.headers["location"]
    assert episodes.list_episodes() == []


# --- autopilot end to end --------------------------------------------------------

def _fake_comfyui(monkeypatch, *, fail_first_n_polls: int = 0, seconds: float = 6.0):
    save_node_id = workflow.resolve_roles(workflow.load_workflow(config.path_to_workflow_json)).save_node_id
    state = {"submits": 0, "polls": 0}

    def fake_submit(url, prompt, client_id):
        state["submits"] += 1
        return f"prompt-{state['submits']}"

    def fake_poll(url, prompt_id, *, timeout_s, **kwargs):
        state["polls"] += 1
        if state["polls"] <= fail_first_n_polls:
            return {"outcome": "failed", "error_detail": "simulated ComfyUI error"}
        filename = f"{prompt_id}.flac"
        out = pathlib.Path(config.output_folder) / "ap" / filename
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             f"aevalsrc=0.4*sin(2*PI*330*t):duration={seconds}:sample_rate=24000", "-ac", "1", str(out)],
            check=True, capture_output=True,
        )
        return {"outcome": "succeeded",
                "outputs": {save_node_id: {"audio": [{"filename": filename, "subfolder": "podcast_foundry/ap"}]}}}

    monkeypatch.setattr(comfyui, "submit_prompt", fake_submit)
    monkeypatch.setattr(comfyui, "poll_history", fake_poll)
    monkeypatch.setattr(comfyui, "fetch_finished_result", lambda url, pid: None)
    monkeypatch.setattr(comfyui, "queue_position", lambda url, pid: None)
    return state


def _drive_to_end(episode_id: int, max_steps: int = 40) -> None:
    for _ in range(max_steps):
        job = render.get_latest_job_for_episode(episode_id)
        if job is not None and job["status"] == "running":
            render._run_job(job["id"])  # the worker thread, run inline
        if not autopilot.advance(episode_id).keep_going:
            return
    raise AssertionError(f"autopilot did not finish: {episodes.get_episode(episode_id)}")


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg/ffprobe not on PATH")
def test_autopilot_takes_upload_all_the_way_to_share_folder(env, monkeypatch):
    _fake_comfyui(monkeypatch)
    episode_id = autopilot.create_from_upload(
        filename="episode.md", data=LONG_SCRIPT.encode(), recipient_emails=[config.default_recipients[0]],
    )
    _drive_to_end(episode_id)
    ep = episodes.get_episode(episode_id)
    assert ep["status"] == "delivered_paused", ep["autopilot_note"]
    assert ep["qa_status"] == "pass"
    assert ep["autopilot"] == 0 and ep["autopilot_note"].startswith("Done")
    assert list((env / "share").glob("*.mp3"))


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg/ffprobe not on PATH")
def test_autopilot_resumes_a_failed_render_keeping_finished_chunks(env, monkeypatch):
    # Every attempt on the first chunk fails -> job fails -> autopilot resumes it.
    state = _fake_comfyui(monkeypatch, fail_first_n_polls=render.MAX_CHUNK_ATTEMPTS)
    episode_id = autopilot.create_from_upload(
        filename="episode.txt", data=LONG_SCRIPT.encode(), recipient_emails=[config.default_recipients[0]],
    )
    _drive_to_end(episode_id)
    ep = episodes.get_episode(episode_id)
    assert ep["status"] == "delivered_paused", ep["autopilot_note"]
    assert ep["autopilot_render_retries"] == 1
    assert state["submits"] == render.MAX_CHUNK_ATTEMPTS + len(
        render.job_progress(render.get_latest_job_for_episode(episode_id)["id"])["chunks"]
    )


def test_autopilot_waits_for_comfyui_and_queues_uploads_in_order(env, monkeypatch):
    monkeypatch.setattr(
        comfyui, "check_reachability",
        lambda url, **kw: {"reachable": False, "url": url, "error_detail": "ConnectError: refused"},
    )
    first = autopilot.create_from_upload(filename="a.txt", data=LONG_SCRIPT.encode(),
                                         recipient_emails=[config.default_recipients[0]])
    second = autopilot.create_from_upload(filename="b.txt", data=LONG_SCRIPT.encode(),
                                          recipient_emails=[config.default_recipients[0]])
    step = autopilot.advance(first)
    assert step.keep_going and render.get_latest_job_for_episode(first) is None
    assert "Waiting for ComfyUI" in episodes.get_episode(first)["autopilot_note"]
    autopilot.advance(second)
    assert "uploaded first" in episodes.get_episode(second)["autopilot_note"]


def test_autopilot_auto_confirms_over_cap_render(env, monkeypatch):
    monkeypatch.setitem(config.values, "MAX_RENDER_HOURS", 0.0001)
    episode_id = autopilot.create_from_upload(filename="a.txt", data=LONG_SCRIPT.encode(),
                                              recipient_emails=[config.default_recipients[0]])
    autopilot.advance(episode_id)
    job = render.get_latest_job_for_episode(episode_id)
    assert job["status"] == "running" and job["confirmed_over_cap"] == 1
    assert "confirmed automatically" in episodes.get_episode(episode_id)["autopilot_note"]


def test_email_bitrate_steps_down_to_fit_attachment_cap():
    assert postprod.email_bitrate_for(20 * 60, 20) == 96
    assert postprod.email_bitrate_for(33 * 60, 20) == 80
    assert postprod.email_bitrate_for(40 * 60, 20) == 64
    assert postprod.email_bitrate_for(300 * 60, 20) == postprod.EMAIL_BITRATE_FLOOR_K


# --- ComfyUI timeout handling ---------------------------------------------------------

class _Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _patch_transport(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(
        comfyui.httpx, "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )


def test_poll_waits_while_queued_then_succeeds(monkeypatch):
    clock = _Clock()

    def handler(request):
        if request.url.path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": [[1, "p1", {}, {}, []]]})
        if clock.t < 100:
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"p1": {"status": {"completed": True, "status_str": "success"},
                                                "outputs": {"3": {}}}})

    _patch_transport(monkeypatch, handler)
    result = comfyui.poll_history("http://comfy", "p1", timeout_s=30, poll_interval_s=5,
                                  sleep=clock.sleep, now=clock.now)
    assert result["outcome"] == "succeeded"


def test_poll_gives_up_on_stuck_running_prompt_and_interrupts_it(monkeypatch):
    clock = _Clock()
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/queue":
            return httpx.Response(200, json={"queue_running": [[1, "p1", {}, {}, []]], "queue_pending": []})
        if request.url.path == "/interrupt":
            return httpx.Response(200)
        return httpx.Response(200, json={})

    _patch_transport(monkeypatch, handler)
    result = comfyui.poll_history("http://comfy", "p1", timeout_s=30, poll_interval_s=5,
                                  sleep=clock.sleep, now=clock.now)
    assert result["outcome"] == "timed_out"
    assert clock.t >= 30 * (1 + comfyui.RUNNING_GRACE_MULTIPLIER)
    assert "/interrupt" in calls


def test_retry_adopts_audio_that_finished_after_timeout(env, monkeypatch):
    save_node_id = workflow.resolve_roles(workflow.load_workflow(config.path_to_workflow_json)).save_node_id
    submits = []
    monkeypatch.setattr(comfyui, "submit_prompt", lambda url, p, cid: submits.append(1) or f"p{len(submits)}")
    monkeypatch.setattr(comfyui, "poll_history",
                        lambda url, pid, **kw: {"outcome": "timed_out", "error_detail": "slow"})
    finished = {"outcome": "succeeded",
                "outputs": {save_node_id: {"audio": [{"filename": "late.flac", "subfolder": "podcast_foundry/x"}]}}}
    monkeypatch.setattr(comfyui, "fetch_finished_result", lambda url, pid: finished)
    monkeypatch.setattr(comfyui, "queue_position", lambda url, pid: None)

    episode_id = autopilot.create_from_upload(
        filename="a.txt", data=("[HOST_A] " + "Short line here. " * 10).encode(),
        recipient_emails=[config.default_recipients[0]],
    )
    job = render.start_render_job(episode_id)
    render._run_job(job["job_id"])
    progress = render.job_progress(job["job_id"])
    assert progress["job"]["status"] == "succeeded"
    assert len(submits) == 1  # timed out once, then adopted — never rendered twice
    assert progress["chunks"][0]["output_wav_path"].endswith("late.flac")


# --- Chatterbox length ceiling and re-render seeds ----------------------------------

@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg/ffprobe not on PATH")
def test_chunk_cut_off_at_token_ceiling_is_split_and_rerendered(env, monkeypatch):
    wf = workflow.load_workflow(config.path_to_workflow_json)
    roles = workflow.resolve_roles(wf)
    ceiling = workflow.max_audio_seconds(wf, roles)
    assert ceiling == pytest.approx(34.0)
    texts = []

    def fake_submit(url, prompt, client_id):
        texts.append(prompt[roles.tts_node_id]["inputs"]["text"])
        return f"p{len(texts)}"

    def fake_poll(url, prompt_id, **kw):
        text = texts[int(prompt_id[1:]) - 1]
        # Multi-sentence text "hits the ceiling"; a single sentence fits.
        seconds = ceiling if text.count(".") > 1 else 4.0
        out = pathlib.Path(config.output_folder) / "cut" / f"{prompt_id}.flac"
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        f"aevalsrc=0.3*sin(2*PI*220*t):duration={seconds}:sample_rate=24000", str(out)],
                       check=True, capture_output=True)
        return {"outcome": "succeeded",
                "outputs": {roles.save_node_id: {"audio": [{"filename": out.name, "subfolder": "podcast_foundry/cut"}]}}}

    monkeypatch.setattr(comfyui, "submit_prompt", fake_submit)
    monkeypatch.setattr(comfyui, "poll_history", fake_poll)
    episode_id = autopilot.create_from_upload(
        filename="a.txt", data=b"[HOST_A] First sentence here. Second sentence here. Third sentence here. "
                                b"Fourth one too. Fifth and last sentence of this test chunk.",
        recipient_emails=[config.default_recipients[0]],
    )
    job = render.start_render_job(episode_id)
    render._run_job(job["job_id"])
    progress = render.job_progress(job["job_id"])
    assert progress["job"]["status"] == "succeeded"
    assert len(progress["chunks"]) == 5  # halved until every piece is one sentence
    assert all(c["text"].count(".") == 1 for c in progress["chunks"])
    assert " ".join(c["text"] for c in progress["chunks"]).startswith("First sentence here.")


def test_retry_uses_a_different_seed():
    wf = workflow.load_workflow(config.path_to_workflow_json)
    roles = workflow.resolve_roles(wf)
    base = wf[roles.tts_node_id]["inputs"]["seed"]
    first = workflow.build_prompt(wf, roles, text="x", filename_prefix="p", voice_reference_filename=None)
    retry = workflow.build_prompt(wf, roles, text="x", filename_prefix="p", voice_reference_filename=None,
                                  seed_offset=101)
    assert first[roles.tts_node_id]["inputs"]["seed"] == base
    assert retry[roles.tts_node_id]["inputs"]["seed"] == base + 101
    assert wf[roles.tts_node_id]["inputs"]["seed"] == base  # loaded workflow untouched
