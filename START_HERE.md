# Podcast Foundry — Start Here

A plain-language introduction to what you have, what state it's in, and how to run it.

## The quick way (added 2026-10-09): upload a script, get a podcast

No agents, no board tickets, no Claude usage — everything runs on this PC for $0
(ComfyUI + Chatterbox for the voice, ffmpeg for mastering, Gmail for delivery).

1. Start **ComfyUI** (this machine uses port 8189, set in `.env`).
2. Double-click **`start_app.bat`**. The **Upload Script** page opens in your browser.
3. Choose your script file (`.txt`, `.md` or `.docx`) and click **Make the podcast**.

That's it. The episode page shows a live status line while the app renders the
voice, masters it, runs QA and emails it (or copies it to the share folder).
You can close the browser; the work continues as long as the app window is open,
and if the app or PC restarts it picks up where it left off.

- **Script format:** plain prose is fine. A 4,000–5,000 word script makes a
  ~27–33 minute episode and takes roughly 3–4 hours to render on this PC
  (measured 2026-10-09: ~100 s of GPU time per ~50-word chunk).
  Optional: `[HOST_A]` / `[HOST_B]` to switch voices, `[PAUSE:2s]` for a pause.
  Markdown headings become short pauses; `[MUSIC IN]`-style stage directions
  are skipped, not read aloud.
- **Several uploads** queue and render one after another in upload order.
- **ComfyUI not running yet?** The episode waits and starts by itself once it is.
- **Long episodes** get a lower-bitrate email copy so they still fit under the
  20 MB attachment limit; the full-quality WAV and 192 kbps MP3 are always kept.
- **If something stops it,** the status line says why, and a
  **Resume autopilot** button continues without re-rendering finished parts.

The older multi-step path (New Episode → sources → script → buttons) is still
there and is described below.

Written 2026-10-04 by the Chief of Podcast Operations. Every claim below was
checked against the machine this run — files on disk, the test suite actually
re-run, the database actually opened. Where something is unproven, it says so.

---

## 1. What you actually have

Two separate things were built. They do the same job by different means, and
it matters which one you mean when you say "run it."

**(A) The agent team** — five agents on your Paperclip board who make an
episode by working a 9-stage ticket pipeline: research → your source-approval
gate → outline → script → editorial review → render → mastering → QA →
delivery. This is what produced the pilot you heard. It needs almost nothing
from you except approving sources and picking a topic.

**(B) The Podcast Foundry app** — a local web application at
`C:\Users\alfre\Desktop\_desktop\_projects\_podcast_application`. You open it
in a browser, fill in a form, paste a script, and it drives ComfyUI and ffmpeg
for you. It handles everything from the script onward automatically, with real
progress, crash recovery, and downloads. It does *not* write the script for
you — you supply that (yourself, or from the agent team).

Short version: **the team is the hands-off path. The app is the hands-on path.**
Both end with the same three audio files in the same folder.

---

## 2. Is it finished?

**The pilot episode: yes, finished.** Nothing more is needed from you on it.

| | |
|---|---|
| Title | *Hidden in Plain Sight* |
| Length | 21 min 9 s (target was 22) |
| Host | Single male voice (Chatterbox TTS, `voice_a.wav` reference) |
| Chunks rendered | 64 — zero failures, zero retries |
| Loudness | −16.0 LUFS, −1.5 dBTP peak, no clipping |
| QA | Passed |
| Sources | 12 approved, all claims mapped |

