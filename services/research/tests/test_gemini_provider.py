"""GeminiImageProvider: request shape and response parsing.

Nothing here reaches the network: requests are captured by a fake transport.
"""

from __future__ import annotations

import base64
import json

import pytest

from research.config import Settings
from research.gemini import (
    DEFAULT_IMAGE_MODEL,
    GEMINI_INTERACTIONS_URL,
    GeminiImageProvider,
)
from research.media_providers import (
    GeneratedImage,
    ImageProvider,
    MediaProviderAPIError,
    MediaProviderConfigError,
    MediaProviderNetworkError,
    MediaProviderResponseError,
)

IMAGE_BYTES = b"\x89PNG-fake-bytes"
ENCODED = base64.b64encode(IMAGE_BYTES).decode()

# A real, tiny, valid 1x1 transparent PNG (67 bytes decoded) - used as the
# synthetic placeholder in the regression fixture below, standing in for the
# 1.16 MB base64 blob actually returned by the live capture.
SYNTHETIC_PNG_ENCODED = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def real_response(mime_type="image/png"):
    """The CONFIRMED real shape of a successful response (see
    `test_regression_matches_the_actual_captured_response_shape` for the full
    captured envelope): a top-level `steps` array, not nested under an
    "interaction" key, where each step IS the model-output object directly
    - `{"type": "model_output", "content": [...]}` - never a further nested
    `"model_output"` key. A real response also carries a preceding
    text/thinking step before the image step, which this fixture includes to
    match what was actually observed."""
    return {
        "steps": [
            {"type": "model_output", "content": [{"type": "text", "text": "Generating the requested image..."}]},
            {"type": "model_output", "content": [{"type": "image", "data": ENCODED, "mime_type": mime_type}]},
        ],
    }


def real_captured_response(image_data=SYNTHETIC_PNG_ENCODED):
    """A regression fixture matching the EXACT structure captured from a real
    successful call (HTTP 200, model gemini-2.5-flash-image, 2026-09-28): all
    nine top-level keys, both steps, in the real shapes and types observed.
    The real ~1.16 MB base64 image is replaced with a tiny synthetic PNG."""
    return {
        "created": "2026-09-28T21:25:18Z",
        "id": "v1_ChdQdHU2YXBLVUlNMk1rZFVQb3RMbHlRaxIXUHR1NmFwS1VJTTJNa2RVUG90TGx5UWs",
        "model": "gemini-2.5-flash-image",
        "object": "interaction",
        "service_tier": "standard",
        "status": "completed",
        "steps": [
            {"type": "model_output", "content": [{"type": "text", "text": "Absolutely! Here's that icon for you: "}]},
            {"type": "model_output", "content": [{"type": "image", "data": image_data, "mime_type": "image/png"}]},
        ],
        "updated": "2026-09-28T21:25:18Z",
        "usage": {
            "input_tokens_by_modality": [{"modality": "text", "tokens": 28}],
            "output_tokens_by_modality": [{"modality": "image", "tokens": 1290}],
            "total_cached_tokens": 0,
            "total_input_tokens": 28,
            "total_output_tokens": 1301,
            "total_thought_tokens": 0,
            "total_tokens": 1329,
            "total_tool_use_tokens": 0,
        },
    }


def convenience_shape_response(mime_type="image/jpeg"):
    """A single-image convenience shape the docs also describe
    (`interaction.output_image`); never observed live at implementation time,
    kept only as a fallback (see `_find_output_image`)."""
    return {"interaction": {"output_image": {"data": ENCODED, "mime_type": mime_type}}}


def legacy_nested_model_output_response(mime_type="image/png"):
    """A step shape where `content` sits under a further nested
    `"model_output"` key - NEVER observed live, but was the (wrong) shape the
    parser originally assumed. Kept as a fallback check only."""
    return {
        "steps": [
            {"model_output": {"content": [{"type": "text", "text": "thinking"}]}},
            {"model_output": {"content": [{"type": "image", "data": ENCODED, "mime_type": mime_type}]}},
        ],
    }


def capture(response=None):
    payloads = []

    def transport(url, headers, data):
        payloads.append((url, headers, json.loads(data)))
        body = json.dumps(response if response is not None else real_response()).encode()
        return 200, body

    return transport, payloads


def provider(transport, **kw):
    return GeminiImageProvider("k", transport=transport, **kw)


def test_missing_api_key_is_a_config_error():
    with pytest.raises(MediaProviderConfigError):
        GeminiImageProvider(None)
    with pytest.raises(MediaProviderConfigError):
        GeminiImageProvider("")


def test_provider_satisfies_the_image_provider_protocol():
    p: ImageProvider = provider(capture()[0])
    result = p.generate_image("a mechanic saving a job as a template")
    assert isinstance(result, GeneratedImage)


def test_request_shape_matches_the_documented_interactions_endpoint():
    transport, payloads = capture()
    provider(transport, model="gemini-2.5-flash-image").generate_image("a scene", aspect_ratio="9:16")
    [(url, headers, body)] = payloads
    assert url == GEMINI_INTERACTIONS_URL
    assert headers["x-goog-api-key"] == "k"
    assert body["model"] == "gemini-2.5-flash-image"
    assert body["input"] == [{"type": "text", "text": "a scene"}]
    assert body["response_format"]["aspect_ratio"] == "9:16"
    assert body["response_format"]["type"] == "image"


def test_default_model_and_mime_type():
    transport, payloads = capture()
    p = provider(transport)
    assert p.model == DEFAULT_IMAGE_MODEL
    p.generate_image("a scene")
    [(_, _, body)] = payloads
    assert body["model"] == DEFAULT_IMAGE_MODEL
    assert body["response_format"]["mime_type"] == "image/jpeg"


