"""LLM provider abstraction and the Anthropic implementation.

`LLMProvider` is the only thing the research engine depends on. The Anthropic
provider uses the Messages API over the standard library (like the YouTube
client) with an injectable transport, so tests never touch the network. It has
no database, YouTube or orchestration knowledge and does not retry.
"""

from __future__ import annotations

import http.client
import json
import logging
import ssl
import time
import traceback
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any, Protocol

from research.classification import (
    ClassificationInput,
    ClassificationResult,
    ClassificationSchemaError,
)
from research.classification_prompt import (
    SYSTEM_PROMPT,
    build_user_prompt,
    strict_output_json_schema,
)
from research.config import Settings
from research.evidence_pack import EvidencePack
from research.logging import get_logger
from research.opportunity import OpportunityCreationOutput, OpportunitySchemaError
from research.opportunity_pack import OpportunityInputPack
from research.opportunity_prompt import (
    SYSTEM_PROMPT as OPPORTUNITY_SYSTEM_PROMPT,
)
from research.opportunity_prompt import (
    build_user_prompt as build_opportunity_user_prompt,
)
from research.opportunity_prompt import (
    opportunity_output_json_schema,
)
from research.production_pack import ProductionInputPack
from research.production_prompt import (
    SYSTEM_PROMPT as PRODUCTION_SYSTEM_PROMPT,
)
from research.production_prompt import (
    build_user_prompt as build_production_user_prompt,
)
from research.production_prompt import (
    production_output_json_schema,
)
from research.production_schema import ProductionOutput, ProductionSchemaError
from research.synthesis import SynthesisOutput, SynthesisSchemaError
from research.synthesis_prompt import (
    SYSTEM_PROMPT as SYNTHESIS_SYSTEM_PROMPT,
)
from research.synthesis_prompt import (
    build_user_prompt as build_synthesis_user_prompt,
)
from research.synthesis_prompt import (
    synthesis_output_json_schema,
)

log = get_logger("research.llm")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
TOOL_NAME = "record_classification"
# A synthesis (several insights, each with observations and quotes) is far larger
# than a classification, and on models whose thinking is always on the thinking
# tokens share this budget. Hitting the limit is a failure, never a partial result.
SYNTHESIS_MAX_TOKENS = 16000
# An opportunity-creation run reads far fewer, already-condensed insights, but
# still needs room for several structured opportunities plus thinking tokens.
OPPORTUNITY_MAX_TOKENS = 8000
# A production is one script (hook + a handful of short scenes + cta/caption),
# smaller than an opportunity-creation run's several opportunities.
PRODUCTION_MAX_TOKENS = 8000

# Values Anthropic accepts for `output_config.effort` (adaptive-thinking depth).
# Which of them a given model accepts varies (e.g. Haiku 4.5 rejects `effort`
# entirely; Sonnet 4.6 has no `xhigh`); that is the API's answer to give, so an
# unsupported combination surfaces as its HTTP 400, not as a local model table.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


def parse_effort(value: str | None, *, source: str = "SYNTHESIS_EFFORT") -> str | None:
    """None/blank means "not set" (send nothing). Otherwise one of EFFORT_LEVELS
    (case and surrounding whitespace ignored), else LLMConfigError."""
    if value is None or not value.strip():
        return None
    level = value.strip().lower()
    if level not in EFFORT_LEVELS:
        raise LLMConfigError(
            f"{source} must be one of {', '.join(EFFORT_LEVELS)} (got {value.strip()!r})"
        )
    return level


# (url, headers, body) -> (http_status, response_body). Must raise
# LLMNetworkError if the request could not be completed.
Transport = Callable[[str, Mapping[str, str], bytes], tuple[int, bytes]]


class LLMError(Exception):
    """Base class for all LLM provider failures."""


class LLMConfigError(LLMError):
    """The provider is not configured (e.g. no API key). Not a model/API failure."""


class LLMNetworkError(LLMError):
    """The request could not reach the API (DNS, timeout, connection...).

    `phase` and `elapsed` are diagnostics only (see `_urllib_transport`); they
    never change the message or when this is raised.
    """

    def __init__(self, message: str, *, phase: str | None = None, elapsed: float | None = None):
        super().__init__(message)
        self.phase = phase
        self.elapsed = elapsed


