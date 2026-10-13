# COLLAB — shared coordination file

Two (or more) agents may work in this repo at once: the **primary agent** that
the user assigned a task to, and the **assistant/monitor** (Claude Code in WSL)
that tracks changes and helps on request. This file prevents stepping on each
other. Keep entries short. Long-term history goes in `PROJECT_LOG.md`.

## Protocol
1. **Before editing**, add/refresh a row in *File claims* for the files you
   will touch. Check no one else holds them.
2. **After finishing**, release the claim and add a line to *Handoff notes*
   (what changed, what to verify, anything half-done).
3. Don't reformat files or change line endings. The tree already has
   CRLF-only noise on 11 files (see `PROJECT_LOG.md`); don't add to it.
4. Don't run the live app on the user's DB (`data/podcast_foundry.db`) while a
   render is in progress; tests are isolated via `tests/conftest.py`.
5. One GPU, one ComfyUI job at a time (port 8189). Check the render queue
   before starting anything that submits to ComfyUI.
6. Disagreements or questions for the user go in *Questions for the user*.
7. Never put secret values in this file.

## Roles
| Agent | Role | Notes |
|---|---|---|
| Qwen (LM Studio, via Hermes) | Started M7 research feature + ep 8 post-prod on 2026-10-11 | Idle since 10-11 ~21:08; LM Studio API on :1234 down as of 10-12 13:40 |
| Claude Code (Opus 5.5, Windows) | **Lead as of 2026-10-12** (user instruction) | Finishing M7 research; see claims |
| Claude Code (Sonnet 5.5, WSL) | Monitor, documenter (2026-10-11) | Keeps `PROJECT_LOG.md` current; helps when asked |
| pf-* Claude subagents (`.claude/agents/`) | Stand-ins for the paused Paperclip roles | Run by the lead session via the `/pf-continue` skill |

## Active task (primary agent: please fill in)
- Task: M7 research — episode topic -> web search (DDG/Wikipedia/arXiv) -> LM Studio writes a 4-6k word report for narration (Qwen's `app/research.py`, `app/routes_research.py`).
- Status (2026-10-12): **fixed and tested** by Claude (88/88 tests). Live search verified. See handoff note.

## File claims
| File / area | Held by | Since | Purpose |
|---|---|---|---|
| _none_ | | | |

## Handoff notes (newest first)
- 2026-10-13 18:50 — Claude (lead): **POD-110 complete, claims released.** QA now flags chunks too short for their text (>4.5 words/s, chunks of 20+ words) or tied (3+, within 0.02 s) at the job's longest duration of 10 s or more, sets `qa_length_detected`, and re-renders only those chunks. Jobs freeze `voice_mapping_snapshot` on the worker's first run (never overwritten on resume) and QA/retry compare against it. 96 tests pass. Real-data check: ep 11 = 0 false flags (max 3.45 w/s), ep 8 = 29/29 flagged. POD-109's config swap is still uncommitted, awaiting the Board.
- 2026-10-13 — Claude (lead): found uncommitted POD-109 (VOICE_MAPPING -> voice_c/voice_d, backup `config/app_config.json.bak-20261013-pod109`) and half-done POD-110 (`app/db.py` columns `voice_mapping_snapshot`, `qa_length_detected`; `app/render.py` chunk-target safety guard) written 10-13 08:21–09:14 by an unidentified agent (no Paperclip process running at 15:40). Tests pass with them (88). Claude is finishing POD-110 (claims above); POD-109's config swap left as-is (Board's choice). M7 + pf-* agents are in PR #1 (`feat/m7-research-pf-agents`), awaiting the user's merge.
- 2026-10-12 — Claude (lead) -> Qwen: **M7 research reworked, claims released.** Changes: added `templates/episode_research.html` (page used to 500); DDG results now unwrap `//duckduckgo.com/l/?uddg=` links (were always 0) and the top 6 pages get scraped; Wikipedia uses plain-text extracts and an honest User-Agent (browser-like UA got 403); ranking is Wikipedia > web > arXiv (arXiv was flooding results with off-topic papers); LM Studio via `httpx` (`requests` isn't a dependency); `LM_STUDIO_URL`/`LM_STUDIO_MODEL` are config keys (set in `.env` to port 65535 + the huihui model); `<think>` blocks stripped; research **no longer sets status `script_ready`** — the report is stored, downloadable as .md, and fed to /upload; the in-progress marker is always cleared, goes stale after 30 min, and double-starts are refused. New `tests/test_research.py` (13 tests). Logic now lives in `research.py` (`start`/`run`/`get_status`); routes are thin. Please build on this rather than the old version. Ep 8: your post-prod/QA run failed the duration floor (986 s vs 1320 s); ep 8 was superseded by ep 11, so I left it.
- 2026-10-11 — Monitor: baseline recorded in `PROJECT_LOG.md`. HEAD `ad669cb`, 75 tests green at that commit. Nothing is claimed. Known hazards: CRLF-only diffs, stale root scratch scripts, episode 8 stuck at `rendered`.

## Questions for the user
1. ~~Which tool is the primary agent?~~ Answered 2026-10-12: Qwen in LM Studio (port 65535) via Hermes; Paperclip stays paused; Claude Code leads with pf-* subagents.
2. May the monitor clean up the root-level scratch files and add `.gitattributes`, or should those stay untouched while the other agent works?
3. Should the monitor commit the two log docs, or leave git to the primary agent?

## Resuming Paperclip-era work
If the user restarts Paperclip, or a new agent takes over those roles: the
unfinished/unknown items are POD-28 (subject unknown), episode 8 post-production,
the native-Windows `start_app.bat` verification, and the voice-clipping probe
(see Open items 4–7 in `PROJECT_LOG.md`). Ticket text isn't stored in this
folder; export it from Paperclip before moving machines if you need it.
