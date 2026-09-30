"""Pure caption text segmentation/timing: given ONE scene's exact,
already-approved voiceover text (verbatim from `research.production` - never
an LLM rewrite, never ASR output) and that scene's actual measured duration,
deterministically split the text into readable, ordered, non-overlapping
caption segments covering `[0, duration_seconds]`.

No I/O, no ffmpeg, no image rendering, no external dependency of any kind -
this module only does text splitting and arithmetic, so it is trivially unit
testable and swappable: a future, smarter timing algorithm (e.g. one that
weights segments by estimated speech rate rather than raw character count)
only has to satisfy `segment_caption_text`'s signature/contract; nothing else
in the caption pipeline (`research.captions`, `research.ffmpeg_engine`) needs
to change.

Timing model (V1, deliberately simple - no ASR/AI alignment): each segment's
share of the scene's total duration is proportional to its share of the
scene's total character count. This is a coarse approximation (real speech
does not advance at a constant rate per character), but it is deterministic,
requires no additional provider call, and is good enough for V1's goal of
"readable, roughly-in-sync captions" - see the module docstring of
`research.assembly_v2` for how this fits the wider pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The name of THIS algorithm (character-proportional timing over
# sentence/phrase-aware wrapping) - folded into the wider caption policy
# string at the assembly layer (see
# `research.assembly_schema.CAPTION_POLICY_BURNED_V1`) so a future,
# differently-timed algorithm can never be mistaken for this one.
CAPTION_TIMING_ALGORITHM = "proportional-char-v1"

# Netflix/BBC-style subtitle guidance targets ~42 characters per line for
# English; used here as the per-segment wrap target, not a hard limit -
# see `_merge_short_tail` for why a segment can end up a little longer.
DEFAULT_MAX_CAPTION_CHARS = 42
# How much longer a merged segment may be than DEFAULT_MAX_CAPTION_CHARS
# before "avoid excessively long caption lines" outweighs "avoid a lone
# trailing word" (see `_merge_short_tail`).
DEFAULT_MAX_MERGE_CHARS = 52

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=\S)")


class CaptionError(Exception):
    """The given text/duration cannot be turned into caption segments."""


@dataclass(frozen=True)
class CaptionSegment:
    """One caption card, in the time-local frame of whatever it was
    segmented from (scene-local seconds here; `research.captions` is what
    shifts these onto the final video's own timeline)."""

    index: int
    text: str
    start_seconds: float
    end_seconds: float


def _split_into_sentences(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(stripped) if s.strip()]


def _wrap_sentence(sentence: str, max_chars: int) -> list[str]:
    """Greedy word-wrap: keep adding words to the current chunk while it
    stays within `max_chars`; a single word longer than `max_chars` still
    becomes its own chunk (never split mid-word - "preserve the exact
    underlying words")."""
    words = sentence.split()
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        added_len = len(word) + (1 if current else 0)
        if current and current_len + added_len > max_chars:
            chunks.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += added_len
    if current:
        chunks.append(" ".join(current))
    return chunks


def _merge_short_tail(chunks: list[str], max_merge_chars: int) -> list[str]:
    """"Avoid single-word captions unless necessary": if wrapping left a
    single trailing word by itself, fold it back into the previous chunk
    when the combined line is still reasonably short. Applied once per
    sentence, so it never joins words from two different sentences."""
    if len(chunks) < 2:
        return chunks
    if len(chunks[-1].split()) == 1:
        merged = f"{chunks[-2]} {chunks[-1]}"
        if len(merged) <= max_merge_chars:
            return [*chunks[:-2], merged]
    return chunks


def segment_caption_text(
    text: str,
    duration_seconds: float,
    *,
    max_chars: int = DEFAULT_MAX_CAPTION_CHARS,
    max_merge_chars: int = DEFAULT_MAX_MERGE_CHARS,
) -> tuple[CaptionSegment, ...]:
    """Split `text` into readable segments spanning exactly
    `[0, duration_seconds]`, preferring sentence/phrase boundaries.

    Guarantees (all exercised by tests, not just asserted here):
      * segments are returned in order, `index` 0..N-1
      * the first segment starts at 0.0 and the last ends at exactly
        `duration_seconds` - no gap, no overrun
      * segments never overlap: segment i's end equals segment i+1's start
      * every word of `text` appears, unmodified, in exactly one segment
      * calling this twice with the same arguments returns identical output

    Raises `CaptionError` for text with no readable content or a
    non-positive duration - callers (`research.captions`,
    `research.assembly_v2`) treat this as a hard, up-front failure, never a
    silently-empty caption track."""
    if not text or not text.strip():
        raise CaptionError("caption text must not be empty")
    if duration_seconds <= 0:
        raise CaptionError(f"caption duration must be positive, got {duration_seconds!r}")

    chunks: list[str] = []
    for sentence in _split_into_sentences(text):
        wrapped = _wrap_sentence(sentence, max_chars)
        chunks.extend(_merge_short_tail(wrapped, max_merge_chars))
    if not chunks:
        raise CaptionError(f"caption text {text!r} produced no readable segments")

    total_chars = sum(len(c) for c in chunks)
    segments: list[CaptionSegment] = []
    consumed_chars = 0
    last_index = len(chunks) - 1
    for i, chunk in enumerate(chunks):
        start = duration_seconds * (consumed_chars / total_chars)
        consumed_chars += len(chunk)
        # The last segment always ends at exactly duration_seconds - never
        # left short (or over) by float rounding of the proportional split.
        end = duration_seconds if i == last_index else duration_seconds * (consumed_chars / total_chars)
        segments.append(CaptionSegment(index=i, text=chunk, start_seconds=start, end_seconds=end))
    return tuple(segments)
