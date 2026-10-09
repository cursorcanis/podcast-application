"""Turn an uploaded script document into render-ready script text.

Accepts .txt, .md and .docx — the formats a 4,000–5,000 word script is
realistically written in — using only the standard library (a .docx is a zip
of XML; no python-docx dependency, so nothing new to install).

The cleaned text keeps the app's own tags exactly as the chunker reads them
(`[HOST_A]`-style speaker tags and `[PAUSE:1.5s]`), and removes what a TTS
voice would otherwise read aloud: Markdown syntax, link URLs, and bracketed
stage directions like `[MUSIC IN]` or `[SFX: door]`. Section structure is
kept as timing: a heading is spoken followed by a short pause, and a
horizontal rule / scene break becomes a slightly longer pause.

A script with no speaker tags at all is a solo narration and is attributed to
the narrator voice chosen on the upload form.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree

from . import chunking

SUPPORTED_EXTENSIONS = (".txt", ".md", ".markdown", ".docx")
MIN_WORDS = 20
MAX_WORDS = 20000
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

HEADING_PAUSE_SECONDS = 1.0
SECTION_BREAK_PAUSE_SECONDS = 1.5

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_RULE_RE = re.compile(r"^\s{0,3}([-*_=])(\s*\1){2,}\s*$")
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
_BLOCKQUOTE_RE = re.compile(r"^\s*>\s?")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:[^)]*)\)")
_BARE_URL_RE = re.compile(r"https?://\S+")
_EMPHASIS_RE = re.compile(r"(\*\*|\*|~~|`)(?=\S)(.+?)(?<=\S)\1")
# Underscore emphasis only at word edges, so HOST_A / snake_case survive.
_UNDERSCORE_EMPHASIS_RE = re.compile(r"(?<!\w)(__|_)(?=\S)(.+?)(?<=\S)\1(?!\w)")
_BRACKET_TAG_RE = re.compile(r"\[([^\]\n]{1,80})\]")
_SPEAKER_LABEL_RE = re.compile(r"^\s*(HOST_[A-Z0-9]+|NARRATOR|GUEST)\s*:\s*", re.IGNORECASE)


class ScriptImportError(ValueError):
    """The uploaded file can't be turned into a script. The message names
    the exact problem and is shown to the user verbatim."""


@dataclass(frozen=True)
class ImportedScript:
    text: str                 # render-ready script, tags included
    word_count: int           # spoken words only (tags excluded)
    estimated_minutes: float  # at 150 wpm, 1.0x
    speakers: list[str]       # speaker tags the script uses


def decode_upload(filename: str, data: bytes) -> str:
    """Raw bytes of an uploaded file -> plain text, by extension."""
    name = (filename or "").lower()
    ext = next((e for e in SUPPORTED_EXTENSIONS if name.endswith(e)), None)
    if ext is None:
        raise ScriptImportError(
            f"'{filename}' is not a supported script file. Upload one of: "
            f"{', '.join(SUPPORTED_EXTENSIONS)}."
        )
    if not data:
        raise ScriptImportError(f"'{filename}' is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ScriptImportError(
            f"'{filename}' is {len(data) / 1_048_576:.1f} MB — over the "
            f"{MAX_UPLOAD_BYTES // 1_048_576} MB limit for a script file."
        )
    if ext == ".docx":
        return _docx_to_text(filename, data)
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding == "utf-16" and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
            continue  # only trust utf-16 when the file says so with a BOM
        return text
    raise ScriptImportError(f"Could not read '{filename}' as text (tried UTF-8 and Windows-1252).")


def _docx_to_text(filename: str, data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ScriptImportError(f"'{filename}' is not a valid Word .docx file ({exc}).") from exc
    root = ElementTree.fromstring(xml)
    paragraphs: list[str] = []
    for para in root.iter(f"{_W_NS}p"):
        parts: list[str] = []
        for node in para.iter():
            if node.tag == f"{_W_NS}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W_NS}tab":
                parts.append(" ")
            elif node.tag in (f"{_W_NS}br", f"{_W_NS}cr"):
                parts.append("\n")
        style = para.find(f"{_W_NS}pPr/{_W_NS}pStyle")
        text = "".join(parts)
        if style is not None and "heading" in (style.get(f"{_W_NS}val") or "").lower() and text.strip():
            text = f"# {text}"  # let the Markdown heading rule add the pause
        paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _clean_bracket_tag(match: re.Match) -> str:
    full = match.group(0)
    if chunking.SPEAKER_TAG_RE.fullmatch(full):
        return full.upper()
    pause = chunking.PAUSE_TAG_RE.fullmatch(full)
    if pause:
        return f"[PAUSE:{pause.group(1)}s]"
    # Anything else in square brackets is a stage direction / production
    # note ([MUSIC IN], [SFX: thunder], [Cut to interview]) — never spoken.
    return " "


def clean_script_text(raw: str) -> str:
    """Markdown/stage-direction cleanup that leaves only speakable text plus
    the app's own speaker and pause tags."""
    lines_out: list[str] = []
    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _RULE_RE.match(line):
            lines_out.append(f"[PAUSE:{SECTION_BREAK_PAUSE_SECONDS}s]")
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            title = heading.group(1).strip()
            if title:
                if not title.endswith((".", "!", "?", ":")):
                    title += "."
                # Pause *before* the heading, then let it run into the next
                # paragraph — a heading on its own would be a 2-word TTS
                # chunk, which is where Chatterbox misbehaves most.
                lines_out.append(f"[PAUSE:{HEADING_PAUSE_SECONDS}s] {title}")
            continue
        line = _BLOCKQUOTE_RE.sub("", line)
        line = _LIST_MARKER_RE.sub("", line)
        line = _SPEAKER_LABEL_RE.sub(lambda m: f"[{m.group(1).upper()}] ", line)
        lines_out.append(line)
    text = "\n".join(lines_out)
    text = _IMAGE_RE.sub(" ", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _BARE_URL_RE.sub(" ", text)
    for _ in range(2):  # nested emphasis like ***bold italic***
        text = _EMPHASIS_RE.sub(r"\2", text)
        text = _UNDERSCORE_EMPHASIS_RE.sub(r"\2", text)
    text = _BRACKET_TAG_RE.sub(_clean_bracket_tag, text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def spoken_word_count(script: str) -> int:
    no_tags = chunking.PAUSE_TAG_RE.sub(" ", chunking.SPEAKER_TAG_RE.sub(" ", script))
    return len([w for w in no_tags.split() if any(ch.isalnum() for ch in w)])


def prepare_script(raw_text: str, *, narrator: str) -> ImportedScript:
    """Clean an uploaded script and attribute untagged text to `narrator`.
    Raises ScriptImportError if what's left is too short or too long to be
    an episode."""
    text = clean_script_text(raw_text)
    # [NARRATOR] is the generic solo-voice tag; it means whichever voice was
    # picked as narrator on the upload form.
    text = re.sub(r"\[NARRATOR\]", f"[{narrator}]", text, flags=re.IGNORECASE)
    if not chunking.SPEAKER_TAG_RE.search(text):
        text = f"[{narrator}]\n{text}"
    words = spoken_word_count(text)
    if words < MIN_WORDS:
        raise ScriptImportError(
            f"The script has only {words} speakable word(s) after cleanup — "
            f"at least {MIN_WORDS} are needed."
        )
    if words > MAX_WORDS:
        raise ScriptImportError(
            f"The script has {words:,} words — over the {MAX_WORDS:,}-word limit "
            "for a single episode. Split it into parts and upload each one."
        )
    speakers = sorted({m.group(1).upper() for m in chunking.SPEAKER_TAG_RE.finditer(text)})
    return ImportedScript(
        text=text,
        word_count=words,
        estimated_minutes=round(words / chunking.WORDS_PER_MINUTE, 1),
        speakers=speakers,
    )


def title_from_filename(filename: str) -> str:
    stem = re.sub(r"\.[^.]+$", "", (filename or "").replace("\\", "/").rsplit("/", 1)[-1])
    stem = re.sub(r"[_\-]+", " ", stem).strip()
    return stem[:1].upper() + stem[1:] if stem else "Uploaded script"
