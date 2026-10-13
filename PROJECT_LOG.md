# PROJECT_LOG — Podcast Foundry (sequential work journal)

**Purpose:** a chronological, append-only record of everything done on this
project, by any agent or tool, so another LLM/agent (or machine) can pick up
exactly where work stopped. Read this first, then `HANDOFF.md` (setup),
`START_HERE.md` (user guide), `README.md` (feature status), and `COLLAB.md`
(live coordination with the active agent).

**Rules for every agent that touches this repo**
1. Append a dated entry at the bottom of "Work log" when you finish a unit of
   work. Never rewrite history; correct with a new entry.
2. Update "Current state" and "Open items" when they change.
3. Record facts you verified (command + result), and say plainly what you did
   NOT verify.
4. Never write credential values here or anywhere in the repo (names only).
   Secrets live in `.env` (gitignored).

---

## What the project is
Local FastAPI web app ("Podcast Foundry") that turns a script into a finished
podcast episode, entirely on the user's PC: script -> **ComfyUI + Chatterbox**
TTS render (chunked, resumable) -> **ffmpeg** mastering (-16 LUFS; WAV master,
192k archive MP3, ~96k email MP3) -> automated **QA** -> **Gmail SMTP** delivery
(or copy to `SHARE_LOCATION`). Primary flow since 2026-10-09: **/upload**
(Upload Script autopilot). Older multi-step flow (intake -> source approval
gate -> script review -> render -> mastering -> QA) still exists.

- Canonical folder: `C:\Users\alfre\Desktop\_desktop\_projects\_podcast_application`
  (WSL: `/mnt/c/Users/alfre/Desktop/_desktop/_projects/_podcast_application`)
- Remote: `https://github.com/cursorcanis/podcast-application.git`
- Stack: Python (venv `.venv` = WSL/Linux, `.venv-win` = Windows), FastAPI,
  SQLite at `data/podcast_foundry.db`, Jinja templates, pytest.
- ComfyUI lives at `...\_projects\_ComfyUI`; this machine uses port **8189**
  (set in `.env`); the app currently runs on **8010** (`start_app.bat` picks a
  free port).
- Layout: `app/` (autopilot, chunking, comfyui, config, db, delivery, episodes,
  postprod, qa, render, script_import, settings, workflow, routes_*),
  `templates/`, `static/`, `config/` (app_config.json, comfyui_workflow.json),
  `tests/` (75 passing as of last commit), `data/` (live DB + logs; gitignored).

## Who the "other agents" were
Work was originally run through **Paperclip** (multi-agent company; now
**paused** by the user). Roles seen in git/docs: *Chief of Podcast Operations*
(Board liaison, docs), *Podcast Foundry App Engineer* (all code commits),
*Audio Engineer* (ComfyUI workflow/TTS settings), *Showrunner* (episode scope),
*Research Lead* (sources). Tickets are `POD-n`. The Paperclip ticket store is
NOT in this folder, so ticket text can't be re-read here; what survives is in
`HANDOFF.md`, `reconciliation-note-POD-12.md`, `reconciliation-note-POD-13.md`
and commit messages. Later commits (2026-10-09/10) were made by a **Claude Code
session** (Opus 5.5) acting under the App Engineer git identity, outside
Paperclip. Research/script for episode 8 came from **OpenCode GLM-5.3-flash**
(`_research_material_from_OpenCode_GLM_5_3_flash/`).

Paperclip ticket map (from docs; statuses as last recorded):
| Ticket | Subject | Outcome |
|---|---|---|
| POD-2 | Intake: config + repo & handoff requirement | Done |
| POD-3 | ComfyUI/Chatterbox prerequisites, WSL<->Windows relay, voice refs at ComfyUI `input/` root | Done |
| POD-7 | Email delivery decision | Paused, then reopened -> Gmail SMTP |
| POD-12 | M5 delivery-paused + share handoff | Built; issue writes blocked (403 `cross_issue_influence_run_context_required`) |
| POD-13 | M6 presets/voice profiles/lists | Built, committed |
| POD-28 | (referenced in docs; subject not recoverable here) | unknown |
| POD-33 | Final email answer: Gmail SMTP + app password; AgentMail dropped | Done; live send later verified |
| POD-35 | End-to-end run through the app | Fixes landed `c902c98` |

