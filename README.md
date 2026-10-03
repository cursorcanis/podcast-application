# Podcast Foundry — application

Local web application that drives the Podcast Foundry episode pipeline on the
Board's Windows machine: episode intake, the Board's source-approval gate,
script review, ComfyUI voice rendering, ffmpeg mastering, QA, and delivery.
This repo is being built milestone by milestone; see the table below for what
works today.

## Current status — M3 (ComfyUI render pipeline)

- FastAPI + SQLite app skeleton (M1).
- **Status / Settings** screen at `http://127.0.0.1:8000` with the live
  ComfyUI reachability probe, the configured caps, the $0/free-only budget
  banner, and the standing **email delivery paused — see POD-7** notice.
- **New Episode** (`/episodes/new`) — query/URL entry; settings (format,
  tone, cadence, speed 0.8x–1.3x, target length, audience level); voice
  label(s) (plain text — the saved voice-profile picker arrives later);
  recipients, restricted to `DEFAULT_RECIPIENTS` or a saved recipient list —
  a typed-in address outside that set is rejected server-side, not just
  hidden in the UI.
- **Source Review** (`/episodes/{id}/sources`) — the Board gate. A standing
  page, not a modal: add hand-entered/pasted candidate sources, approve or
  remove each, and close the gate once every source has a decision and at
  least one is approved. The decision persists in SQLite across sessions.
  **No later stage is reachable until the gate closes** — enforced in the
  route handlers, not only hidden in the UI, so a direct POST to a
  later-stage route is refused the same way.
- **Script Review** (`/episodes/{id}/script`) — locked until the source gate
  closes. Paste in outline/script text and a citation map produced outside
  the app for now (no automatic generation yet — open Board decision, see
  build plan Risk R5). "Mark script ready" moves the episode to
  `script_ready`.
- **Voice rendering** (`/episodes/{id}/render`) — chunks the saved script by
  speaker turn and submits each chunk serially to ComfyUI (`POST /prompt`,
  poll `/history/{id}`), one render job system-wide at a time. Node ids are
  discovered dynamically from the workflow JSON's `class_type`/wiring (see
  `app/workflow.py`) — never hardcoded, because the production Chatterbox
  node package is still a pending Board approval (POD-3). Every chunk's
  state is durable in `render_jobs`/`render_chunks`: a crash, a reboot, or a
  closed browser tab costs at most the one in-flight chunk — the app resumes
  from the first non-succeeded chunk, never from the top. A 10-minute
  per-chunk timeout, OOM backoff (halves the chunk-size target and re-splits
  only the failing chunk), and a `MAX_RENDER_HOURS` confirm-to-proceed gate
  (nothing submits to ComfyUI until the Board explicitly confirms) are all
  enforced in `app/render.py`. Progress is measured, not guessed: the
  progress screen shows real elapsed time and, once at least one chunk has
  rendered, a projection based on this job's own measured chunk times.
- **Episode Library** (`/episodes`) — every episode, filterable by status,
  with a detail view (settings, source-gate state, script/citation map,
  render progress link).

Not yet: ffmpeg mastering/export, QA, email, voice-profile picker, presets,
saved recipient-list management. Those are M4+.

## Prerequisites (Windows)

1. **Python 3.11+** on PATH (the Board already has Python to run ComfyUI).
2. **ComfyUI** running (for a green status). If it is not running, the status
   screen honestly shows **UNREACHABLE** with the fallback steps.
3. No other runtime: no Node, no npm, no database server.

## Run it

Double-click `start_app.bat` (creates a `.venv`, installs `requirements.txt`
on first run, starts uvicorn), or from a shell:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Then open <http://127.0.0.1:8000>.

## Configuration (env vars — names only, values never in the repo)

All settings read from process environment variables first, falling back to
Board-confirmed defaults in `app/config.py`. On Windows set them via
**System Properties → Environment Variables**, or copy `.env.example` to a
local `.env` (gitignored) and fill values there — never commit a value.

| Variable | Meaning | Confirmed default |
| --- | --- | --- |
| `COMFYUI_URL` | ComfyUI HTTP endpoint | `http://127.0.0.1:8188` |
| `OUTPUT_FOLDER` | episode output root (`<date>_<slug>/` subfolders) | Board path under `_ComfyUI\output\podcast_foundry` |
| `SHARE_LOCATION` | oversized-file share folder | Board path under `_podcast_application\_output_podcast_folder` |
| `MAX_RENDER_HOURS` | render cap before confirm-to-proceed | `3` |
| `CHUNK_TIMEOUT_MIN` | per-chunk timeout | `10` |
| `MAX_ATTACHMENT_MB` | email attachment limit | `20` |
| `MONTHLY_BUDGET` | budget banner ($0 = free-only) | `0` |
| `EMAIL_METHOD` | **leave empty — delivery is PAUSED (POD-7)** | empty |
| `SMTP_USER` / `SMTP_PASS` | env var **names** for future delivery; do not set until POD-7 restarts | unset |
| `PATH_TO_WORKFLOW_JSON` | ComfyUI API-format workflow file (node ids read dynamically, never hardcoded) | `config/comfyui_workflow.json` |
| `RENDER_SECONDS_PER_AUDIO_SECOND` | compute-seconds-per-audio-second ratio used for the pre-render cap projection (POD-3 benchmark) | `3.83` |
| `CHUNK_SECONDS_TARGET_DEFAULT` | audio seconds targeted per chunk before a speaker turn is split further | `60` |

