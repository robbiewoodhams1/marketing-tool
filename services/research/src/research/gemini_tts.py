"""Google Gemini text-to-speech: the V1 concrete `VoiceProvider`.

Deliberately uses the STABLE, longer-established `generateContent` endpoint
(`models/{model}:generateContent`) rather than the newer "Interactions"
endpoint `research.gemini`/`research.veo` use for images/video: Google's own
docs explicitly recommend `generateContent` for stable production
deployments, and it is also the older, far more extensively documented
surface. That matters here specifically because this project has already
been burned TWICE by the newer surface's docs being wrong in ways only a
real call revealed (the image endpoint's response shape, and Veo's
`durationSeconds` needing a JSON number rather than the docs' quoted
string) - so for a genuinely production-quality choice, the more
conservative, better-established surface was picked on purpose.

UNVERIFIED AGAINST A LIVE CALL at implementation time (per instructions: no
live calls during this chapter; the user will run one deliberate smoke test
themselves afterwards). Verified only via documentation
(ai.google.dev/gemini-api/docs/generate-content/speech-generation and
ai.google.dev/gemini-api/docs/pricing), cross-checked across several
independent fetches on 2026-09-29 - including one genuine discrepancy found
and resolved between them: single-speaker requests nest the voice as
`speechConfig.voiceConfig.voice`, NOT `voiceConfig.prebuiltVoiceConfig.voiceName`
(that longer form is real, but is for MULTI-speaker requests, which this
module does not use - V1 is single-voice narration only).

  * endpoint: POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
  * auth header: x-goog-api-key (same as research.gemini / research.veo)
  * request: {"contents": [{"role": "user", "parts": [{"text": <narration, UNCHANGED>}]}],
              "generationConfig": {"responseModalities": ["AUDIO"],
                                    "speechConfig": {"voiceConfig": {"voice": <prebuilt voice name>}}}}
  * response: {"candidates": [{"content": {"parts": [{"inlineData":
                {"mimeType": <e.g. "audio/L16;rate=24000" or "audio/wav">, "data": <base64>}}]}}],
               "usageMetadata": {"promptTokenCount": ..., "candidatesTokenCount": ...}}

Audio encoding is another point of genuine documentation disagreement across
fetches: some describe the default unary response as a complete WAV file
(RIFF header, 24kHz mono 16-bit PCM); others describe headerless raw PCM,
distinguished only by the mimeType's own `rate=` parameter. This module
handles BOTH without guessing which is real: if the returned bytes already
carry a RIFF/WAVE header, they are stored as returned; otherwise they are
treated as headerless 16-bit little-endian mono PCM and wrapped in a minimal
WAV header before storage (`_ensure_wav`), so the dashboard's `<audio>`
element can always play the result either way. Duration is measured
directly from the actual decoded PCM sample count and the real sample rate
(`_parse_wav`) - never trusted from a provider-reported field (none is
documented for this endpoint) and never assumed from input text length.

`speed`/non-default `language` are accepted for interface completeness (the
`VoiceProvider` contract asks for them) but are NOT sent to the API: no
numeric speed or language-code parameter is documented for single-speaker
requests at implementation time. The only documented delivery-style lever
is `speech_metadata.style` on the input text itself, which this module does
not use, because attaching a style descriptor not present in Production's
own words would be a small act of narration-editorialising - exactly what
`research.voice`'s module docstring says this layer must never do. If a
real need for speed/style control arises, it belongs in a future chapter,
built against the real, live-verified mechanism - never guessed here.
"""

from __future__ import annotations

import base64
import json
import re
import struct
import time
from collections.abc import Mapping
from typing import Any

from research.config import Settings
from research.llm import Transport, _urllib_transport
from research.logging import get_logger
from research.media_providers import (
    GeneratedAudio,
    MediaProviderAPIError,
    MediaProviderConfigError,
    MediaProviderNetworkError,
    MediaProviderResponseError,
)

log = get_logger("research.gemini_tts")

