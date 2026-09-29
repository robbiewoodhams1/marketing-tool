"""GeminiVoiceProvider: request shape, narration passthrough, WAV handling,
duration measurement, and cost. Nothing here reaches the network: requests
are captured by a fake transport."""

from __future__ import annotations

import base64
import json
import struct

import pytest

from research.config import Settings
from research.gemini_tts import (
    DEFAULT_VOICE_MODEL,
    GEMINI_GENERATE_CONTENT_BASE_URL,
    GeminiVoiceProvider,
)
from research.media_providers import (
    GeneratedAudio,
    MediaProviderAPIError,
    MediaProviderConfigError,
    MediaProviderNetworkError,
    MediaProviderResponseError,
    VoiceProvider,
)


def pcm_bytes(*, seconds: float = 1.0, sample_rate: int = 24000) -> bytes:
    """Headerless 16-bit mono silence - deterministic, no audio library needed."""
    frame_count = int(seconds * sample_rate)
    return b"\x00\x00" * frame_count


def wav_bytes(*, seconds: float = 1.0, sample_rate: int = 24000) -> bytes:
    pcm = pcm_bytes(seconds=seconds, sample_rate=sample_rate)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(pcm), b"WAVE", b"fmt ", 16, 1, 1, sample_rate,
        sample_rate * 2, 2, 16, b"data", len(pcm),
    )
    return header + pcm


def response_with(data_bytes: bytes, *, mime_type: str, usage: dict | None = None) -> dict:
    encoded = base64.b64encode(data_bytes).decode()
    body: dict = {
        "candidates": [{"content": {"parts": [{"inlineData": {"mimeType": mime_type, "data": encoded}}]}}],
    }
    if usage is not None:
        body["usageMetadata"] = usage
    return body


def capture(response: dict | None = None, status: int = 200):
    payloads = []

    def transport(url, headers, data):
        payloads.append((url, headers, json.loads(data)))
        body = json.dumps(response if response is not None else response_with(wav_bytes(), mime_type="audio/wav")).encode()
        return status, body

    return transport, payloads


def provider(transport, **kw):
    return GeminiVoiceProvider("k", transport=transport, **kw)


# --- configuration -----------------------------------------------------------------------------


def test_missing_api_key_is_a_config_error():
    with pytest.raises(MediaProviderConfigError):
        GeminiVoiceProvider(None)
    with pytest.raises(MediaProviderConfigError):
        GeminiVoiceProvider("")


def test_empty_narration_is_a_config_error_before_any_request():
    transport, payloads = capture()
    with pytest.raises(MediaProviderConfigError, match="non-empty narration"):
        provider(transport).generate_voice("   ")
    assert payloads == []


def test_provider_satisfies_the_voice_provider_protocol():
    p: VoiceProvider = provider(capture()[0])
    result = p.generate_voice("Hello there")
    assert isinstance(result, GeneratedAudio)


# --- request shape / narration passthrough -----------------------------------------------------


