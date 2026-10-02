# HANDOFF — Podcast Foundry application

This document is for moving the project to another machine (or picking it back
up after a break). It is required by the Board's configuration decision
(Paperclip [POD-2](https://github.com/cursorcanis/podcast-application) intake,
"Repository & handoff" section) and is kept current at every milestone.

## What this is

A local web app that drives the Podcast Foundry episode pipeline end to end:
episode intake, the Board's source-approval gate, script review, ComfyUI voice
rendering, ffmpeg mastering, QA, and delivery — see `README.md` for the
feature-by-feature status (currently **M1** — the Status/Settings screen only).

## Clone → running, on a clean Windows machine

1. Install **Python 3.11+** (the Board's machine already has this to run
   ComfyUI) and make sure `python` is on `PATH`.
2. `git clone https://github.com/cursorcanis/podcast-application.git`
3. Double-click `start_app.bat` inside the clone. First run creates `.venv`
   and installs `requirements.txt`; every run after that just starts the
   server.
4. Open <http://127.0.0.1:8000>. You should see the Status/Settings screen.
   If **ComfyUI** (the TTS render server) is not already running at
   `COMFYUI_URL` (default `http://127.0.0.1:8188`), the screen honestly shows
   a red **UNREACHABLE** state with the exact error and a fallback checklist —
   that is expected, not a bug, until ComfyUI is started.

No Node/npm, no database server, no other runtime dependency for this
milestone. `ffmpeg` is required starting at M4 (mastering/export), not before.

## Configuration

All settings are Board-confirmed defaults baked into `config/app_config.json`
(no credentials in that file — see its `_meta` note), overridable by process
environment variables of the same name. See the README's configuration table
for the full variable list. **Only variable names ever appear in this repo or
its history — never a value.** Set real values via Windows System Properties →
Environment Variables, or a local `.env` (already gitignored, never commit it).

Email delivery is paused by Board decision — see the Board's POD-7 ticket.
`EMAIL_METHOD` stays unset until that is reopened; nothing in this app sends
mail today.

## Repository location and push discipline

- Canonical working copy: this folder
  (`...\_desktop\_projects\_podcast_application` on the Board's machine).
- Remote: `https://github.com/cursorcanis/podcast-application.git`.
- Push periodically as milestones land — each milestone's commit should be
  something a stranger could `git clone` and run via the steps above.
- Never commit: a credential value, a `.env` with real values, the SQLite
  file under `data/`, or `__pycache__`/`.venv` (all gitignored already).

## Verification status (be honest about what has and hasn't run)

- **Verified in this repo, WSL2-side (`/mnt/c` mount of this same folder):**
  `python -m pytest tests/ -q` (9/9 passing) and a live `uvicorn` run serving
  the Status screen with a real (negative) ComfyUI reachability probe.
- **Not yet verified:** the native-Windows double-click path
  (`start_app.bat`, Windows path separators, long-path limits). WSL-side
  testing proves the Python logic; it does not prove the Windows experience.
  This is tracked as an open risk (build plan risk R7) until it is actually
  run on Windows and this section is updated to say so.

## Who owns what from here

- App/backend/UI: App Engineer.
- ComfyUI workflow JSON, TTS model, render settings: Audio Engineer (Board
  gate — this app reads those as configuration, never hardcodes them).
- Episode scope, tone, schedule: Showrunner.
- Sources and citations: Research Lead.
- Any Board decision (budget, hiring, email-provider restart): Chief of
  Podcast Operations.
