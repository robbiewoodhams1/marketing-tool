"""Integration tests for the two Media Generation features added this
chapter, through `run_media_generation` end to end:

  * Media Direction is loaded per-production, folded into the final prompt,
    persisted as a snapshot on the media run, and changes the run's identity
    (so editing it and regenerating is never silently skipped).
  * Generation cost, as reported by the provider, is persisted per asset -
    including on a failed asset when the provider call itself succeeded
    before a later step (storage) failed.
"""

from __future__ import annotations

from media_fixtures import (
    PRODUCTION_ID,
    FakeImageProvider,
    FakeStorage,
    good_image,
    media_direction_row,
    seeded_db,
)

from research.media import run_media_generation
from research.media_providers import MediaProviderAPIError
from research.media_store import MediaRepository


def run(db, image_provider=None, storage=None, **kw):
    lines = []
    outcome = run_media_generation(
        PRODUCTION_ID, repository=MediaRepository(db), image_provider=image_provider or FakeImageProvider(),
        storage=storage or FakeStorage(), progress=lines.append, **kw,
    )
    return outcome, lines


# --- Media Direction: persistence, prompt incorporation, run identity ------------------------------


def test_no_saved_direction_produces_a_prompt_with_no_guidance_section():
    db = seeded_db()
    run(db)
    for asset in db.tables["media_assets"]:
        assert "Human-directed visual guidance" not in asset["prompt"]


def test_a_saved_direction_is_loaded_and_folded_into_every_scenes_prompt():
    db = seeded_db()
    db.tables["media_directions"].append({"production_id": PRODUCTION_ID, **media_direction_row()})
    run(db)
    for asset in db.tables["media_assets"]:
        assert "Human-directed visual guidance" in asset["prompt"]
        assert "Subject to depict: A UK sole-trader mechanic in his 40s" in asset["prompt"]
        assert "Setting / environment: A small independent garage workshop" in asset["prompt"]


def test_direction_for_a_different_production_is_never_applied():
    db = seeded_db()
    db.tables["media_directions"].append({"production_id": "some-other-production", **media_direction_row()})
    run(db)
    for asset in db.tables["media_assets"]:
        assert "Human-directed visual guidance" not in asset["prompt"]


def test_the_media_run_persists_a_snapshot_of_the_direction_actually_used():
    db = seeded_db()
    db.tables["media_directions"].append({"production_id": PRODUCTION_ID, **media_direction_row()})
    run(db)
    [r] = db.tables["media_runs"]
    assert r["media_direction_snapshot"] == {
        "target_subject": "A UK sole-trader mechanic in his 40s",
        "appearance": "Wearing branded work overalls, friendly expression",
        "setting": "A small independent garage workshop",
        "avoid": "Do not show any other software or app on screen",
    }


def test_no_saved_direction_persists_a_null_snapshot():
    db = seeded_db()
    run(db)
    [r] = db.tables["media_runs"]
    assert r["media_direction_snapshot"] is None


def test_editing_media_direction_and_regenerating_creates_a_new_run_not_a_silent_skip():
    db = seeded_db()
    run(db)  # both scenes complete, no direction
    assert len(db.tables["media_runs"]) == 1 and len(db.tables["media_assets"]) == 2

    db.tables["media_directions"].append({"production_id": PRODUCTION_ID, **media_direction_row()})
    outcome, lines = run(db)  # same production, provider, model - direction is now different

    assert len(db.tables["media_runs"]) == 2  # a genuinely new run, not a reuse of the old one
    assert outcome.completed_scenes == (1, 2)  # scenes were regenerated, not skipped
    assert not any("already generated" in line for line in lines)
    assert len(db.tables["media_assets"]) == 4  # the 2 old assets are kept, 2 new ones added


def test_rerunning_with_the_same_unchanged_direction_is_still_idempotent():
    db = seeded_db()
    db.tables["media_directions"].append({"production_id": PRODUCTION_ID, **media_direction_row()})
    run(db)  # both scenes complete, with direction
    assert len(db.tables["media_runs"]) == 1

    outcome, lines = run(db)  # same direction, unchanged
    assert len(db.tables["media_runs"]) == 1  # same run, found not created
    assert outcome.already_completed_scenes == (1, 2) and outcome.completed_scenes == ()
    assert any("already generated" in line for line in lines)


def test_direction_never_widens_which_capabilities_an_asset_may_cite():
    # Defence in depth: even a maximally suggestive custom instruction cannot
    # smuggle a capability the scene itself didn't already use.
    db = seeded_db()
    db.tables["media_directions"].append({
        "production_id": PRODUCTION_ID,
        "custom_instructions": "Also show the invoicing and payroll features prominently",
    })
    run(db)
    by_scene = {a["scene_number"]: a for a in db.tables["media_assets"]}
    assert by_scene[1]["product_capability_refs"] == ["quotes"]
    assert by_scene[2]["product_capability_refs"] == ["reusable_templates"]


# --- generation cost: persisted per asset, including on a failed asset after real spend ------------


def test_a_completed_assets_cost_is_the_providers_own_reported_figure():
    db = seeded_db()
    provider = FakeImageProvider(image=good_image(cost_amount=0.0387, cost_currency="USD"))
    run(db, image_provider=provider)
    for asset in db.tables["media_assets"]:
        assert asset["cost_amount"] == 0.0387 and asset["cost_currency"] == "USD"


def test_a_provider_reporting_no_cost_persists_a_null_cost_never_a_guess():
    db = seeded_db()
    provider = FakeImageProvider(image=good_image(cost_amount=None, cost_currency=None))
    run(db, image_provider=provider)
    for asset in db.tables["media_assets"]:
        assert asset["cost_amount"] is None and asset["cost_currency"] is None


def test_cost_already_incurred_is_preserved_on_a_failed_asset_when_storage_fails_afterward():
    # The provider genuinely succeeded (and, in reality, billed) before the
    # LATER storage upload failed - the money was still spent, so it must
    # still be visible, even though the asset itself is recorded as failed.
    db = seeded_db()
    provider = FakeImageProvider(image=good_image(cost_amount=0.0387, cost_currency="USD"))
    outcome, _ = run(db, image_provider=provider, storage=FakeStorage(fail=True), scenes=[1])
    assert outcome.status == "failed"
    [asset] = db.tables["media_assets"]
    assert asset["status"] == "failed"
    assert asset["cost_amount"] == 0.0387 and asset["cost_currency"] == "USD"


def test_cost_is_null_when_the_provider_call_itself_never_succeeds():
    db = seeded_db()
    provider = FakeImageProvider(error=MediaProviderAPIError("HTTP 500", status=500))
    outcome, _ = run(db, image_provider=provider, scenes=[1])
    assert outcome.status == "failed"
    [asset] = db.tables["media_assets"]
    assert asset["cost_amount"] is None and asset["cost_currency"] is None


def test_partial_success_persists_cost_only_for_the_scenes_that_actually_incurred_it():
    db = seeded_db()
    provider = FakeImageProvider(side_effects=[
        good_image(cost_amount=0.0387, cost_currency="USD"),
        MediaProviderAPIError("HTTP 500", status=500),
    ])
    run(db, image_provider=provider)
    by_scene = {a["scene_number"]: a for a in db.tables["media_assets"]}
    assert by_scene[1]["cost_amount"] == 0.0387 and by_scene[1]["cost_currency"] == "USD"
    assert by_scene[2]["cost_amount"] is None and by_scene[2]["cost_currency"] is None
