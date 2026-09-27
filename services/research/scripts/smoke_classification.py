"""DEVELOPMENT SMOKE TEST: one LIVE (paid) Anthropic classification request.

Loads ONE stored video from an existing research job, classifies it through
the real AnthropicProvider and prints the result. By default it is read-only;
with --save it also appends one row to `interpretations` (needs the 20260927100000
migration); content is not modified.
Makes no YouTube requests and is NOT part of the unit tests.

    python scripts/smoke_classification.py --confirm-live-request [--save] [--job-id JOB_ID]

Needs LLM_API_KEY, SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the
environment (LLM_MODEL is optional).
"""

from __future__ import annotations

import argparse
import json
import sys

from research.classification import ClassificationInput, ClassificationInputError
from research.classification_prompt import PROMPT_VERSION
from research.config import Settings
from research.llm import (
    DEFAULT_MODEL,
    AnthropicProvider,
    LLMAPIError,
    LLMConfigError,
    LLMNetworkError,
    LLMResponseError,
)
from research.persistence import PersistenceError, ResearchRepository, SupabaseDatabase

DEFAULT_JOB_ID = "d8aee101-d48c-4be5-9247-57b33b56fe65"
COLUMNS = "id,external_id,title,description,transcript,views,likes,comments_count"
FIELDS = [
    ("topic", "Topic"),
    ("audience", "Audience"),
    ("pain_point", "Pain point"),
    ("hook", "Hook"),
    ("hook_type", "Hook type"),
    ("format", "Format"),
    ("emotion", "Emotion"),
    ("cta", "CTA"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--job-id", default=DEFAULT_JOB_ID)
    parser.add_argument("--confirm-live-request", action="store_true",
                        help="required: acknowledge that ONE real paid API request is made")
    parser.add_argument("--save", action="store_true",
                        help="also append the classification to interpretations (one row)")
    args = parser.parse_args()
    if not args.confirm_live_request:
        parser.error("refusing to call the Anthropic API without --confirm-live-request")

    settings = Settings.from_env()
    if not settings.llm_api_key:
        print("FAILED [authentication/configuration]: LLM_API_KEY is not set; no request made")
        return 1
    if not (settings.supabase_url and settings.supabase_service_role_key):
        print("FAILED [configuration]: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set; no request made")
        return 1

    try:
        db = SupabaseDatabase(settings.supabase_url, settings.supabase_service_role_key)
        rows = db.select("content", COLUMNS, eq={"research_job_id": args.job_id, "platform": "youtube"})
    except PersistenceError as exc:
        print(f"FAILED [supabase read]: {exc}")
        return 1
    if not rows:
        print(f"FAILED: no youtube content rows for job {args.job_id}; no request made")
        return 1
    row = rows[0]

    try:
        item = ClassificationInput(
            video_id=row["external_id"],
            title=row["title"],
            description=row["description"],
            transcript=row["transcript"],
            views=row["views"],
            likes=row["likes"],
            comments_count=row["comments_count"],
        )
    except ClassificationInputError as exc:
        print(f"FAILED [stored data is not valid classification input]: {exc}")
        return 1

    model = settings.llm_model or DEFAULT_MODEL
    try:
        provider = AnthropicProvider.from_settings(settings)
        result = provider.classify_content(item)  # the ONE live request
    except LLMConfigError as exc:
        print(f"FAILED [authentication/configuration]: {exc}")
        return 1
    except LLMNetworkError as exc:
        print(f"FAILED [network]: {exc}")
        return 1
    except LLMAPIError as exc:
        kind = "authentication/configuration" if exc.status in (401, 403) else "Anthropic API error"
        print(f"FAILED [{kind}]: {exc}")
        return 1
    except LLMResponseError as exc:
        # The provider wraps extraction, JSON and ClassificationResult failures here.
        print(f"FAILED [response extraction / JSON parsing / ClassificationResult validation]: {exc}")
        # Dev diagnostic: what the model actually returned (never the API key).
        print(f"stop_reason: {exc.stop_reason}")
        print(f"raw model output: {json.dumps(exc.raw, ensure_ascii=False, default=str)[:4000]}")
        return 1

    print("Classification smoke test")
    print("─────────────────────────")
    print(f"Video: {item.title}")
    print(f"Video ID: {item.video_id}")
    print(f"Model: {model}")
    print(f"Input: description={'yes' if item.description else 'no'}, "
          f"transcript={'yes' if item.transcript else 'no'}, views={item.views}, "
          f"likes={item.likes}, comments={item.comments_count}")
    for name, label in FIELDS:
        field = getattr(result, name)
        print(f"\n{label}:")
        print(f"Value: {field.value}")
        print(f"Confidence: {field.confidence}")
        print(f"Evidence: {field.evidence}")
    print("\nResult passed ClassificationResult validation.")
    if not args.save:
        print("Nothing was written to Supabase (use --save to store it).")
        return 0
    try:
        saved = ResearchRepository(db).save_classifications(
            args.job_id, {item.video_id: result}, model=model, prompt_version=PROMPT_VERSION
        )
    except PersistenceError as exc:
        print(f"FAILED [classification persistence]: {exc}")
        return 1
    print(f"Interpretation for content row {row['id']}: inserted={saved.inserted}, "
          f"skipped (identical one already exists)={saved.skipped}. content was not modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
