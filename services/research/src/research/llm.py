"""LLM provider abstraction and the Anthropic implementation.

`LLMProvider` is the only thing the research engine depends on. The Anthropic
provider uses the Messages API over the standard library (like the YouTube
client) with an injectable transport, so tests never touch the network. It has
no database, YouTube or orchestration knowledge and does not retry.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from research.classification import (
    ClassificationInput,
    ClassificationResult,
    ClassificationSchemaError,
)
from research.classification_prompt import (
    SYSTEM_PROMPT,
    build_user_prompt,
    output_json_schema,
)
from research.config import Settings
from research.logging import get_logger

log = get_logger("research.llm")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
TOOL_NAME = "record_classification"

# (url, headers, body) -> (http_status, response_body). Must raise
# LLMNetworkError if the request could not be completed.
Transport = Callable[[str, Mapping[str, str], bytes], tuple[int, bytes]]


class LLMError(Exception):
    """Base class for all LLM provider failures."""


class LLMConfigError(LLMError):
    """The provider is not configured (e.g. no API key). Not a model/API failure."""


class LLMNetworkError(LLMError):
    """The request could not reach the API (DNS, timeout, connection...)."""


class LLMAPIError(LLMError):
    """The API returned an HTTP error."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class LLMResponseError(LLMError):
    """The model answered, but not with a valid classification."""


class LLMProvider(Protocol):
    def classify_content(self, item: ClassificationInput) -> ClassificationResult:
        """Classify one video. Raises an `LLMError` subclass on failure."""
        ...


def _urllib_transport(timeout: float) -> Transport:
    def send(url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, bytes]:
        req = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMNetworkError(f"request failed: {type(exc).__name__}") from exc

    return send


class AnthropicProvider:
    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 1024,
        timeout: float = 60.0,
        transport: Transport | None = None,
    ):
        if not api_key:
            raise LLMConfigError("LLM_API_KEY is not set")
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._transport = transport or _urllib_transport(timeout)

    @classmethod
    def from_settings(
        cls, settings: Settings, *, transport: Transport | None = None
    ) -> AnthropicProvider:
        return cls(
            settings.llm_api_key,
            model=settings.llm_model or DEFAULT_MODEL,
            transport=transport,
        )

    def classify_content(self, item: ClassificationInput) -> ClassificationResult:
        payload = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": build_user_prompt(item)}],
            "tools": [
                {
                    "name": TOOL_NAME,
                    "description": "Record the structured classification.",
                    "input_schema": output_json_schema(),
                }
            ],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
        }
        headers = {
            "content-type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
        status, body = self._transport(
            ANTHROPIC_URL, headers, json.dumps(payload).encode("utf-8")
        )
        if status != 200:
            # Only the API's error message is surfaced; never headers or the key.
            raise LLMAPIError(f"Anthropic API error (HTTP {status}): {_error_message(body)}", status)
        return self._parse(body)

    @staticmethod
    def _parse(body: bytes) -> ClassificationResult:
        try:
            data = json.loads(body)
            blocks = data["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMResponseError("response was not valid Messages API JSON") from exc
        for block in blocks if isinstance(blocks, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == TOOL_NAME:
                try:
                    return ClassificationResult.from_dict(block.get("input"))
                except ClassificationSchemaError as exc:
                    raise LLMResponseError(f"invalid classification: {exc}") from exc
        raise LLMResponseError("model did not return a classification")


def _error_message(body: bytes) -> str:
    try:
        return str(json.loads(body)["error"]["message"])[:300]
    except (ValueError, KeyError, TypeError):
        return "unparseable error body"
