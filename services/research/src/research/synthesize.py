"""Terminal entry point and service: synthesise one research job.

    python -m research.synthesize --job-id JOB_ID

    load job -> load content, comments, classifications -> build evidence pack
    -> run key -> (already stored? stop) -> LLM -> validate structure
    -> validate evidence (refs, verbatim quotes, rules) -> save atomically

Synthesis is a separate analytical operation. It never touches the research
job's status: a synthesis failure leaves a completed job completed.

Needs LLM_API_KEY, SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (SYNTHESIS_MODEL
optional). Exit codes: 0 stored (or an identical run already existed),
1 job cannot be synthesised, 2 bad arguments / missing configuration,
3 the run failed (nothing stored), 4 insufficient evidence (no LLM call made).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from research.config import Settings
from research.evidence_pack import (
    InsufficientEvidenceError,
    PackBounds,
    build_evidence_pack,
)
from research.llm import AnthropicProvider, LLMConfigError, SynthesisProvider
from research.logging import configure_logging, get_logger
from research.persistence import (
    STATUS_COMPLETED,
    PersistenceConfigError,
    SupabaseDatabase,
)
from research.synthesis import SynthesisValidationError
from research.synthesis_store import (
    SynthesisRepository,
    build_save_payload,
    compute_run_key,
)
from research.synthesis_validation import validate_synthesis

log = get_logger("research.synthesize")

Progress = Callable[[str], None]

EXIT_OK, EXIT_JOB, EXIT_CONFIG, EXIT_FAILED, EXIT_INSUFFICIENT = 0, 1, 2, 3, 4


class SynthesisJobError(Exception):
    """The job cannot be synthesised. Nothing was changed."""


@dataclass(frozen=True)
class SynthesisOutcome:
    status: str  # "created" | "exists" | "insufficient_evidence"
    synthesis_id: str | None = None
    run_key: str | None = None
    insights: int = 0
    content_total: int = 0
    classified_content_count: int = 0
    message: str = ""


def _ignore(_: str) -> None:
    pass


def run_synthesis(
    job_id: str,
    *,
    repository: SynthesisRepository,
    llm: SynthesisProvider,
    bounds: PackBounds = PackBounds(),
    progress: Progress = _ignore,
) -> SynthesisOutcome:
    """Synthesise `job_id`. Raises on any failure; nothing partial is ever stored.

    Returns `insufficient_evidence` (no LLM call, nothing stored) when the job
    has too little evidence, `exists` when an identical run is already stored
    (no LLM call, or the race was lost), and `created` otherwise - including
    when the model found no supported insight (the run is still recorded).
    """
    job = repository.get_job(job_id)
    if job is None:
        raise SynthesisJobError(f"Research job {job_id} does not exist")
    if job.status != STATUS_COMPLETED:
        raise SynthesisJobError(
            f"Research job {job_id} is {job.status!r}, not 'completed'; only completed "
            "jobs are synthesised"
        )

    progress("Loading evidence...")
    evidence = repository.load_evidence(job)
    progress(
        f"✓ {len(evidence.content)} content, {len(evidence.comments)} comments, "
        f"{len(evidence.interpretations)} classification interpretations"
    )

    try:
        pack = build_evidence_pack(
            job={"query": job.query, "audience": job.audience, "objective": job.objective},
            content_rows=evidence.content,
            comment_rows=evidence.comments,
            interpretation_rows=evidence.interpretations,
            bounds=bounds,
        )
    except InsufficientEvidenceError as exc:
        progress(f"⚠ {exc}")
        return SynthesisOutcome(
            status="insufficient_evidence", content_total=len(evidence.content), message=str(exc)
        )
    progress(
        f"✓ Evidence pack: {pack.content_total} content, {pack.classified_content_count} classified "
        f"({pack.classification_coverage:.0%}), {len(pack.comment_ids)} comments shown"
    )

    run_key = compute_run_key(research_job_id=job.id, model=llm.model, pack=pack)
    if (existing := repository.find_run(run_key)) is not None:
        progress("✓ An identical synthesis already exists; nothing to do")
        return SynthesisOutcome(
            status="exists", synthesis_id=existing, run_key=run_key,
            content_total=pack.content_total,
            classified_content_count=pack.classified_content_count,
        )

    progress("Synthesising...")
    output = llm.synthesize_research(pack)
    progress(f"✓ Model returned {len(output.insights)} insight(s)")
    # TEMPORARY DEBUG (revert): print the parsed model output before validation.
    print("=== DEBUG: parsed synthesis (before validation) ===", flush=True)
    print(json.dumps(output.to_dict(), indent=2, ensure_ascii=False), flush=True)
    print("=== END DEBUG ===", flush=True)

    progress("Validating evidence...")
    try:
        validated = validate_synthesis(output, pack)
    except SynthesisValidationError as exc:
        exc.raw = output.to_dict()  # development diagnostics; never persisted
        raise
    progress("✓ Every reference, quote and rule verified")

    saved = repository.save(
        build_save_payload(
            research_job_id=job.id, model=llm.model, run_key=run_key,
            pack=pack, output=output, validated=validated,
        )
    )
    progress(f"✓ Synthesis {saved.status}: {saved.synthesis_id}")
    return SynthesisOutcome(
        status=saved.status, synthesis_id=saved.synthesis_id, run_key=run_key,
        insights=saved.insights, content_total=pack.content_total,
        classified_content_count=pack.classified_content_count,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m research.synthesize", description="Synthesise one completed research job."
    )
    parser.add_argument("--job-id", required=True, help="id of a completed research_jobs row")
    args = parser.parse_args(argv)
    try:
        job_id = str(uuid.UUID(args.job_id))
    except ValueError:
        print(f"error: --job-id must be a UUID, got {args.job_id!r}", file=sys.stderr)
        return EXIT_CONFIG

    settings = Settings.from_env()
    configure_logging(settings.log_level)
    if not (settings.supabase_url and settings.supabase_service_role_key):
        print("error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        return EXIT_CONFIG
    try:
        llm = AnthropicProvider.for_synthesis(settings)
    except LLMConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    try:
        repository = SynthesisRepository(
            SupabaseDatabase(settings.supabase_url, settings.supabase_service_role_key)
        )
    except PersistenceConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    print("Research Synthesis\n────────────────────────────────", flush=True)
    try:
        outcome = run_synthesis(
            job_id, repository=repository, llm=llm, progress=lambda line: print(line, flush=True)
        )
    except SynthesisJobError as exc:
        print(f"\nCannot synthesise job: {exc}", file=sys.stderr)
        return EXIT_JOB
    except Exception as exc:  # nothing partial was stored; the job status is untouched
        print(f"\nSynthesis failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILED

    if outcome.status == "insufficient_evidence":
        print(f"\nNot enough evidence to synthesise: {outcome.message}")
        return EXIT_INSUFFICIENT
    print(f"\nSynthesis {outcome.status}: {outcome.synthesis_id} ({outcome.insights} insight(s)).")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
