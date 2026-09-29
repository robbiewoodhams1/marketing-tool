import pytest

from research.media_schema import AssetStatus, AssetType, GeneratedAsset, MediaValidationError


def asset(**overrides):
    fields = dict(
        scene_number=1, asset_type=AssetType.IMAGE, status=AssetStatus.COMPLETED,
        provider="gemini", model="gemini-2.5-flash-image", prompt="a still frame",
        generation_config={"aspect_ratio": "9:16"}, product_capability_refs=("quotes",),
        provider_metadata={}, storage_bucket="media-assets", storage_path="prod-1/run-1/scene-1.png",
        storage_url="https://example.com/scene-1.png", mime_type="image/png", width=1080, height=1920,
        error_message=None,
    )
    fields.update(overrides)
    return GeneratedAsset(**fields)


def test_asset_type_taxonomy_is_small():
    assert [t.value for t in AssetType] == ["image", "video"]


def test_asset_status_taxonomy_is_small():
    assert [s.value for s in AssetStatus] == ["completed", "failed"]


def test_a_completed_asset_round_trips_to_payload():
    a = asset()
    payload = a.to_payload()
    assert payload["scene_number"] == 1 and payload["asset_type"] == "image" and payload["status"] == "completed"
    assert payload["storage_path"] == "prod-1/run-1/scene-1.png" and payload["error_message"] is None


def test_a_failed_asset_has_no_storage_reference():
    a = asset(
        status=AssetStatus.FAILED, storage_bucket=None, storage_path=None, storage_url=None,
        mime_type=None, width=None, height=None, error_message="MediaProviderAPIError: HTTP 500",
    )
    payload = a.to_payload()
    assert payload["status"] == "failed"
    assert payload["storage_path"] is None and payload["error_message"]


def test_capability_refs_are_stored_as_a_plain_list():
    a = asset(product_capability_refs=("quotes", "reusable_templates"))
    assert a.to_payload()["product_capability_refs"] == ["quotes", "reusable_templates"]


def test_media_validation_error_carries_the_scene_number():
    err = MediaValidationError("bad thing", scene_number=3)
    assert err.scene_number == 3 and str(err) == "scene 3: bad thing"


def test_media_validation_error_without_a_scene_number():
    err = MediaValidationError("bad thing")
    assert err.scene_number is None and str(err) == "bad thing"


@pytest.mark.parametrize("bad", ["pending", "PROCESSING", ""])
def test_unknown_status_values_are_not_a_valid_enum_member(bad):
    with pytest.raises(ValueError):
        AssetStatus(bad)


@pytest.mark.parametrize("bad", ["audio", "gif", ""])
def test_unknown_asset_type_values_are_not_a_valid_enum_member(bad):
    with pytest.raises(ValueError):
        AssetType(bad)