def test_request_shape_matches_the_documented_generate_content_endpoint():
    transport, payloads = capture()
    provider(transport, model="gemini-2.5-flash-preview-tts").generate_voice("Build your first template today.", voice="Kore")
    [(url, headers, body)] = payloads
    assert url == f"{GEMINI_GENERATE_CONTENT_BASE_URL}/gemini-2.5-flash-preview-tts:generateContent"
    assert headers["x-goog-api-key"] == "k"
    assert body["contents"] == [{"role": "user", "parts": [{"text": "Build your first template today."}]}]
    assert body["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert body["generationConfig"]["speechConfig"]["voiceConfig"]["voice"] == "Kore"


def test_narration_text_is_passed_through_byte_for_byte_never_altered():
    transport, payloads = capture()
    narration = "Pick a job you do often, build it once, and let TradeFlow do the repeating for you."
    provider(transport).generate_voice(narration)
    [(_, _, body)] = payloads
    assert body["contents"][0]["parts"][0]["text"] == narration


def test_narration_with_special_characters_and_whitespace_survives_unchanged():
    transport, payloads = capture()
    narration = "Quotes, jobs & invoices — all in one place.\nSave time today!"
    provider(transport).generate_voice(narration)
    [(_, _, body)] = payloads
    assert body["contents"][0]["parts"][0]["text"] == narration


def test_default_model_and_voice():
    assert GeminiVoiceProvider("k").model == DEFAULT_VOICE_MODEL
    transport, payloads = capture()
    provider(transport).generate_voice("hello")
    [(url, _, body)] = payloads
    assert url == f"{GEMINI_GENERATE_CONTENT_BASE_URL}/{DEFAULT_VOICE_MODEL}:generateContent"
    assert body["generationConfig"]["speechConfig"]["voiceConfig"]["voice"] == "Kore"


def test_a_different_voice_is_requested_verbatim():
    transport, payloads = capture()
    provider(transport).generate_voice("hello", voice="Puck")
    [(_, _, body)] = payloads
    assert body["generationConfig"]["speechConfig"]["voiceConfig"]["voice"] == "Puck"


def test_speed_and_language_are_accepted_but_never_sent_since_no_real_mechanism_is_documented():
    # See the module docstring: no verified speed/language parameter exists
    # for single-speaker requests, so nothing is guessed onto the request.
    transport, payloads = capture()
    provider(transport).generate_voice("hello", language="fr-FR", speed=1.5)
    [(_, _, body)] = payloads
    assert "speed" not in json.dumps(body).lower()
    assert "fr-FR" not in json.dumps(body)


# --- response parsing: WAV already present -------------------------------------------------------


def test_a_response_already_wrapped_in_wav_is_stored_as_is():
    audio_bytes = wav_bytes(seconds=2.0, sample_rate=24000)
    transport, _ = capture(response_with(audio_bytes, mime_type="audio/wav"))
    result = provider(transport).generate_voice("hello")
    assert result.data == audio_bytes
    assert result.mime_type == "audio/wav"
    assert result.duration_seconds == pytest.approx(2.0, abs=1e-6)


# --- response parsing: headerless PCM must be wrapped ---------------------------------------------


def test_headerless_pcm_is_wrapped_in_a_wav_header():
    raw_pcm = pcm_bytes(seconds=1.5, sample_rate=24000)
    transport, _ = capture(response_with(raw_pcm, mime_type="audio/L16;rate=24000"))
    result = provider(transport).generate_voice("hello")
    assert result.data[:4] == b"RIFF" and result.data[8:12] == b"WAVE"
    assert result.mime_type == "audio/wav"
    assert result.duration_seconds == pytest.approx(1.5, abs=1e-6)
    assert result.metadata["sample_rate"] == 24000


def test_headerless_pcm_with_no_rate_in_the_mime_type_falls_back_to_the_documented_default():
    raw_pcm = pcm_bytes(seconds=1.0, sample_rate=24000)
    transport, _ = capture(response_with(raw_pcm, mime_type="audio/L16"))  # no rate= parameter
    result = provider(transport).generate_voice("hello")
    assert result.metadata["sample_rate"] == 24000
    assert result.duration_seconds == pytest.approx(1.0, abs=1e-6)


def test_headerless_pcm_at_a_non_default_rate_is_measured_correctly():
    raw_pcm = pcm_bytes(seconds=3.0, sample_rate=16000)
    transport, _ = capture(response_with(raw_pcm, mime_type="audio/L16;rate=16000"))
    result = provider(transport).generate_voice("hello")
    assert result.metadata["sample_rate"] == 16000
    assert result.duration_seconds == pytest.approx(3.0, abs=1e-6)


# --- duration is measured, never assumed or trusted from a claimed field --------------------------


def test_duration_reflects_the_real_audio_length_not_a_fixed_value():
    short = provider(capture(response_with(pcm_bytes(seconds=0.5), mime_type="audio/L16;rate=24000"))[0]).generate_voice("hi")
    long = provider(capture(response_with(pcm_bytes(seconds=5.0), mime_type="audio/L16;rate=24000"))[0]).generate_voice(
        "a much longer piece of narration text, spoken for longer"
    )
    assert short.duration_seconds == pytest.approx(0.5, abs=1e-6)
    assert long.duration_seconds == pytest.approx(5.0, abs=1e-6)


# --- cost: computed from the response's own usage, never invented --------------------------------


def test_cost_is_computed_from_real_usage_tokens():
    usage = {"promptTokenCount": 20, "candidatesTokenCount": 250}  # 250 audio tokens @ 25/s = 10s of audio
    transport, _ = capture(response_with(wav_bytes(), mime_type="audio/wav", usage=usage))
    result = provider(transport, model="gemini-2.5-flash-preview-tts").generate_voice("hello")
    # (20/1e6)*0.50 + (250/1e6)*10.00 = 0.00001 + 0.0025 = 0.00251
    assert result.cost_amount == pytest.approx(0.00251, abs=1e-9)
    assert result.cost_currency == "USD"


def test_cost_is_none_when_the_response_has_no_usage_block():
    transport, _ = capture(response_with(wav_bytes(), mime_type="audio/wav"))  # no usageMetadata
    result = provider(transport).generate_voice("hello")
    assert result.cost_amount is None and result.cost_currency is None


def test_cost_is_none_for_a_model_with_no_published_rate_on_file():
    usage = {"promptTokenCount": 20, "candidatesTokenCount": 250}
    transport, _ = capture(response_with(wav_bytes(), mime_type="audio/wav", usage=usage))
    result = provider(transport, model="gemini-4-hypothetical-future-tts").generate_voice("hello")
    assert result.cost_amount is None and result.cost_currency is None


def test_cost_uses_only_the_rates_available_when_only_one_usage_field_is_present():
    usage = {"promptTokenCount": 20}  # no candidatesTokenCount at all
    transport, _ = capture(response_with(wav_bytes(), mime_type="audio/wav", usage=usage))
    result = provider(transport, model="gemini-2.5-flash-preview-tts").generate_voice("hello")
    assert result.cost_amount == pytest.approx(0.00001, abs=1e-9)


# --- errors ---------------------------------------------------------------------------------------


def test_api_error_carries_status():
    def transport(url, headers, data):
        return 500, json.dumps({"error": {"message": "boom"}}).encode()

    with pytest.raises(MediaProviderAPIError) as info:
        provider(transport).generate_voice("hello")
    assert info.value.status == 500


def test_network_error_is_wrapped():
    def transport(url, headers, data):
        raise TimeoutError("down")

    with pytest.raises(MediaProviderNetworkError):
        provider(transport).generate_voice("hello")


def test_malformed_json_response_is_a_response_error():
    def transport(url, headers, data):
        return 200, b"not json"

    with pytest.raises(MediaProviderResponseError, match="not valid JSON"):
        provider(transport).generate_voice("hello")


def test_missing_audio_in_response_names_the_top_level_keys():
    transport, _ = capture({"something_else": True})
    with pytest.raises(MediaProviderResponseError, match="no audio found") as info:
        provider(transport).generate_voice("hello")
    assert info.value.raw == {"something_else": True}


def test_invalid_base64_is_a_response_error():
    transport, _ = capture({"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/wav", "data": "not-base64!!"}}]}}]})
    with pytest.raises(MediaProviderResponseError, match="not valid base64"):
        provider(transport).generate_voice("hello")