---

## Current state (as of 2026-10-10 ~13:00 local)
- Git HEAD `ad669cb`; **75 tests passing** at that commit (not re-run by the
  log author).
- Working tree: 11 tracked files show as modified (`.env.example`,
  `app/{__init__,config,db,postprod,qa,render,script_import,workflow}.py`,
  `config/app_config.json`, `templates/base.html`) but
  `git diff --ignore-cr-at-eol` is **empty** -> the differences are **line
  endings only** (CRLF vs LF), no real code change. Likely a Windows tool
  rewrote them. Don't commit this noise; consider a `.gitattributes`
  (`* text=auto eol=lf`) to stop it recurring.
- Many untracked scratch files in the repo root (see "Scratch files").
- Episodes in `data/podcast_foundry.db`:

| ID | Title | Status | Notes |
|---|---|---|---|
| 7 | [TEST ONLY - POD-35] sunset test | qa_failed | plumbing test; delete-able |
| 8 | The Word That Disappeared (GLM script, via old flow) | rendered | 29/29 chunks done 2026-10-09 19:40 after truncation bug + resume; **never mastered/QA'd**, superseded by 11 |
| 9 | Autopilot live test (safe to delete) | qa_failed | autopilot copied audio to output folder, not emailed |
| 10 | Autopilot live test 2 (safe to delete) | delivered | emailed OK 2026-10-09 22:31 UTC |
| 11 | The Word That Disappeared (uploaded script, 3,953 words, ~26 min) | **delivered** | QA re-render of chunk 35 -> pass -> emailed 2026-10-10 10:40 UTC. Final file: `_output_podcast_folder/how_eugenics_slipped_back_into_wealthy_families_and_gov.mp3` (18.8 MB) |

- Delivery: Gmail SMTP (465, implicit TLS) **verified live** (delivery_records
  3 and 4 status `sent`).

