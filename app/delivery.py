"""Email delivery + the share-folder handoff — M5 (POD-12).

Delivery is the one stage the Board has explicitly PAUSED today: `EMAIL_METHOD`
is unset (see POD-7), so nothing in this app ever sends a real email. This
module builds the delivery *shape* behind that decision so the flag-off state
is honest and the future activation is a config change, not a code change:

- On a QA pass, instead of sending, the app writes a `delivery_records` row
  with `status='paused'`, copies the final email MP3 to `SHARE_LOCATION`, and
  surfaces a "collect your episode here" notice with that path — the episode
  still gets *handed off* to the Board even though no mail is sent.
- The email-provider interface lives behind `EMAIL_METHOD` (currently only
  `smtp` exists, and only activates when the method is set). When POD-7
  resolves and a credential is issued via the environment (`SMTP_USER` /
  `SMTP_PASS`), setting `EMAIL_METHOD=smtp` flips the same `send()` path on
  with **no code change**.
- Resend stays visible in the UI but is disabled while the method is unset,
  with the pause reason shown. When active, Resend is explicit, idempotent,
  and logged — never automatic, never a double-send.

Idempotence is the rule, not a nicety: `resend()` refuses to re-send an
episode whose latest delivery record is already `sent`, and only ever sends to
the episode's recorded recipient list (already validated at intake against
allowed_recipient_emails) — so a retry cannot sneak an unapproved recipient in.

Nothing here prints, logs, or stores a credential. `SMTP_USER`/`SMTP_PASS`
(and `SMTP_HOST`/`SMTP_PORT`) are read from the environment by name only, on
demand, and never echoed.
"""
from __future__ import annotations

import json
import shutil
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from . import episodes
from .config import config
from .db import get_connection

DELIVERY_STATUSES = ("not_attempted", "paused", "sent", "failed")

# The pause reason is shown verbatim to the Board, so it names the exact
# unblocker (POD-7 / EMAIL_METHOD), never a generic "not available".
PAUSE_REASON_NO_METHOD = (
    "Email delivery is paused (EMAIL_METHOD is not set — see POD-7). "
    "The episode has been copied to SHARE_LOCATION instead; collect it there."
)


class DeliveryError(ValueError):
    """A delivery step failed or was refused. The message names the exact
    reason (paused, already sent, bad provider config, actual SMTP error) —
    never 'something went wrong'."""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pause_reason() -> str | None:
    """Human-readable reason delivery is paused, or None when it is active.
    The single source of truth for what the Resend button shows."""
    if config.email_paused:
        return PAUSE_REASON_NO_METHOD
    return None


def latest_delivery_record(episode_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM delivery_records WHERE episode_id = ? ORDER BY id DESC LIMIT 1",
            (episode_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delivery_view(episode_id: int) -> dict[str, Any]:
    """The full state the episode page needs to render the delivery card:
    status, pause state + reason, share path, and the latest record (with
    recipients decoded to a Python list) — all non-secret."""
    record = latest_delivery_record(episode_id)
    if record is not None:
        record = dict(record)
        try:
            record["recipients_sent_to"] = json.loads(record.get("recipients_sent_to") or "[]")
        except (json.JSONDecodeError, TypeError):
            record["recipients_sent_to"] = []
    return {
        "paused": config.email_paused,
        "reason": pause_reason(),
        "record": record,
        "share_location": config.share_location,
    }


def _share_dest(episode: dict) -> Path:
    """Where the final email MP3 lands in SHARE_LOCATION. The filename keeps
    the episode slug so a folder full of handoffs stays identifiable."""
    from .postprod import slugify

    src = episode.get("email_mp3_path")
    if not src:
        raise DeliveryError(
            f"Episode {episode['id']} has no email MP3 yet — run mastering/export first."
        )
    src_path = Path(src)
    share_dir = Path(config.share_location)
    if not share_dir.exists():
        raise DeliveryError(
            f"SHARE_LOCATION does not exist on disk: {config.share_location}. "
            "Create it (or correct the env var / config) before delivery can hand off a file."
        )
    dest = share_dir / f"{slugify(episode['title'])}_{src_path.name}"
    return dest


def copy_to_share_location(episode: dict) -> str:
    """Copy the final email MP3 into SHARE_LOCATION and return the destination
    path. Idempotent: re-copying over the same name is a no-op for the Board's
    collection (same name, fresh bytes). Never deletes anything already there."""
    src = episode.get("email_mp3_path")
    if not src:
        raise DeliveryError(
            f"Episode {episode['id']} has no email MP3 yet — run mastering/export first."
        )
    src_path = Path(src)
    if not src_path.exists():
        raise DeliveryError(
            f"Email MP3 does not exist on disk: {src}. The export step may not have completed."
        )
    dest = _share_dest(episode)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src_path), str(dest))
    return str(dest)