def test_a_wav_missing_its_data_chunk_is_a_response_error():
    truncated = wav_bytes()[:20]  # RIFF/WAVE/fmt header only, no data chunk
    transport, _ = capture(response_with(truncated, mime_type="audio/wav"))
    with pytest.raises(MediaProviderResponseError):
        provider(transport).generate_voice("hello")


def test_the_api_key_is_never_included_in_error_messages():
    def bad_transport(url, headers, data):
        assert headers["x-goog-api-key"] == "k-secret-123"
        return 500, json.dumps({"error": {"message": "rate limited"}}).encode()

    with pytest.raises(MediaProviderAPIError) as info:
        GeminiVoiceProvider("k-secret-123", transport=bad_transport).generate_voice("hello")
    assert "k-secret-123" not in str(info.value)


# --- from_settings -----------------------------------------------------------------------------


def test_from_settings_uses_the_configured_model_and_gemini_api_key():
    transport, payloads = capture()
    settings = Settings.from_env({"GEMINI_API_KEY": "g-key", "MEDIA_VOICE_MODEL": "gemini-3.8-flash-tts"})
    p = GeminiVoiceProvider.from_settings(settings, transport=transport)
    assert p.model == "gemini-3.8-flash-tts"
    p.generate_voice("hello")
    [(url, headers, _)] = payloads
    assert headers["x-goog-api-key"] == "g-key"
    assert url.endswith("gemini-3.8-flash-tts:generateContent")


def test_from_settings_falls_back_to_the_default_model():
    settings = Settings.from_env({"GEMINI_API_KEY": "g-key"})
    assert GeminiVoiceProvider.from_settings(settings).model == DEFAULT_VOICE_MODEL


def test_metadata_records_voice_and_language_and_never_the_base64_blob():
    transport, _ = capture()
    result = provider(transport).generate_voice("hello", voice="Puck", language="fr-FR")
    assert result.metadata["voice"] == "Puck" and result.metadata["language"] == "fr-FR"
    assert "data" not in result.metadata
