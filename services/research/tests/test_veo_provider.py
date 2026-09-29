"""VeoVideoProvider: request shape, polling, and response parsing.

Nothing here reaches the network or sleeps for real: the POST transport (used
for the `predictLongRunning` start call) and the GET transport (used for
polling and for downloading the finished video) are both fakes, and `sleep`/
`clock` are injected so a timeout test runs instantly.
"""

from __future__ import annotations

import json

import pytest

from research.config import Settings
from research.media_providers import (
    GeneratedVideo,
    MediaProviderAPIError,
    MediaProviderConfigError,
    MediaProviderNetworkError,
    MediaProviderResponseError,
    VideoProvider,
)
from research.veo import (
    DEFAULT_ASPECT_RATIO,
    DEFAULT_RESOLUTION,
    DEFAULT_VIDEO_MODEL,
    VEO_BASE_URL,
    VIDEO_COST_USD_PER_SECOND,
    MediaProviderTimeoutError,
    VeoVideoProvider,
    nearest_supported_duration,
)

OPERATION_NAME = "models/veo-3.1-fast-generate-preview/operations/abc123"
VIDEO_URI = "https://generativelanguage.googleapis.com/v1beta/files/xyz:download?alt=media"
VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp4-fake-video-bytes"


def start_response():
    return {"name": OPERATION_NAME}


def done_response(uri=VIDEO_URI, extra_sample_fields=None):
    sample = {"video": {"uri": uri}}
    if extra_sample_fields:
        sample.update(extra_sample_fields)
    return {
        "done": True,
        "response": {"generateVideoResponse": {"generatedSamples": [sample]}},
    }


def not_done_response():
    return {"done": False}


class FakeClock:
    """A controllable monotonic clock: advances only when told to."""

    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def capturing_post_transport(response=None, status=200):
    # Captures BOTH the round-tripped dict (convenient for most assertions)
    # AND the raw bytes actually sent over the wire (`raw_bodies`): a bug like
    # `str(duration)` before `json.dumps` survives a json.loads/json.dumps
    # round trip (it's still valid JSON, just the wrong type), so anything
    # asserting only against the parsed dict must compare against the CORRECT
    # type, not merely be internally consistent with a wrong implementation
    # (see test_a_scene_duration_is_sent_as_a_json_number_never_a_string,
    # which inspects raw_bodies directly for exactly this reason).
    payloads = []
    raw_bodies = []

    def transport(url, headers, data):
        raw_bodies.append(data)
        payloads.append((url, headers, json.loads(data)))
        body = json.dumps(response if response is not None else start_response()).encode()
        return status, body

    transport.raw_bodies = raw_bodies
    return transport, payloads


def scripted_get_transport(responses):
    """Returns one (status, body-dict-or-bytes) per call, in order; the last
    entry repeats once exhausted."""
    calls = []
    remaining = list(responses)

    def transport(url, headers):
        calls.append((url, dict(headers)))
        entry = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        status, body = entry
        return status, (json.dumps(body).encode() if isinstance(body, dict) else body)

    return transport, calls


def provider(transport=None, get_transport=None, **kw):
    return VeoVideoProvider(
        "k", transport=transport or capturing_post_transport()[0],
        get_transport=get_transport or scripted_get_transport([(200, done_response())])[0],
        sleep=lambda s: None, clock=lambda: 0.0, **kw,
    )


# --- configuration -----------------------------------------------------------------------------


def test_missing_api_key_is_a_config_error():
    with pytest.raises(MediaProviderConfigError):
        VeoVideoProvider(None)
    with pytest.raises(MediaProviderConfigError):
        VeoVideoProvider("")


def test_provider_satisfies_the_video_provider_protocol():
    p: VideoProvider = provider()
    result = p.generate_video("a mechanic saving a job as a template")
    assert isinstance(result, GeneratedVideo)


def test_default_model_and_aspect_ratio_and_resolution():
    assert VeoVideoProvider("k").model == DEFAULT_VIDEO_MODEL
    transport, payloads = capturing_post_transport()
    provider(transport).generate_video("a scene")
    [(url, headers, body)] = payloads
    assert url == f"{VEO_BASE_URL}/models/{DEFAULT_VIDEO_MODEL}:predictLongRunning"
    assert headers["x-goog-api-key"] == "k"
    assert body["parameters"]["aspectRatio"] == DEFAULT_ASPECT_RATIO == "9:16"
    assert body["parameters"]["resolution"] == DEFAULT_RESOLUTION