def _record(conn, episode_id: int, *, status: str, reason: str | None = None,
            recipients: list[str] | None = None, provider_message_id: str | None = None) -> int:
    cur = conn.execute(
        """
        INSERT INTO delivery_records (
            episode_id, status, provider_message_id, recipients_sent_to, attempted_at, reason
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            episode_id,
            status,
            provider_message_id,
            json.dumps(recipients) if recipients is not None else None,
            utcnow_iso(),
            reason,
        ),
    )
    return int(cur.lastrowid)


def run_delivery_on_qa_pass(episode_id: int) -> dict[str, Any]:
    """Invoked by app/qa.py the moment QA passes. In the paused state (today)
    this records `status='paused'`, hands the MP3 off to SHARE_LOCATION, and
    leaves a reason — no send, no network. When EMAIL_METHOD is set it would
    send instead; the interface is the same so activation is config-only.

    Idempotent: refuses to run if the episode already has a `sent` record —
    a QA re-run after a successful delivery must never re-send."""
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT * FROM delivery_records WHERE episode_id = ? ORDER BY id DESC LIMIT 1",
            (episode_id,),
        ).fetchone()
        if existing and existing["status"] == "sent":
            raise DeliveryError(
                f"Episode {episode_id} was already delivered — refusing to re-send on a QA re-run."
            )
        episode_row = conn.execute(
            "SELECT * FROM episodes WHERE id = ?", (episode_id,)
        ).fetchone()
        if episode_row is None:
            raise DeliveryError(f"Episode {episode_id} does not exist.")
        episode = dict(episode_row)
        recipients = json.loads(episode.get("recipient_emails") or "[]")
    finally:
        conn.close()

    if config.email_paused:
        # Paused handoff: copy to SHARE_LOCATION, record 'paused', no send.
        share_path = copy_to_share_location(episode)
        conn = get_connection()
        try:
            _record(
                conn, episode_id,
                status="paused",
                reason=PAUSE_REASON_NO_METHOD,
                recipients=recipients,
            )
            conn.execute(
                "UPDATE episodes SET status = 'delivered_paused', updated_at = ? WHERE id = ?",
                (utcnow_iso(), episode_id),
            )
            conn.commit()
        finally:
            conn.close()
        return {
            "episode_id": episode_id,
            "status": "paused",
            "share_path": share_path,
            "reason": PAUSE_REASON_NO_METHOD,
            "sent": False,
        }

    # Active path (EMAIL_METHOD set): record not_attempted then send.
    return resend(episode_id)


def _smtp_send(episode: dict, recipients: list[str], *, subject: str, body: str) -> str:
    """Send via SMTP using EMAIL_METHOD env names. Only ever reads
    SMTP_USER / SMTP_PASS / SMTP_HOST / SMTP_PORT from the environment by
    name — never stored, logged, or echoed. Returns the provider message id
    (or a derived id when the provider returns none)."""
    import os

    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT") or 465)
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    sender = config.values.get("SENDER_ADDRESS") or user
    if not user or not password or not sender:
        raise DeliveryError(
            "EMAIL_METHOD is set but SMTP_USER / SMTP_PASS are missing from the "
            "environment. Delivery cannot send until those are configured (POD-7)."
        )
    if not recipients:
        raise DeliveryError("No recipients recorded on this episode to send to.")

    mp3 = episode.get("email_mp3_path")
    if not mp3 or not Path(mp3).exists():
        raise DeliveryError(f"Email MP3 is missing: {mp3}. Export must complete before sending.")

    size_mb = Path(mp3).stat().st_size / (1024 * 1024)
    if size_mb > config.max_attachment_mb:
        raise DeliveryError(
            f"Email MP3 {mp3} is {size_mb:.1f}MB, over the MAX_ATTACHMENT_MB={config.max_attachment_mb} cap. "
            "Not sending — re-export at a lower bitrate or shorten the episode."
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    data = Path(mp3).read_bytes()
    filename = Path(mp3).name
    msg.add_attachment(data, maintype="audio", subtype="mpeg", filename=filename)

    # Two TLS modes, picked by port rather than guessed: 465 speaks TLS from
    # the first byte (implicit/SMTPS), 587 negotiates it with STARTTLS. Using
    # the wrong one hangs until timeout rather than failing clearly, so this
    # is explicit. 465 is the default because outbound 587 is blocked on the
    # Board's network (verified 2026-10-05: 25/587/2525 all time out, 465
    # connects) — a network we cannot change from here.
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=60) as server:
                server.login(user, password)
                result = server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=60) as server:
                server.starttls()
                server.login(user, password)
                result = server.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        raise DeliveryError(f"SMTP send failed: {type(exc).__name__}: {exc}") from exc

    # Provider may return a dict of failures; if any recipient refused, fail
    # specific rather than pretending success.
    refused = result or {}
    if refused:
        raise DeliveryError(f"SMTP send refused for some recipients: {refused}")

    return f"smtp:{host}:{utcnow_iso()}"


def resend(episode_id: int) -> dict[str, Any]:
    """Explicit, idempotent, logged resend. Refuses when delivery is paused
    (returns the pause state rather than sending) so the UI's disabled button
    and this path can never diverge; refuses when the latest record is
    already `sent` (no double-send); and sends only to the episode's recorded
    recipients (never an address outside that set)."""
    conn = get_connection()
    try:
        episode_row = conn.execute(
            "SELECT * FROM episodes WHERE id = ?", (episode_id,)
        ).fetchone()
        if episode_row is None:
            raise DeliveryError(f"Episode {episode_id} does not exist.")
        episode = dict(episode_row)
        recipients = json.loads(episode.get("recipient_emails") or "[]")
        existing = conn.execute(
            "SELECT * FROM delivery_records WHERE episode_id = ? ORDER BY id DESC LIMIT 1",
            (episode_id,),
        ).fetchone()
    finally:
        conn.close()

    if config.email_paused:
        reason = PAUSE_REASON_NO_METHOD
        return {
            "episode_id": episode_id,
            "status": "paused",
            "sent": False,
            "reason": reason,
        }

    if existing and existing["status"] == "sent":
        raise DeliveryError(
            f"Episode {episode_id} was already delivered (provider id "
            f"{existing['provider_message_id']!r}); resend is disabled to avoid a double-send."
        )

    # Validate recipients against the allowed set at send time (defense in
    # depth — the episode record itself was already validated at intake).
    allowed = set(episodes.allowed_recipient_emails(conn=None))
    rejected = [r for r in recipients if r not in allowed]
    if rejected:
        raise DeliveryError(
            f"Cannot send to unapproved recipient(s): {', '.join(rejected)}."
        )

    subject = f"{episode['title']} — Podcast Foundry episode"
    body = (
        f"{episode['title']}\n\n"
        f"{episode['topic_or_query']}\n\n"
        f"Attached: {Path(episode['email_mp3_path'] or '').name}"
    )
    provider_id = _smtp_send(episode, recipients, subject=subject, body=body)

    conn = get_connection()
    try:
        _record(
            conn, episode_id,
            status="sent",
            recipients=recipients,
            provider_message_id=provider_id,
        )
        conn.execute(
            "UPDATE episodes SET status = 'delivered', updated_at = ? WHERE id = ?",
            (utcnow_iso(), episode_id),
        )
        conn.commit()
    finally:
        conn.close()

    # The share-folder handoff is not a fallback for a paused email — the
    # Board wants both: a local archive copy in SHARE_LOCATION alongside
    # every real send, not just while EMAIL_METHOD is unset. Runs after the
    # send and its delivery_records row are already committed, so a copy
    # failure here is reported specifically without putting an email that
    # genuinely sent into a false 'failed' state.
    share_path = copy_to_share_location(episode)

    return {
        "episode_id": episode_id,
        "status": "sent",
        "sent": True,
        "provider_message_id": provider_id,
        "recipients": recipients,
        "share_path": share_path,
    }