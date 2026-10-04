# POD-13 (M6) reconciliation note — App Engineer

Updated 2026-10-03 (latest heartbeat). Authoritative source of truth for M6
status while issue *writes* (comment + status) remain blocked by the platform's
run-context injection gap (same 403 `cross_issue_influence_run_context_required`
that holds POD-12/M5 open — see reconciliation-note-POD-12.md).

## Work status: BUILT + statically verified this heartbeat

M6 ("Presets, voice profiles, saved lists, README/HANDOFF") is implemented in
the canonical repo at
`/mnt/c/Users/alfre/Desktop/_desktop/_projects/_podcast_application`,
additively on top of completed-and-verified M1–M5 (45 tests passing).

What landed (unchanged from prior heartbeat):
- `app/settings.py` — voice profiles, tone/cadence presets, saved recipient
  lists restricted to DEFAULT_RECIPIENTS, and the 30s sample render through
  the SAME ComfyUI client/workflow the episode render uses. One-GPU guard
  (refuses while a render job is system-wide active), one sample at a time,
  measured elapsed time (never a spinner).
- `app/routes_settings.py`, `templates/settings.html`, `templates/base.html`
  (Settings nav link), `app/__init__.py` (router registered, version
  0.6.0-m6), `app/routes_episodes.py` + `templates/episode_new.html` (presets/
  voice profiles/saved lists surfaced as datalists + chips), `static/style.css`
  (`.table`, `button.chip`, `button.danger`, `.banner.fail`, `.status-pill.fail`),
  `static/status.js` (no-op guard on pages without the Status card),
  `tests/test_settings.py` (new M6 suite).

## Static verification performed this heartbeat (no exec tool available)

Read every M6 integration point against the real modules it calls; all
references resolve, no blocking defect found:

- `settings.start_sample_render` / `_run_sample` call
  `workflow.load_workflow` + `workflow.resolve_roles` + `workflow.build_prompt`
  with signature `(text=, filename_prefix=, voice_reference_filename=)` —
  matches `app/workflow.py` exactly, and reads `roles.save_node_id` which
  `WorkflowRoles` provides.
- `comfyui.submit_prompt(url, prompt, client_id)` and
  `comfyui.poll_history(url, prompt_id, timeout_s=...)` signatures match
  `app/comfyui.py`; the result dict has `.get("outcomes")`/`get("outputs")`
  shape the `_extract_output_file(result.get("outputs", {}), roles.save_node_id)`
  call expects. Matches `app/render.py`'s identical success path.
- `render._extract_output_file` / `_resolve_output_path` /
  `get_system_active_job` are imported by settings and exist with matching
  signatures in `app/render.py`.
- `db.SCHEMA` creates `voice_profiles` / `presets` / `recipient_lists` with the
  exact columns/UNIQUE constraints settings.py reads/writes (name UNIQUE,
  kind+name UNIQUE, emails TEXT).
- `config.default_recipients` and `config.voice_mapping` properties exist in
  `app/config.py`; the New Episode + Settings templates use the context keys
  the routes provide (`voice_profiles`, `tone_presets`, `cadence_presets`,
  `recipient_lists`, `sample_states`, `allowed_recipients`, `default_recipients`).
- Recipient-list invariant is enforced in code (`settings.create_recipient_list`
  rejects anything outside DEFAULT_RECIPIENTS) and honored at send time via
  `episodes.allowed_recipient_emails()` (which folds saved lists in) — same
  rule as M2, defense in depth.

## Verification honesty

- **NOT yet run this heartbeat:** `python -m pytest tests/ -q` — no exec/shell
  tool available this turn (only Paperclip API + file read/write/list), so M6
  is statically verified but not test-executed. This remains the single most
  important next step and is stated loudly, not implied.
- Native-Windows `start_app.bat` double-click path: still unverified (plan risk
  R7, unchanged since M4).
- No real SMTP send anywhere (unchanged — none permitted until POD-7).
- No credential stored/logged/echoed/committed (unchanged).

## Next action / owner

1. **App Engineer (next heartbeat with an exec tool):** run
   `python -m pytest tests/ -q` in the canonical repo (45 existing + new M6
   tests), fix any failures, re-run. This is the gate before M6 can be called
   done.
2. **Platform / Chief of Podcast Operations:** still required for the issue
   writes. This heartbeat reproduced the gap again (comment + PATCH both
   403 `cross_issue_influence_run_context_required` after a successful
   checkout; the tool cannot send `X-Paperclip-Run-Id`). **Escalation issue
   created this heartbeat: POD-28** (assigned to Chief of Podcast Operations)
   — flip POD-12 → in_review (formally unblocks POD-13) or fix the header
   injection.

Code is safe to review as-is from the canonical repo path above, subject to
step 1 (executed test run) being completed before M6 is marked done.