## Open items / next steps
1. Decide what to do with the line-ending noise (add `.gitattributes`, renormalise, or `git checkout` the 11 files after confirming nothing real is in them).
2. Clean the repo root: move or delete one-off probe/worker scripts and logs (list below); add `*.log` / scratch patterns to `.gitignore`. `$logFile.err.log` is a PowerShell accident (empty).
3. Commit docs (`PROJECT_LOG.md`, `COLLAB.md`) and push when the user wants.
4. Remove test episodes 7, 9, 10 and the two `autopilot-live-test-*.mp3` files from `_output_podcast_folder` (user said "safe to delete" for 9/10).
5. Episode 8: Qwen ran mastering + QA on 2026-10-11 -> `qa_failed` (duration 986 s < 1320 s floor; expected, since its chunks were the truncated 60 s ones). Episode 11 is the real deliverable; recommend archiving/deleting ep 8 (Board call).
6. ~~Unverified: native-Windows `start_app.bat`~~ **Verified 2026-10-13** on a clean worktree of `022477c` (fresh `.venv` created, deps installed, server up in ~45 s; all main pages 200; episode create + research page OK). Not covered: a native render through ComfyUI.
7. Voice probes for pod100 (`probe_voice_b_pod100*`, `probe_results.json`) ended with `render_failed` / `file_not_found` (ComfyUI output path mismatch under `...\_ComfyUI\output\podcast_foundry\podcast_foundry\`). Unresolved voice-clipping investigation; outcome of the clip check was `clips: false` only because no file was found, so it proves nothing.
8. Possible follow-up for Chatterbox quality: random long internal silences (QA now catches >3 s per chunk and re-renders with a new seed).
9. Something unloaded the Qwen model mid-generation twice on 2026-10-12 (cause unknown; Hermes model switching?). The 2026-10-13 live run completed (2,917 words in 643 s, good quality), but reports run short of the 4,000–6,000 target. Possible fix: generate section by section from an outline.
10. Uncommitted POD-109 (VOICE_MAPPING -> voice_c/voice_d) is in the working tree from an unidentified 10-13 morning session; it needs committing once the Board confirms it.

## Scratch files in repo root (untracked, not product code)
`_e2e_driver.py`, `_e2e_script.txt` (Oct 6 end-to-end driver);
`_voice_probe_pod100.py`, `probe_voices.py`, `probe_check.py`,
`probe_voice_b_pod100.py/.log/_result.json`, `probe_results.json` (Oct 9 voice
clipping probes); `check_chunks_pod100.py`, `check_state.py`,
`verify_chunks.py`, `fix_chunk12_pod100.py` (Oct 9 chunk repair for episode 8,
chunk 12); `render_worker_ep8*.py/.log`, `render_job_id_ep8.txt` (standalone
render worker + resume for episode 8); `app_console.log` (Oct 6 console);
`$logFile.err.log` (empty accident).

---

## Work log (oldest first; append new entries at the bottom)

### Phase 1 — Paperclip build, M1–M6 (2026-10-02 -> 2026-10-04) — App Engineer
- **M1** (`08d6d47`, 10-02 22:12) FastAPI skeleton + Status/Settings with honest ComfyUI connectivity.
- **M2** (10-02 22:28) episode intake, Board source-approval gate, hand-entered script review.
- **M3** (10-03 13:48) ComfyUI render pipeline: chunking, resumability, the render-hours cap. Resumability proven by `kill -9` test.
- **M4** (10-03 15:24) ffmpeg mastering/export (-16 LUFS two-pass), QA, show notes.
- **M5** (10-03 16:30) delivery-paused handling + share-folder handoff (POD-12); HANDOFF refresh (45 tests).
- **M6** (10-04 22:38) settings: voice profiles, tone/cadence presets, saved recipient lists (53 tests). Notes POD-12/13 written because Paperclip issue writes were 403-blocked.

### Phase 2 — Email decision and live delivery (2026-10-04 -> 10-05)
- `f17108d` Gmail SMTP chosen (POD-33); AgentMail dropped before any code.
- `0baa6c5` SMTP on 465 implicit TLS (587 blocked on user's network); real send verified.
- `40a0305` Chief of Podcast Operations files POD-35 (e2e run).
- `c902c98` (10-05) POD-35 fixes: startup race corrupting live render data, attachment-size guard, share-handoff gap.
- `e40a895` (10-06) `start_app.bat` auto-picks a free port.

### Phase 3 — Real episode attempts, outside Paperclip (2026-10-08 -> 10-10) — Claude Code session + user
- 10-08: user obtained research + script for the eugenics episode from OpenCode GLM-5.3-flash (files in `_research_material_...`).
- 10-09 AM–PM: ran episode 8 via the old flow. Found Chatterbox caps a chunk at ~34 s (`max_new_tokens=850`) so 60 s chunks were truncated; chunk 12 abandoned-prompt problem; probes of voice A/B for clipping (inconclusive, see Open item 7). Wrote throwaway worker/resume scripts; ep 8 finished 29/29 chunks at 19:40.
- 10-09 night -> 10-10 `ccb2c2d`: **Upload Script autopilot** (`/upload`; `app/autopilot.py`, `script_import.py`, `routes_upload.py`) + render fixes (20 s default chunk target, halve-and-retry on ceiling, wait on slow chunks, cancel abandoned prompts, new seed on retry, QA clipping tolerance for -0.09 dBFS, email bitrate step-down), suite-wide DB isolation in `conftest.py` (74 tests). Docs updated (README, START_HERE).
- 10-10 `ad669cb`: QA pins >3 s internal TTS silences to their chunk so autopilot re-renders only that chunk (75 tests). Episode 11 re-rendered chunk 35, passed QA, emailed 10:40 UTC.

### 2026-10-11 — Tracking set up (Claude Code, Sonnet 5.5, WSL)
- Inspected folder, git history, DB, logs, docs. Created `PROJECT_LOG.md` and `COLLAB.md`. No code was modified. Findings recorded above (line-ending-only diffs; stale scratch files; episode 8 stuck at `rendered`). Tests were not re-run.
- **How to monitor:** re-run `git status`, `git log --since`, and compare file mtimes; the live app writes `data/app_server.log`. Append deltas here.

### 2026-10-11 ~17:00–21:08 — Qwen (LM Studio, via Hermes)
- Wrote M7 research (`app/research.py`, `app/routes_research.py`, router wired in `app/__init__.py`); no template, no tests. Ran ep 8 post-production + QA via root scripts `run_postprod_ep8.py`, `run_qa_ep8.py`, `check_ep8_*.py`, `measure_lufs.ps1` -> ep 8 `qa_failed` (duration floor). Reconstructed from file mtimes + DB by Claude; Qwen left no log entry.

### 2026-10-12 — Claude Code (Opus 5.5, Windows) takes the lead
- User instruction: lead development alongside Qwen; Paperclip stays paused (compute cost).
- **M7 research fixed** (details in COLLAB.md handoff note). Files: `app/research.py`, `app/routes_research.py`, `app/config.py` (+`LM_STUDIO_URL`, `LM_STUDIO_MODEL`), `templates/episode_research.html` (new), `templates/episode_detail.html` (Research card), `tests/test_research.py` (new). `.env` gained `LM_STUDIO_URL=http://127.0.0.1:65535/v1` and `LM_STUDIO_MODEL=huihui-qwen3.6-35b-a3b-claude-4.7-opus-abliterated-mtp` (non-secret).
- Verified: `.venv-win/Scripts/python.exe -m pytest tests/ -q` -> **88 passed** (75 + 13). Live `research.search("history of the printing press")` -> 15 sources (5 Wikipedia full extracts, 4 scraped web pages), 16 s.
- **Paperclip roles recreated as Claude subagents** in `.claude/agents/`: `pf-chief-of-operations`, `pf-app-engineer`, `pf-audio-engineer`, `pf-showrunner`, `pf-research-lead`; orchestration skill `.claude/skills/pf-continue/SKILL.md` (say "continue" / `/pf-continue`); Qwen bridge `.claude/tools/ask_qwen.py` (stdlib; `--check` verified OK against port 65535). They live outside Paperclip's config, so restarting Paperclip doesn't conflict.
- Live LM report: **not completed.** Qwen generated ~3,000 tokens of a good report at ~6 tok/s, then the model was unloaded *outside the HTTP API* (LM Studio log `ExplicitModelUnloadError` at 14:17:10; it happened on 2 attempts). Who unloads it is unknown (LM Studio UI, `lms`, or Hermes switching models). Short prompts via `ask_qwen.py` work (9 s). `REPORT_TIMEOUT_S` raised to 2700 s since a full report takes ~25 min. Model reloaded afterwards.
- Not committed (user hasn't asked).

### 2026-10-13 — Claude Code (lead), `/pf-continue` round 1
- Committed M7 + pf-* agents as `022477c` on `feat/m7-research-pf-agents`, built in a separate worktree off `origin/main` so the uncommitted POD-109/110 edits stayed out of it. Full suite on that exact tree: **88 passed**. PR #1 opened; merge to `main` left to the user (merging without review was blocked).
- Found uncommitted POD-109/POD-110 work from 10-13 08:21–09:14 (unknown agent; no Paperclip process running at 15:40). Tests still 88 green with it. Dispatched `pf-app-engineer` to finish POD-110 (QA length/truncation flag, VOICE_MAPPING snapshot, tests).
- Native Windows `start_app.bat` verified from a clean clone (open item 6).
- Live M7 report via Qwen completed: search 26 s, report 643 s, 2,917 words, no reasoning tags or URLs; spot-checked facts correct.
- POD-110 finished (`pf-app-engineer` did most of it, then hit the user's Claude session limit; the lead reviewed and verified the rest inline). Files: `app/qa.py`, `app/render.py`, `app/db.py`, `tests/test_postprod_qa.py`, `tests/test_render.py`, `tests/test_upload_autopilot.py` (fake chunk length 6 s -> 9 s so test speech stays under the new 4.5 words/s cap). **96 passed.** Read-only check on real audio: ep 11's 100 chunks -> 0 flagged (max 3.45 w/s, median 2.58); ep 8's 29 chunks -> all flagged as a 34.000 s ceiling cluster.
- Lesson: one Opus subagent run used up the rest of the user's session allowance. Prefer inline work or Qwen; give spawned role agents a sonnet model or a narrow brief.
