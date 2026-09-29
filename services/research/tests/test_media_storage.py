import httpx
import pytest

from research.media_storage import (
    DEFAULT_BUCKET,
    StorageConfigError,
    StorageError,
    SupabaseStorage,
    extension_for_mime_type,
    scene_asset_path,
)

SECRET = "sr-secret-not-real"


@pytest.mark.parametrize("mime_type,ext", [
    ("image/jpeg", "jpg"), ("image/png", "png"), ("image/webp", "webp"),
    ("video/mp4", "mp4"), ("application/octet-stream", "bin"),
])
def test_extension_for_mime_type(mime_type, ext):
    assert extension_for_mime_type(mime_type) == ext


def test_scene_asset_path_is_scoped_by_production_run_and_scene():
    path = scene_asset_path(production_id="prod-1", media_run_id="run-1", scene_number=2, extension="png")
    assert path.startswith("prod-1/run-1/scene-2-") and path.endswith(".png")


def test_scene_asset_path_is_unique_per_call():
    a = scene_asset_path(production_id="p", media_run_id="r", scene_number=1, extension="png")
    b = scene_asset_path(production_id="p", media_run_id="r", scene_number=1, extension="png")
    assert a != b  # a retry must not silently collide with/overwrite the earlier attempt


@pytest.mark.parametrize("url,key", [(None, SECRET), ("https://x.supabase.co", None), (None, None)])
def test_missing_configuration_is_a_config_error(url, key):
    with pytest.raises(StorageConfigError):
        SupabaseStorage(url, key)


def test_successful_upload_returns_the_bucket_path_and_public_url():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(201, json={"Id": "abc"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    storage = SupabaseStorage("https://proj.supabase.co", SECRET, http_client=client)
    result = storage.upload("prod-1/run-1/scene-1.png", b"bytes-here", content_type="image/png")

    assert result.bucket == DEFAULT_BUCKET
    assert result.path == "prod-1/run-1/scene-1.png"
    assert result.public_url == "https://proj.supabase.co/storage/v1/object/public/media-assets/prod-1/run-1/scene-1.png"
    [req] = seen
    assert req.url.path == "/storage/v1/object/media-assets/prod-1/run-1/scene-1.png"
    assert req.headers["authorization"] == f"Bearer {SECRET}"
    assert req.headers["content-type"] == "image/png"
    assert req.content == b"bytes-here"


def test_upload_uses_the_configured_bucket():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    storage = SupabaseStorage("https://proj.supabase.co", SECRET, bucket="custom-bucket", http_client=client)
    result = storage.upload("path.png", b"x", content_type="image/png")
    assert result.bucket == "custom-bucket"
    assert "/object/public/custom-bucket/" in result.public_url


def test_a_non_2xx_response_is_a_storage_error_without_leaking_the_key():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(403, text=f"forbidden {SECRET}")))
    storage = SupabaseStorage("https://proj.supabase.co", SECRET, http_client=client)
    with pytest.raises(StorageError) as info:
        storage.upload("path.png", b"x", content_type="image/png")
    assert SECRET not in str(info.value) and "403" in str(info.value)


def test_a_transport_exception_is_wrapped_as_a_storage_error():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    storage = SupabaseStorage("https://proj.supabase.co", SECRET, http_client=client)
    with pytest.raises(StorageError, match="ConnectError"):
        storage.upload("path.png", b"x", content_type="image/png")