class LLMAPIError(LLMError):
    """The API returned an HTTP error."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class LLMResponseError(LLMError):
    """The model answered, but not with a valid classification.

    `raw` is what the model actually returned (the tool input if there was one,
    otherwise a truncated copy of the response body), kept for development
    diagnostics only. It is deliberately not part of the message, so it never
    reaches logs; it holds video-derived text, never credentials.
    """

    def __init__(self, message: str, raw: Any = None, stop_reason: str | None = None):
        super().__init__(message)
        self.raw = raw
        self.stop_reason = stop_reason


class LLMProvider(Protocol):
    model: str  # the model used for requests; stored with each interpretation

    def classify_content(self, item: ClassificationInput) -> ClassificationResult:
        """Classify one video. Raises an `LLMError` subclass on failure."""
        ...


class SynthesisProvider(Protocol):
    model: str  # stored with each synthesis run

    def synthesize_research(self, pack: EvidencePack) -> SynthesisOutput:
        """Synthesise one evidence pack. Raises an `LLMError` subclass on failure.

        Returns a schema-valid output; whether it is supported by the evidence is
        checked by `research.synthesis_validation`.
        """
        ...


class OpportunityProvider(Protocol):
    model: str  # stored with each opportunity-creation run

    def generate_opportunities(self, pack: OpportunityInputPack) -> OpportunityCreationOutput:
        """Create opportunities from one input pack. Raises an `LLMError` subclass on failure.

        Returns a schema-valid output; whether it is supported by the supplied
        research and product context is checked by `research.opportunity_validation`.
        """
        ...


class ProductionProvider(Protocol):
    model: str  # stored with each production run

    def generate_production(self, pack: ProductionInputPack) -> ProductionOutput:
        """Produce a short-form video package from one input pack. Raises an
        `LLMError` subclass on failure.

        Returns a schema-valid output; whether it is supported by the supplied
        opportunity and product context is checked by `research.production_validation`.
        """
        ...


# Where in the request a network failure happened. What urllib lets us tell apart
# reliably, and no more:
#   * connecting: DNS, TCP connect, TLS (and a proxy tunnel). Detected by a
#     connection subclass noting when `connect()` returns.
#   * sending / waiting for headers: after the connection is up until urllib has
#     the response status line. Sending and waiting are NOT separable (a request
#     body is written and then the client blocks for the reply), so they are one
#     phase. For a non-streaming request this is where a long generation is spent.
#   * reading body / reading error body: after the headers, inside `read()`.
# Caveat: `timeout` is a per-socket-operation timeout, not a deadline for the
# whole request. Each phase can therefore take up to `timeout` on its own, and
# the total can exceed it.
PHASE_CONNECTING = "connecting (DNS, TCP, TLS)"
PHASE_WAITING = "sending request / waiting for response headers"
PHASE_BODY = "reading response body"
PHASE_ERROR_BODY = "reading error response body"

_NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, OSError)


class _Marks:
    connected_at: float | None = None


def _marked(conn_cls: type[http.client.HTTPConnection], marks: _Marks) -> type:
    class Marked(conn_cls):  # type: ignore[misc, valid-type]
        def connect(self) -> None:
            super().connect()
            marks.connected_at = time.monotonic()

    return Marked


class _MarkedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, marks: _Marks):
        super().__init__()
        self._marks = marks

    def http_open(self, req: urllib.request.Request) -> Any:
        return self.do_open(_marked(http.client.HTTPConnection, self._marks), req)


class _MarkedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, marks: _Marks):
        self._ssl_context = ssl.create_default_context()  # what urlopen uses by default
        super().__init__(context=self._ssl_context)
        self._marks = marks

    def https_open(self, req: urllib.request.Request) -> Any:
        return self.do_open(
            _marked(http.client.HTTPSConnection, self._marks), req, context=self._ssl_context
        )


def _urllib_transport(timeout: float) -> Transport:
    """One POST over urllib (same handlers, proxies and TLS defaults as `urlopen`).

    The only additions are timing and phase bookkeeping for diagnostics; the
    return value and the exceptions raised (`LLMNetworkError`) are unchanged.
    """

    def send(url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, bytes]:
        marks = _Marks()
        opener = urllib.request.build_opener(_MarkedHTTPHandler(marks), _MarkedHTTPSHandler(marks))
        req = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
        started = time.monotonic()

        def failed(exc: BaseException, phase: str) -> LLMNetworkError:
            return LLMNetworkError(
                f"request failed: {type(exc).__name__}",
                phase=phase,
                elapsed=time.monotonic() - started,
            )

        try:
            resp = opener.open(req, timeout=timeout)  # returns once the headers are in
        except urllib.error.HTTPError as exc:
            try:
                error_body = exc.read()
            except _NETWORK_ERRORS as read_exc:
                raise failed(read_exc, PHASE_ERROR_BODY) from read_exc
            return exc.code, error_body
        except _NETWORK_ERRORS as exc:
            phase = PHASE_CONNECTING if marks.connected_at is None else PHASE_WAITING
            raise failed(exc, phase) from exc

        headers_at = time.monotonic()
        try:
            with resp:
                data = resp.read()
        except _NETWORK_ERRORS as exc:
            raise failed(exc, PHASE_BODY) from exc
        done = time.monotonic()
        connected = marks.connected_at
        log.debug(
            "Anthropic transport timings: connect=%s headers_after=%.2fs body_read=%.2fs",
            f"{connected - started:.2f}s" if connected is not None else "n/a",
            headers_at - started,
            done - headers_at,
        )
        return resp.status, data

    return send


def _redact(text: str, secret: str | None) -> str:
    return text.replace(secret, "***") if secret else text


def _cause_chain(exc: BaseException, secret: str | None = None) -> list[str]:
    """`Type: message` for the exception and each cause, key redacted.

    Follows `__cause__`, and for urllib's `URLError` its `.reason`, which is where
    urllib keeps the original OSError (e.g. ConnectionRefusedError).
    """
    chain: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(_redact(f"{type(current).__name__}: {current}", secret))
        reason = getattr(current, "reason", None)
        current = current.__cause__ or (reason if isinstance(reason, BaseException) else None)
    return chain


def _usage_summary(body: bytes) -> str:
    """stop_reason and token counts from a Messages response. Numbers and one
    enum-like string only: never any content."""
    try:
        data = json.loads(body)
    except ValueError:
        return "usage=unavailable"
    if not isinstance(data, dict):
        return "usage=unavailable"
    parts = []
    stop_reason = data.get("stop_reason")
    if isinstance(stop_reason, str):
        parts.append(f"stop_reason={stop_reason}")
    usage = data.get("usage")
    if isinstance(usage, dict):
        for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            if isinstance(usage.get(key), int) and not isinstance(usage.get(key), bool):
                parts.append(f"{key}={usage[key]}")
    return " ".join(parts) or "usage=unavailable"


class AnthropicProvider:
    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 1024,
        timeout: float = 60.0,
        transport: Transport | None = None,
        effort: str | None = None,
    ):
        if not api_key:
            raise LLMConfigError("LLM_API_KEY is not set")
        self._effort = parse_effort(effort, source="effort")
        self._api_key = api_key
        self._model = model
        self.model = model
        self._timeout = timeout
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

    @classmethod
    def for_synthesis(
        cls, settings: Settings, *, transport: Transport | None = None, timeout: float = 180.0
    ) -> AnthropicProvider:
        """Synthesis reads a much larger prompt: longer timeout, optional own model."""
        return cls(
            settings.llm_api_key,
            model=settings.synthesis_model or settings.llm_model or DEFAULT_MODEL,
            timeout=timeout,
            transport=transport,
            effort=parse_effort(settings.synthesis_effort),
        )

    @classmethod
    def for_opportunities(
        cls, settings: Settings, *, transport: Transport | None = None, timeout: float = 120.0
    ) -> AnthropicProvider:
        """Opportunity creation reads a small, already-condensed prompt, but still
        benefits from its own model/effort override, independent of synthesis."""
        return cls(
            settings.llm_api_key,
            model=settings.opportunity_model or settings.llm_model or DEFAULT_MODEL,
            timeout=timeout,
            transport=transport,
            effort=parse_effort(settings.opportunity_effort, source="OPPORTUNITY_EFFORT"),
        )

    @classmethod
    def for_production(
        cls, settings: Settings, *, transport: Transport | None = None, timeout: float = 120.0
    ) -> AnthropicProvider:
        """Production reads a small, already-condensed prompt (one opportunity, one
        product profile), but still benefits from its own model/effort override,
        independent of synthesis and opportunity creation."""
        return cls(
            settings.llm_api_key,
            model=settings.production_model or settings.llm_model or DEFAULT_MODEL,
            timeout=timeout,
            transport=transport,
            effort=parse_effort(settings.production_effort, source="PRODUCTION_EFFORT"),
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
                    "input_schema": strict_output_json_schema(),
                    # Strict tool use: the API constrains the tool input to this schema.
                    "strict": True,
                }
            ],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
        }
        return self._parse(self._post(payload, purpose="classification"))

    def _post(self, payload: dict[str, Any], *, purpose: str = "request") -> bytes:
        headers = {
            "content-type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
        data = json.dumps(payload).encode("utf-8")
        # Diagnostics never include the key, headers, prompts or bodies: only
        # sizes, timings, the model name and token counts.
        level = logging.INFO if purpose in ("synthesis", "opportunity_creation", "production") else logging.DEBUG
        log.log(
            level,
            "Anthropic %s request start: started=%s model=%s timeout=%gs streaming=off "
            "request_bytes=%d max_tokens=%s effort=%s",
            purpose,
            datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            payload.get("model"),
            self._timeout,
            len(data),
            payload.get("max_tokens"),
            payload.get("output_config", {}).get("effort", "default"),
        )
        started = time.monotonic()
        try:
            status, body = self._transport(ANTHROPIC_URL, headers, data)
        except Exception as exc:  # logged, then re-raised unchanged
            self._log_failure(purpose, exc, time.monotonic() - started)
            raise
        elapsed = time.monotonic() - started
        if status != 200:
            log.warning(
                "Anthropic %s request returned HTTP %d: elapsed=%.1fs timeout=%gs response_bytes=%d",
                purpose, status, elapsed, self._timeout, len(body),
            )
            # Only the API's error message is surfaced; never headers or the key.
            raise LLMAPIError(f"Anthropic API error (HTTP {status}): {_error_message(body)}", status)
        log.log(
            level,
            "Anthropic %s request completed: status=%d elapsed=%.1fs timeout=%gs response_bytes=%d %s",
            purpose, status, elapsed, self._timeout, len(body), _usage_summary(body),
        )
        return body

    def _log_failure(self, purpose: str, exc: BaseException, elapsed: float) -> None:
        log.error(
            "Anthropic %s request failed: elapsed=%.1fs timeout=%gs exception=%s message=%s "
            "phase=%s cause_chain=[%s]",
            purpose, elapsed, self._timeout, type(exc).__name__,
            _redact(str(exc), self._api_key),
            getattr(exc, "phase", None) or "unknown",
            " <- ".join(_cause_chain(exc, self._api_key)),
        )
        # Formatted here (not passed as exc_info) so the key is redacted from it too.
        log.debug(
            "Anthropic %s request failure traceback:\n%s",
            purpose,
            _redact("".join(traceback.format_exception(exc)), self._api_key),
        )

    def synthesize_research(self, pack: EvidencePack) -> SynthesisOutput:
        # Structured outputs (`output_config.format`), not a forced tool call:
        # some models (e.g. Claude Fable 5.1, Mythos 5.1, Opus 5.5) reject
        # `tool_choice` of type "tool"/"any" with HTTP 400, whereas structured
        # outputs are supported by every current model. The schema is the same
        # strict Synthesis v1 schema, and the API constrains the reply to it, so
        # this is the same contract by a mechanism that does not depend on the
        # model. There is deliberately no per-model branching.
        payload = {
            "model": self._model,
            "max_tokens": SYNTHESIS_MAX_TOKENS,
            "system": SYNTHESIS_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": build_synthesis_user_prompt(pack)}],
            "output_config": {
                "format": {"type": "json_schema", "schema": synthesis_output_json_schema()}
            },
        }
        if self._effort is not None:  # unset: nothing is sent, the model's default applies
            payload["output_config"]["effort"] = self._effort
        return self._parse_synthesis(self._post(payload, purpose="synthesis"))

    @staticmethod
    def _parse_synthesis(body: bytes) -> SynthesisOutput:
        try:
            data = json.loads(body)
            blocks = data["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMResponseError(
                "response was not valid Messages API JSON", raw=body[:2000].decode("utf-8", "replace")
            ) from exc
        stop_reason = data.get("stop_reason") if isinstance(data, dict) else None
        if stop_reason == "refusal":
            # The output schema is only guaranteed when the model answers.
            details = data.get("stop_details") if isinstance(data, dict) else None
            category = details.get("category") if isinstance(details, dict) else None
            raise LLMResponseError(
                f"the model declined the request (category: {category or 'unspecified'})",
                raw=blocks, stop_reason=stop_reason,
            )
        if stop_reason == "max_tokens":
            raise LLMResponseError(
                "synthesis was truncated (max_tokens reached); no partial result is used",
                raw=blocks, stop_reason=stop_reason,
            )
        # Models that think first return thinking blocks before the answer; the
        # answer is the (first) text block, which structured outputs make valid JSON.
        text = next(
            (
                block["text"]
                for block in (blocks if isinstance(blocks, list) else [])
                if isinstance(block, dict) and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ),
            None,
        )
        if text is None:
            raise LLMResponseError(
                "model did not return a synthesis", raw=blocks, stop_reason=stop_reason
            )
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            raise LLMResponseError(
                "synthesis was not valid JSON", raw=text[:2000], stop_reason=stop_reason
            ) from exc
        try:
            return SynthesisOutput.from_dict(parsed)
        except SynthesisSchemaError as exc:
            raise LLMResponseError(
                f"invalid synthesis: {exc}", raw=parsed, stop_reason=stop_reason
            ) from exc

    def generate_opportunities(self, pack: OpportunityInputPack) -> OpportunityCreationOutput:
        # Same structured-outputs approach as synthesis (see the comment there):
        # no per-model tool-choice branching, the API enforces the schema.
        payload = {
            "model": self._model,
            "max_tokens": OPPORTUNITY_MAX_TOKENS,
            "system": OPPORTUNITY_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": build_opportunity_user_prompt(pack)}],
            "output_config": {
                "format": {"type": "json_schema", "schema": opportunity_output_json_schema()}
            },
        }
        if self._effort is not None:  # unset: nothing is sent, the model's default applies
            payload["output_config"]["effort"] = self._effort
        return self._parse_opportunities(self._post(payload, purpose="opportunity_creation"))

    @staticmethod
    def _parse_opportunities(body: bytes) -> OpportunityCreationOutput:
        try:
            data = json.loads(body)
            blocks = data["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMResponseError(
                "response was not valid Messages API JSON", raw=body[:2000].decode("utf-8", "replace")
            ) from exc
        stop_reason = data.get("stop_reason") if isinstance(data, dict) else None
        if stop_reason == "refusal":
            details = data.get("stop_details") if isinstance(data, dict) else None
            category = details.get("category") if isinstance(details, dict) else None
            raise LLMResponseError(
                f"the model declined the request (category: {category or 'unspecified'})",
                raw=blocks, stop_reason=stop_reason,
            )
        if stop_reason == "max_tokens":
            raise LLMResponseError(
                "opportunity creation was truncated (max_tokens reached); no partial result is used",
                raw=blocks, stop_reason=stop_reason,
            )
        text = next(
            (
                block["text"]
                for block in (blocks if isinstance(blocks, list) else [])
                if isinstance(block, dict) and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ),
            None,
        )
        if text is None:
            raise LLMResponseError(
                "model did not return an opportunity result", raw=blocks, stop_reason=stop_reason
            )
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            raise LLMResponseError(
                "opportunity result was not valid JSON", raw=text[:2000], stop_reason=stop_reason
            ) from exc
        try:
            return OpportunityCreationOutput.from_dict(parsed)
        except OpportunitySchemaError as exc:
            raise LLMResponseError(
                f"invalid opportunity result: {exc}", raw=parsed, stop_reason=stop_reason
            ) from exc

    def generate_production(self, pack: ProductionInputPack) -> ProductionOutput:
        # Same structured-outputs approach as synthesis/opportunities (see the
        # comment on synthesize_research): no per-model tool-choice branching,
        # the API enforces the schema.
        payload = {
            "model": self._model,
            "max_tokens": PRODUCTION_MAX_TOKENS,
            "system": PRODUCTION_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": build_production_user_prompt(pack)}],
            "output_config": {
                "format": {"type": "json_schema", "schema": production_output_json_schema()}
            },
        }
        if self._effort is not None:  # unset: nothing is sent, the model's default applies
            payload["output_config"]["effort"] = self._effort
        return self._parse_production(self._post(payload, purpose="production"))

    @staticmethod
    def _parse_production(body: bytes) -> ProductionOutput:
        try:
            data = json.loads(body)
            blocks = data["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMResponseError(
                "response was not valid Messages API JSON", raw=body[:2000].decode("utf-8", "replace")
            ) from exc
        stop_reason = data.get("stop_reason") if isinstance(data, dict) else None
        if stop_reason == "refusal":
            details = data.get("stop_details") if isinstance(data, dict) else None
            category = details.get("category") if isinstance(details, dict) else None
            raise LLMResponseError(
                f"the model declined the request (category: {category or 'unspecified'})",
                raw=blocks, stop_reason=stop_reason,
            )
        if stop_reason == "max_tokens":
            raise LLMResponseError(
                "production was truncated (max_tokens reached); no partial result is used",
                raw=blocks, stop_reason=stop_reason,
            )
        text = next(
            (
                block["text"]
                for block in (blocks if isinstance(blocks, list) else [])
                if isinstance(block, dict) and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ),
            None,
        )
        if text is None:
            raise LLMResponseError(
                "model did not return a production", raw=blocks, stop_reason=stop_reason
            )
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            raise LLMResponseError(
                "production was not valid JSON", raw=text[:2000], stop_reason=stop_reason
            ) from exc
        try:
            return ProductionOutput.from_dict(parsed)
        except ProductionSchemaError as exc:
            raise LLMResponseError(
                f"invalid production: {exc}", raw=parsed, stop_reason=stop_reason
            ) from exc

    @staticmethod
    def _parse(body: bytes) -> ClassificationResult:
        try:
            data = json.loads(body)
            blocks = data["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMResponseError(
                "response was not valid Messages API JSON", raw=body[:2000].decode("utf-8", "replace")
            ) from exc
        stop_reason = data.get("stop_reason") if isinstance(data, dict) else None
        for block in blocks if isinstance(blocks, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == TOOL_NAME:
                try:
                    return ClassificationResult.from_dict(_expand_null_fields(block.get("input")))
                except ClassificationSchemaError as exc:
                    raise LLMResponseError(
                        f"invalid classification: {exc}", raw=block.get("input"), stop_reason=stop_reason
                    ) from exc
        raise LLMResponseError(
            "model did not return a classification", raw=blocks, stop_reason=stop_reason
        )


def _expand_null_fields(data: Any) -> Any:
    """Turn a whole-field `null` (the strict schema's "insufficient evidence") into
    the FieldResult shape. This is the ONLY normalisation: bare strings, missing
    fields and everything else are left for ClassificationResult to reject.
    """
    if not isinstance(data, dict):
        return data
    empty = {"value": None, "confidence": None, "evidence": None}
    return {k: (dict(empty) if v is None else v) for k, v in data.items()}


def _error_message(body: bytes) -> str:
    try:
        return str(json.loads(body)["error"]["message"])[:300]
    except (ValueError, KeyError, TypeError):
        return "unparseable error body"
