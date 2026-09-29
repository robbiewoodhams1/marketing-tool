import pytest
from media_fixtures import production_row, scene_row

from research.media_schema import AssetStatus, AssetType, GeneratedAsset, MediaValidationError
from research.media_validation import production_scene, validate_asset


def asset(**overrides):
    fields = dict(
        scene_number=1, asset_type=AssetType.IMAGE, status=AssetStatus.COMPLETED,
        provider="gemini", model="gemini-2.5-flash-image", prompt="p",
        generation_config={}, product_capability_refs=("quotes",),
        storage_bucket="media-assets", storage_path="p/r/scene-1.png",
        storage_url="https://example.com/scene-1.png", mime_type="image/png",
    )
    fields.update(overrides)
    return GeneratedAsset(**fields)


PRODUCTION = production_row()  # scene 1 -> ["quotes"], scene 2 -> ["reusable_templates"]


def test_a_valid_completed_asset_passes():
    validate_asset(asset(), production=PRODUCTION)  # no raise


def test_an_unknown_scene_number_is_rejected():
    with pytest.raises(MediaValidationError, match="does not exist"):
        validate_asset(asset(scene_number=99), production=PRODUCTION)


def test_a_capability_ref_outside_the_scenes_own_is_rejected():
    with pytest.raises(MediaValidationError, match="does not use in the production"):
        validate_asset(asset(product_capability_refs=("reusable_templates",)), production=PRODUCTION)


def test_a_capability_ref_matching_the_scenes_own_is_accepted():
    validate_asset(asset(scene_number=2, product_capability_refs=("reusable_templates",)), production=PRODUCTION)


def test_a_subset_of_the_scenes_capabilities_is_fine():
    wider = production_row(scenes=[scene_row(1, product_capability_refs=["quotes", "reusable_templates"])])
    validate_asset(asset(product_capability_refs=("quotes",)), production=wider)


def test_no_capabilities_cited_is_fine():
    validate_asset(asset(product_capability_refs=()), production=PRODUCTION)


def test_a_completed_asset_without_a_storage_path_is_rejected():
    with pytest.raises(MediaValidationError, match="must have a storage path"):
        validate_asset(asset(storage_path=None), production=PRODUCTION)


def test_a_completed_asset_without_a_storage_url_is_rejected():
    with pytest.raises(MediaValidationError, match="must have a storage path"):
        validate_asset(asset(storage_url=None), production=PRODUCTION)


def test_a_completed_asset_with_an_error_message_is_rejected():
    with pytest.raises(MediaValidationError, match="must not carry an error_message"):
        validate_asset(asset(error_message="oops"), production=PRODUCTION)


def test_a_failed_asset_needs_an_error_message():
    failed = asset(
        status=AssetStatus.FAILED, storage_bucket=None, storage_path=None, storage_url=None,
        mime_type=None,
    )
    with pytest.raises(MediaValidationError, match="must have an error_message"):
        validate_asset(failed, production=PRODUCTION)


def test_a_failed_asset_with_a_storage_reference_is_rejected():
    failed = asset(status=AssetStatus.FAILED, error_message="boom")
    with pytest.raises(MediaValidationError, match="must not have a storage reference"):
        validate_asset(failed, production=PRODUCTION)


def test_a_valid_failed_asset_passes():
    failed = asset(
        status=AssetStatus.FAILED, storage_bucket=None, storage_path=None, storage_url=None,
        mime_type=None, error_message="MediaProviderAPIError: HTTP 500",
    )
    validate_asset(failed, production=PRODUCTION)  # no raise


def test_the_error_names_the_scene_number():
    with pytest.raises(MediaValidationError, match="scene 1:"):
        validate_asset(asset(storage_path=None), production=PRODUCTION)


def test_production_scene_lookup():
    assert production_scene(PRODUCTION, 1)["number"] == 1
    assert production_scene(PRODUCTION, 99) is None
