"""Versioned, inspectable classification prompt and output schema.

Bump PROMPT_VERSION whenever the wording or schema changes so stored results
can later be traced to the prompt that produced them.
"""

from __future__ import annotations

from typing import Any

from research.classification import FIELD_VOCABULARY, ClassificationInput

PROMPT_VERSION = "classification-v2"

# Transcripts are truncated for the prompt only; the input model is unchanged.
MAX_TRANSCRIPT_CHARS = 12_000
MAX_DESCRIPTION_CHARS = 2_000

FIELD_DEFINITIONS = {
    "topic": "what the content is primarily about (its subject).",
    "audience": "the apparent intended viewer, based on evidence. Not the same as the topic.",
    "pain_point": (
        "the problem, frustration, fear, desire or unmet need the content addresses. "
        "Not the same as the general subject."
    ),
    "hook": (
        "the attention-grabbing opening mechanism or wording, taken from the actual "
        "opening of the transcript, the title, or the description."
    ),
    "hook_type": "the primary mechanism used by the hook.",
    "format": "the structural format of the content. Not the same as the topic.",
    "emotion": "the dominant emotional response the content appears designed to evoke.",
    "cta": "an explicit call to action, only if one is actually present.",
}

SYSTEM_PROMPT = """\
You classify short-form and long-form video content for marketing research.

Output structure (applies to EVERY field, including the controlled ones):
- Each field is an object with exactly the keys "value", "confidence" and "evidence". A field is never a bare string.
- For controlled fields (hook_type, format, emotion) the allowed value goes in that object's "value" key.
  VALID:   "hook_type": {"value": "pain", "confidence": 0.9, "evidence": "..."}
  INVALID: "hook_type": "pain"

Rules:
- Classify ONLY from the evidence supplied. Do not invent facts or use outside knowledge of the video.
- Infer cautiously. When the evidence is insufficient for a field, set the whole field to null (not an object with null parts). Never guess to fill a field.
- Identify the hook from the actual opening of the transcript, the title, or the description.
- Distinguish audience from topic, pain point from general subject, and format from topic.
- Identify a CTA only if one is explicitly present; otherwise the cta field is null.
- For every non-null value give a confidence between 0 and 1 and a concise evidence string: a short excerpt or factual reference from the supplied content. Do not explain your reasoning.
- For controlled fields, "value" must be exactly one of the listed values (if none fits, use "other"; if the evidence is insufficient, the whole field is null).
- Return only the structured classification, nothing else.

Fields:
""" + "\n".join(f"- {name}: {text}" for name, text in FIELD_DEFINITIONS.items()) + "\n\nAllowed \"value\"s for the controlled fields:\n" + "\n".join(
    f"- {name}: {', '.join(m.value for m in vocab)}"
    for name, vocab in FIELD_VOCABULARY.items()
    if vocab is not None
)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + " [truncated]"


def build_user_prompt(item: ClassificationInput) -> str:
    """Render the evidence. Missing evidence is stated as missing, never filled in."""
    parts = [f"Title: {item.title}"]
    parts.append(
        "Description: "
        + (_clip(item.description, MAX_DESCRIPTION_CHARS) if item.description else "(not available)")
    )
    parts.append(
        "Transcript: "
        + (_clip(item.transcript, MAX_TRANSCRIPT_CHARS) if item.transcript else "(not available)")
    )
    stats = [
        f"{label}={value}"
        for label, value in (
            ("views", item.views),
            ("likes", item.likes),
            ("comments", item.comments_count),
        )
        if value is not None
    ]
    if stats:
        parts.append("Performance: " + ", ".join(stats))
    return "\n\n".join(parts)


def strict_output_json_schema() -> dict[str, Any]:
    """The same schema in the subset Anthropic's strict tool use accepts.

    Strict mode rejects numeric bounds, and limits how many parameters may be
    unions (16). So each of the 8 fields is ONE nullable construct: either
    null (insufficient evidence) or an object whose `value`, `confidence` and
    `evidence` are all required and non-null. That is 8 unions, not 24.
    Nothing is loosened: the enums are kept, and the 0-1 confidence range and
    the exact object shape are still enforced locally by `ClassificationResult`.
    """
    props: dict[str, Any] = {}
    for name, vocab in FIELD_VOCABULARY.items():
        text: dict[str, Any] = {"type": "string"}
        if vocab is not None:
            text["enum"] = [m.value for m in vocab]
        props[name] = {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "value": text,
                        "confidence": {"type": "number"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["value", "confidence", "evidence"],
                    "additionalProperties": False,
                },
                {"type": "null"},
            ]
        }
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def output_json_schema() -> dict[str, Any]:
    """JSON Schema of the expected classification object."""
    props: dict[str, Any] = {}
    for name, vocab in FIELD_VOCABULARY.items():
        value: dict[str, Any] = {"type": ["string", "null"]}
        if vocab is not None:
            value["enum"] = [m.value for m in vocab] + [None]
        props[name] = {
            "type": "object",
            "properties": {
                "value": value,
                "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                "evidence": {"type": ["string", "null"]},
            },
            "required": ["value", "confidence", "evidence"],
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }
