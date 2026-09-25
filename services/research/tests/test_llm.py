import json

import pytest

from research.classification import ClassificationInput, ClassificationResult
from research.classification_prompt import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
    output_json_schema,
)
from research.config import Settings
from research.llm import (
    TOOL_NAME,
    AnthropicProvider,
    LLMAPIError,
    LLMConfigError,
    LLMError,
    LLMNetworkError,
    LLMProvider,
    LLMResponseError,
)

ITEM = ClassificationInput("dQw4w9WgXcQ", "Title", None, "Hello there")


def f(value, conf=0.8, ev="quote"):
    return {"value": value, "confidence": conf, "evidence": ev}


GOOD = {
    "topic": f("tax"), "audience": f("freelancers"), "pain_point": f("fear"),
    "hook": f("Stop"), "hook_type": f("mistake"), "format": f("tutorial"),
    "emotion": f("fear"), "cta": {"value": None, "confidence": None, "evidence": None},
}


def reply(tool_input, status=200):
    body = {"content": [{"type": "tool_use", "name": TOOL_NAME, "input": tool_input}]}
    return lambda url, headers, data: (status, json.dumps(body).encode())


def provider(transport):
    return AnthropicProvider("sk-secret", transport=transport)


def test_provider_satisfies_protocol():
    p: LLMProvider = provider(reply(GOOD))
    assert isinstance(p.classify_content(ITEM), ClassificationResult)


def test_success_and_request_shape():
    seen = {}

    def transport(url, headers, data):
        seen.update(url=url, headers=headers, payload=json.loads(data))
        return reply(GOOD)(url, headers, data)

    result = provider(transport).classify_content(ITEM)
    assert result.hook_type.value == "mistake"
    assert seen["headers"]["x-api-key"] == "sk-secret"
    assert seen["payload"]["tool_choice"]["name"] == TOOL_NAME
    assert "Hello there" in seen["payload"]["messages"][0]["content"]


def test_malformed_output_is_response_error():
    bad = dict(GOOD, hook_type=f("clickbait"))
    with pytest.raises(LLMResponseError):
        provider(reply(bad)).classify_content(ITEM)
    with pytest.raises(LLMResponseError):
        provider(lambda *a: (200, b"not json")).classify_content(ITEM)
    with pytest.raises(LLMResponseError):
        provider(lambda *a: (200, b'{"content":[{"type":"text","text":"hi"}]}')).classify_content(ITEM)


def test_api_error_carries_status_and_no_key():
    body = json.dumps({"error": {"message": "overloaded"}}).encode()
    with pytest.raises(LLMAPIError) as exc:
        provider(lambda *a: (529, body)).classify_content(ITEM)
    assert exc.value.status == 529
    assert "sk-secret" not in str(exc.value)


def test_network_error_propagates():
    def boom(*a):
        raise LLMNetworkError("timeout")

    with pytest.raises(LLMNetworkError):
        provider(boom).classify_content(ITEM)


def test_config_error_is_distinct():
    for key in (None, ""):
        with pytest.raises(LLMConfigError) as exc:
            AnthropicProvider(key)
        assert not isinstance(exc.value, (LLMAPIError, LLMNetworkError))
        assert isinstance(exc.value, LLMError)
    with pytest.raises(LLMConfigError):
        AnthropicProvider.from_settings(Settings())


def test_from_settings_uses_model_override():
    seen = {}

    def transport(url, headers, data):
        seen["model"] = json.loads(data)["model"]
        return reply(GOOD)(url, headers, data)

    s = Settings(llm_api_key="k", llm_model="some-model")
    AnthropicProvider.from_settings(s, transport=transport).classify_content(ITEM)
    assert seen["model"] == "some-model"


# --- prompt ---


def test_prompt_names_all_fields_and_evidence_rules():
    for name in ("topic", "audience", "pain_point", "hook", "hook_type", "format", "emotion", "cta"):
        assert name in SYSTEM_PROMPT
        assert name in output_json_schema()["properties"]
    lowered = SYSTEM_PROMPT.lower()
    assert "only from the evidence" in lowered
    assert "null" in lowered
    assert "do not invent" in lowered
    assert "evidence" in lowered
    assert "chain-of-thought" not in lowered
    assert PROMPT_VERSION


def test_user_prompt_marks_missing_evidence():
    text = build_user_prompt(ClassificationInput("dQw4w9WgXcQ", "T"))
    assert "Description: (not available)" in text
    assert "Transcript: (not available)" in text
    assert "Performance" not in text


def test_schema_enums_match_taxonomy():
    props = output_json_schema()["properties"]
    assert "mistake" in props["hook_type"]["properties"]["value"]["enum"]
    assert "enum" not in props["topic"]["properties"]["value"]