def test_request_shape_matches_the_documented_predict_long_running_endpoint():
    transport, payloads = capturing_post_transport()
    provider(transport).generate_video("a scene", aspect_ratio="16:9", resolution="1080p", duration_seconds=6)
    [(url, headers, body)] = payloads
    assert url == f"{VEO_BASE_URL}/models/{DEFAULT_VIDEO_MODEL}:predictLongRunning"
    assert body["instances"] == [{"prompt": "a scene"}]
    assert body["parameters"]["aspectRatio"] == "16:9"
    assert body["parameters"]["resolution"] == "1080p"
    # a JSON number, never a string (a live smoke test on 2026-09-29 got HTTP
    # 400 "The value type for durationSeconds needs to be a number" when this
    # was `str(duration)` - see test_a_scene_duration_is_sent_as_a_json_number_never_a_string)
    assert body["parameters"]["durationSeconds"] == 6
    assert isinstance(body["parameters"]["durationSeconds"], int)


def test_a_scene_duration_is_sent_as_a_json_number_never_a_string():
    """Regression test for a real live-API failure (2026-09-29): the request
    was originally built with `str(duration)`, which is indistinguishable
    from the correct value once round-tripped through `json.loads` (a plain
    Python dict comparison like `body["durationSeconds"] == 6` would pass
    for `"6"` too, since `int(6) == "6"` is False but a careless assertion
    written against the wrong assumption asserted `== "6"` and passed).
    This inspects the RAW serialized HTTP body bytes directly, so it fails
    if `durationSeconds` is ever again encoded as a quoted string."""
    transport, _ = capturing_post_transport()
    provider(transport).generate_video("a scene", duration_seconds=6)
    [raw] = transport.raw_bodies
    assert b'"durationSeconds": 6' in raw or b'"durationSeconds":6' in raw
    assert b'"durationSeconds": "6"' not in raw and b'"durationSeconds":"6"' not in raw
    # belt-and-braces: re-parse and check the TYPE, not just equality (equality
    # alone is a trap here - see the docstring above)
    parsed = json.loads(raw)
    assert isinstance(parsed["parameters"]["durationSeconds"], int)
    assert not isinstance(parsed["parameters"]["durationSeconds"], str)


def test_an_unsupported_aspect_ratio_is_rejected_before_any_request():
    transport, payloads = capturing_post_transport()
    with pytest.raises(MediaProviderConfigError, match="aspect_ratio"):
        provider(transport).generate_video("a scene", aspect_ratio="1:1")
    assert payloads == []


def test_an_unsupported_resolution_is_rejected_before_any_request():
    transport, payloads = capturing_post_transport()
    with pytest.raises(MediaProviderConfigError, match="resolution"):
        provider(transport).generate_video("a scene", resolution="480p")
    assert payloads == []


@pytest.mark.parametrize("requested,expected", [(4, 4), (6, 6), (8, 8), (5, 4), (7, 6), (100, 8), (None, 8)])
def test_nearest_supported_duration(requested, expected):
    assert nearest_supported_duration(requested) == expected


def test_a_scene_duration_that_veo_does_not_support_is_rounded_and_the_requested_value_is_never_lost():
    transport, payloads = capturing_post_transport()
    video = provider(transport).generate_video("a scene", duration_seconds=5)
    [(_, _, body)] = payloads
    assert body["parameters"]["durationSeconds"] == 4  # nearest supported value actually requested
    assert isinstance(body["parameters"]["durationSeconds"], int)
    assert video.duration_seconds == 4.0  # the value ACTUALLY requested of the provider
    assert video.metadata["requested_duration_seconds"] == 5  # what the scene originally asked for, preserved


def test_from_settings_uses_configured_model_and_gemini_api_key():
    transport, payloads = capturing_post_transport()
    settings = Settings.from_env({"GEMINI_API_KEY": "g-key", "MEDIA_VIDEO_MODEL": "veo-3.1-generate-preview"})
    p = VeoVideoProvider.from_settings(
        settings, transport=transport, get_transport=scripted_get_transport([(200, done_response())])[0]
    )
    assert p.model == "veo-3.1-generate-preview"
    p.generate_video("a scene")
    [(_, headers, _)] = payloads
    assert headers["x-goog-api-key"] == "g-key"


