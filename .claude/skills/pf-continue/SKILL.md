---
name: pf-continue
description: Continue work on Podcast Foundry using the pf-* Claude subagent team (stand-ins for the paused Paperclip company) plus the local Qwen model. Use when the user asks to continue, resume, or pick up work on this project, or asks for one of the Paperclip roles (Chief of Ops, App Engineer, Audio Engineer, Showrunner, Research Lead).
---

# Continue Podcast Foundry work

Paperclip is **paused by the user** (it burns their Claude compute). Do not
start it. The roles it ran are recreated as Claude subagents in
`.claude/agents/pf-*.md`; the local Qwen model collaborates through Hermes
and through `.claude/tools/ask_qwen.py`.

| Paperclip role | Subagent | Owns |
|---|---|---|
| Chief of Podcast Operations | `pf-chief-of-operations` | triage, PROJECT_LOG/COLLAB/HANDOFF, Board questions |
| Podcast Foundry App Engineer | `pf-app-engineer` | app code, tests |
| Audio Engineer | `pf-audio-engineer` | ComfyUI/TTS, audio QA investigations |
| Showrunner | `pf-showrunner` | episode scope, narration scripts |
| Research Lead | `pf-research-lead` | sources, research reports, fact checks |

## Steps
1. **Orient yourself (lead session).** Read `PROJECT_LOG.md` (Current state,
   Open items, latest Work log entries) and `COLLAB.md` (claims, handoff
   notes, questions), then `git status` and `git log --oneline -10`. Check
   whether the Qwen/Hermes agent changed files since the last log entry
   (`find . -newer PROJECT_LOG.md -not -path './.venv*' -not -path './.git/*'`).
2. **Check Qwen.** `python .claude/tools/ask_qwen.py --check`. If it's down,
   start it: `lms server start --port 65535` then
   `lms load huihui-qwen3.6-35b-a3b-claude-4.7-opus-abliterated-mtp`
   (`lms` is at `D:\_thinkpad_backup_Aug_21_2026\lmstudio\bin\lms.exe` if not
   on PATH). Mind the single GPU if a ComfyUI render is running.
3. **Pick the work.** If the user named a task, do that. Otherwise take the
   top unblocked Open item. Spawn `pf-chief-of-operations` only when triage
   is genuinely unclear. Spawning is expensive, so do small things inline.
4. **Dispatch.** Give each role subagent a self-contained brief: goal, files,
   constraints, what "done" means. Run independent roles in parallel. Send
   bulk drafting (long scripts, source digests, first-pass reviews) to Qwen
   via `ask_qwen.py` rather than a Claude subagent when quality allows.
5. **Verify.** You are accountable for subagent and Qwen output: read the
   diffs, run `.venv-win/Scripts/python.exe -m pytest tests/ -q`, and check
   claims against the code.
6. **Record.** Claim/release files in COLLAB.md, leave a handoff note for
   Qwen, append a dated Work log entry to PROJECT_LOG.md, update Open items.
   Commit/push only when the user asks.

## Coexistence with Paperclip
If the user turns Paperclip back on, its agents own the POD-n tickets. The
pf-* agents then act only on direct user requests, and coordinate through
COLLAB.md claims like any other agent. Nothing here changes Paperclip's
configuration.
