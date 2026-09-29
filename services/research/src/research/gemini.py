"""Google Gemini image generation: the V1 concrete `ImageProvider`.

Uses the Gemini API's Interactions endpoint directly over the standard library
(same approach as `research.llm`'s Anthropic client: no vendor SDK, an
injectable transport so tests never touch the network). No `VideoProvider` is
implemented here - Veo (Google's video model) exists but wiring it up is left
to a later chapter; `research.media_providers.VideoProvider` stands ready for
it or any other vendor.

Verified against the current official Gemini API docs (ai.google.dev) at
implementation time, then against a real successful call:
  * endpoint: POST https://generativelanguage.googleapis.com/v1beta/interactions
  * auth header: x-goog-api-key
  * request: {"model": ..., "input": [{"type": "text", "text": ...}],
              "response_format": {"type": "image", "mime_type": ..., "aspect_ratio": ...}}
  * response (CONFIRMED live, HTTP 200, model gemini-2.5-flash-image, response
    body inspected directly - not guessed from docs): the generated image is
    at the TOP LEVEL, not nested under an "interaction" key:
      steps[] -> {"type": "model_output", "content": [...]} -> content item
      {"type": "image", "data": <base64>, "mime_type": "image/png"}
    Each step in `steps` IS the model-output object directly (`content` is a
    sibling of that step's own `type`); there is no further nested
    "model_output" key inside a step. A preceding text/reasoning step (e.g.
    {"type": "model_output", "content": [{"type": "text", ...}]}) comes
    before the image step, so every step must be searched, never a fixed
    index. The docs' own convenience shape (`interaction.output_image`) and a
    legacy nested-`model_output`-inside-a-step shape were never observed
    live; both are still checked as fallbacks (see `_find_output_image`) in
    case a different model/response mode uses them, but the shape above is
    the one that matters in practice. Also observed: the response's actual
    image `mime_type` was `image/png` even though the request's
    `response_format.mime_type` asked for `image/jpeg` - that request field
    does not appear to control the output encoding for this model.
This is a newer API surface than the older `models.{model}:generateContent`
endpoint; parsing raises a diagnostic-rich error rather than a bare KeyError
if none of the known shapes match, precisely because "current documented
shape" is not the same guarantee as "exercised against the real API".
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Mapping
from typing import Any

from research.config import Settings
from research.llm import Transport, _urllib_transport
from research.logging import get_logger
from research.media_providers import (
    GeneratedImage,
    MediaProviderAPIError,
    MediaProviderConfigError,
    MediaProviderNetworkError,
    MediaProviderResponseError,
)

log = get_logger("research.gemini")

GEMINI_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
DEFAULT_IMAGE_MODEL = "gemini-2.5-flash-image"
DEFAULT_IMAGE_MIME_TYPE = "image/jpeg"

# Values the API documents for response_format.aspect_ratio.
SUPPORTED_ASPECT_RATIOS = frozenset({
    "1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9",
})


class GeminiImageProvider:
    """An `ImageProvider` backed by the Gemini API. Errors become
    `MediaProviderError` subclasses; never raises a bare network/HTTP exception."""

    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_IMAGE_MODEL,
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
    def from_settings(
        cls, settings: Settings, *, transport: Transport | None = None
    ) -> GeminiImageProvider:
        return cls(
            settings.gemini_api_key,
            model=settings.media_image_model or DEFAULT_IMAGE_MODEL,
            transport=transport,
        )

    def generate_image(
        self, prompt: str, *, aspect_ratio: str = "9:16", **options: Any
    ) -> GeneratedImage:
        if aspect_ratio not in SUPPORTED_ASPECT_RATIOS:
            raise MediaProviderConfigError(
                f"aspect_ratio {aspect_ratio!r} is not one of {sorted(SUPPORTED_ASPECT_RATIOS)}"
            )
        payload = {
            "model": self._model,
            "input": [{"type": "text", "text": prompt}],
            "response_format": {
                "type": "image",
                "mime_type": options.pop("mime_type", DEFAULT_IMAGE_MIME_TYPE),
                "aspect_ratio": aspect_ratio,
            },
        }
        return self._parse(self._post(payload))

    def _post(self, payload: dict[str, Any]) -> bytes:
        headers = {"content-type": "application/json", "x-goog-api-key": self._api_key}
        data = json.dumps(payload).encode("utf-8")
        log.info(
            "Gemini image request start: model=%s aspect_ratio=%s timeout=%gs request_bytes=%d",
            payload.get("model"), payload.get("response_format", {}).get("aspect_ratio"),
            self._timeout, len(data),
        )
        started = time.monotonic()
        try:
            status, body = self._transport(GEMINI_INTERACTIONS_URL, headers, data)
        except Exception as exc:
            log.error(
                "Gemini image request failed: elapsed=%.1fs exception=%s message=%s",
                time.monotonic() - started, type(exc).__name__, _redact(str(exc), self._api_key),
            )
            raise MediaProviderNetworkError(f"request failed: {type(exc).__name__}") from exc
        elapsed = time.monotonic() - started
        if status != 200:
            log.warning(
                "Gemini image request returned HTTP %d: elapsed=%.1fs response_bytes=%d",
                status, elapsed, len(body),
            )
            raise MediaProviderAPIError(
                f"Gemini API error (HTTP {status}): {_error_message(body)}", status
            )
        log.info("Gemini image request completed: status=%d elapsed=%.1fs response_bytes=%d",
                 status, elapsed, len(body))
        return body

    @staticmethod
    def _parse(body: bytes) -> GeneratedImage:
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise MediaProviderResponseError(
                "response was not valid JSON", raw=body[:500]
            ) from exc
        if not isinstance(data, Mapping):
            raise MediaProviderResponseError("response was not a JSON object", raw=data)

        image = _find_output_image(data)
        if image is None:
            # Diagnostics only: top-level keys, never the (potentially huge) body.
            raise MediaProviderResponseError(
                f"no image found in response (top-level keys: {sorted(data.keys())})", raw=data
            )
        encoded = image.get("data")
        if not isinstance(encoded, str) or not encoded:
            raise MediaProviderResponseError("image entry had no base64 data", raw=image)
        try:
            raw_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise MediaProviderResponseError("image data was not valid base64", raw=None) from exc
        mime_type = image.get("mime_type") or image.get("mimeType") or DEFAULT_IMAGE_MIME_TYPE
        metadata = {k: v for k, v in image.items() if k not in ("data",)}
        return GeneratedImage(data=raw_bytes, mime_type=mime_type, metadata=metadata)


def _images_from_steps(steps: Any, out: list[Any]) -> None:
    """`steps[] -> {"type": "model_output", "content": [...]} -> {type: "image",
    data, mime_type}` - the CONFIRMED real shape (verified against a live,
    successful response; see the module docstring). Each step IS the
    model-output object directly: `content` is a sibling of the step's own
    `type`, not nested under a further `model_output` key. Every step is
    searched (never assumes the image is at a fixed index - a response may
    have any number of leading text/reasoning steps before it), and a legacy
    nested-`model_output` shape is also checked in case a different response
    mode ever uses it."""
    if not isinstance(steps, list):
        return
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        content = step.get("content")
        if not isinstance(content, list):
            nested = step.get("model_output")  # legacy/fallback shape
            content = nested.get("content") if isinstance(nested, Mapping) else None
        if not isinstance(content, list):
            continue
        for item in content:
            if isinstance(item, Mapping) and item.get("type") == "image":
                out.append(item)


def _find_output_image(data: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Checked in the order real API behaviour has actually shown, most
    likely first:

      1. top-level `steps[]` (confirmed live: a successful response's steps
         are NOT nested under an "interaction" key),
      2. `interaction.steps[]` (kept in case some response nests everything
         under "interaction" after all),
      3. `interaction.output_image` / top-level `output_image` (a
         single-image convenience shape the docs also describe; never
         observed live at implementation time, kept for compatibility).

    Returns the first image-typed candidate with base64 `data`, or None.
    """
    candidates: list[Any] = []
    _images_from_steps(data.get("steps"), candidates)

    interaction = data.get("interaction")
    if isinstance(interaction, Mapping):
        _images_from_steps(interaction.get("steps"), candidates)
        candidates.append(interaction.get("output_image"))
    candidates.append(data.get("output_image"))

    for candidate in candidates:
        if isinstance(candidate, Mapping) and isinstance(candidate.get("data"), str):
            return candidate
    return None


def _redact(text: str, secret: str | None) -> str:
    return text.replace(secret, "***") if secret else text


def _error_message(body: bytes) -> str:
    try:
        parsed = json.loads(body)
        return str(parsed.get("error", {}).get("message", parsed))[:300]
    except (ValueError, AttributeError, TypeError):
        return "unparseable error body"
