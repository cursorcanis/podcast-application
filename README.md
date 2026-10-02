# Podcast Foundry — application

Local web application that drives the Podcast Foundry episode pipeline on the
Board's Windows machine: episode intake, the Board's source-approval gate,
script review, ComfyUI voice rendering, ffmpeg mastering, QA, and delivery.
This repo is being built milestone by milestone; see the table below for what
works today.

## Current status — M2 (episode intake, Board source gate, hand-entered script)

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
- **Episode Library** (`/episodes`) — every episode, filterable by status,
  with a detail view (settings, source-gate state, script/citation map once
  ready).
- `render_jobs` / `render_chunks` schema exists (SQLite under `data/`) for
  the M3 render pipeline; nothing writes to it yet.

Not yet: rendering, ffmpeg mastering, QA, email, voice-profile picker,
presets, saved recipient-list management. Those are M3–M6.

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

## Development notes

- The shipped app runs **natively on Windows** (see the build plan, section 1):
  it reaches ComfyUI the same way ComfyUI's own browser does, and writes the
  Board's Windows paths directly. Development/smoke-testing happened WSL-side;
  Windows-native behavior is verified only when actually run there (plan Risk
  R7), and this README says so rather than claiming it.
- `ffmpeg` is not used until M4 (mastering/export). Nothing here needs it yet.

## Project layout

```
app/__init__.py        FastAPI app + M1 routes (/ , /api/status, /api/comfyui/status, /healthz)
app/config.py          non-secret settings loader (env-first, confirmed defaults)
app/db.py              SQLite schema bootstrap (plan section 3)
app/comfyui.py         ComfyUI reachability probe (M1) and render path (M3)
app/episodes.py        M2 domain logic: intake, source gate, script review
app/routes_episodes.py M2 routes: /episodes, /episodes/{id}/sources, /episodes/{id}/script
app/web.py             shared Jinja2Templates instance
config/app_config.json   the 18 Board-confirmed values (no credentials)
templates/             Jinja2 templates (base, status, episodes_list, episode_new,
                       episode_detail, episode_sources, episode_script)
static/                style.css, status.js (measured polling, no spinner)
tests/test_smoke.py    M1 smoke tests
tests/test_episodes.py M2 smoke tests (intake -> source gate -> script_ready)
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