GEMINI_GENERATE_CONTENT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_VOICE_MODEL = "gemini-2.5-flash-preview-tts"
DEFAULT_VOICE = "Kore"
DEFAULT_LANGUAGE = "en-US"
DEFAULT_SAMPLE_RATE = 24000  # Hz; the documented default for Gemini TTS PCM output

# USD per 1,000,000 tokens (Gemini API pricing, fetched and cross-checked
# 2026-09-29; ai.google.dev/gemini-api/docs/pricing). TEXT input and AUDIO
# output are billed at different, documented rates; both are read from the
# response's own usage.promptTokenCount / usage.candidatesTokenCount and
# priced separately, then summed - never a flat per-second/per-clip guess.
# A model not listed here has no entry and therefore no cost is ever guessed
# for it (see `_cost_from_usage`).
TEXT_INPUT_RATE_USD_PER_MILLION: dict[str, float] = {
    "gemini-2.5-flash-preview-tts": 0.50,
    "gemini-3.8-flash-tts": 0.50,
    "gemini-3.8-flash-lite-tts": 0.50,
}
AUDIO_OUTPUT_RATE_USD_PER_MILLION: dict[str, float] = {
    "gemini-2.5-flash-preview-tts": 10.00,
    "gemini-3.8-flash-tts": 9.00,
    "gemini-3.8-flash-lite-tts": 6.00,
}

# Documented prebuilt voice names (ai.google.dev/gemini-api/docs/speech-generation,
# fetched 2026-09-29). Advisory only, not a hard allowlist (unlike
# aspect_ratio/resolution in research.gemini/research.veo, which are real,
# closed, well-documented sets): Google may add voices this list doesn't yet
# know about, and an unrecognised name is passed through as-is rather than
# rejected, since rejecting it would risk blocking a real, valid voice this
# module simply hasn't been told about.
KNOWN_VOICES = frozenset({
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe", "Autonoe",
    "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi",
    "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
})


class GeminiVoiceProvider:
    """A `VoiceProvider` backed by the Gemini API. Errors become
    `MediaProviderError` subclasses; never raises a bare network/HTTP/JSON/
    struct exception."""

    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_VOICE_MODEL,
        timeout: float = 120.0,
        transport: Transport | None = None,
    ):
        if not api_key:
            raise MediaProviderConfigError("GEMINI_API_KEY is not set")
        self._api_key = api_key
        self._model = model
        self.model = model
        self._timeout = timeout
        self._transport = transport or _urllib_transport(timeout)

    @classmethod
    def from_settings(cls, settings: Settings, *, transport: Transport | None = None) -> GeminiVoiceProvider:
        return cls(
            settings.gemini_api_key, model=settings.media_voice_model or DEFAULT_VOICE_MODEL, transport=transport,
        )

    def generate_voice(
        self, text: str, *, voice: str = DEFAULT_VOICE, language: str = DEFAULT_LANGUAGE,
        speed: float | None = None, **options: Any,
    ) -> GeneratedAudio:
        if not text or not text.strip():
            raise MediaProviderConfigError("generate_voice requires non-empty narration text")
        payload = {
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"voice": voice}},
            },
        }
        url = f"{GEMINI_GENERATE_CONTENT_BASE_URL}/{self._model}:generateContent"
        return self._parse(self._post(url, payload, voice=voice), voice=voice, language=language)

    def _post(self, url: str, payload: dict[str, Any], *, voice: str) -> bytes:
        headers = {"content-type": "application/json", "x-goog-api-key": self._api_key}
        data = json.dumps(payload).encode("utf-8")
        log.info(
            "Gemini voice request start: model=%s voice=%s timeout=%gs request_bytes=%d",
            self._model, voice, self._timeout, len(data),
        )
        started = time.monotonic()
        try:
            status, body = self._transport(url, headers, data)
        except Exception as exc:
            log.error(
                "Gemini voice request failed: elapsed=%.1fs exception=%s",
                time.monotonic() - started, type(exc).__name__,
            )
            raise MediaProviderNetworkError(f"request failed: {type(exc).__name__}") from exc
        elapsed = time.monotonic() - started
        if status != 200:
            log.warning("Gemini voice request returned HTTP %d: elapsed=%.1fs", status, elapsed)
            raise MediaProviderAPIError(f"Gemini API error (HTTP {status}): {_error_message(body)}", status)
        log.info(
            "Gemini voice request completed: status=%d elapsed=%.1fs response_bytes=%d", status, elapsed, len(body)
        )
        return body

    def _parse(self, body: bytes, *, voice: str, language: str) -> GeneratedAudio:
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise MediaProviderResponseError("response was not valid JSON", raw=body[:500]) from exc
        if not isinstance(data, Mapping):
            raise MediaProviderResponseError("response was not a JSON object", raw=data)

        inline = _find_inline_audio(data)
        if inline is None:
            raise MediaProviderResponseError(
                f"no audio found in response (top-level keys: {sorted(data.keys())})", raw=data
            )
        encoded = inline.get("data")
        if not isinstance(encoded, str) or not encoded:
            raise MediaProviderResponseError("audio entry had no base64 data", raw=inline)
        try:
            raw_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise MediaProviderResponseError("audio data was not valid base64", raw=None) from exc

        reported_mime = inline.get("mimeType") or inline.get("mime_type") or ""
        wav_bytes, sample_rate = _ensure_wav(raw_bytes, reported_mime)
        duration = _pcm_duration_seconds(wav_bytes)

        cost_amount, cost_currency = _cost_from_usage(data, self._model)
        metadata = {
            "voice": voice, "language": language, "sample_rate": sample_rate, "reported_mime_type": reported_mime,
        }
        return GeneratedAudio(
            data=wav_bytes, mime_type="audio/wav", duration_seconds=duration, metadata=metadata,
            cost_amount=cost_amount, cost_currency=cost_currency,
        )