`VOICE_MAPPING` (speaker tag -> reference clip filename) lives in
`config/app_config.json` only — it is a dict, not a simple scalar, so it is
not in the env-override table above; edit the JSON file directly to change it.

No credential value is stored, logged, or displayed by this application.

## Tests

```
python -m pytest tests/ -q
```

`tests/test_smoke.py` checks the M1 Status screen and the ComfyUI probe.
`tests/test_episodes.py` walks one episode from intake through the source
gate and a pasted script to `script_ready` (the M2 demoable outcome), and
checks the gate's hard boundaries: a recipient outside `DEFAULT_RECIPIENTS`
is rejected, an out-of-range speed is rejected, and script review/source
mutation stay refused outside their valid episode status, even via a direct
POST.

`tests/test_chunking.py` and `tests/test_workflow.py` are pure unit tests
(no DB, no network) for script chunking and dynamic ComfyUI node discovery.
`tests/test_render.py` drives the actual render_jobs/render_chunks pipeline
end to end with ComfyUI itself monkeypatched (`app.comfyui.submit_prompt` /
`poll_history`) — it proves the one-job-system-wide constraint, the
MAX_RENDER_HOURS confirm-before-submit gate, and, by raising mid-chunk to
simulate a crash and then re-running the worker loop, that resuming never
re-renders an already-succeeded chunk and never duplicates chunk rows.

**Also verified against the Board's real, live ComfyUI** (not just
monkeypatched): a two-chunk two-host episode rendered end to end through the
actual app routes, producing real FLAC audio files on disk (confirmed with
`file(1)`) with two distinct voices and durations. Separately, a
three-chunk render was started, the app process was `kill -9`'d immediately
after chunk 1 succeeded and while chunk 2 was mid-flight, then the process
was restarted: chunk 1 resumed with its *original* ComfyUI prompt_id
(proving it was not re-submitted), chunk 2 got a *new* prompt_id (proving
the in-flight chunk — and only that chunk — was re-rendered), and chunk 3
rendered normally afterward to a succeeded job. This is the milestone's
demoable outcome, run for real, not simulated.

## Development notes

- The shipped app runs **natively on Windows** (see the build plan, section 1):
  it reaches ComfyUI the same way ComfyUI's own browser does, and writes the
  Board's Windows paths directly. Development/smoke-testing (including the
  real-ComfyUI render proof above) happened WSL-side, through the WSL↔Windows
  loopback relay documented on POD-3 (`COMFYUI_URL` pointed at the relay
  address for that session only, via an env var override — never committed).
  Windows-native behavior is verified only when actually run there (plan Risk
  R7), and this README says so rather than claiming it.
- `ffmpeg` is not used until M4 (mastering/export). Nothing here needs it yet.
- The render worker runs in a background thread inside the same FastAPI
  process (one worker, one job — the GPU is the bottleneck, so there is no
  separate task queue to run or configure). It is **not** restarted by a
  code change or `--reload`; only a real process restart (or the app's own
  startup, which calls `render.resume_pending_jobs()`) picks a `running` job
  back up.

## Project layout

```
app/__init__.py        FastAPI app + M1 routes (/ , /api/status, /api/comfyui/status, /healthz)
app/config.py          non-secret settings loader (env-first, confirmed defaults)
app/db.py              SQLite schema bootstrap + additive migrations (plan section 3)
app/comfyui.py         ComfyUI reachability probe (M1), submit_prompt/poll_history (M3)
app/workflow.py        dynamic ComfyUI node discovery from the workflow JSON (M3)
app/chunking.py        script -> per-speaker-turn chunk plan (M3)
app/render.py          render_jobs/render_chunks orchestration, cap gate, OOM backoff,
                       crash/reboot resumability (M3)
app/episodes.py        M2 domain logic: intake, source gate, script review
app/routes_episodes.py M2 routes: /episodes, /episodes/{id}/sources, /episodes/{id}/script
app/routes_render.py   M3 routes: /episodes/{id}/render, start/confirm/cancel, status API
app/web.py             shared Jinja2Templates instance
config/app_config.json       the Board-confirmed values (no credentials)
config/comfyui_workflow.json the measured, working ComfyUI workflow (POD-3) — swap freely;
                             node ids are never read from code
templates/             Jinja2 templates (base, status, episodes_list, episode_new,
                       episode_detail, episode_sources, episode_script, episode_render)
static/                style.css, status.js, render.js (all measured polling, no spinner)
tests/test_smoke.py    M1 smoke tests
tests/test_episodes.py M2 smoke tests (intake -> source gate -> script_ready)
tests/test_chunking.py M3 unit tests for script chunking
tests/test_workflow.py M3 unit tests for dynamic ComfyUI node discovery
tests/test_render.py   M3 integration tests (render_jobs/render_chunks pipeline, cap gate,
                       crash/resume), ComfyUI itself monkeypatched
start_app.bat          Windows launcher
.env.example           env var NAMES only
app.py / config.py / db.py   superseded fail-loud stubs at repo root (see below)
```

> `uvicorn app:app` resolves to the **`app/` package**, not the root `app.py`
> module (a directory package shadows a same-named module). The root
> `app.py`, `config.py`, `db.py` are leftovers from a first M1 draft that
> briefly drifted into a duplicate implementation during the milestone (the
> duplicate draft tree itself has since been deleted). They now **fail loud
> on import** so a stale shell reference or tool can never silently execute
> a duplicate. The canonical implementation is exactly one place: the `app/`
> package.