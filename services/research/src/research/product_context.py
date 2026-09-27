"""Product context: what the marketed product actually does.

Pure data: no I/O, no LLM knowledge of any specific product hardcoded into a
prompt. A `ProductProfile` is the ONLY source of truth the opportunity engine
may draw product claims from. The prompt and the validator both work from the
same object, so a claim not traceable to it is rejected rather than repaired.

Product-agnostic by construction: the opportunity engine (research.opportunity,
research.opportunity_pack, research.opportunity_validation) is built against
this shape, never against a specific product's facts. A second product (e.g.
HouseFlow) is a second `ProductProfile` living in `research.products`, never a
prompt or validator change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

PRODUCT_PROFILE_SCHEMA_VERSION = "product-profile-v1"

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ProductContextError(Exception):
    """A ProductProfile/ProductCapability was built with an invalid shape."""


@dataclass(frozen=True)
class ProductCapability:
    """One thing the product can actually, verifiably do.

    `key` is the stable, opaque identity the opportunity engine cites (as a
    `P#` reference in the generation pack) and the validator resolves back to
    this capability. It is never shown to the model as free text alone.
    """

    key: str  # stable, short, lowercase snake_case, e.g. "reusable_templates"
    name: str  # human label, e.g. "Reusable templates"
    description: str  # what it does, in plain, verifiable terms
    problem_solved: str  # the concrete pain it addresses

    def __post_init__(self) -> None:
        if not _KEY_RE.match(self.key):
            raise ProductContextError(
                f"capability key {self.key!r} must be lowercase snake_case"
            )
        for field_name in ("name", "description", "problem_solved"):
            if not getattr(self, field_name).strip():
                raise ProductContextError(f"capability {self.key}: {field_name} must not be blank")


@dataclass(frozen=True)
class ProductProfile:
    """Everything the opportunity engine is allowed to know about one product.

    Absence is meaningful: `pricing=None` means pricing is unknown, not free.
    `limitations` states constraints on capabilities that DO exist;
    `out_of_scope` names capabilities the product explicitly does NOT have -
    the deterministic validator rejects any opportunity that claims one of
    these phrases as something the product does (see
    `research.opportunity_validation`). Neither list is inferred: every entry
    must come from a verified product fact, never a guess.
    """

    key: str  # stable product identity, e.g. "tradeflow"
    version: str  # bump whenever the profile's content changes
    name: str
    description: str
    audience: tuple[str, ...]
    capabilities: tuple[ProductCapability, ...]
    differentiators: tuple[str, ...] = ()
    positioning: str | None = None
    limitations: tuple[str, ...] = ()
    out_of_scope: tuple[str, ...] = ()  # phrases naming capabilities the product does NOT have
    pricing: str | None = None  # None = unknown; never invented
    marketing_objectives: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _KEY_RE.match(self.key):
            raise ProductContextError(f"product key {self.key!r} must be lowercase snake_case")
        if not self.version.strip():
            raise ProductContextError(f"product {self.key}: version must not be blank")
        if not self.name.strip() or not self.description.strip():
            raise ProductContextError(f"product {self.key}: name/description must not be blank")
        if not self.capabilities:
            raise ProductContextError(f"product {self.key}: at least one capability is required")
        keys = [c.key for c in self.capabilities]
        if len(keys) != len(set(keys)):
            raise ProductContextError(f"product {self.key}: duplicate capability keys {keys}")
        if not all(a.strip() for a in self.audience):
            raise ProductContextError(f"product {self.key}: audience entries must not be blank")

    def capability(self, key: str) -> ProductCapability | None:
        return next((c for c in self.capabilities if c.key == key), None)

    def to_manifest(self) -> dict[str, Any]:
        """Deterministic, stored-with-the-run snapshot of exactly what the
        model was told about the product. Used for provenance and folded into
        run identity, so a changed profile always produces a new run."""
        return {
            "schema_version": PRODUCT_PROFILE_SCHEMA_VERSION,
            "key": self.key,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "audience": list(self.audience),
            "capabilities": [
                {
                    "key": c.key,
                    "name": c.name,
                    "description": c.description,
                    "problem_solved": c.problem_solved,
                }
                for c in self.capabilities
            ],
            "differentiators": list(self.differentiators),
            "positioning": self.positioning,
            "limitations": list(self.limitations),
            "out_of_scope": list(self.out_of_scope),
            "pricing": self.pricing,
            "marketing_objectives": list(self.marketing_objectives),
        }