def _find_inline_audio(data: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """`candidates[] -> content.parts[] -> inlineData` - the standard
    generateContent response envelope (shared across every Gemini modality,
    not TTS-specific), so this part of the shape is trusted with much more
    confidence than the TTS-specific request fields above."""
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        return None
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, Mapping) else None
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, Mapping):
                continue
            inline = part.get("inlineData")
            if not isinstance(inline, Mapping):
                inline = part.get("inline_data")  # snake_case fallback
            if isinstance(inline, Mapping) and isinstance(inline.get("data"), str):
                return inline
    return None


_RIFF_MAGIC = b"RIFF"
_WAVE_MAGIC = b"WAVE"
_RATE_RE = re.compile(r"rate=(\d+)")


def _sample_rate_from_mime(mime_type: str) -> int | None:
    match = _RATE_RE.search(mime_type or "")
    return int(match.group(1)) if match else None


def _ensure_wav(raw_bytes: bytes, reported_mime: str) -> tuple[bytes, int]:
    """Returns `(wav_bytes, sample_rate)`. If `raw_bytes` already carries a
    RIFF/WAVE header, it is used as-is (its own sample rate is read from
    that header); otherwise it is treated as headerless 16-bit
    little-endian mono PCM and wrapped in a minimal WAV header, at the
    sample rate named in `reported_mime` (e.g. "audio/L16;rate=24000") or
    `DEFAULT_SAMPLE_RATE` if none is present."""
    if len(raw_bytes) >= 12 and raw_bytes[:4] == _RIFF_MAGIC and raw_bytes[8:12] == _WAVE_MAGIC:
        sample_rate, _channels, _bits, _size = _parse_wav_chunks(raw_bytes)
        return raw_bytes, sample_rate
    sample_rate = _sample_rate_from_mime(reported_mime) or DEFAULT_SAMPLE_RATE
    return _wrap_pcm_as_wav(raw_bytes, sample_rate=sample_rate), sample_rate


