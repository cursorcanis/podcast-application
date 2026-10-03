# HANDOFF — Podcast Foundry application

This document is for moving the project to another machine (or picking it back
up after a break). It is required by the Board's configuration decision
(Paperclip [POD-2](https://github.com/cursorcanis/podcast-application) intake,
"Repository & handoff" section) and is kept current at every milestone.

## What this is

A local web app that drives the Podcast Foundry episode pipeline end to end:
episode intake, the Board's source-approval gate, script review, ComfyUI voice
rendering, ffmpeg mastering, QA, and delivery — see `README.md` for the
feature-by-feature status (currently **M5** — delivery-paused handling and the
share-folder handoff, on top of M1's Status/Settings screen, M2's episode
intake / Board source-approval gate / hand-entered script review, M3's
ComfyUI render pipeline, and M4's ffmpeg mastering/export + QA).

## Clone → running, on a clean Windows machine

1. Install **Python 3.11+** (the Board's machine already has this to run
   ComfyUI) and make sure `python` is on `PATH`.
2. Install **ffmpeg + ffprobe** and put them on `PATH` (the Board's machine
   has ffmpeg 8.0.1) — required for M4 mastering/export and QA.
3. `git clone https://github.com/cursorcanis/podcast-application.git`
4. Double-click `start_app.bat` inside the clone. First run creates `.venv`
   and installs `requirements.txt`; every run after that just starts the
   server.
5. Open <http://127.0.0.1:8000>. You should see the Status/Settings screen.
   If **ComfyUI** (the TTS render server) is not already running at
   `COMFYUI_URL` (default `http://127.0.0.1:8188`), the screen honestly shows
   a red **UNREACHABLE** state with the exact error and a fallback checklist —
   that is expected, not a bug, until ComfyUI is started.
6. Click **New Episode** in the nav, submit the intake form, and you land on
   **Source Review** (the Board gate) for that episode. Add a source,
   Approve or Remove it, then "Approve sources & close gate" once every
   source has a decision and at least one is approved. That unlocks
   **Script Review**, where you paste outline/script/citation-map text and
   click "Mark script ready." The episode now shows `script_ready` in the
   **Episode Library**.
7. From the episode page, click **Start render**. If the projected render
   time is within `MAX_RENDER_HOURS` it starts immediately; if not, you get
   an explicit confirm screen and nothing is submitted to ComfyUI until you
   confirm. The render page polls real progress (measured elapsed time and,
   once a chunk has finished, a projection based on this job's own measured
   times — never a spinner or a fake percentage). The episode moves to
   `rendered` once every chunk succeeds.
8. On the episode page, click **Run mastering & export**. The app concatenates
   the rendered chunks with real `[PAUSE]` silence, applies speed, normalizes
   to -16 LUFS, and exports the WAV master + 192kbps archive MP3 + 96kbps
   email MP3 (with ID3 tags) to `OUTPUT_FOLDER/<date>_<slug>/`. Then click
   **Run QA**, which checks duration floor, per-chunk clipping, silence gaps
   over 3s, and speaker-voice match, writes a QA report (and show notes on
   pass), and offers "Re-render only the chunks QA flagged" on a per-chunk
   failure. The mastered files are downloadable from the episode page.
9. Delivery is paused today (Board decision, POD-7): on a QA pass the email
   MP3 is copied into `SHARE_LOCATION` and the episode shows a "collect your
   episode here" notice with the exact path. The **Resend email** button is
   visible but disabled with the pause reason. No email is sent until the
   Board reopens POD-7 and sets `EMAIL_METHOD` + `SMTP_USER`/`SMTP_PASS` in
   the environment — at which point the same button activates a real send
   with no code change.

No Node/npm, no database server, no other runtime dependency for this
milestone.

## ComfyUI prerequisites for a render (M3)

- ComfyUI must be reachable at `COMFYUI_URL` (Status screen shows this live).
- The workflow file at `PATH_TO_WORKFLOW_JSON` (default
  `config/comfyui_workflow.json`) must resolve to exactly one Chatterbox-family
  node and exactly one save-type node wired to its output — `app/workflow.py`
  discovers node ids dynamically, so the Audio Engineer can swap this file for
  a different Chatterbox package (once the Board approves one — see POD-3)
  with no code change.
- Every speaker tag used in the script (`[HOST_A]`, `[HOST_B]`, ...) needs an
  entry in `VOICE_MAPPING` (`config/app_config.json`) naming the reference
  clip filename ComfyUI's voice-reference loader can find — per POD-3, that
  means the file must sit at the root of ComfyUI's configured `input/`
  directory, not a subfolder.

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
  `python -m pytest tests/ -q` — 45 tests pass across the M1…M5 suites. The
  M4 tests (`tests/test_postprod_qa.py`) cover concatenation with [PAUSE]
  silence, speed, two-pass -16 LUFS loudnorm, WAV/MP3 export with ID3 tags,
  per-chunk clipping, silence-gap detection, speaker-voice matching, and
  re-queue of only the failing chunks, with ComfyUI itself monkeypatched and
  **real ffmpeg** (each fake chunk writes a real tone file) —
  "explicitly-stubbed render, real ffmpeg." The M5 tests
  (`tests/test_delivery.py`) cover the paused handoff (QA pass →
  `delivery_records` row `paused` → email MP3 copied into `SHARE_LOCATION` →
  status `delivered_paused`), that `resend()` in the paused state sends
  nothing, that an activated `EMAIL_METHOD` with missing SMTP credentials
  fails loud naming the env names (never a fake success), that resend
  refuses a double-send after a `sent` record, and that a recipient outside
  the allowed set is refused. No real SMTP send happens anywhere in the suite.
- **Verified against the Board's real, live ComfyUI** (not just
  monkeypatched, via the WSL↔Windows relay documented on POD-3): walked a
  real episode from intake through a real two-chunk, two-voice render to
  `rendered`, with real FLAC files confirmed on disk. Separately proved
  crash/reboot resumability for real: started a 3-chunk render, `kill -9`'d
  the app process right after chunk 1 succeeded and while chunk 2 was
  mid-flight, restarted the process, and confirmed chunk 1 kept its original
  ComfyUI prompt_id (not re-rendered) while chunk 2 got a new one (the
  in-flight chunk, and only that chunk, re-rendered) before chunk 3 rendered
  and the job reached `succeeded`.
- **Not yet verified:** the native-Windows double-click path
  (`start_app.bat`, Windows path separators, long-path limits), and this
  milestone's render path specifically running as a native Windows process
  talking to ComfyUI without the WSL relay. WSL-side testing proves the
  Python logic; it does not prove the Windows experience. This is tracked as
  an open risk (build plan risk R7) until it is actually run on Windows and
  this section is updated to say so.

## Who owns what from here

- App/backend/UI: App Engineer.
- ComfyUI workflow JSON, TTS model, render settings: Audio Engineer (Board
  gate — this app reads those as configuration, never hardcodes them).
- Episode scope, tone, schedule: Showrunner.
- Sources and citations: Research Lead.
- Any Board decision (budget, hiring, email-provider restart): Chief of
  Podcast Operations.