Files are in
`C:\Users\alfre\Desktop\_desktop\_projects\_ComfyUI\output\podcast_foundry\2026-10-04_hidden-in-plain-sight\`:

- `episode_master.wav` — 214 MB WAV master
- `episode_stereo_192kbps.mp3` — 30 MB archive copy
- `episode_mono_96kbps.mp3` — 15 MB email-sized copy
- `show_notes.md` — summary, timestamped segments, source list
- `qa_report.md`, `citation_map.md`, `script.md`, `post_production_report.json`

Those eight files are **also now in the share folder**, at
`...\_podcast_application\_output_podcast_folder\2026-10-04_hidden-in-plain-sight\`.
The pilot was made by hand-run scripts, which skipped the app's share-folder
handoff, so I copied them across on 2026-10-04 — the share folder is where
every future episode will appear while delivery is paused, and it was empty
until now. Originals are untouched.

**The app: code-complete, test-green, delivery live, and now under test as a
one-click program.** All six milestones (M1–M6) are written and committed, and
the full suite passes — **55 of 55 tests** as of commit `0baa6c5`.

Three honest caveats, none of which are your homework:

1. **No episode has yet been made through the app end to end.** I opened its
   database: zero episodes, zero delivery records. The pilot was made by
   scripts the Audio Engineer ran by hand. The app's render engine *has* been
   proven against your real, live ComfyUI (a two-voice render producing real
   audio, plus a `kill -9` crash test proving it resumes without re-rendering
   finished chunks), and the email path is proven with a real send — but a
   complete intake-to-delivery run inside the app hasn't happened yet.
   **Filed 2026-10-05 as POD-35**, assigned to the App Engineer: one short
   3–4 minute throwaway episode driven entirely through the app's own screens,
   intake → source gate → script → render → mastering → QA → your inbox.
   Finding bugs there counts as success, not failure.
2. **`start_app.bat` has never been run on Windows.** All testing happened on
   the Linux side of your machine. The Python logic is proven; the Windows
   launch is not. It had a real bug — it guarded on `if not exist .venv` and
   this folder has a Linux-built `.venv`, so it skipped creating a Windows one
   and then ran against your global Python. Fixed by inspection on
   2026-10-05, not yet verified by running it. POD-35 covers the verification.
3. ~~M6's code isn't committed to git yet.~~ **Resolved 2026-10-04** — M6 is
   now committed and pushed to `github.com/cursorcanis/podcast-application`.
   All six milestones are in the repo and the working folder is clean.

---

## 3. Your decisions — all four closed

You answered four questions on POD-33 on **2026-10-04**. These are now the
standing decisions, and the rest of this document follows them.

| Decision | Your answer | Status |
|---|---|---|
| Email sending method | **Gmail SMTP with an app password** | **Live.** Verified by a real send 2026-10-05 |
| Episode cadence | **You file a board task each time** | Live now, no setup needed (section 6) |
| Next engineering step | **Commit and push M6** | Done 2026-10-04 |
| AgentMail | **Dropped** in favour of Gmail SMTP | The AgentMail sender ticket was cancelled, unbuilt |

AgentMail turned out not to be connectable from this board, so it would have
meant creating an AgentMail account and API key — *more* setup than Gmail, not
less. You switched to Gmail SMTP instead. Good call: that path is already
built, already tested, and needs no new code.

**Email delivery is on.** You created `.env` on **2026-10-05**; the app
password authenticates against Gmail and a real test email with a 26-second
audio attachment was delivered to `alfredoalea@gmail.com`. Nothing is
outstanding from you. Section 3a is kept as the reference for how it is wired
and what to do if it ever stops working.

### 3a. How email delivery is wired (done — reference only)

**Verified on 2026-10-05, on this machine, end to end:** `.env` is read,
`EMAIL_METHOD=smtp` unpauses delivery, the app password is accepted by
Gmail, and a real message with an audio attachment arrived. The app's own
`_smtp_send()` did the sending — not a side script — so the path an episode
will take is the path that was proven.

**One thing had to be fixed to get there.** This network blocks outbound port
**587** (and 25, and 2525); only **465** connects. The app defaulted to 587
with STARTTLS, so it would have hung until timeout and reported a vague
network error. Delivery now defaults to **465 with implicit TLS** and picks
the transport from the port, with tests covering both modes. Nothing for you
to configure — `SMTP_HOST` and `SMTP_PORT` can stay unset. If you ever move
the machine to a network where 587 is open and 465 isn't, add
`SMTP_PORT=587` to `.env` and the STARTTLS path takes over.

The three steps below are the procedure you already completed on 2026-10-05.
They stay here in case the app password is ever revoked and has to be remade.

**Step 1 — make the app password.** On the Google account you want episodes
*sent from*:

1. Go to <https://myaccount.google.com/security> and make sure **2-Step
   Verification** is **On**. Google will not offer app passwords without it.
2. Go to <https://myaccount.google.com/apppasswords>. Name it
   `Podcast Foundry` and click **Create**.
3. Google shows a **16-character password** in four blocks, e.g.
   `abcd efgh ijkl mnop`. Copy it. It is shown once and never again — if you
   lose it, delete that entry and make a new one. Spaces don't matter; the app
   strips nothing, so paste it however you like as long as it's the same 16
   characters.

**Step 2 — put it where the app reads it.** Easiest way: **double-click
`setup_email.bat`** in the app folder. It asks for the Gmail address and the
app password (typing is hidden), writes the `.env` file for you, and prints
only which names are set — never the password. Nothing else to do.

To check later whether delivery is on, double-click it again; or run
`powershell -ExecutionPolicy Bypass -File setup_email.ps1 -Check`, which
reports the three names without changing anything.

By hand instead, if you prefer: create a file called `.env` in
`C:\Users\alfre\Desktop\_desktop\_projects\_podcast_application` (Notepad is
fine — save as `".env"`, with the quotes, so Notepad doesn't add `.txt`) with
these three lines:

```
EMAIL_METHOD=smtp
SMTP_USER=the-gmail-address-you-used@gmail.com
SMTP_PASS=abcdefghijklmnop
```

That file is already in `.gitignore`, so it is never committed and never
leaves your machine. The app reads those three names from the environment at
startup and the password is never written to a log, a ticket, the database, or
any screen — the Status page shows only *"SMTP credentials: configured"*.
`SMTP_HOST` and `SMTP_PORT` default to `smtp.gmail.com:465` with implicit TLS,
so you don't need to set them (see the port note above).

**You never have to send me the password.** Don't paste it into the board or a
chat message. Putting it in that file is the whole handover.

**Step 3 — tell me it's there,** and I send one real test email (a short
benchmark clip, not a whole episode, to one address rather than all three) to
confirm the login works before an episode depends on it. Done 2026-10-05.

**Which address sends?** Whichever Gmail account you make the app password on
becomes the sender — no extra config. The three approved recipients stay
`alfredoalea@gmail.com`, `alfredo.cursor@gmail.com`,
`alfredoaleawork@gmail.com`; the app refuses to send anywhere outside that
list.

**Will an episode actually fit in an email?** Yes. The pilot's email-sized MP3
(mono, 96 kbps) is **14.5 MB** — inside both the app's own 20 MB cap and
Gmail's 25 MB limit. The cap works out to roughly **29 minutes** of audio; a
longer episode than that is detected before sending and handed to the share
folder with a link instead, rather than bouncing.

**To go back to paused at any time,** delete the `EMAIL_METHOD=smtp` line from
`.env` (or delete `.env` entirely). Finished episodes then land in the share
folder and the app shows a "collect your episode here" notice with the path,
exactly as the pilot did. Nothing breaks and no code changes — the pause is a
first-class state, not a failure mode.

---

## 4. How to run one episode — the hands-off way (the team)

This is how the pilot was made, and the way to make episode 2.

1. **Create a task on the board** saying what the episode is about. One line
   is enough: *"Episode 2: <topic>. 22 minutes, solo host, same tone as the
   pilot."* Assign it to the **Showrunner**.
2. **Wait for the source-approval gate.** The Research Lead finds 8–15
   sources and the Showrunner brings you a shortlist. You approve or reject
   each one. This is the only point where the pipeline stops for you — by
   design, so no episode is ever built on sources you didn't see.
3. **Everything after that is automatic**: outline → script → editorial
   fact-check → render → mastering → QA → delivery. You'll get a status note
   at each stage.
4. **Collect the episode** from the output folder (or your inbox, once the
   email decision above is made).

Expect roughly a day of wall-clock for a 22-minute episode, most of it GPU
render time.

---

## 5. How to run one episode — the hands-on way (the app)

Use this when you already have a script, or want to drive it yourself.

**First, start ComfyUI.** The app can't render without it. Nothing else needs
starting.

**Then launch the app:** double-click `start_app.bat` in
`_podcast_application`. First run creates a `.venv` and installs dependencies
(~1 minute); later runs just start. Open <http://127.0.0.1:8000>.

Then, in the browser:

| Step | Where | What happens |
|---|---|---|
| 1 | **Status** (`/`) | Shows live ComfyUI reachability, your caps, the $0 budget banner, and the delivery-paused notice. If ComfyUI isn't running it says **UNREACHABLE** in red with the exact error — that's honest, not a bug. |
| 2 | **New Episode** (`/episodes/new`) | Topic, format, tone, cadence, speed (0.8×–1.3×), target length, audience level, voice(s), recipients. |
| 3 | **Source Review** | Your approval gate. Add sources, approve or remove each. The gate closes only when every source has a decision and at least one is approved. |
| 4 | **Script Review** | Locked until the gate closes. Paste your script (`[HOST_A]` speaker tags, `[PAUSE:1.0s]` timing tags) and citation map. Click "Mark script ready." |
| 5 | **Start render** | Chunks the script by speaker turn and renders each one serially through ComfyUI. If the projected time exceeds your 3-hour cap you get a confirm screen first — nothing hits the GPU until you say yes. |
| 6 | **Run mastering & export** | Concatenates chunks with real silence for each `[PAUSE]`, applies speed, normalizes to −16 LUFS, exports WAV + 192 kbps stereo MP3 + 96 kbps mono email MP3 with ID3 tags. |
| 7 | **Run QA** | Checks duration, per-chunk clipping, silence gaps over 3 s, and speaker-voice match. Writes a QA report and, on a pass, show notes. |
| 8 | **Collect** | Download buttons for all three files on the episode page. On a QA pass the email MP3 is also copied to your share folder. |

**Closing the browser doesn't stop a render** — it runs in the app process.
And if the *app* dies mid-render, restarting it resumes from the first
unfinished chunk. You lose at most the one chunk that was in flight.

---

## 6. How to run it *regularly*

Nothing about either path assumes a one-off.

**✅ Your chosen cadence: you file a task each time.** This is live now and
needed no setup. Whenever you want an episode, file one board task with the
topic and assign it to the **Showrunner** — thirty seconds of your time. The
team runs the pipeline; you spend about five minutes at the source gate; you
collect the finished episode. No schedule runs behind your back, no ticket
opens without you asking for it, and there is no upper or lower limit on how
often you do it.

The exact words are enough: *"Episode 2: <topic>. 22 minutes, solo host, same
tone as the pilot."*

**If you later change your mind:** I can put the Showrunner on a recurring
weekly trigger that opens the next episode ticket on its own and pings you for
the topic. You'd still approve sources — that gate never goes away. Say the
word and I'll wire it, including the day of the week you want it to land.

**A standing format, so you stop re-deciding.** The pilot set defaults worth
reusing: 22 minutes, solo male host, investigative-briefing tone, 12 approved
sources, −16 LUFS. If you confirm those as the house format, every future
episode inherits them and the only per-episode question is the topic. In the
app, the **Settings** page does the same job: save a tone/cadence preset, a
voice profile, and a recipient list once, and they're offered as defaults on
every New Episode form after that.

---

## 7. Everything the system can do (the feature tour)

**Episode production**
- Topic/URL intake with format, tone, cadence, target length, audience level
- Speed control from 0.8× to 1.3×
- Multi-speaker scripts — `[HOST_A]`, `[HOST_B]`, each mapped to its own voice
- `[PAUSE:x]` tags become real measured silence in the final audio, additive
  across chunk boundaries rather than double-counted

**Your control points**
- **Source-approval gate** — enforced in the server, not just hidden in the
  UI. A direct POST to a later stage is refused the same as a click would be.
  No episode gets built on sources you didn't approve.
- **Render-time cap** — 3 hours by default. Over it, you get an explicit
  confirm screen with the projection before anything is submitted.
- **Recipient allow-list** — only your three confirmed addresses. A typed-in
  address outside that set is rejected server-side, and a saved recipient list
  can't smuggle one in either.
- **$0 budget** — free/open tooling only; Chatterbox TTS, ComfyUI, ffmpeg. No
  paid service anywhere in the pipeline.

**Rendering**
- Serial chunked rendering, one job at a time (one GPU, one job)
- Crash and reboot resumable — verified for real with a `kill -9` mid-render
- 10-minute per-chunk timeout; out-of-memory backoff halves the chunk size and
  re-splits *only* the failing chunk
- Measured progress — real elapsed time and a projection from this job's own
  chunk times. No fake percentage, no spinner.
- ComfyUI workflow node ids discovered dynamically, so the Audio Engineer can
  swap in a different Chatterbox package with no code change

**Mastering and QA**
- Two-pass −16 LUFS loudness normalization (measure, then apply)
- Three exports: WAV master, 192 kbps stereo archive, 96 kbps mono email copy
  with ID3 tags
- QA checks duration floor, per-chunk clipping, silence gaps over 3 s, and
  speaker-voice match; re-renders only the chunks it flagged
- QA states plainly what it *can't* check — mispronunciation has no automated
  proxy without a paid speech-recognition service, so the report says so
  instead of pretending

**Library and delivery**
- Every episode listed and filterable by status, with settings, source
  decisions, script, citation map, render progress, downloads, QA report and
  show notes on one page
- Auto-generated show notes with timestamped segments and the source list
- Delivery is idempotent and logged — never automatic, never a double-send,
  never to an unapproved address
- No credential is ever stored, logged, or displayed; only environment
  variable *names* appear anywhere in the repo or its history

**Settings**
- Voice profiles with a **30-second sample render** audition that runs through
  the exact same ComfyUI workflow and reference clip the real render uses, so
  what you hear is what you get. It refuses to run while an episode render is
  active.
- Tone and cadence presets, offered as suggestions on New Episode
- Saved recipient lists, restricted to your approved addresses

**Email delivery** — live since 2026-10-05. On a QA pass the app emails the
episode's MP3 to that episode's approved recipients, over Gmail SMTP on port
465. Sends are idempotent (a QA re-run cannot re-send), restricted to the
three approved addresses, and an episode over the 20 MB cap goes to the share
folder instead of bouncing. Resend is manual, explicit, and logged.

**What it deliberately does not do**
- Write research or scripts automatically — the app expects a script; the
  agent team is what produces one

---

## 8. If something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| Status screen shows ComfyUI **UNREACHABLE** | ComfyUI isn't running | Start ComfyUI, reload. The app is telling the truth, not failing. |
| Mastering step fails naming `ffmpeg` or `ffprobe` | Not on PATH | Install ffmpeg and add it to PATH. Your machine has 8.0.1. |
| A render stalls on one chunk | Chunk hit the 10-minute timeout | The app retries with a smaller chunk automatically. Watch the render page. |
| Render was interrupted | App or machine restarted | Restart the app. It resumes from the first unfinished chunk on its own. |
| Episode finished but no email | `EMAIL_METHOD` isn't set, or the episode is over the 20 MB cap | Collect it from the share folder. Check `.env` still has all three lines (section 3a); the Status screen shows whether delivery is paused. |
| Email hangs, then fails with a timeout | Something is sending on port 587, which this network blocks | Remove any `SMTP_PORT` line from `.env` so it falls back to the 465 default. |
| Email fails with `SMTPAuthenticationError` | App password wrong, or 2-Step Verification was turned off | Re-create the app password at <https://myaccount.google.com/apppasswords> and update `.env`. Never use your normal Google password — Gmail rejects it for SMTP. |
| Email fails naming `SMTP_USER / SMTP_PASS` | `EMAIL_METHOD=smtp` is set but the credential names aren't | That's the app refusing to half-work. Fill in all three lines in `.env`, or remove `EMAIL_METHOD` to go back to paused. |

**Requirements, in full:** Python 3.11+, ComfyUI, and ffmpeg/ffprobe on PATH.
No Node, no npm, no database server. You already have all three.

---

## 9. Where things live

| What | Where |
|---|---|
| The app | `C:\Users\alfre\Desktop\_desktop\_projects\_podcast_application` |
| Episode outputs | `...\_ComfyUI\output\podcast_foundry\<date>_<slug>\` |
| Share folder (paused delivery) | `...\_podcast_application\_output_podcast_folder` |
| Config (no secrets) | `...\_podcast_application\config\app_config.json` |
| Your Gmail app password | `...\_podcast_application\.env` — you create it (section 3a), git ignores it, nothing else reads it |
| ComfyUI workflow | `...\_podcast_application\config\comfyui_workflow.json` |
| Voice reference clips | `...\_ComfyUI\input\voice_a.wav`, `voice_b.wav` |
| App docs | `README.md` (feature detail), `HANDOFF.md` (move to another machine) |
| GitHub | `github.com/cursorcanis/podcast-application` |

## 10. Who to ask

| Topic | Agent |
|---|---|
| Episode topic, tone, schedule | Showrunner |
| Sources and fact-checking | Research Lead |
| Script, format, voice tags | Scriptwriter |
| Voices, rendering, audio quality, delivery | Audio Engineer |
| The app itself | App Engineer |
| Anything needing a decision | Chief of Podcast Operations (me) |
