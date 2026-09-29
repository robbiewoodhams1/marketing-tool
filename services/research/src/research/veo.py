"""Google Veo video generation: the V2 concrete `VideoProvider`.

Uses the Gemini API's Veo endpoints directly over the standard library (same
approach as `research.gemini.GeminiImageProvider`: no vendor SDK, injectable
transports so tests never touch the network or sleep for real).

Initially verified against the official Gemini API docs
(ai.google.dev/gemini-api/docs/veo), via independent fetches including two
literal curl examples for the start and poll calls. One of those docs
examples was WRONG in a way that only a live call caught (see below):

  * start:    POST https://generativelanguage.googleapis.com/v1beta/models/{model}:predictLongRunning
              body: {"instances": [{"prompt": <text>}],
                     "parameters": {"aspectRatio": "16:9"|"9:16" (default "16:9"),
                                    "durationSeconds": 4|6|8 (a JSON NUMBER, not a string - see below),
                                    "resolution": "720p"|"1080p"|"4k" (default "720p")}}
              response: {"name": "<operation name, e.g. models/veo-.../operations/...>", ...}
  * poll:     GET  https://generativelanguage.googleapis.com/v1beta/{operation name}
              response, while running: {"done": false, ...} (or "done" absent)
              response, once done:     {"done": true,
                                         "response": {"generateVideoResponse":
                                           {"generatedSamples": [{"video": {"uri": "<url>"}}]}}}
              or, on failure:          {"done": true, "error": {"message": ...}}
  * download: GET <video.uri> with the SAME x-goog-api-key header (the curl
              example follows redirects, `-L`; Python's urlopen does this by
              default) -> raw video bytes.
  * auth header: x-goog-api-key (same as GeminiImageProvider).

CORRECTED after a real live call (2026-09-29): the docs' curl example showed
`"durationSeconds": "8"` as a quoted string, and the request was originally
built that way. The live API rejected it with HTTP 400: "The value type for
durationSeconds needs to be a number." `durationSeconds` must be sent as a
plain JSON number (see `generate_video`'s payload construction) - this is the
second time a docs example has been wrong in a way only a real call revealed
(the first was the image endpoint's response shape - see `research.gemini`'s
docstring), so the lesson holds: verify the ACTUAL request/response over the
wire, not just the docs, before trusting a shape.

Response parsing has NOT been exercised against a real completed operation
(the smoke test failed at the request-validation stage, before an operation
was ever created - no credit was spent). It remains deliberately defensive:
on any missing field it raises `MediaProviderResponseError` naming exactly
which key was missing and what top-level keys WERE present, so a first real
completed response that reveals a different shape can be diagnosed and fixed
without repeated paid calls.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from research.config import Settings
from research.llm import Transport, _urllib_transport
from research.logging import get_logger
from research.media_providers import (
    GeneratedVideo,
    MediaProviderAPIError,
    MediaProviderConfigError,
    MediaProviderError,
    MediaProviderNetworkError,
    MediaProviderResponseError,
)

log = get_logger("research.veo")

VEO_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_VIDEO_MODEL = "veo-3.1-fast-generate-preview"  # the cheaper/quicker Veo 3.1 tier
DEFAULT_RESOLUTION = "720p"
DEFAULT_ASPECT_RATIO = "9:16"

# Documented values only (ai.google.dev/gemini-api/docs/veo); Veo does not
# accept an arbitrary integer duration.
SUPPORTED_DURATIONS_SECONDS = (4, 6, 8)
DEFAULT_DURATION_SECONDS = 8

SUPPORTED_ASPECT_RATIOS = frozenset({"16:9", "9:16"})
SUPPORTED_RESOLUTIONS = frozenset({"720p", "1080p", "4k"})

# USD per second, by model and resolution (Gemini API pricing, fetched and
# cross-checked against two independent sources 2026-09-29:
# ai.google.dev/gemini-api/docs/pricing and a second, unaffiliated summary).
# The completed-operation response carries NO usage/billing data of any kind
# (confirmed from real captured operations - see the module docstring), so
# this is the only honest, non-invented basis available: Google's own
# published rate, applied to the ACTUAL requested resolution/duration (never
# the scene's original ask if it had to be rounded - see
# `nearest_supported_duration`). A model/resolution pair not listed here
# (e.g. a future model) has no entry and therefore no cost is ever guessed
# for it (see `_cost_for`).
VIDEO_COST_USD_PER_SECOND: dict[str, dict[str, float]] = {
    "veo-3.1-fast-generate-preview": {"720p": 0.10, "1080p": 0.12, "4k": 0.30},
    "veo-3.1-generate-preview": {"720p": 0.40, "1080p": 0.40, "4k": 0.60},
}

DEFAULT_POLL_INTERVAL = 10.0  # seconds; matches the docs' own recommended cadence
DEFAULT_POLL_TIMEOUT = 600.0  # seconds; comfortably above the documented "max 6 minutes at peak"

# (url, headers) -> (status, body); GET only. Distinct from research.llm's
# POST-only `Transport`, used here for polling and downloading.
GetTransport = Callable[[str, Mapping[str, str]], tuple[int, bytes]]

Sleep = Callable[[float], None]
Clock = Callable[[], float]


class MediaProviderTimeoutError(MediaProviderError):
    """Polling exceeded the configured timeout without the operation completing."""


def nearest_supported_duration(requested: float | int | None) -> int:
    """The closest value Veo actually accepts to what the scene asked for.
    Never invents an exact match - the caller must record the value actually
    used (see `research.media_schema.GeneratedAsset.duration_seconds`)."""
    if requested is None:
        return DEFAULT_DURATION_SECONDS
    return min(SUPPORTED_DURATIONS_SECONDS, key=lambda d: abs(d - requested))


def _cost_for(model: str, resolution: str, duration_seconds: int) -> tuple[float | None, str | None]:
    """`(None, None)` if this model/resolution has no published rate on file
    - never a guessed figure (see `VIDEO_COST_USD_PER_SECOND`)."""
    rate = VIDEO_COST_USD_PER_SECOND.get(model, {}).get(resolution)
    if rate is None:
        return None, None
    return round(rate * duration_seconds, 6), "USD"


def _urllib_get_transport(timeout: float) -> GetTransport:
    def send(url: str, headers: Mapping[str, str]) -> tuple[int, bytes]:
        req = urllib.request.Request(url, headers=dict(headers), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # follows redirects by default
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.read()
            except Exception as read_exc:
                raise MediaProviderNetworkError(f"request failed: {type(read_exc).__name__}") from read_exc
        except Exception as exc:
            raise MediaProviderNetworkError(f"request failed: {type(exc).__name__}") from exc

    return send


class VeoVideoProvider:
    """A `VideoProvider` backed by Google Veo (via the Gemini API). Errors
    become `MediaProviderError` subclasses; never raises a bare
    network/HTTP/JSON exception."""

    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_VIDEO_MODEL,
        timeout: float = 60.0,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        poll_timeout: float = DEFAULT_POLL_TIMEOUT,
        transport: Transport | None = None,
        get_transport: GetTransport | None = None,
        sleep: Sleep = time.sleep,
        clock: Clock = time.monotonic,
    ):
        if not api_key:
            raise MediaProviderConfigError("GEMINI_API_KEY is not set")
        self._api_key = api_key
        self._model = model
        self.model = model
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout
        self._transport = transport or _urllib_transport(timeout)
        self._get_transport = get_transport or _urllib_get_transport(timeout)
        self._sleep = sleep
        self._clock = clock

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        transport: Transport | None = None,
        get_transport: GetTransport | None = None,
        sleep: Sleep | None = None,
        clock: Clock | None = None,
    ) -> VeoVideoProvider:
        kwargs: dict[str, Any] = dict(
            model=settings.media_video_model or DEFAULT_VIDEO_MODEL,
            poll_interval=settings.media_video_poll_interval or DEFAULT_POLL_INTERVAL,
            poll_timeout=settings.media_video_poll_timeout or DEFAULT_POLL_TIMEOUT,
            transport=transport,
            get_transport=get_transport,
        )
        if sleep is not None:
            kwargs["sleep"] = sleep
        if clock is not None:
            kwargs["clock"] = clock
        return cls(settings.gemini_api_key, **kwargs)

    def generate_video(
        self, prompt: str, *, aspect_ratio: str = DEFAULT_ASPECT_RATIO, **options: Any
    ) -> GeneratedVideo:
        if aspect_ratio not in SUPPORTED_ASPECT_RATIOS:
            raise MediaProviderConfigError(
                f"aspect_ratio {aspect_ratio!r} is not one of {sorted(SUPPORTED_ASPECT_RATIOS)}"
            )
        resolution = options.pop("resolution", DEFAULT_RESOLUTION)
        if resolution not in SUPPORTED_RESOLUTIONS:
            raise MediaProviderConfigError(
                f"resolution {resolution!r} is not one of {sorted(SUPPORTED_RESOLUTIONS)}"
            )
        requested_duration = options.pop("duration_seconds", None)
        duration = nearest_supported_duration(requested_duration)

        payload = {
            "instances": [{"prompt": prompt}],
            "parameters": {
                "aspectRatio": aspect_ratio,
                "durationSeconds": duration,  # a JSON number - a live smoke test confirmed the API
                # rejects this as a string with HTTP 400 ("The value type for durationSeconds needs
                # to be a number"), contradicting the quoted-string curl example in the docs at
                # implementation time. Never cast to str() here again without re-verifying live.
                "resolution": resolution,
            },
        }
        operation_name = self._start(payload)
        operation = self._poll(operation_name)
        video = self._extract_video(operation)
        data = self._download(video["uri"])
        metadata = {k: v for k, v in video.items() if k != "uri"}
        metadata["operation_name"] = operation_name
        metadata["requested_duration_seconds"] = requested_duration
        cost_amount, cost_currency = _cost_for(self._model, resolution, duration)
        return GeneratedVideo(
            data=data, mime_type="video/mp4", duration_seconds=float(duration), metadata=metadata,
            cost_amount=cost_amount, cost_currency=cost_currency,
        )

    # --- start ---------------------------------------------------------------------

    def _start(self, payload: dict[str, Any]) -> str:
        url = f"{VEO_BASE_URL}/models/{self._model}:predictLongRunning"
        headers = {"content-type": "application/json", "x-goog-api-key": self._api_key}
        data = json.dumps(payload).encode("utf-8")
        log.info(
            "Veo video request start: model=%s aspect_ratio=%s duration=%s resolution=%s",
            self._model, payload["parameters"]["aspectRatio"],
            payload["parameters"]["durationSeconds"], payload["parameters"]["resolution"],
        )
        try:
            status, body = self._transport(url, headers, data)
        except Exception as exc:
            log.error("Veo video start request failed: %s", type(exc).__name__)
            raise MediaProviderNetworkError(f"request failed: {type(exc).__name__}") from exc
        if status != 200:
            log.warning("Veo video start request returned HTTP %d", status)
            raise MediaProviderAPIError(f"Veo API error (HTTP {status}): {_error_message(body)}", status)
        parsed = _parse_json(body, where="start response")
        name = parsed.get("name") if isinstance(parsed, Mapping) else None
        if not isinstance(name, str) or not name:
            raise MediaProviderResponseError(
                f"no operation name in start response (top-level keys: "
                f"{sorted(parsed.keys()) if isinstance(parsed, Mapping) else type(parsed).__name__})",
                raw=parsed,
            )
        log.info("Veo video operation started: %s", name)
        return name

    # --- poll ------------------------------------------------------------------------

    def _poll(self, operation_name: str) -> Mapping[str, Any]:
        url = f"{VEO_BASE_URL}/{operation_name}"
        headers = {"x-goog-api-key": self._api_key}
        started = self._clock()
        attempt = 0
        while True:
            attempt += 1
            try:
                status, body = self._get_transport(url, headers)
            except Exception as exc:
                raise MediaProviderNetworkError(f"poll request failed: {type(exc).__name__}") from exc
            if status != 200:
                raise MediaProviderAPIError(f"Veo API error polling (HTTP {status}): {_error_message(body)}", status)
            operation = _parse_json(body, where="poll response")
            if not isinstance(operation, Mapping):
                raise MediaProviderResponseError("poll response was not a JSON object", raw=operation)

            error = operation.get("error")
            if error:
                message = error.get("message") if isinstance(error, Mapping) else str(error)
                raise MediaProviderAPIError(f"Veo operation failed: {message}")

            if operation.get("done"):
                log.info("Veo video operation completed after %d poll(s)", attempt)
                return operation

            elapsed = self._clock() - started
            if elapsed >= self._poll_timeout:
                raise MediaProviderTimeoutError(
                    f"Veo operation {operation_name} did not complete within {self._poll_timeout:g}s "
                    f"({attempt} poll(s))"
                )
            self._sleep(self._poll_interval)

    # --- extract / download ------------------------------------------------------------

    @staticmethod
    def _extract_video(operation: Mapping[str, Any]) -> Mapping[str, Any]:
        response = operation.get("response")
        if not isinstance(response, Mapping):
            raise MediaProviderResponseError(
                f"completed operation had no 'response' object (top-level keys: {sorted(operation.keys())})",
                raw=operation,
            )
        generate = response.get("generateVideoResponse")
        if not isinstance(generate, Mapping):
            raise MediaProviderResponseError(
                f"no 'generateVideoResponse' in operation.response (keys: {sorted(response.keys())})",
                raw=response,
            )
        samples = generate.get("generatedSamples")
        if not isinstance(samples, list) or not samples:
            raise MediaProviderResponseError(
                f"no generatedSamples in generateVideoResponse (keys: {sorted(generate.keys())})",
                raw=generate,
            )
        video = samples[0].get("video") if isinstance(samples[0], Mapping) else None
        if not isinstance(video, Mapping) or not isinstance(video.get("uri"), str) or not video["uri"]:
            raise MediaProviderResponseError(
                f"first generated sample had no usable video.uri (sample keys: "
                f"{sorted(samples[0].keys()) if isinstance(samples[0], Mapping) else type(samples[0]).__name__})",
                raw=samples[0],
            )
        return video

    def _download(self, uri: str) -> bytes:
        headers = {"x-goog-api-key": self._api_key}
        try:
            status, body = self._get_transport(uri, headers)
        except Exception as exc:
            raise MediaProviderNetworkError(f"video download failed: {type(exc).__name__}") from exc
        if status != 200:
            raise MediaProviderAPIError(f"video download failed (HTTP {status}): {_error_message(body)}", status)
        if not body:
            raise MediaProviderResponseError("video download returned an empty body")
        return body


def _parse_json(body: bytes, *, where: str) -> Any:
    try:
        return json.loads(body)
    except ValueError as exc:
        raise MediaProviderResponseError(f"{where} was not valid JSON", raw=body[:500]) from exc


def _error_message(body: bytes) -> str:
    try:
        parsed = json.loads(body)
        return str(parsed.get("error", {}).get("message", parsed))[:300]
    except (ValueError, AttributeError, TypeError):
        return "unparseable error body"