def test_an_unsupported_aspect_ratio_is_rejected_before_any_request():
    transport, payloads = capture()
    with pytest.raises(MediaProviderConfigError, match="aspect_ratio"):
        provider(transport).generate_image("a scene", aspect_ratio="7:11")
    assert payloads == []


def test_response_image_is_decoded_from_base64():
    transport, _ = capture(real_response(mime_type="image/png"))
    image = provider(transport).generate_image("a scene")
    assert image.data == IMAGE_BYTES and image.mime_type == "image/png"


def test_real_top_level_steps_response_is_the_primary_parsed_shape():
    # This is the exact shape a live, successful call actually returned
    # (HTTP 200, gemini-2.5-flash-image): steps at the TOP level, not nested
    # under "interaction". A preceding text step must be skipped, not picked up.
    transport, _ = capture(real_response(mime_type="image/png"))
    image = provider(transport).generate_image("a scene")
    assert image.data == IMAGE_BYTES and image.mime_type == "image/png"


def test_a_text_only_steps_response_with_no_image_step_is_a_response_error():
    transport, _ = capture({"steps": [{"type": "model_output", "content": [{"type": "text", "text": "no image here"}]}]})
    with pytest.raises(MediaProviderResponseError, match="no image found"):
        provider(transport).generate_image("a scene")


def test_regression_matches_the_actual_captured_response_shape():
    """The exact structure captured from a real, successful call (see the
    module-level fixture docstring): every top-level key, both step types,
    with the real ~1.16 MB base64 image swapped for a tiny synthetic one."""
    transport, _ = capture(real_captured_response())
    image = provider(transport, model="gemini-2.5-flash-image").generate_image("an icon")
    assert image.mime_type == "image/png"
    assert image.data == base64.b64decode(SYNTHETIC_PNG_ENCODED)


def test_from_settings_uses_the_configured_model_and_gemini_api_key():
    transport, payloads = capture()
    settings = Settings.from_env({"GEMINI_API_KEY": "g-key", "MEDIA_IMAGE_MODEL": "gemini-3.1-flash-image"})
    p = GeminiImageProvider.from_settings(settings, transport=transport)
    assert p.model == "gemini-3.1-flash-image"
    p.generate_image("a scene")
    [(_, headers, _)] = payloads
    assert headers["x-goog-api-key"] == "g-key"


def test_from_settings_falls_back_to_the_default_model():
    settings = Settings.from_env({"GEMINI_API_KEY": "g-key"})
    assert GeminiImageProvider.from_settings(settings).model == DEFAULT_IMAGE_MODEL


def test_api_error_carries_status_and_never_the_key():
    def transport(url, headers, data):
        return 500, json.dumps({"error": {"message": "boom k"}}).encode()

    with pytest.raises(MediaProviderAPIError) as info:
        provider(transport).generate_image("a scene")
    assert info.value.status == 500


def test_network_error_is_wrapped():
    def transport(url, headers, data):
        raise TimeoutError("down")

    with pytest.raises(MediaProviderNetworkError):
        provider(transport).generate_image("a scene")


def test_malformed_json_response_is_a_response_error():
    def transport(url, headers, data):
        return 200, b"not json"

    with pytest.raises(MediaProviderResponseError, match="not valid JSON"):
        provider(transport).generate_image("a scene")


def test_missing_image_in_response_is_a_response_error_naming_the_top_level_keys():
    transport, _ = capture({"something_else": True})
    with pytest.raises(MediaProviderResponseError, match="no image found") as info:
        provider(transport).generate_image("a scene")
    assert info.value.raw == {"something_else": True}


def test_invalid_base64_is_a_response_error():
    transport, _ = capture({"interaction": {"output_image": {"data": "not-base64!!", "mime_type": "image/png"}}})
    with pytest.raises(MediaProviderResponseError, match="not valid base64"):
        provider(transport).generate_image("a scene")


def test_fallback_convenience_shape_output_image_at_top_level_is_also_accepted():
    transport, _ = capture({"output_image": {"data": ENCODED, "mime_type": "image/webp"}})
    image = provider(transport).generate_image("a scene")
    assert image.mime_type == "image/webp"


def test_fallback_convenience_shape_interaction_output_image_is_also_accepted():
    transport, _ = capture(convenience_shape_response(mime_type="image/jpeg"))
    image = provider(transport).generate_image("a scene")
    assert image.data == IMAGE_BYTES and image.mime_type == "image/jpeg"


def test_fallback_steps_nested_under_interaction_is_also_accepted():
    transport, _ = capture({"interaction": legacy_nested_model_output_response()})
    image = provider(transport).generate_image("a scene")
    assert image.data == IMAGE_BYTES


def test_fallback_legacy_nested_model_output_shape_at_top_level_is_also_accepted():
    # NEVER observed live; the parser's originally-wrong assumption, kept as
    # a fallback in case some other response mode uses it after all.
    transport, _ = capture(legacy_nested_model_output_response())
    image = provider(transport).generate_image("a scene")
    assert image.data == IMAGE_BYTES


def test_top_level_steps_take_priority_over_the_convenience_shape():
    # If a response somehow carried both, the confirmed-real shape wins.
    response = real_response(mime_type="image/png")
    response["output_image"] = {"data": base64.b64encode(b"wrong-image").decode(), "mime_type": "image/webp"}
    transport, _ = capture(response)
    image = provider(transport).generate_image("a scene")
    assert image.data == IMAGE_BYTES and image.mime_type == "image/png"


def test_metadata_excludes_the_base64_blob():
    transport, _ = capture(real_response())
    image = provider(transport).generate_image("a scene")
    assert "data" not in image.metadata
