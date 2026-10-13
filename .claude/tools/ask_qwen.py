"""Hand a task to the local Qwen model in LM Studio and print its answer.

Used by the pf-* Claude subagents (and the lead session) to offload bulk
drafting — research digests, script drafts, first-pass reviews — to the
local model instead of spending Claude compute. Stdlib only, so any Python
works:

    python .claude/tools/ask_qwen.py "Summarize this file" --file app/qa.py
    python .claude/tools/ask_qwen.py --prompt-file task.md --out draft.md
    python .claude/tools/ask_qwen.py --check        # is the model up?

Endpoint: $QWEN_URL, else http://127.0.0.1:65535/v1.
Model:    $QWEN_MODEL, else huihui-qwen3.6-35b-a3b-claude-4.7-opus-abliterated-mtp.
Qwen's output is a draft: the calling agent verifies it before using it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

URL = os.environ.get("QWEN_URL", "http://127.0.0.1:65535/v1").rstrip("/")
MODEL = os.environ.get("QWEN_MODEL", "huihui-qwen3.6-35b-a3b-claude-4.7-opus-abliterated-mtp")
SYSTEM = (
    "You are a collaborator on Podcast Foundry, a local FastAPI app that turns "
    "scripts into podcast episodes (ComfyUI + Chatterbox TTS, ffmpeg mastering, "
    "QA, Gmail delivery). Be concrete and accurate; say when you are unsure."
)


def _request(path: str, body: dict | None = None, timeout: float = 10) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        URL + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check() -> int:
    try:
        ids = [m["id"] for m in _request("/models").get("data", [])]
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"Qwen unreachable at {URL}: {exc}. Start LM Studio's server on that port "
              f"and load {MODEL}.", file=sys.stderr)
        return 1
    if MODEL not in ids:
        print(f"LM Studio is up at {URL} but {MODEL} is not available. Models: {ids}",
              file=sys.stderr)
        return 1
    print(f"OK: {MODEL} at {URL}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt", nargs="?", help="task text (or use --prompt-file / stdin)")
    ap.add_argument("--prompt-file", help="read the task from this file")
    ap.add_argument("--file", action="append", default=[], help="attach a file's text (repeatable)")
    ap.add_argument("--system", default=SYSTEM)
    ap.add_argument("--max-tokens", type=int, default=6000)
    ap.add_argument("--temperature", type=float, default=0.4)
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--out", help="write the answer here instead of stdout")
    ap.add_argument("--check", action="store_true", help="only check the model is reachable")
    args = ap.parse_args()

    if args.check:
        return check()

    if args.prompt_file:
        with open(args.prompt_file, encoding="utf-8") as fh:
            prompt = fh.read()
    elif args.prompt:
        prompt = args.prompt
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read()
    else:
        ap.error("give a prompt, --prompt-file, or pipe text on stdin")

    for path in args.file:
        with open(path, encoding="utf-8", errors="replace") as fh:
            prompt += f"\n\n--- {path} ---\n{fh.read()}"

    try:
        reply = _request("/chat/completions", {
            "model": MODEL,
            "messages": [{"role": "system", "content": args.system},
                         {"role": "user", "content": prompt}],
            "max_tokens": args.max_tokens,
            "temperature": args.temperature,
        }, timeout=args.timeout)
    except urllib.error.HTTPError as exc:
        print(f"LM Studio returned {exc.code}: {exc.read()[:500]!r}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as exc:
        print(f"Qwen unreachable at {URL}: {exc}", file=sys.stderr)
        return 1

    text = reply["choices"][0]["message"].get("content") or ""
    text = re.sub(r"(?s)<think>.*?</think>", "", text).strip()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {len(text.split())} words to {args.out}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
