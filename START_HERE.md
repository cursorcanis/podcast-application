# Podcast Foundry — Start Here

A plain-language introduction to what you have, what state it's in, and how to run it.

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

**The app: code-complete, test-green, but not yet proven as a one-click
program.** All six milestones (M1–M6) are written, and I re-ran the full test
suite this run: **53 of 53 tests pass**, including the M6 settings suite that
the previous handoff note said had never been test-run. That closes the one
item that was gating M6.

Three honest caveats, none of which are your homework:

1. **No episode has ever been made through the app end to end.** I opened its
   database: zero episodes. The pilot was made by scripts the Audio Engineer
   ran by hand. The app's render engine *has* been proven against your real,
   live ComfyUI (a two-voice render producing real audio, plus a `kill -9`
   crash test proving it resumes without re-rendering finished chunks) — but
   a complete intake-to-delivery run inside the app hasn't happened yet.
2. **`start_app.bat` has never been double-clicked on Windows.** All testing
   happened on the Linux side of your machine. The Python logic is proven;
   the Windows launch experience is not.
3. ~~M6's code isn't committed to git yet.~~ **Resolved 2026-10-04** — M6 is
   now committed and pushed to `github.com/cursorcanis/podcast-application`.
   All six milestones are in the repo and the working folder is clean.

---

## 3. Your decisions, and the one thing still open

You answered three questions on POD-33 on **2026-10-04**. These are now the
standing decisions, and the rest of this document follows them.

| Decision | Your answer | Status |
|---|---|---|
| Email sending method | **AgentMail** (not Gmail SMTP) | Being built — see below |
| Episode cadence | **You file a board task each time** | Live now, no setup needed (section 6) |
| Next engineering step | **Commit and push M6** | Done 2026-10-04 |

**The one thing still open: AgentMail needs a credential before it can send.**

Picking AgentMail settled the conflict in your 2026-10-01 config answers
(AgentMail address + Gmail SMTP method, which don't compose). But AgentMail is
not currently connectable from this board — I checked, and it comes back
unavailable — so there is no bundled inbox to switch on. To send from an
AgentMail address the app needs its own AgentMail API key and from-address,
read from the environment by name (`AGENTMAIL_API_KEY`, `AGENTMAIL_FROM`), the
same way SMTP credentials would be. Nothing secret ever goes in a file, a
ticket, or a log.

So the work splits in two:

1. **Ours.** The App Engineer builds the `EMAIL_METHOD=agentmail` sender
   behind the delivery boundary that already exists, with tests. Tracked as a
   child ticket of POD-33. No input from you needed for this part.
2. **Yours, when you want it.** Create an AgentMail inbox, hand over the API
   key, and I set it as a local environment variable. The day that happens,
   delivery turns on with a config change.

**Until then, delivery stays paused** — which is exactly where the pilot is.
Finished episodes land in the share folder and the app shows a "collect your
episode here" notice with the path. Nothing is lost and nothing is waiting on
you. If you'd rather not bother with AgentMail at all, Gmail SMTP is already
written and tested and needs only a Google app password — say so and we flip
to that instead.

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

**What it deliberately does not do**
- Send email — paused. You chose AgentMail; the sender is being built and then
  needs an AgentMail API key (section 3). Episodes go to the share folder
  meanwhile.
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
| Episode finished but no email | Delivery is paused by design | Collect it from the output folder or the share folder. Email turns on once the AgentMail credential exists (section 3). |

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
