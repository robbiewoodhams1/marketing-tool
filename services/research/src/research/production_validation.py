"""Is the production model's output supported by the supplied opportunity and
product context? `ProductionOutput.from_dict` checks the SHAPE. This checks
the SUBSTANCE, in application code, and never trusts the model:

* every product_claims / scene capability ref resolves to a supplied product
  capability (unknown refs are rejected) - this is what makes "unsupported
  product claims" structurally impossible: the model can only ever point at a
  capability that was actually supplied,
* no free-text field claims something the product profile explicitly says it
  does NOT do (`ProductProfile.out_of_scope`) - checked broadly, across every
  field where a claim could land (title, hook, cta, caption, limitations,
  product_claims.explanation, and every scene's purpose/voiceover/on-screen
  text/visual direction), because unlike an Opportunity brief a Production IS
  the finished script. The prompt explicitly asks the model to name, in
  `limitations`, anything that "could be misread as claiming something the
  product does not do" - so a bare substring match would reject the model for
  writing exactly the honest disclaimer it was asked for. The check therefore
  only flags a phrase that is NOT negated nearby ("no bookkeeping is shown",
  "does not do accounting" are allowed; "handles your bookkeeping" is not),
* scenes are structurally sound: numbered 1..N with no gaps, each with a
  sensible duration, and the scene durations sum to (within a small
  tolerance) the stated estimated_duration_seconds, itself within the V1
  short-form range,
* text fields stay within a generous length bound - the deterministic
  backstop against an "unrelated finished asset" (a blog post, an SEO
  article...) smuggled into a caption or a voiceover field. A production IS
  allowed to be a complete script (unlike an Opportunity brief); this bound
  only catches something that has stopped being a short-form video package.

These are deterministic heuristics: a backstop, not a proof. They can reject
valid output; they must never be relaxed to make a model answer pass. Any
failure rejects the whole production; nothing partial is ever stored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from research.product_context import ProductProfile
from research.production_pack import ProductionInputPack
from research.production_schema import ProductionOutput, ProductionValidationError, Scene

# Short-form video, approximately 20-60s (Part 5); a small tolerance either
# side accounts for "approximately" without allowing a full-length video.
MIN_TOTAL_DURATION = 15
MAX_TOTAL_DURATION = 65
DURATION_SUM_TOLERANCE = 3  # seconds the scenes may drift from the stated total

MIN_SCENE_DURATION = 1
MAX_SCENE_DURATION = 20

# Generous length bounds: a backstop against an unrelated finished asset
# (a blog post, an SEO article) landing in a field, not a style constraint.
MAX_SHORT_FIELD_CHARS = 200  # title, hook, cta, scene purpose/on_screen_text/transition
MAX_MEDIUM_FIELD_CHARS = 500  # caption, scene voiceover/visual_direction
MAX_LONG_FIELD_CHARS = 900  # limitations, product_claims.explanation

# A negation cue within this many characters before an out-of-scope phrase
# makes it a disclaimer ("no bookkeeping is shown here"), not a claim. Known
# limitation: a negation can "bleed" into a second, genuinely unnegated
# mention of the same phrase if the two are within this many characters of
# each other - acceptable for a deterministic backstop that is meant to catch
# an isolated overclaim, not to parse discourse structure.
_NEGATION_WINDOW_CHARS = 60
_NEGATION_RE = re.compile(
    r"\b(not|n't|no|none|never|without|nor|cannot|can't|isn't|doesn't|does not|"
    r"neither|instead of|rather than)\b", re.IGNORECASE,
)


@dataclass(frozen=True)
class ValidatedScene:
    scene: Scene
    capability_keys: tuple[str, ...]


@dataclass(frozen=True)
class ValidatedProduction:
    output: ProductionOutput
    scenes: tuple[ValidatedScene, ...]
    capability_keys: tuple[str, ...]  # resolved from product_claims.capability_refs


def validate_production(
    output: ProductionOutput, pack: ProductionInputPack, product: ProductProfile
) -> ValidatedProduction:
    """Validate against the pack and product profile, or raise ProductionValidationError."""
    _check_duration(output)
    capability_keys = _resolve_refs(output.product_claims.capability_refs, "product_claims.capability_refs", pack)
    scenes = tuple(_validate_scene(s, n, pack) for n, s in enumerate(output.scenes))
    _check_lengths(output)
    _check_product_truth(output, product)
    return ValidatedProduction(output=output, scenes=scenes, capability_keys=capability_keys)


# --- references ----------------------------------------------------------------


def _resolve_refs(refs: tuple[str, ...], where: str, pack: ProductionInputPack) -> tuple[str, ...]:
    keys: list[str] = []
    for i, ref in enumerate(refs):
        key = pack.capability_refs.get(ref)
        if key is None:
            raise ProductionValidationError(
                f"{where}[{i}]: unknown reference {ref!r} (not in the supplied product profile)"
            )
        keys.append(key)
    return tuple(keys)


def _validate_scene(scene: Scene, n: int, pack: ProductionInputPack) -> ValidatedScene:
    if not (MIN_SCENE_DURATION <= scene.duration_seconds <= MAX_SCENE_DURATION):
        raise ProductionValidationError(
            f"duration_seconds {scene.duration_seconds} is outside the sane per-scene range "
            f"[{MIN_SCENE_DURATION}, {MAX_SCENE_DURATION}]", n
        )
    keys = _resolve_refs(scene.product_capability_refs, "product_capability_refs", pack)
    return ValidatedScene(scene=scene, capability_keys=keys)


# --- duration --------------------------------------------------------------------


def _check_duration(output: ProductionOutput) -> None:
    total = output.estimated_duration_seconds
    if not (MIN_TOTAL_DURATION <= total <= MAX_TOTAL_DURATION):
        raise ProductionValidationError(
            f"estimated_duration_seconds {total} is outside the V1 short-form range "
            f"[{MIN_TOTAL_DURATION}, {MAX_TOTAL_DURATION}]"
        )
    scene_sum = sum(s.duration_seconds for s in output.scenes)
    if abs(scene_sum - total) > DURATION_SUM_TOLERANCE:
        raise ProductionValidationError(
            f"scene durations sum to {scene_sum}s, which does not match "
            f"estimated_duration_seconds ({total}s) within {DURATION_SUM_TOLERANCE}s"
        )


# --- length backstop ---------------------------------------------------------------


def _check_lengths(output: ProductionOutput) -> None:
    short = {"title": output.title, "hook": output.hook, "cta": output.cta}
    for n, s in enumerate(output.scenes):
        short[f"scenes[{n}].purpose"] = s.purpose
        if s.on_screen_text:
            short[f"scenes[{n}].on_screen_text"] = s.on_screen_text
        if s.transition:
            short[f"scenes[{n}].transition"] = s.transition
    for name, text in short.items():
        if len(text) > MAX_SHORT_FIELD_CHARS:
            _too_long(name, text, MAX_SHORT_FIELD_CHARS)

    medium = {"caption": output.caption}
    for n, s in enumerate(output.scenes):
        medium[f"scenes[{n}].voiceover"] = s.voiceover
        medium[f"scenes[{n}].visual_direction"] = s.visual_direction
    for name, text in medium.items():
        if len(text) > MAX_MEDIUM_FIELD_CHARS:
            _too_long(name, text, MAX_MEDIUM_FIELD_CHARS)

    long = {"limitations": output.limitations, "product_claims.explanation": output.product_claims.explanation}
    for name, text in long.items():
        if len(text) > MAX_LONG_FIELD_CHARS:
            _too_long(name, text, MAX_LONG_FIELD_CHARS)


def _too_long(name: str, text: str, limit: int) -> None:
    raise ProductionValidationError(
        f"{name} is {len(text)} characters (> {limit}); this reads like an unrelated finished "
        "asset, not a short-form video field"
    )


# --- product truth -----------------------------------------------------------------


def _check_product_truth(output: ProductionOutput, product: ProductProfile) -> None:
    """Checked broadly: unlike an Opportunity brief, every field here IS the
    finished script, so a false claim can land almost anywhere."""
    fields: dict[str, str] = {
        "title": output.title,
        "hook": output.hook,
        "cta": output.cta,
        "caption": output.caption,
        "limitations": output.limitations,
        "product_claims.explanation": output.product_claims.explanation,
    }
    for n, s in enumerate(output.scenes):
        fields[f"scenes[{n}].purpose"] = s.purpose
        fields[f"scenes[{n}].voiceover"] = s.voiceover
        fields[f"scenes[{n}].visual_direction"] = s.visual_direction
        if s.on_screen_text:
            fields[f"scenes[{n}].on_screen_text"] = s.on_screen_text

    for field_name, text in fields.items():
        lowered = text.lower()
        for phrase in product.out_of_scope:
            if _claims_capability(lowered, phrase.lower()):
                raise ProductionValidationError(
                    f"{field_name} claims or implies {phrase!r}, which {product.name} does not "
                    "currently do (product truth)"
                )


def _claims_capability(lowered_text: str, phrase: str) -> bool:
    """True if `phrase` appears as an apparent claim, not a negated
    disclaimer ("no bookkeeping is shown", "does not do accounting"). Every
    occurrence of the phrase is checked independently, so one negated mention
    does not excuse an unrelated, unnegated one elsewhere in the same field."""
    start = 0
    while (index := lowered_text.find(phrase, start)) != -1:
        window = lowered_text[max(0, index - _NEGATION_WINDOW_CHARS):index]
        if not _NEGATION_RE.search(window):
            return True
        start = index + len(phrase)
    return False