def test_from_settings_falls_back_to_the_default_model_and_poll_settings():
    settings = Settings.from_env({"GEMINI_API_KEY": "g-key"})
    p = VeoVideoProvider.from_settings(settings)
    assert p.model == DEFAULT_VIDEO_MODEL


# --- polling -------------------------------------------------------------------------------------


def test_a_completed_operation_on_the_first_poll_downloads_and_returns_the_video():
    get_transport, get_calls = scripted_get_transport([(200, done_response()), (200, VIDEO_BYTES)])
    video = provider(get_transport=get_transport).generate_video("a scene")
    assert video.data == VIDEO_BYTES and video.mime_type == "video/mp4"
    # poll then download: two GET calls, both with the api key header
    assert len(get_calls) == 2
    assert all(h["x-goog-api-key"] == "k" for _, h in get_calls)
    assert get_calls[0][0] == f"{VEO_BASE_URL}/{OPERATION_NAME}"
    assert get_calls[1][0] == VIDEO_URI


def test_polling_continues_until_done_sleeping_between_attempts():
    sleeps = []
    get_transport, get_calls = scripted_get_transport(
        [(200, not_done_response()), (200, not_done_response()), (200, done_response()), (200, VIDEO_BYTES)]
    )
    p = VeoVideoProvider(
        "k", transport=capturing_post_transport()[0], get_transport=get_transport,
        sleep=sleeps.append, clock=lambda: 0.0, poll_interval=10.0,
    )
    p.generate_video("a scene")
    assert sleeps == [10.0, 10.0]  # slept once per not-done poll, never after "done"
    assert len(get_calls) == 4  # 3 polls (2 not-done + 1 done) + 1 download


def test_polling_times_out_without_completion():
    clock = FakeClock()

    def get_transport(url, headers):
        clock.advance(100.0)  # each poll attempt "takes" 100s of wall-clock time
        return 200, json.dumps(not_done_response()).encode()

    p = VeoVideoProvider(
        "k", transport=capturing_post_transport()[0], get_transport=get_transport,
        sleep=lambda s: None, clock=clock, poll_timeout=250.0, poll_interval=1.0,
    )
    with pytest.raises(MediaProviderTimeoutError, match="did not complete within"):
        p.generate_video("a scene")


def test_an_operation_level_error_is_a_provider_api_error():
    get_transport, _ = scripted_get_transport([(200, {"done": True, "error": {"message": "quota exceeded"}})])
    with pytest.raises(MediaProviderAPIError, match="quota exceeded"):
        provider(get_transport=get_transport).generate_video("a scene")


# --- start: transport / response errors -----------------------------------------------------------


def test_api_error_on_start_carries_status():
    transport, _ = capturing_post_transport({"error": {"message": "boom"}}, status=500)
    with pytest.raises(MediaProviderAPIError) as info:
        provider(transport).generate_video("a scene")
    assert info.value.status == 500


def test_network_error_on_start_is_wrapped():
    def transport(url, headers, data):
        raise TimeoutError("down")

    with pytest.raises(MediaProviderNetworkError):
        provider(transport).generate_video("a scene")


def test_malformed_json_start_response_is_a_response_error():
    def transport(url, headers, data):
        return 200, b"not json"

    with pytest.raises(MediaProviderResponseError, match="not valid JSON"):
        provider(transport).generate_video("a scene")


def test_missing_operation_name_in_start_response_names_the_top_level_keys():
    transport, _ = capturing_post_transport({"something_else": True})
    with pytest.raises(MediaProviderResponseError, match="no operation name") as info:
        provider(transport).generate_video("a scene")
    assert info.value.raw == {"something_else": True}


# --- poll: transport / response errors --------------------------------------------------------------


def test_api_error_while_polling_carries_status():
    get_transport, _ = scripted_get_transport([(500, {"error": {"message": "server error"}})])
    with pytest.raises(MediaProviderAPIError) as info:
        provider(get_transport=get_transport).generate_video("a scene")
    assert info.value.status == 500


def test_network_error_while_polling_is_wrapped():
    def get_transport(url, headers):
        raise TimeoutError("down")

    with pytest.raises(MediaProviderNetworkError):
        provider(get_transport=get_transport).generate_video("a scene")


def test_malformed_json_poll_response_is_a_response_error():
    get_transport, _ = scripted_get_transport([(200, b"not json")])
    with pytest.raises(MediaProviderResponseError, match="not valid JSON"):
        provider(get_transport=get_transport).generate_video("a scene")


