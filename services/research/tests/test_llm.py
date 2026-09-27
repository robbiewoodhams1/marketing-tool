import json

import pytest

from research.classification import (
    FIELD_VOCABULARY,
    ClassificationInput,
    ClassificationResult,
    ClassificationSchemaError,
)
from research.classification_prompt import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
    output_json_schema,
    strict_output_json_schema,
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


# --- malformed hook_type (bare string instead of {value, confidence, evidence}) -----------


def test_bare_string_hook_type_is_rejected_and_raw_output_is_kept():
    bad = dict(GOOD, hook_type="pain")
    with pytest.raises(LLMResponseError, match=r"hook_type must be an object \(got str\)") as info:
        provider(reply(bad)).classify_content(ITEM)
    assert info.value.raw == bad  # the model's actual output, for diagnostics
    assert "sk-secret" not in str(info.value) and "pain" not in str(info.value)


def test_bare_string_is_rejected_by_the_schema_directly():
    with pytest.raises(ClassificationSchemaError, match="must be an object"):
        ClassificationResult.from_dict(dict(GOOD, hook_type="pain"))


def test_first_bad_field_is_reported_in_declaration_order():
    bad = dict(GOOD, format="tutorial", hook_type="pain")
    with pytest.raises(ClassificationSchemaError, match="hook_type"):
        ClassificationResult.from_dict(bad)


def test_valid_object_hook_type_is_accepted():
    result = provider(reply(dict(GOOD, hook_type=f("pain", 0.7, "Stop paying too much")))).classify_content(ITEM)
    assert (result.hook_type.value, result.hook_type.confidence, result.hook_type.evidence) == (
        "pain", 0.7, "Stop paying too much")


def test_request_schema_requires_an_object_for_every_field():
    schema = output_json_schema()
    assert set(schema["required"]) == set(GOOD)
    for name, prop in schema["properties"].items():
        assert prop["type"] == "object" and prop["required"] == ["value", "confidence", "evidence"], name


def test_non_tool_response_keeps_raw_content_for_diagnostics():
    with pytest.raises(LLMResponseError) as info:
        provider(lambda *a: (200, b'{"stop_reason":"end_turn","content":[{"type":"text","text":"hi"}]}')).classify_content(ITEM)
    assert info.value.stop_reason == "end_turn" and info.value.raw[0]["text"] == "hi"


# --- strict tool use + prompt clarification (1.4.2 live fix) ------------------------------


def _request_payload():
    seen = {}

    def transport(url, headers, data):
        seen.update(headers=headers, payload=json.loads(data))
        return reply(GOOD)(url, headers, data)

    provider(transport).classify_content(ITEM)
    return seen


