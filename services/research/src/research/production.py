"""Terminal entry point and service: produce a short-form video package from
one persisted Opportunity.

    python -m research.production --opportunity-id OPPORTUNITY_ID

    load opportunity -> load its product profile -> load its research context
    (background only) -> build input pack -> run key -> (already stored? stop)
    -> LLM -> validate structure -> validate substance (refs, product truth,
    scene/duration sanity, not-unrelated-content) -> save atomically

Production is a separate analytical operation, downstream of Opportunity
Creation. It never touches the opportunity, its run, or the research it
reads from.

Needs LLM_API_KEY, SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
(PRODUCTION_MODEL optional). Exit codes: 0 stored (or an identical run already
existed), 1 the opportunity cannot be used, 2 bad arguments / missing
configuration, 3 the run failed (nothing stored).
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from research.config import Settings
from research.llm import AnthropicProvider, LLMConfigError, ProductionProvider
from research.logging import configure_logging, get_logger
from research.production_pack import build_production_pack
from research.production_schema import ProductionValidationError
from research.production_store import (
    ProductionRepository,
    build_save_payload,
    compute_run_key,
)
from research.production_validation import validate_production
from research.persistence import PersistenceConfigError, SupabaseDatabase
from research.products import UnknownProductError, get_product_profile

log = get_logger("research.production")

Progress = Callable[[str], None]

EXIT_OK, EXIT_SOURCE, EXIT_CONFIG, EXIT_FAILED = 0, 1, 2, 3


class ProductionSourceError(Exception):
    """The requested opportunity cannot be used. Nothing was changed."""


@dataclass(frozen=True)
class ProductionRunOutcome:
    status: str  # "created" | "exists"
    run_id: str | None = None
    production_id: str | None = None
    run_key: str | None = None


def _ignore(_: str) -> None:
    pass


def run_production(
    opportunity_id: str,
    *,
    repository: ProductionRepository,
    llm: ProductionProvider,
    progress: Progress = _ignore,
) -> ProductionRunOutcome:
    """Produce a short-form video package from `opportunity_id`.

    The product profile is resolved from the opportunity's own opportunity_run
    (the product it was created for), never re-selected here - a production is
    always for the product its opportunity belongs to. Raises on any failure;
    nothing partial is ever stored. Returns `exists` when an identical run is
    already stored (no LLM call, or the race was lost), and `created`
    otherwise.
    """
    opportunity = repository.get_opportunity(opportunity_id)
    if opportunity is None:
        raise ProductionSourceError(f"Opportunity {opportunity_id} does not exist")
    try:
        product = get_product_profile(opportunity.product_key)
    except UnknownProductError as exc:
        raise ProductionSourceError(str(exc)) from None

    progress("Loading research context...")
    research_context = repository.load_research_context(opportunity_id)
    progress(f"✓ {len(research_context)} supporting insight(s)")

    pack = build_production_pack(
        opportunity=opportunity.row, research_context=research_context, product=product
    )
    progress(f"✓ Input pack built for product {product.name!r}")

    run_key = compute_run_key(opportunity_id=opportunity_id, model=llm.model, pack=pack)
    if (existing := repository.find_run(run_key)) is not None:
        progress("✓ An identical production run already exists; nothing to do")
        return ProductionRunOutcome(status="exists", run_id=existing, run_key=run_key)

    progress("Producing...")
    output = llm.generate_production(pack)
    progress(f"✓ Model returned a {output.estimated_duration_seconds}s, {len(output.scenes)}-scene production")

    progress("Validating...")
    try:
        validated = validate_production(output, pack, product)
    except ProductionValidationError as exc:
        exc.raw = output.to_dict()  # development diagnostics; never persisted
        raise
    progress("✓ Every reference and rule verified")

    saved = repository.save(
        build_save_payload(
            opportunity=opportunity, model=llm.model, run_key=run_key, pack=pack,
            output=output, validated=validated,
        )
    )
    progress(f"✓ Production run {saved.status}: {saved.run_id}")
    return ProductionRunOutcome(
        status=saved.status, run_id=saved.run_id, production_id=saved.production_id, run_key=run_key
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m research.production",
        description="Produce a short-form video package from one persisted opportunity.",
    )
    parser.add_argument("--opportunity-id", required=True, help="id of a stored `opportunities` row")
    args = parser.parse_args(argv)
    try:
        opportunity_id = str(uuid.UUID(args.opportunity_id))
    except ValueError:
        print(f"error: --opportunity-id must be a UUID, got {args.opportunity_id!r}", file=sys.stderr)
        return EXIT_CONFIG

    settings = Settings.from_env()
    configure_logging(settings.log_level)
    if not (settings.supabase_url and settings.supabase_service_role_key):
        print("error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        return EXIT_CONFIG
    try:
        llm = AnthropicProvider.for_production(settings)
    except LLMConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    try:
        repository = ProductionRepository(
            SupabaseDatabase(settings.supabase_url, settings.supabase_service_role_key)
        )
    except PersistenceConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    print("Production\n────────────────────────────────", flush=True)
    try:
        outcome = run_production(
            opportunity_id, repository=repository, llm=llm,
            progress=lambda line: print(line, flush=True),
        )
    except ProductionSourceError as exc:
        print(f"\nCannot produce: {exc}", file=sys.stderr)
        return EXIT_SOURCE
    except Exception as exc:  # nothing partial was stored
        print(f"\nProduction failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED

    print(f"\nProduction run {outcome.status}: {outcome.run_id} (production {outcome.production_id}).")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
