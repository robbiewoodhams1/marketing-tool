"""Content classification: input evidence, result schema and taxonomies.

Pure data + validation: no I/O, no LLM calls, no database. A provider (see
`research.llm`) turns a `ClassificationInput` into a `ClassificationResult`.
The result keeps, per field, a confidence and a short evidence excerpt so the
question "why this label?" can always be answered.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import Enum
from typing import Any


class ClassificationError(Exception):
    """Base class for classification failures."""


class ClassificationInputError(ClassificationError, ValueError):
    """The evidence supplied for classification is not valid."""


class ClassificationSchemaError(ClassificationError, ValueError):
    """A classification result does not match the schema."""


# Version of the ClassificationResult shape (fields + taxonomies), stored with
# every persisted interpretation. Bump when either changes incompatibly.
CLASSIFICATION_SCHEMA_VERSION = "classification-v1"


# --- Taxonomies -------------------------------------------------------------
# Small, initial controlled vocabularies. To extend, add a member: validation,
# the prompt and the tool schema all derive from these enums.


class HookType(str, Enum):
    CURIOSITY = "curiosity"
    PAIN = "pain"
    FEAR = "fear"
    PROMISE = "promise"
    QUESTION = "question"
    CONTRADICTION = "contradiction"
    SPECIFICITY = "specificity"
    AUTHORITY = "authority"
    STORY = "story"
    CONTROVERSY = "controversy"
    LIST = "list"
    RESULT = "result"
    MISTAKE = "mistake"
    COMPARISON = "comparison"
    OTHER = "other"


class ContentFormat(str, Enum):
    TUTORIAL = "tutorial"
    LISTICLE = "listicle"
    STORY = "story"
    CASE_STUDY = "case_study"
    MYTH_BUSTING = "myth_busting"
    COMPARISON = "comparison"
    REACTION = "reaction"
    FAQ = "faq"
    DEMONSTRATION = "demonstration"
    NEWS_COMMENTARY = "news_commentary"
    PROBLEM_SOLUTION = "problem_solution"
    REVIEW = "review"
    OTHER = "other"


class Emotion(str, Enum):
    CURIOSITY = "curiosity"
    FEAR = "fear"
    RELIEF = "relief"
    EXCITEMENT = "excitement"
    FRUSTRATION = "frustration"
    CONFIDENCE = "confidence"
    SURPRISE = "surprise"
    URGENCY = "urgency"
    AMUSEMENT = "amusement"
    NEUTRAL = "neutral"
    OTHER = "other"


# --- Input ------------------------------------------------------------------

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _clean_text(value: str | None) -> str | None:
    """Blank/whitespace-only text is treated as missing, never fabricated."""
    if value is None:
        return None
    value = value.strip()
    return value or None


@dataclass(frozen=True)
class ClassificationInput:
    """The evidence available for one video. Description and transcript are optional."""

    video_id: str
    title: str
    description: str | None = None
    transcript: str | None = None
    views: int | None = None
    likes: int | None = None
    comments_count: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.video_id, str) or not _VIDEO_ID_RE.match(self.video_id):
            raise ClassificationInputError(
                "video_id must be an 11-character YouTube video id"
            )
        title = _clean_text(self.title) if isinstance(self.title, str) else None
        if title is None:
            raise ClassificationInputError("title must not be blank")
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "description", _clean_text(self.description))
        object.__setattr__(self, "transcript", _clean_text(self.transcript))
        for name in ("views", "likes", "comments_count"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ClassificationInputError(
                    f"{name} must be a non-negative integer or None"
                )


# --- Output -----------------------------------------------------------------


@dataclass(frozen=True)
class FieldResult:
    """One classified field. `value=None` means the evidence was insufficient.

    A label always carries a confidence (0-1) and a concise evidence excerpt
    taken from the supplied content.
    """

    value: str | None
    confidence: float | None = None
    evidence: str | None = None

    def __post_init__(self) -> None:
        if self.value is not None:
            if not isinstance(self.value, str) or not self.value.strip():
                raise ClassificationSchemaError("value must be a non-blank string or null")
            if self.confidence is None:
                raise ClassificationSchemaError("a label requires a confidence")
            if not isinstance(self.evidence, str) or not self.evidence.strip():
                raise ClassificationSchemaError("a label requires non-blank evidence")
        if self.confidence is not None:
            if isinstance(self.confidence, bool) or not isinstance(
                self.confidence, (int, float)
            ):
                raise ClassificationSchemaError("confidence must be a number")
            if not 0.0 <= self.confidence <= 1.0:
                raise ClassificationSchemaError("confidence must be between 0 and 1")
        if self.evidence is not None and not isinstance(self.evidence, str):
            raise ClassificationSchemaError("evidence must be a string or null")


# field name -> controlled vocabulary (None = free text)
FIELD_VOCABULARY: dict[str, type[Enum] | None] = {
    "topic": None,
    "audience": None,
    "pain_point": None,
    "hook": None,
    "hook_type": HookType,
    "format": ContentFormat,
    "emotion": Emotion,
    "cta": None,
}


@dataclass(frozen=True)
class ClassificationResult:
    topic: FieldResult
    audience: FieldResult
    pain_point: FieldResult
    hook: FieldResult
    hook_type: FieldResult
    format: FieldResult
    emotion: FieldResult
    cta: FieldResult

    def __post_init__(self) -> None:
        for name, vocab in FIELD_VOCABULARY.items():
            field = getattr(self, name)
            if not isinstance(field, FieldResult):
                raise ClassificationSchemaError(f"{name} must be a FieldResult")
            if vocab is not None and field.value is not None:
                allowed = {m.value for m in vocab}
                if field.value not in allowed:
                    raise ClassificationSchemaError(
                        f"{name}: {field.value!r} is not one of {sorted(allowed)}"
                    )

    @classmethod
    def from_dict(cls, data: Any) -> ClassificationResult:
        """Strictly parse `{field: {value, confidence, evidence}}`.

        Every field must be present (use `value: null` when unknown); unknown
        keys are rejected.
        """
        if not isinstance(data, Mapping):
            raise ClassificationSchemaError("classification must be a JSON object")
        expected = {f.name for f in fields(cls)}
        if unknown := set(data) - expected:
            raise ClassificationSchemaError(f"unexpected fields: {sorted(unknown)}")
        if missing := expected - set(data):
            raise ClassificationSchemaError(f"missing fields: {sorted(missing)}")
        parsed: dict[str, FieldResult] = {}
        for name in FIELD_VOCABULARY:  # declaration order, so errors are deterministic
            raw = data[name]
            if not isinstance(raw, Mapping):
                raise ClassificationSchemaError(
                    f"{name} must be an object (got {type(raw).__name__})"
                )
            if extra := set(raw) - {"value", "confidence", "evidence"}:
                raise ClassificationSchemaError(f"{name}: unexpected keys {sorted(extra)}")
            parsed[name] = FieldResult(
                value=raw.get("value"),
                confidence=raw.get("confidence"),
                evidence=raw.get("evidence"),
            )
        return cls(**parsed)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {
            f.name: {
                "value": getattr(self, f.name).value,
                "confidence": getattr(self, f.name).confidence,
                "evidence": getattr(self, f.name).evidence,
            }
            for f in fields(self)
        }
