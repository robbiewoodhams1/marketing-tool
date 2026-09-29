"""Production output schema: what the production model must return.

Pure data + strict shape validation: no I/O, no LLM calls, no database. This
only checks that the output is well formed. Whether it is *supported* -
product claims stay inside the supplied product profile, scenes/durations are
sensible, nothing reads as an unrelated finished asset - is
`research.production_validation`.

Opportunity Creation answers "what is worth producing?". Production answers
"given ONE specific opportunity, what exactly should be made?" - a complete,
concrete short-form video specification, not generic marketing advice. Unlike
an Opportunity, a Production IS allowed to be a complete script: producing the
actual script is the point of this stage.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

PRODUCTION_ANALYSIS_TYPE = "production"
# Version of the output shape below. Bump on an incompatible change.
PRODUCTION_SCHEMA_VERSION = "production-v1"

MIN_SCENES = 2
MAX_SCENES = 8
MAX_HASHTAGS = 8

# Opaque reference assigned by research.production_pack: P# for a supplied
# product capability. Never a database id.
_CAPABILITY_REF_RE = re.compile(r"^P[1-9][0-9]*$")


class ProductionError(Exception):
    """Base class for production failures."""


class ProductionSchemaError(ProductionError, ValueError):
    """The model output does not match the schema."""


class ProductionValidationError(ProductionError, ValueError):
    """The output is well formed but not supported by the supplied opportunity/product context."""

    def __init__(self, message: str, scene_index: int | None = None):
        super().__init__(message if scene_index is None else f"scene {scene_index}: {message}")
        self.scene_index = scene_index
        self.raw: Any = None  # the offending output, for development diagnostics


class Platform(str, Enum):
    """Deliberately small; extend only when an implementation needs it."""

    TIKTOK = "tiktok"
    INSTAGRAM_REELS = "instagram_reels"
    YOUTUBE_SHORTS = "youtube_shorts"
    GENERIC_SHORT_FORM = "generic_short_form"


@dataclass(frozen=True)
class Scene:
    number: int
    duration_seconds: int
    purpose: str
    voiceover: str
    on_screen_text: str | None
    visual_direction: str
    product_capability_refs: tuple[str, ...]  # opaque "P#"s; may be empty
    transition: str | None


@dataclass(frozen=True)
class ProductClaims:
    """Which product capabilities this production actually shows/uses, and why."""

    capability_refs: tuple[str, ...]  # opaque "P#"s, resolved to real capability keys
    explanation: str


@dataclass(frozen=True)
class ProductionOutput:
    title: str
    hook: str
    platform: Platform
    estimated_duration_seconds: int
    scenes: tuple[Scene, ...]
    cta: str
    caption: str
    hashtags: tuple[str, ...]
    product_claims: ProductClaims
    limitations: str

    @classmethod
    def from_dict(cls, data: Any) -> ProductionOutput:
        """Strictly parse the model output. Unknown or missing keys are errors."""
        obj = _mapping(
            data, "output",
            {
                "title", "hook", "platform", "estimated_duration_seconds", "scenes", "cta",
                "caption", "hashtags", "product_claims", "limitations",
            },
        )
        scenes = tuple(_scene(s, n) for n, s in enumerate(_list(obj["scenes"], "scenes")))
        if not (MIN_SCENES <= len(scenes) <= MAX_SCENES):
            raise ProductionSchemaError(f"scenes must have between {MIN_SCENES} and {MAX_SCENES} entries")
        numbers = [s.number for s in scenes]
        if numbers != list(range(1, len(scenes) + 1)):
            raise ProductionSchemaError(f"scene numbers must be sequential from 1, got {numbers}")

        duration = obj["estimated_duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, int):
            raise ProductionSchemaError("estimated_duration_seconds must be an integer")

        raw_hashtags = _list(obj["hashtags"], "hashtags")
        if len(raw_hashtags) > MAX_HASHTAGS:
            raise ProductionSchemaError(f"at most {MAX_HASHTAGS} hashtags")
        hashtags = tuple(_text(h, f"hashtags[{n}]") for n, h in enumerate(raw_hashtags))
        if len(set(hashtags)) != len(hashtags):
            raise ProductionSchemaError("hashtags: duplicates are not allowed")

        return cls(
            title=_text(obj["title"], "title"),
            hook=_text(obj["hook"], "hook"),
            platform=_enum(Platform, obj["platform"], "platform"),
            estimated_duration_seconds=duration,
            scenes=scenes,
            cta=_text(obj["cta"], "cta"),
            caption=_text(obj["caption"], "caption"),
            hashtags=hashtags,
            product_claims=_product_claims(obj["product_claims"], "product_claims"),
            limitations=_text(obj["limitations"], "limitations"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "hook": self.hook,
            "platform": self.platform.value,
            "estimated_duration_seconds": self.estimated_duration_seconds,
            "scenes": [_scene_dict(s) for s in self.scenes],
            "cta": self.cta,
            "caption": self.caption,
            "hashtags": list(self.hashtags),
            "product_claims": _product_claims_dict(self.product_claims),
            "limitations": self.limitations,
        }


# --- parsing helpers ---------------------------------------------------------


def _mapping(value: Any, where: str, keys: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductionSchemaError(f"{where} must be an object")
    if unknown := set(value) - keys:
        raise ProductionSchemaError(f"{where}: unexpected keys {sorted(unknown)}")
    if missing := keys - set(value):
        raise ProductionSchemaError(f"{where}: missing keys {sorted(missing)}")
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProductionSchemaError(f"{where} must be a non-blank string")
    return value.strip()


def _optional_text(value: Any, where: str) -> str | None:
    if value is None:
        return None
    return _text(value, where)


def _list(value: Any, where: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ProductionSchemaError(f"{where} must be a list")
    return value


def _enum(enum: type[Enum], value: Any, where: str) -> Any:
    try:
        return enum(value)
    except ValueError:
        allowed = sorted(m.value for m in enum)
        raise ProductionSchemaError(f"{where}: {value!r} is not one of {allowed}") from None


def _capability_refs(value: Any, where: str) -> tuple[str, ...]:
    raw = _list(value, where)
    refs: list[str] = []
    for n, ref in enumerate(raw):
        if not isinstance(ref, str) or not _CAPABILITY_REF_RE.match(ref):
            raise ProductionSchemaError(f"{where}[{n}]: malformed capability reference {ref!r}")
        refs.append(ref)
    if len(set(refs)) != len(refs):
        raise ProductionSchemaError(f"{where}: duplicate references {refs}")
    return tuple(refs)


def _product_claims(value: Any, where: str) -> ProductClaims:
    obj = _mapping(value, where, {"capability_refs", "explanation"})
    refs = _capability_refs(obj["capability_refs"], f"{where}.capability_refs")
    if not refs:
        raise ProductionSchemaError(f"{where}: at least one capability reference is required")
    return ProductClaims(capability_refs=refs, explanation=_text(obj["explanation"], f"{where}.explanation"))


def _scene(value: Any, index: int) -> Scene:
    where = f"scenes[{index}]"
    obj = _mapping(
        value, where,
        {
            "number", "duration_seconds", "purpose", "voiceover", "on_screen_text",
            "visual_direction", "product_capability_refs", "transition",
        },
    )
    number = obj["number"]
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise ProductionSchemaError(f"{where}.number must be a positive integer")
    duration = obj["duration_seconds"]
    if isinstance(duration, bool) or not isinstance(duration, int) or duration < 1:
        raise ProductionSchemaError(f"{where}.duration_seconds must be a positive integer")
    return Scene(
        number=number,
        duration_seconds=duration,
        purpose=_text(obj["purpose"], f"{where}.purpose"),
        voiceover=_text(obj["voiceover"], f"{where}.voiceover"),
        on_screen_text=_optional_text(obj["on_screen_text"], f"{where}.on_screen_text"),
        visual_direction=_text(obj["visual_direction"], f"{where}.visual_direction"),
        product_capability_refs=_capability_refs(
            obj["product_capability_refs"], f"{where}.product_capability_refs"
        ),
        transition=_optional_text(obj["transition"], f"{where}.transition"),
    )


def _scene_dict(s: Scene) -> dict[str, Any]:
    return {
        "number": s.number,
        "duration_seconds": s.duration_seconds,
        "purpose": s.purpose,
        "voiceover": s.voiceover,
        "on_screen_text": s.on_screen_text,
        "visual_direction": s.visual_direction,
        "product_capability_refs": list(s.product_capability_refs),
        "transition": s.transition,
    }


def _product_claims_dict(p: ProductClaims) -> dict[str, Any]:
    return {"capability_refs": list(p.capability_refs), "explanation": p.explanation}