@pytest.mark.parametrize("broken_response", [
    {"done": True, "response": "not-an-object"},
    {"done": True, "response": {"generateVideoResponse": "not-an-object"}},
    {"done": True, "response": {"generateVideoResponse": {"generatedSamples": []}}},
    {"done": True, "response": {"generateVideoResponse": {"generatedSamples": [{"no_video_key": True}]}}},
    {"done": True, "response": {"generateVideoResponse": {"generatedSamples": [{"video": {}}]}}},
])
def test_a_completed_operation_missing_the_video_is_a_response_error(broken_response):
    get_transport, _ = scripted_get_transport([(200, broken_response)])
    with pytest.raises(MediaProviderResponseError):
        provider(get_transport=get_transport).generate_video("a scene")


# --- download: transport / response errors ------------------------------------------------------------


def test_download_http_error_is_a_provider_api_error():
    get_transport, _ = scripted_get_transport([(200, done_response()), (403, b"forbidden")])
    with pytest.raises(MediaProviderAPIError) as info:
        provider(get_transport=get_transport).generate_video("a scene")
    assert info.value.status == 403


def test_download_empty_body_is_a_response_error():
    get_transport, _ = scripted_get_transport([(200, done_response()), (200, b"")])
    with pytest.raises(MediaProviderResponseError, match="empty body"):
        provider(get_transport=get_transport).generate_video("a scene")


# --- secrecy --------------------------------------------------------------------------------------


def test_the_api_key_is_never_included_in_error_messages():
    def bad_transport(url, headers, data):
        assert headers["x-goog-api-key"] == "k-secret-123"  # confirms the key IS sent in the request
        return 500, json.dumps({"error": {"message": "rate limited"}}).encode()

    with pytest.raises(MediaProviderAPIError) as info:
        VeoVideoProvider(
            "k-secret-123", transport=bad_transport, sleep=lambda s: None, clock=lambda: 0.0
        ).generate_video("a scene")
    assert "k-secret-123" not in str(info.value)


def test_metadata_never_carries_the_uri_twice_but_does_carry_operation_name():
    video = provider().generate_video("a scene")
    assert video.metadata["operation_name"] == OPERATION_NAME
    assert "uri" not in video.metadata


# --- generation cost: from the published rate card (the completed-operation ------------------------
# response carries no usage/billing data of its own - see the module docstring) --------------------


def test_cost_is_the_published_rate_times_the_actual_duration_used():
    transport, _ = capturing_post_transport()
    video = VeoVideoProvider(
        "k", transport=transport, get_transport=scripted_get_transport([(200, done_response())])[0],
        sleep=lambda s: None, clock=lambda: 0.0, model=DEFAULT_VIDEO_MODEL,
    ).generate_video("a scene", duration_seconds=8, resolution="720p")
    assert video.cost_amount == pytest.approx(0.80, abs=1e-9)  # 8s * $0.10/s (Fast, 720p)
    assert video.cost_currency == "USD"


def test_cost_uses_the_rounded_duration_actually_requested_not_the_scenes_original_ask():
    transport, _ = capturing_post_transport()
    video = provider(transport).generate_video("a scene", duration_seconds=5, resolution="720p")  # rounds to 4
    assert video.cost_amount == pytest.approx(0.40, abs=1e-9)  # 4s * $0.10/s, not 5 * 0.10


def test_cost_reflects_resolution():
    transport, _ = capturing_post_transport()
    video = provider(transport).generate_video("a scene", duration_seconds=8, resolution="1080p")
    assert video.cost_amount == pytest.approx(0.96, abs=1e-9)  # 8s * $0.12/s


def test_cost_is_none_for_a_model_with_no_published_rate_on_file():
    transport, _ = capturing_post_transport()
    video = VeoVideoProvider(
        "k", model="veo-4-hypothetical-future-model", transport=transport,
        get_transport=scripted_get_transport([(200, done_response())])[0],
        sleep=lambda s: None, clock=lambda: 0.0,
    ).generate_video("a scene")
    assert video.cost_amount is None and video.cost_currency is None


def test_the_published_rate_card_has_an_entry_for_every_supported_resolution_on_the_default_model():
    assert set(VIDEO_COST_USD_PER_SECOND[DEFAULT_VIDEO_MODEL]) == {"720p", "1080p", "4k"}