def _wrap_pcm_as_wav(pcm_bytes: bytes, *, sample_rate: int, channels: int = 1, bits_per_sample: int = 16) -> bytes:
    """A minimal, standard 44-byte WAV/RIFF header around headerless PCM -
    no third-party audio dependency needed for something this small and
    this well-specified (see e.g. the canonical WAV format reference)."""
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(pcm_bytes)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE", b"fmt ", 16, 1, channels, sample_rate,
        byte_rate, block_align, bits_per_sample, b"data", data_size,
    )
    return header + pcm_bytes


def _parse_wav_chunks(wav_bytes: bytes) -> tuple[int, int, int, int]:
    """Returns `(sample_rate, channels, bits_per_sample, data_size)` by
    walking the RIFF chunks properly - never assumes a fixed 44-byte header,
    since a WAV file the PROVIDER returns directly (as opposed to one this
    module wrapped itself) may include extra chunks before 'fmt '/'data'."""
    if len(wav_bytes) < 12 or wav_bytes[:4] != _RIFF_MAGIC or wav_bytes[8:12] != _WAVE_MAGIC:
        raise MediaProviderResponseError("not a valid RIFF/WAVE file")
    pos = 12
    fmt: tuple[int, int, int] | None = None
    data_size: int | None = None
    while pos + 8 <= len(wav_bytes):
        chunk_id = wav_bytes[pos:pos + 4]
        try:
            chunk_size = struct.unpack_from("<I", wav_bytes, pos + 4)[0]
        except struct.error as exc:
            raise MediaProviderResponseError("truncated WAV chunk header") from exc
        chunk_start = pos + 8
        if chunk_id == b"fmt " and chunk_start + 16 <= len(wav_bytes):
            _audio_format, channels, sample_rate = struct.unpack_from("<HHI", wav_bytes, chunk_start)
            bits_per_sample = struct.unpack_from("<H", wav_bytes, chunk_start + 14)[0]
            fmt = (sample_rate, channels, bits_per_sample)
        elif chunk_id == b"data":
            data_size = chunk_size
        pos = chunk_start + chunk_size + (chunk_size % 2)  # chunks are word-aligned
        if fmt is not None and data_size is not None:
            break
    if fmt is None or data_size is None:
        raise MediaProviderResponseError("WAV file was missing a 'fmt ' or 'data' chunk")
    sample_rate, channels, bits_per_sample = fmt
    return sample_rate, channels, bits_per_sample, data_size


def _pcm_duration_seconds(wav_bytes: bytes) -> float:
    """Measured directly from the WAV file's own header + data size - never
    trusted from a provider-reported field (none is documented for this
    endpoint) and never assumed from the input text length."""
    sample_rate, channels, bits_per_sample, data_size = _parse_wav_chunks(wav_bytes)
    if sample_rate <= 0:
        raise MediaProviderResponseError("WAV header reported a non-positive sample rate")
    bytes_per_frame = max(channels * (bits_per_sample // 8), 1)
    return data_size / bytes_per_frame / sample_rate


def _cost_from_usage(data: Mapping[str, Any], model: str) -> tuple[float | None, str | None]:
    usage = data.get("usageMetadata")
    if not isinstance(usage, Mapping):
        return None, None
    input_rate = TEXT_INPUT_RATE_USD_PER_MILLION.get(model)
    output_rate = AUDIO_OUTPUT_RATE_USD_PER_MILLION.get(model)
    if input_rate is None and output_rate is None:
        return None, None
    prompt_tokens = usage.get("promptTokenCount")
    output_tokens = usage.get("candidatesTokenCount")
    cost = 0.0
    counted = False
    if input_rate is not None and isinstance(prompt_tokens, (int, float)):
        cost += (prompt_tokens / 1_000_000) * input_rate
        counted = True
    if output_rate is not None and isinstance(output_tokens, (int, float)):
        cost += (output_tokens / 1_000_000) * output_rate
        counted = True
    if not counted:
        return None, None
    return round(cost, 6), "USD"


def _error_message(body: bytes) -> str:
    try:
        parsed = json.loads(body)
        return str(parsed.get("error", {}).get("message", parsed))[:300]
    except (ValueError, AttributeError, TypeError):
        return "unparseable error body"
