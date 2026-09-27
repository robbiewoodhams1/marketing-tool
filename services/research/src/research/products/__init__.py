"""Registry of known product profiles, keyed by `ProductProfile.key`.

Product-agnostic by construction: adding a new product (e.g. HouseFlow) means
adding one module here and registering it below - never touching the
opportunity engine (`research.opportunity`, `research.opportunity_pack`,
`research.opportunity_validation`, `research.opportunity_prompt`).
"""

from __future__ import annotations

from research.product_context import ProductProfile
from research.products.tradeflow import TRADEFLOW_PROFILE


class UnknownProductError(Exception):
    """No product profile is registered for the requested key."""


_PROFILES: dict[str, ProductProfile] = {p.key: p for p in (TRADEFLOW_PROFILE,)}


def get_product_profile(key: str) -> ProductProfile:
    try:
        return _PROFILES[key]
    except KeyError:
        raise UnknownProductError(
            f"no product profile registered for {key!r}; known: {sorted(_PROFILES)}"
        ) from None


def known_product_keys() -> tuple[str, ...]:
    return tuple(sorted(_PROFILES))
