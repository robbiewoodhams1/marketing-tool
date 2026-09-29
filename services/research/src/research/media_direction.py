"""Media Direction: human-editable visual guidance for media generation.

A separate, third input layer alongside Production and the provider:

  Production      = WHAT should be created (scene purpose, voiceover, the
                     real product capability being shown - decided once,
                     validated, immutable).
  Media Direction  = HOW the human wants it visually represented (subject,
                     appearance, setting, style, camera, lighting, things to
                     avoid) - editable at any time, reused across
                     regenerations, never a source of new capability claims.
  Media Provider   = HOW the model actually renders it (aspect ratio,
                     resolution, negative-prompt boilerplate - see
                     `research.gemini` / `research.veo`).

Persisted one row per production (`media_directions`, keyed by
production_id) - unlike every other table in this schema, it is genuinely
mutable: it is meant to be edited and reused, not an immutable analytical
record. `research.media_pack.build_scene_prompt` is where it actually enters
the prompt, positioned between Production's own scene fields and the
provider-specific boilerplate.

Editing it changes what gets generated, so it is folded into a run's
identity (see `research.media_store.compute_run_key`'s `media_direction`
param): the same production regenerated with different direction gets a new
run, never silently skipped by idempotency.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any

# Field name -> the label rendered in the prompt. Order here is the order
# direction appears in the prompt.
_PROMPT_LABELS: dict[str, str] = {
    "target_subject": "Subject to depict",
    "appearance": "Appearance",
    "setting": "Setting / environment",
    "visual_style": "Visual style",
    "camera_composition": "Camera / composition",
    "lighting": "Lighting",
    "avoid": "Do NOT include",
    "custom_instructions": "Custom instruction",
    "additional_direction": "Additional direction",
}


@dataclass(frozen=True)
class MediaDirection:
    """Freeform, all-optional human visual direction for one production.

    Every field is plain text the human wrote. Nothing here is validated
    against the product or the production - it is advisory only:
    `research.media_validation` still enforces product-capability boundaries
    against the scene's own `product_capability_refs` regardless of what
    direction text says, so direction can never smuggle in a new capability
    claim the production didn't already make.
    """

    target_subject: str | None = None
    appearance: str | None = None
    setting: str | None = None
    visual_style: str | None = None
    camera_composition: str | None = None
    lighting: str | None = None
    avoid: str | None = None
    custom_instructions: str | None = None
    additional_direction: str | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any] | None) -> MediaDirection:
        """Builds from a `media_directions` row (or `None`, meaning no
        direction has ever been saved for this production - equivalent to
        every field being empty)."""
        if row is None:
            return cls()
        return cls(**{f.name: (row.get(f.name) or None) for f in fields(cls)})

    def is_empty(self) -> bool:
        return not any(getattr(self, f.name) for f in fields(self))

    def to_prompt_lines(self) -> tuple[str, ...]:
        """Only the fields the human actually filled in, each one line,
        labelled. Empty for an empty direction."""
        return tuple(
            f"{_PROMPT_LABELS[f.name]}: {value}"
            for f in fields(self)
            if (value := getattr(self, f.name))
        )

    def to_snapshot(self) -> dict[str, Any] | None:
        """Exactly what a media run stores as `media_direction_snapshot` and
        folds into its `run_key` identity. `None` for an empty direction, so
        "no direction saved" and "direction saved but every field left
        blank" both produce the same (no-op) run identity."""
        snapshot = {f.name: getattr(self, f.name) for f in fields(self) if getattr(self, f.name)}
        return snapshot or None