def test_request_uses_strict_tool_use_with_the_strict_schema():
    seen = _request_payload()
    [tool] = seen["payload"]["tools"]
    assert tool["strict"] is True
    assert tool["input_schema"] == strict_output_json_schema()
    assert seen["payload"]["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert "anthropic-beta" not in seen["headers"]  # strict tool use needs no beta header


def _inner(field_schema):
    """The object branch of a strict field: anyOf [object, null]."""
    [obj] = [b for b in field_schema["anyOf"] if b.get("type") == "object"]
    return obj


def _count_unions(node):
    if isinstance(node, dict):
        here = int("anyOf" in node or "oneOf" in node or isinstance(node.get("type"), list))
        return here + sum(_count_unions(v) for v in node.values())
    if isinstance(node, list):
        return sum(_count_unions(v) for v in node)
    return 0


def test_strict_schema_has_at_most_one_union_per_field():
    schema = strict_output_json_schema()
    assert _count_unions(schema) == 8 <= 16  # Anthropic's limit is 16 union-typed parameters
    for name, prop in schema["properties"].items():
        assert set(prop) == {"anyOf"} and len(prop["anyOf"]) == 2, name
        assert {"type": "null"} in prop["anyOf"], name


def test_strict_schema_inner_properties_are_required_and_non_null():
    for name, prop in strict_output_json_schema()["properties"].items():
        inner = _inner(prop)
        assert inner["additionalProperties"] is False, name
        assert inner["required"] == ["value", "confidence", "evidence"], name
        assert set(inner["properties"]) == {"value", "confidence", "evidence"}, name
        assert inner["properties"]["value"]["type"] == "string", name
        assert inner["properties"]["confidence"] == {"type": "number"}, name
        assert inner["properties"]["evidence"] == {"type": "string"}, name
        assert "anyOf" not in json.dumps(inner) and '"null"' not in json.dumps(inner), name


def test_strict_schema_requires_all_eight_fields_and_forbids_extras():
    schema = strict_output_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["required"] == list(GOOD) and len(schema["required"]) == 8
    assert set(schema["properties"]) == set(FIELD_VOCABULARY)


def test_strict_schema_only_uses_the_supported_subset():
    text = json.dumps(strict_output_json_schema())
    for unsupported in ("minimum", "maximum", "minLength", "maxLength", "multipleOf"):
        assert unsupported not in text


def test_strict_schema_keeps_the_existing_enums():
    props = strict_output_json_schema()["properties"]
    for name, vocab in FIELD_VOCABULARY.items():
        value = _inner(props[name])["properties"]["value"]
        assert value.get("enum") == ([m.value for m in vocab] if vocab else None), name


def test_whole_field_null_means_insufficient_evidence():
    tool_input = dict(GOOD, cta=None, pain_point=None)
    result = provider(reply(tool_input)).classify_content(ITEM)
    assert (result.cta.value, result.cta.confidence, result.cta.evidence) == (None, None, None)
    assert result.pain_point.value is None and result.hook.value == "Stop"


def test_only_whole_field_null_is_expanded_bare_values_still_rejected():
    for bad in (dict(GOOD, hook_type="pain"), dict(GOOD, hook_type=""), dict(GOOD, cta=0)):
        with pytest.raises(LLMResponseError, match="must be an object"):
            provider(reply(bad)).classify_content(ITEM)
    missing = {k: v for k, v in GOOD.items() if k != "cta"}
    with pytest.raises(LLMResponseError, match="missing fields"):
        provider(reply(missing)).classify_content(ITEM)


def test_local_validation_still_rejects_bad_confidence_and_null_parts_are_strict():
    with pytest.raises(LLMResponseError, match="between 0 and 1"):
        provider(reply(dict(GOOD, topic=f("tax", 1.5)))).classify_content(ITEM)
    with pytest.raises(LLMResponseError):
        provider(reply(dict(GOOD, topic=f("tax", None)))).classify_content(ITEM)
    with pytest.raises(LLMResponseError):
        provider(reply(dict(GOOD, hook_type=f("clickbait")))).classify_content(ITEM)
    with pytest.raises(ClassificationSchemaError):  # from_dict itself does not accept null
        ClassificationResult.from_dict(dict(GOOD, cta=None))


def test_strict_schema_matches_the_lenient_schema_field_for_field():
    assert list(strict_output_json_schema()["properties"]) == list(output_json_schema()["properties"])


def test_prompt_distinguishes_field_objects_from_field_values():
    assert '"hook_type": {"value": "pain", "confidence": 0.9, "evidence": "..."}' in SYSTEM_PROMPT
    assert 'INVALID: "hook_type": "pain"' in SYSTEM_PROMPT
    assert "never a bare string" in SYSTEM_PROMPT
    for name in ("hook_type", "format", "emotion"):
        assert name in SYSTEM_PROMPT.split("Rules:")[0]  # named in the structure section
    assert '"value" must be exactly one of the listed values' in SYSTEM_PROMPT


def test_prompt_taxonomy_is_unchanged():
    for name, vocab in FIELD_VOCABULARY.items():
        if vocab is not None:
            assert f"- {name}: {', '.join(m.value for m in vocab)}" in SYSTEM_PROMPT
    assert PROMPT_VERSION == "classification-v2"


def test_provider_exposes_the_model_it_uses():
    assert AnthropicProvider("k").model == "claude-haiku-4-5-20251001"
    assert AnthropicProvider.from_settings(Settings(llm_api_key="k", llm_model="m")).model == "m"
