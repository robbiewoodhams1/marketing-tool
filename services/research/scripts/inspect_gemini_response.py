"""DIAGNOSTIC ONLY - LOCAL, READ-ONLY. Makes NO network request of any kind.

Prints a structural map of a Gemini API response body that is ALREADY saved
to a local JSON file: every key, its type, and for lists/strings their
length - with base64-looking payloads redacted, never printed.

This exists because the live "Interactions" response shape did not match
either of the two shapes `research.gemini._find_output_image` currently
checks (see PR discussion): the real response has top-level keys `created,
id, model, object, service_tier, status, steps, updated, usage` (an
OpenAI-Chat-Completions-flavoured envelope around a Gemini-specific `steps`
field), and `steps[].model_output.content[]` items evidently do not use
`{"type": "image", "data": ..., "mime_type": ...}` either, since the current
parser still reports "no image found". This script is how to find out what
they DO look like, from a response the user already has saved, without
spending another paid request just to look at its shape again.

Usage:
    python scripts/inspect_gemini_response.py --body-file /path/to/response.json

The file is never modified. Nothing here is imported by, or changes, the
production parser (`research.gemini._find_output_image`) - see that module's
docstring for the currently-implemented shapes; this script's findings are
meant to inform a *manual* follow-up edit there, not to be wired in itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

# A string this long or longer, made up almost entirely of base64 alphabet
# characters, is treated as a payload and redacted rather than printed - this
# is a heuristic for a diagnostic tool, not a security boundary.
BASE64_LIKE_MIN_LEN = 200
_BASE64_ALPHABET = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
MAX_STRING_PREVIEW = 80


def _looks_like_base64_blob(value: str) -> bool:
    if len(value) < BASE64_LIKE_MIN_LEN:
        return False
    sample = value[:2000]
    base64_chars = sum(1 for c in sample if c in _BASE64_ALPHABET)
    return base64_chars / len(sample) > 0.95


def describe(value: Any, path: str = "$") -> str:
    """One or more lines describing `value`'s structure at `path`. Redacts
    anything that looks like a base64 payload; truncates other long strings
    to a short preview instead of printing them in full."""
    lines: list[str] = []
    if isinstance(value, dict):
        lines.append(f"{path}: object with {len(value)} key(s): {sorted(value.keys())}")
        for key in sorted(value.keys()):
            lines.extend(describe(value[key], f"{path}.{key}").splitlines())
    elif isinstance(value, list):
        lines.append(f"{path}: array, length={len(value)}")
        # Every element's shape, not just the first - the whole point is to
        # see whether the elements are uniform or not.
        for index, item in enumerate(value):
            lines.extend(describe(item, f"{path}[{index}]").splitlines())
    elif isinstance(value, str):
        if _looks_like_base64_blob(value):
            lines.append(f"{path}: string, len={len(value)}, base64-looking -> REDACTED, not printed")
        else:
            preview = value if len(value) <= MAX_STRING_PREVIEW else value[:MAX_STRING_PREVIEW] + "..."
            lines.append(f"{path}: string, len={len(value)}, value={preview!r}")
    elif isinstance(value, bool):
        lines.append(f"{path}: bool, value={value}")
    elif isinstance(value, (int, float)):
        lines.append(f"{path}: {type(value).__name__}, value={value}")
    elif value is None:
        lines.append(f"{path}: null")
    else:
        lines.append(f"{path}: {type(value).__name__} (unhandled)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/inspect_gemini_response.py",
        description="Print the structure of an already-saved Gemini response body. Makes no network request.",
    )
    parser.add_argument("--body-file", required=True, help="path to a local JSON file with the raw response body")
    args = parser.parse_args(argv)

    with open(args.body_file, encoding="utf-8") as f:
        text = f.read()
    try:
        data = json.loads(text)
    except ValueError as exc:
        print(f"error: {args.body_file} is not valid JSON: {exc}", file=sys.stderr)
        return 1

    print(f"=== structure of {args.body_file} ({len(text)} bytes as text) ===")
    print(describe(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
