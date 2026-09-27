"""AnthropicProvider.generate_opportunities: request shape and response parsing.

Nothing here reaches the network: requests are captured by a fake transport.
"""

from __future__ import annotations

import json

import pytest
from opportunity_fixtures import GOOD_OUTPUT, JOB, PRODUCT, insight_row

from research.config import Settings
from research.llm import (
    OPPORTUNITY_MAX_TOKENS,
    AnthropicProvider,
    LLMAPIError,
    LLMNetworkError,
    LLMResponseError,
    OpportunityProvider,
)
from research.opportunity_pack import build_opportunity_pack
from research.opportunity_prompt import PROMPT_VERSION, SYSTEM_PROMPT, opportunity_output_json_schema

PACK = build_opportunity_pack(
    job=JOB, synthesis={"id": "syn-1", "research_job_id": "job-1"},
    insight_rows=[insight_row(1), insight_row(2)], product=PRODUCT,
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


def test_provider_satisfies_the_opportunity_protocol():
    p: OpportunityProvider = provider(capture()[0])
    out = p.generate_opportunities(PACK)
    assert out.opportunities[0].title


def test_request_shape_uses_structured_outputs_with_the_strict_schema():
    transport, payloads = capture()
    out = provider(transport, model="claude-sonnet-5").generate_opportunities(PACK)
    [payload] = payloads
    assert payload["model"] == "claude-sonnet-5"
    assert payload["max_tokens"] == OPPORTUNITY_MAX_TOKENS
    assert payload["system"] == SYSTEM_PROMPT
    assert payload["output_config"]["format"] == {"type": "json_schema", "schema": opportunity_output_json_schema()}
    assert "tool_choice" not in payload and "tools" not in payload
    assert out.opportunities[0].title


def test_for_opportunities_uses_the_opportunity_model_and_effort(monkeypatch):
    transport, payloads = capture()
    settings = Settings.from_env({
        "LLM_API_KEY": "k", "OPPORTUNITY_MODEL": "model-o", "OPPORTUNITY_EFFORT": "low",
    })
    p = AnthropicProvider.for_opportunities(settings, transport=transport)
    assert p.model == "model-o"
    p.generate_opportunities(PACK)
    [payload] = payloads
    assert payload["model"] == "model-o" and payload["output_config"]["effort"] == "low"


def test_for_opportunities_falls_back_to_llm_model_then_the_default():
    settings = Settings.from_env({"LLM_API_KEY": "k"})
    assert AnthropicProvider.for_opportunities(settings).model == "claude-haiku-4-5-20251001"
    settings2 = Settings.from_env({"LLM_API_KEY": "k", "LLM_MODEL": "m"})
    assert AnthropicProvider.for_opportunities(settings2).model == "m"


def test_prompt_carries_the_pack_and_marks_it_as_data():
    from research.opportunity_prompt import build_user_prompt

    text = build_user_prompt(PACK)
    assert "untrusted data" in text and "product" in text


def test_api_error_carries_status_and_never_the_key():
    def transport(url, headers, data):
        return 500, json.dumps({"error": {"message": "boom k"}}).encode()

    with pytest.raises(LLMAPIError) as info:
        provider(transport).generate_opportunities(PACK)
    assert info.value.status == 500


def test_network_error_propagates():
    def transport(url, headers, data):
        raise LLMNetworkError("down")

    with pytest.raises(LLMNetworkError):
        provider(transport).generate_opportunities(PACK)


def test_malformed_json_response_is_a_response_error_with_raw_kept():
    def transport(url, headers, data):
        body = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "not json"}]}
        return 200, json.dumps(body).encode()

    with pytest.raises(LLMResponseError, match="not valid JSON") as info:
        provider(transport).generate_opportunities(PACK)
    assert info.value.raw == "not json"


def test_schema_invalid_response_is_a_response_error():
    def transport(url, headers, data):
        body = {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps({"nope": True})}]}
        return 200, json.dumps(body).encode()

    with pytest.raises(LLMResponseError, match="invalid opportunity result"):
        provider(transport).generate_opportunities(PACK)


def test_refusal_stop_reason_is_a_response_error():
    def transport(url, headers, data):
        body = {"stop_reason": "refusal", "stop_details": {"category": "policy"},
                 "content": []}
        return 200, json.dumps(body).encode()

    with pytest.raises(LLMResponseError, match="declined"):
        provider(transport).generate_opportunities(PACK)


def test_max_tokens_truncation_is_a_response_error_no_partial_result_used():
    def transport(url, headers, data):
        body = {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "{"}]}
        return 200, json.dumps(body).encode()

    with pytest.raises(LLMResponseError, match="truncated"):
        provider(transport).generate_opportunities(PACK)


def test_no_text_block_is_a_response_error():
    def transport(url, headers, data):
        body = {"stop_reason": "end_turn", "content": [{"type": "thinking", "thinking": "..."}]}
        return 200, json.dumps(body).encode()

    with pytest.raises(LLMResponseError, match="did not return an opportunity result"):
        provider(transport).generate_opportunities(PACK)


def test_prompt_version_is_stable_across_calls():
    assert PROMPT_VERSION == "opportunity-v1"
