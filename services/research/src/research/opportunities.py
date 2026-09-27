"""Terminal entry point and service: create opportunities from one synthesis.

    python -m research.opportunities --synthesis-id SYNTHESIS_ID [--product tradeflow]

    load synthesis -> load its insights -> load the product profile -> build
    input pack -> run key -> (already stored? stop) -> LLM -> validate
    structure -> validate substance (refs, product truth, not-finished-content)
    -> save atomically

Opportunity Creation is a separate analytical operation, downstream of
Synthesis. It never touches the research job or the synthesis it reads from.

Needs LLM_API_KEY, SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
(OPPORTUNITY_MODEL optional). Exit codes: 0 stored (or an identical run already
existed), 1 the synthesis cannot be used, 2 bad arguments / missing
configuration (including an unregistered --product), 3 the run failed (nothing
stored), 4 insufficient research (no LLM call made).
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from research.config import Settings
from research.llm import AnthropicProvider, LLMConfigError, OpportunityProvider
from research.logging import configure_logging, get_logger
from research.opportunity import OpportunityValidationError
from research.opportunity_pack import InsufficientResearchError, build_opportunity_pack
from research.opportunity_store import (
    OpportunityRepository,
    build_save_payload,
    compute_run_key,
)
from research.opportunity_validation import validate_opportunities
from research.persistence import PersistenceConfigError, SupabaseDatabase
from research.product_context import ProductProfile
from research.products import UnknownProductError, get_product_profile

log = get_logger("research.opportunities")

Progress = Callable[[str], None]

EXIT_OK, EXIT_SOURCE, EXIT_CONFIG, EXIT_FAILED, EXIT_INSUFFICIENT = 0, 1, 2, 3, 4


class OpportunitySourceError(Exception):
    """The requested synthesis cannot be used. Nothing was changed."""


@dataclass(frozen=True)
class OpportunityRunOutcome:
    status: str  # "created" | "exists" | "insufficient_research"
    run_id: str | None = None
    run_key: str | None = None
    opportunities: int = 0
    insight_total: int = 0
    message: str = ""


def _ignore(_: str) -> None:
    pass


def run_opportunity_creation(
    synthesis_id: str,
    product: ProductProfile,
    *,
    repository: OpportunityRepository,
    llm: OpportunityProvider,
    progress: Progress = _ignore,
) -> OpportunityRunOutcome:
    """Create opportunities from `synthesis_id` using the given product profile.

    `product` is a resolved `ProductProfile`, injected by the caller (the CLI
    resolves a `--product` key against the registry before calling this; tests
    can pass any profile directly). Raises on any failure; nothing partial is
    ever stored. Returns `insufficient_research` (no LLM call, nothing stored)
    when the synthesis has no insights, `exists` when an identical run is
    already stored (no LLM call, or the race was lost), and `created`
    otherwise - including when the model found no viable opportunity (the run
    is still recorded).
    """
    synthesis = repository.get_synthesis(synthesis_id)
    if synthesis is None:
        raise OpportunitySourceError(f"Synthesis {synthesis_id} does not exist")
    job = repository.get_job(synthesis.research_job_id)
    if job is None:
        raise OpportunitySourceError(f"Research job {synthesis.research_job_id} does not exist")

    progress("Loading insights...")
    insight_rows = repository.load_insights(synthesis_id)
    progress(f"✓ {len(insight_rows)} insight(s) from synthesis {synthesis_id}")

    try:
        pack = build_opportunity_pack(
            job={"query": job.query, "audience": job.audience, "objective": job.objective},
            synthesis={"id": synthesis.id, "research_job_id": synthesis.research_job_id},
            insight_rows=insight_rows,
            product=product,
        )
    except InsufficientResearchError as exc:
        progress(f"⚠ {exc}")
        return OpportunityRunOutcome(
            status="insufficient_research", insight_total=len(insight_rows), message=str(exc)
        )
    progress(f"✓ Input pack: {len(pack.insight_ids)} insight(s), product {product.name!r}")

    run_key = compute_run_key(
        synthesis_id=synthesis_id, research_job_id=synthesis.research_job_id, model=llm.model, pack=pack
    )
    if (existing := repository.find_run(run_key)) is not None:
        progress("✓ An identical opportunity run already exists; nothing to do")
        return OpportunityRunOutcome(
            status="exists", run_id=existing, run_key=run_key, insight_total=len(insight_rows)
        )

    progress("Generating opportunities...")
    output = llm.generate_opportunities(pack)
    progress(f"✓ Model returned {len(output.opportunities)} opportunity(ies)")

    progress("Validating...")
    try:
        validated = validate_opportunities(output, pack, product)
    except OpportunityValidationError as exc:
        exc.raw = output.to_dict()  # development diagnostics; never persisted
        raise
    progress("✓ Every reference and rule verified")

    saved = repository.save(
        build_save_payload(
            research_job_id=synthesis.research_job_id,
            synthesis_id=synthesis_id,
            product_key=product.key,
            product_version=product.version,
            model=llm.model,
            run_key=run_key,
            pack=pack,
            output=output,
            validated=validated,
        )
    )
    progress(f"✓ Opportunity run {saved.status}: {saved.run_id}")
    return OpportunityRunOutcome(
        status=saved.status,
        run_id=saved.run_id,
        run_key=run_key,
        opportunities=saved.opportunities,
        insight_total=len(insight_rows),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m research.opportunities",
        description="Create marketing opportunities from one research synthesis.",
    )
    parser.add_argument("--synthesis-id", required=True, help="id of a stored `syntheses` row")
    parser.add_argument(
        "--product", default="tradeflow", help="registered product profile key (default: tradeflow)"
    )
    args = parser.parse_args(argv)
    try:
        synthesis_id = str(uuid.UUID(args.synthesis_id))
    except ValueError:
        print(f"error: --synthesis-id must be a UUID, got {args.synthesis_id!r}", file=sys.stderr)
        return EXIT_CONFIG
    try:
        product = get_product_profile(args.product)
    except UnknownProductError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    settings = Settings.from_env()
    configure_logging(settings.log_level)
    if not (settings.supabase_url and settings.supabase_service_role_key):
        print("error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        return EXIT_CONFIG
    try:
        llm = AnthropicProvider.for_opportunities(settings)
    except LLMConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    try:
        repository = OpportunityRepository(
            SupabaseDatabase(settings.supabase_url, settings.supabase_service_role_key)
        )
    except PersistenceConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    print("Opportunity Creation\n────────────────────────────────", flush=True)
    try:
        outcome = run_opportunity_creation(
            synthesis_id, product, repository=repository, llm=llm,
            progress=lambda line: print(line, flush=True),
        )
    except OpportunitySourceError as exc:
        print(f"\nCannot create opportunities: {exc}", file=sys.stderr)
        return EXIT_SOURCE
    except Exception as exc:  # nothing partial was stored
        print(f"\nOpportunity creation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED

    if outcome.status == "insufficient_research":
        print(f"\nNot enough research to create opportunities: {outcome.message}")
        return EXIT_INSUFFICIENT
    print(f"\nOpportunity run {outcome.status}: {outcome.run_id} ({outcome.opportunities} opportunity(ies)).")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
