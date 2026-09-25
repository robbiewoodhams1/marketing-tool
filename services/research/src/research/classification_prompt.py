"""Versioned, inspectable classification prompt and output schema.

Bump PROMPT_VERSION whenever the wording or schema changes so stored results
can later be traced to the prompt that produced them.
"""

from __future__ import annotations

from typing import Any

from research.classification import FIELD_VOCABULARY, ClassificationInput

PROMPT_VERSION = "classification-v1"

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

Rules:
- Classify ONLY from the evidence supplied. Do not invent facts or use outside knowledge of the video.
- Infer cautiously. When the evidence is insufficient for a field, set its value to null (with null confidence and evidence). Never guess to fill a field.
- Identify the hook from the actual opening of the transcript, the title, or the description.
- Distinguish audience from topic, pain point from general subject, and format from topic.
- Identify a CTA only if one is explicitly present; otherwise use null.
- For every non-null value give a confidence between 0 and 1 and a concise evidence string: a short excerpt or factual reference from the supplied content. Do not explain your reasoning.
- Controlled fields must use exactly one of the listed values.
- Return only the structured classification, nothing else.

Fields:
""" + "\n".join(f"- {name}: {text}" for name, text in FIELD_DEFINITIONS.items()) + "\n\nControlled vocabularies:\n" + "\n".join(
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
