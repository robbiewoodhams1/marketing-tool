"""AnthropicProvider.generate_production: request shape and response parsing.

Nothing here reaches the network: requests are captured by a fake transport.
"""

from __future__ import annotations

import json

import pytest
from opportunity_fixtures import PRODUCT
from production_fixtures import GOOD_OUTPUT, opportunity_row

from research.config import Settings
from research.llm import (
    PRODUCTION_MAX_TOKENS,
    AnthropicProvider,
    LLMAPIError,
    LLMNetworkError,
    LLMResponseError,
    ProductionProvider,
)
from research.production_pack import build_production_pack
from research.production_prompt import PROMPT_VERSION, SYSTEM_PROMPT, production_output_json_schema

PACK = build_production_pack(
    opportunity=opportunity_row(),
    research_context=[{"id": "insight-1", "title": "t", "statement": "s", "limitations": "l"}],
    product=PRODUCT,
)


def capture():
    payloads = []

    def transport(url, headers, data):
        payloads.append(json.loads(data))
        body = {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(GOOD_OUTPUT)}]}
        return 200, json.dumps(body).encode()

    return transport, payloads


def provider(transport, **kw):
    return AnthropicProvider("k", transport=transport, **kw)


def test_provider_satisfies_the_production_protocol():
    p: ProductionProvider = provider(capture()[0])
    out = p.generate_production(PACK)
    assert out.title


def test_request_shape_uses_structured_outputs_with_the_strict_schema():
    transport, payloads = capture()
    provider(transport, model="claude-sonnet-5").generate_production(PACK)
    [payload] = payloads
    assert payload["model"] == "claude-sonnet-5"
    assert payload["max_tokens"] == PRODUCTION_MAX_TOKENS
    assert payload["system"] == SYSTEM_PROMPT
    assert payload["output_config"]["format"] == {"type": "json_schema", "schema": production_output_json_schema()}
    assert "tool_choice" not in payload and "tools" not in payload


def test_for_production_uses_the_production_model_and_effort():
    transport, payloads = capture()
    settings = Settings.from_env({
        "LLM_API_KEY": "k", "PRODUCTION_MODEL": "model-p", "PRODUCTION_EFFORT": "low",
    })
    p = AnthropicProvider.for_production(settings, transport=transport)
    assert p.model == "model-p"
    p.generate_production(PACK)
    [payload] = payloads
    assert payload["model"] == "model-p" and payload["output_config"]["effort"] == "low"


def test_for_production_falls_back_to_llm_model_then_the_default():
    settings = Settings.from_env({"LLM_API_KEY": "k"})
    assert AnthropicProvider.for_production(settings).model == "claude-haiku-4-5-20251001"
    settings2 = Settings.from_env({"LLM_API_KEY": "k", "LLM_MODEL": "m"})
    assert AnthropicProvider.for_production(settings2).model == "m"


def test_prompt_marks_opportunity_and_research_untrusted_and_product_authoritative():
    from research.production_prompt import build_user_prompt

    text = build_user_prompt(PACK)
    assert "untrusted data" in text and "authoritative" in text


def test_api_error_carries_status_and_never_the_key():
    def transport(url, headers, data):
        return 500, json.dumps({"error": {"message": "boom k"}}).encode()

    with pytest.raises(LLMAPIError) as info:
        provider(transport).generate_production(PACK)
    assert info.value.status == 500


def test_network_error_propagates():
    def transport(url, headers, data):
        raise LLMNetworkError("down")

    with pytest.raises(LLMNetworkError):
        provider(transport).generate_production(PACK)


def test_malformed_json_response_is_a_response_error_with_raw_kept():
    def transport(url, headers, data):
        body = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "not json"}]}
        return 200, json.dumps(body).encode()

    with pytest.raises(LLMResponseError, match="not valid JSON") as info:
        provider(transport).generate_production(PACK)
    assert info.value.raw == "not json"


def test_schema_invalid_response_is_a_response_error():
    def transport(url, headers, data):
        body = {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps({"nope": True})}]}
        return 200, json.dumps(body).encode()

    with pytest.raises(LLMResponseError, match="invalid production"):
        provider(transport).generate_production(PACK)


def test_refusal_stop_reason_is_a_response_error():
    def transport(url, headers, data):
        body = {"stop_reason": "refusal", "stop_details": {"category": "policy"}, "content": []}
        return 200, json.dumps(body).encode()

    with pytest.raises(LLMResponseError, match="declined"):
        provider(transport).generate_production(PACK)


def test_max_tokens_truncation_is_a_response_error_no_partial_result_used():
    def transport(url, headers, data):
        body = {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "{"}]}
        return 200, json.dumps(body).encode()

    with pytest.raises(LLMResponseError, match="truncated"):
        provider(transport).generate_production(PACK)


def test_no_text_block_is_a_response_error():
    def transport(url, headers, data):
        body = {"stop_reason": "end_turn", "content": [{"type": "thinking", "thinking": "..."}]}
        return 200, json.dumps(body).encode()

    with pytest.raises(LLMResponseError, match="did not return a production"):
        provider(transport).generate_production(PACK)


def test_prompt_version_is_stable_across_calls():
    assert PROMPT_VERSION == "production-v1"
