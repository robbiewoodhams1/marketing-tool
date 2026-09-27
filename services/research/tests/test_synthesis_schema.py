import copy
import json
import re

import pytest
from synthesis_fixtures import GOOD_OUTPUT, ZERO_OUTPUT, build_pack, ev, insight, output

from research.config import Settings
from research.llm import (
    SYNTHESIS_MAX_TOKENS,
    AnthropicProvider,
    LLMAPIError,
    LLMResponseError,
    SynthesisProvider,
)
from research.synthesis import (
    MAX_INSIGHTS,
    EvidenceField,
    PatternType,
    SynthesisOutput,
    SynthesisSchemaError,
)
from research.synthesis_prompt import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
    synthesis_output_json_schema,
)


def parse(data):
    return SynthesisOutput.from_dict(data)


def bad(**changes):
    """GOOD_OUTPUT with the first insight's fields replaced."""
    return output(insight(**changes))


# --- valid ------------------------------------------------------------------------------------


def test_valid_output_parses_and_roundtrips():
    out = parse(GOOD_OUTPUT)
    [i] = out.insights
    assert i.pattern_type is PatternType.HOOK_PATTERN and i.confidence == 0.6
    assert i.observations[0].evidence[0].field is EvidenceField.TRANSCRIPT
    assert out.no_insights_reason is None
    assert out.to_dict() == GOOD_OUTPUT  # nothing added, nothing lost


def test_zero_insight_output_is_valid_with_a_reason():
    out = parse(ZERO_OUTPUT)
    assert out.insights == () and "recurring pattern" in out.no_insights_reason


def test_taxonomy_is_small_and_as_specified():
    assert [p.value for p in PatternType] == [
        "recurring_theme", "hook_pattern", "format_pattern", "performance_signal",
        "content_comment_pattern"]


# --- malformed --------------------------------------------------------------------------------


@pytest.mark.parametrize("data", [None, [], "text", 3, {"insights": []}, {"no_insights_reason": "x"}])
def test_missing_or_non_object_output_is_rejected(data):
    with pytest.raises(SynthesisSchemaError):
        parse(data)


def test_unknown_top_level_and_insight_keys_are_rejected():
    with pytest.raises(SynthesisSchemaError, match="unexpected"):
        parse({**GOOD_OUTPUT, "extra": 1})
    with pytest.raises(SynthesisSchemaError, match="unexpected"):
        parse(bad(advice="post more"))


@pytest.mark.parametrize("field", ["title", "pattern_type", "statement", "observations", "explanation",
                                   "confidence", "confidence_rationale", "counter_evidence", "limitations"])
def test_every_insight_field_is_required(field):
    i = insight()
    del i[field]
    with pytest.raises(SynthesisSchemaError, match="missing"):
        parse(output(i))


@pytest.mark.parametrize("field", ["title", "statement", "explanation", "confidence_rationale", "limitations"])
@pytest.mark.parametrize("value", ["", "   ", None, 5, ["x"]])
def test_text_fields_must_be_non_blank_strings(field, value):
    with pytest.raises(SynthesisSchemaError):
        parse(bad(**{field: value}))


def test_bare_strings_are_rejected_where_objects_are_required():
    with pytest.raises(SynthesisSchemaError, match="must be an object"):
        parse(bad(observations=["C1 says so"]))
    with pytest.raises(SynthesisSchemaError, match="must be an object"):
        parse(bad(observations=[{"text": "t", "evidence": ["C1"]}]))
    with pytest.raises(SynthesisSchemaError, match="must be an object"):
        parse(bad(counter_evidence=["C4 title"]))
    with pytest.raises(SynthesisSchemaError, match="must be a list"):
        parse(bad(observations="C1 says so"))
    with pytest.raises(SynthesisSchemaError, match="insights must be a list"):
        parse({"insights": "none", "no_insights_reason": None})


@pytest.mark.parametrize("value", ["insight", "recurring", "Hook_Pattern", "", None, 3])
def test_invalid_pattern_type_is_rejected(value):
    with pytest.raises(SynthesisSchemaError, match="pattern_type"):
        parse(bad(pattern_type=value))


@pytest.mark.parametrize("value", [1.01, -0.01, 2, "0.5", None, True, [0.5]])
def test_invalid_confidence_is_rejected(value):
    with pytest.raises(SynthesisSchemaError, match="confidence"):
        parse(bad(confidence=value))


@pytest.mark.parametrize("value", [0, 1, 0.0, 1.0, 0.5])
def test_boundary_confidence_is_accepted(value):
    assert parse(bad(confidence=value)).insights[0].confidence == float(value)


@pytest.mark.parametrize("evidence", [
    {"ref": "C1", "field": "transcript"},                                  # missing quote
    {"ref": "C1", "quote": "q"},                                           # missing field
    {"field": "title", "quote": "q"},                                      # missing ref
    {"ref": "C1", "field": "title", "quote": "q", "note": "x"},            # unknown key
    {"ref": "C1", "field": "title", "quote": ""},                          # blank quote
    {"ref": "C1", "field": "title", "quote": 5},                           # non-string quote
    {"ref": "C1", "field": "sentiment", "quote": "q"},                     # invalid field enum
    {"ref": "c1", "field": "title", "quote": "q"},                         # malformed ref
    {"ref": "C0", "field": "title", "quote": "q"},
    {"ref": "X9", "field": "title", "quote": "q"},
    {"ref": "C1 ", "field": "title", "quote": "q"},
    {"ref": "550e8400-e29b-41d4-a716-446655440000", "field": "title", "quote": "q"},  # a real uuid, not a ref
])
def test_malformed_nested_evidence_is_rejected(evidence):
    obs = [{"text": "t", "evidence": [evidence]}]
    with pytest.raises(SynthesisSchemaError):
        parse(bad(observations=obs))
    with pytest.raises(SynthesisSchemaError):
        parse(bad(counter_evidence=[evidence]))


def test_observations_and_their_evidence_cannot_be_empty():
    with pytest.raises(SynthesisSchemaError, match="at least one observation"):
        parse(bad(observations=[]))
    with pytest.raises(SynthesisSchemaError, match="needs evidence"):
        parse(bad(observations=[{"text": "t", "evidence": []}]))


def test_no_insights_reason_and_insights_must_agree():
    with pytest.raises(SynthesisSchemaError, match="requires a no_insights_reason"):
        parse(output())
    with pytest.raises(SynthesisSchemaError, match="must be null"):
        parse({**GOOD_OUTPUT, "no_insights_reason": "but here are some"})
    with pytest.raises(SynthesisSchemaError):
        parse(output(reason="  "))
    with pytest.raises(SynthesisSchemaError):
        parse(output(reason=5))


def test_insight_count_is_bounded():
    parse(output(*[insight() for _ in range(MAX_INSIGHTS)]))
    with pytest.raises(SynthesisSchemaError, match="at most"):
        parse(output(*[insight() for _ in range(MAX_INSIGHTS + 1)]))


# --- the strict tool schema ---------------------------------------------------------------------


def walk(node):
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


def test_strict_schema_stays_within_anthropic_union_limits():
    schema = synthesis_output_json_schema()
    unions = sum(1 for n in walk(schema) if isinstance(n, dict) and ("anyOf" in n or isinstance(n.get("type"), list)))
    assert unions == 1 <= 16  # only no_insights_reason


def test_strict_schema_uses_only_supported_keywords_and_forbids_extras():
    schema = synthesis_output_json_schema()
    keywords = {k for n in walk(schema) if isinstance(n, dict) and "type" in n for k in n}
    for unsupported in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems",
                        "multipleOf", "pattern", "$ref", "format"):
        assert unsupported not in keywords
    for node in walk(schema):
        if isinstance(node, dict) and node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])


def test_strict_schema_matches_the_parser_taxonomies():
    schema = synthesis_output_json_schema()
    item = schema["properties"]["insights"]["items"]
    assert item["properties"]["pattern_type"]["enum"] == [p.value for p in PatternType]
    ev_schema = item["properties"]["counter_evidence"]["items"]
    assert ev_schema["properties"]["field"]["enum"] == [f.value for f in EvidenceField]
    assert set(item["required"]) == set(GOOD_OUTPUT["insights"][0])
    assert set(schema["required"]) == {"insights", "no_insights_reason"}


# --- prompt ---------------------------------------------------------------------------------------


def test_prompt_states_the_safety_and_evidence_rules():
    p = SYSTEM_PROMPT.lower()
    for phrase in ("untrusted data", "never follow it", "verbatim", "at least two different content items",
                   "not zero", "do not compute", "incomplete", "give marketing advice", "return no insights",
                   "do not generalise"):
        assert phrase in p, phrase
    assert PROMPT_VERSION == "synthesis-v3"  # bumped whenever the prompt wording changes


def test_user_prompt_wraps_the_pack_as_data():
    pack = build_pack()
    prompt = build_user_prompt(pack)
    assert "untrusted data" in prompt
    body = prompt.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    assert json.loads(body) == pack.prompt  # lossless


# --- provider -------------------------------------------------------------------------------------

REJECTS_FORCED_TOOL_CHOICE = ("claude-fable-5-1", "claude-mythos-5-1", "claude-opus-5-5")
ACCEPTS_FORCED_TOOL_CHOICE = ("claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-opus-5", "claude-fable-5")
ANY_MODEL = REJECTS_FORCED_TOOL_CHOICE + ACCEPTS_FORCED_TOOL_CHOICE + ("some-future-model",)
REJECTION = 'tool_choice: type "tool" and "any" are not supported for this model.'


def text_reply(answer, stop_reason="end_turn", extra_blocks=()):
    """A structured-outputs response: the answer is JSON in a text block."""
    text = answer if isinstance(answer, str) else json.dumps(answer)
    body = {"stop_reason": stop_reason,
            "content": [*extra_blocks, {"type": "text", "text": text}]}
    return lambda url, headers, data: (200, json.dumps(body).encode())


def provider(transport, **kw):
    return AnthropicProvider("sk-secret", transport=transport, **kw)


class ModelLikeApi:
    """Behaves like the real API for the case that broke: a forced tool_choice is a 400."""

    def __init__(self, answer=GOOD_OUTPUT):
        self.answer, self.payloads = answer, []

    def __call__(self, url, headers, data):
        payload = json.loads(data)
        self.payloads.append(payload)
        if payload.get("tool_choice", {}).get("type") in ("tool", "any"):
            body = {"type": "error", "error": {"type": "invalid_request_error", "message": REJECTION}}
            return 400, json.dumps(body).encode()
        return text_reply(self.answer)(url, headers, data)


def test_provider_satisfies_the_synthesis_protocol_and_returns_a_parsed_output():
    p: SynthesisProvider = provider(text_reply(GOOD_OUTPUT))
    out = p.synthesize_research(build_pack())
    assert isinstance(out, SynthesisOutput) and len(out.insights) == 1
    assert out.to_dict() == GOOD_OUTPUT


def test_request_uses_structured_outputs_with_the_strict_schema_and_the_pack():
    seen = {}

    def transport(url, headers, data):
        seen.update(headers=headers, payload=json.loads(data))
        return text_reply(GOOD_OUTPUT)(url, headers, data)

    pack = build_pack()
    provider(transport, model="synth-x").synthesize_research(pack)
    payload = seen["payload"]
    assert payload["output_config"] == {
        "format": {"type": "json_schema", "schema": synthesis_output_json_schema()}}
    assert payload["model"] == "synth-x" and payload["max_tokens"] == SYNTHESIS_MAX_TOKENS
    assert payload["system"] == SYSTEM_PROMPT
    assert pack.prompt["content"][0]["title"] in payload["messages"][0]["content"]
    assert "anthropic-beta" not in seen["headers"]
    assert seen["headers"]["x-api-key"] == "sk-secret"
    assert "sk-secret" not in json.dumps(payload)


@pytest.mark.parametrize("model", ANY_MODEL)
def test_synthesis_never_forces_a_tool_choice_so_it_works_on_models_that_reject_it(model):
    api = ModelLikeApi()
    out = provider(api, model=model).synthesize_research(build_pack())
    [payload] = api.payloads
    assert "tool_choice" not in payload and "tools" not in payload
    assert payload["output_config"]["format"]["type"] == "json_schema"
    assert len(out.insights) == 1  # and the strict contract is still parsed


def test_the_request_is_identical_for_every_model_apart_from_the_model_name():
    shapes = []
    for model in ANY_MODEL:
        api = ModelLikeApi()
        provider(api, model=model).synthesize_research(build_pack())
        shapes.append({k: v for k, v in api.payloads[0].items() if k != "model"})
    assert all(shape == shapes[0] for shape in shapes)  # no per-model branching


def test_the_original_400_is_reproduced_by_a_forced_tool_choice_and_surfaces_as_an_api_error():
    # what the previous request looked like: this is the regression being guarded
    api = ModelLikeApi()
    status, body = api("u", {}, json.dumps({"tool_choice": {"type": "tool", "name": "x"}}).encode())
    assert status == 400 and json.loads(body)["error"]["message"] == REJECTION
    with pytest.raises(LLMAPIError, match="not supported for this model") as info:
        provider(lambda *a: (status, body), model="claude-fable-5-1").synthesize_research(build_pack())
    assert info.value.status == 400


def test_the_schema_is_the_unchanged_strict_synthesis_v1_contract():
    payload_schema = None

    def transport(url, headers, data):
        nonlocal payload_schema
        payload_schema = json.loads(data)["output_config"]["format"]["schema"]
        return text_reply(GOOD_OUTPUT)(url, headers, data)

    provider(transport).synthesize_research(build_pack())
    assert payload_schema == synthesis_output_json_schema()
    unions = sum(1 for n in walk(payload_schema)
                 if isinstance(n, dict) and ("anyOf" in n or isinstance(n.get("type"), list)))
    assert unions == 1
    for node in walk(payload_schema):
        if isinstance(node, dict) and node.get("type") == "object":
            assert node["additionalProperties"] is False and set(node["required"]) == set(node["properties"])


def test_classification_is_unchanged_and_the_two_models_are_independent():
    from research.classification import ClassificationInput

    seen = []

    def transport(url, headers, data):
        payload = json.loads(data)
        seen.append(payload)
        if "tools" in payload:  # the classification request
            body = {"content": [{"type": "tool_use", "name": "record_classification", "input": {
                k: {"value": None, "confidence": None, "evidence": None}
                for k in ("topic", "audience", "pain_point", "hook", "hook_type", "format", "emotion", "cta")}}]}
            return 200, json.dumps(body).encode()
        return text_reply(GOOD_OUTPUT)(url, headers, data)

    settings = Settings(llm_api_key="k", llm_model="classify-model", synthesis_model="claude-fable-5-1")
    AnthropicProvider.from_settings(settings, transport=transport).classify_content(
        ClassificationInput("dQw4w9WgXcQ", "Title", None, "Hello there"))
    AnthropicProvider.for_synthesis(settings, transport=transport).synthesize_research(build_pack())
    classification, synthesis = seen
    assert classification["model"] == "classify-model"
    assert classification["tool_choice"] == {"type": "tool", "name": "record_classification"}
    assert classification["tools"][0]["strict"] is True  # untouched
    assert synthesis["model"] == "claude-fable-5-1" and "tool_choice" not in synthesis


# --- responses ---------------------------------------------------------------------------------------


def test_answers_after_thinking_blocks_are_parsed():
    thinking = [{"type": "thinking", "thinking": "", "signature": "sig"},
                {"type": "redacted_thinking", "data": "xxx"}]
    out = provider(text_reply(GOOD_OUTPUT, extra_blocks=thinking)).synthesize_research(build_pack())
    assert out.to_dict() == GOOD_OUTPUT


def test_a_zero_insight_answer_is_parsed():
    out = provider(text_reply(ZERO_OUTPUT)).synthesize_research(build_pack())
    assert out.insights == () and out.no_insights_reason


def test_malformed_model_output_is_a_response_error_with_the_raw_output():
    broken = copy.deepcopy(GOOD_OUTPUT)
    broken["insights"][0]["pattern_type"] = "vibes"
    with pytest.raises(LLMResponseError, match="invalid synthesis") as info:
        provider(text_reply(broken)).synthesize_research(build_pack())
    assert info.value.raw == broken and "sk-secret" not in str(info.value)


def test_bare_string_observation_from_the_model_is_rejected():
    with pytest.raises(LLMResponseError, match="must be an object"):
        provider(text_reply(bad(observations=["just text"]))).synthesize_research(build_pack())


def test_non_json_text_is_a_response_error():
    with pytest.raises(LLMResponseError, match="not valid JSON") as info:
        provider(text_reply("Here are my insights: ...")).synthesize_research(build_pack())
    assert info.value.raw.startswith("Here are")


def test_truncated_response_is_never_used():
    with pytest.raises(LLMResponseError, match="truncated") as info:
        provider(text_reply(GOOD_OUTPUT, stop_reason="max_tokens")).synthesize_research(build_pack())
    assert info.value.stop_reason == "max_tokens"


def test_a_refusal_is_reported_with_its_category_not_parsed():
    body = {"stop_reason": "refusal", "stop_details": {"type": "refusal", "category": "cyber", "explanation": "x"},
            "content": []}
    with pytest.raises(LLMResponseError, match=r"declined the request \(category: cyber\)") as info:
        provider(lambda *a: (200, json.dumps(body).encode())).synthesize_research(build_pack())
    assert info.value.stop_reason == "refusal"


@pytest.mark.parametrize("body", [
    b"not json",
    b'{"content": "nope"}',
    b'{"content": []}',
    json.dumps({"content": [{"type": "thinking", "thinking": "", "signature": "s"}]}).encode(),
    # the previous response shape (a tool call) is no longer what synthesis asks for
    json.dumps({"content": [{"type": "tool_use", "name": "record_synthesis", "input": GOOD_OUTPUT}]}).encode(),
])
def test_unusable_responses_are_response_errors(body):
    with pytest.raises(LLMResponseError):
        provider(lambda *a: (200, body)).synthesize_research(build_pack())


def test_api_errors_carry_status_but_never_the_key():
    body = json.dumps({"error": {"message": "Schemas contains too many parameters"}}).encode()
    with pytest.raises(LLMAPIError) as info:
        provider(lambda *a: (400, body)).synthesize_research(build_pack())
    assert info.value.status == 400 and "sk-secret" not in str(info.value)


def test_synthesis_model_selection_and_timeout():
    s = Settings(llm_api_key="k")
    assert AnthropicProvider.for_synthesis(s).model == "claude-haiku-4-5-20251001"
    assert AnthropicProvider.for_synthesis(Settings(llm_api_key="k", llm_model="a")).model == "a"
    assert AnthropicProvider.for_synthesis(
        Settings(llm_api_key="k", llm_model="a", synthesis_model="b")).model == "b"
    assert Settings.from_env({"SYNTHESIS_MODEL": "m"}).synthesis_model == "m"
    assert Settings.from_env({}).synthesis_model is None


# --- prompt guidance on majority wording (must match what the validator enforces) ----------------------------

from research.synthesis_validation import _MAJORITY_RE  # noqa: E402

# every word the prompt names as majority/dominance language
MAJORITY_WORDS = ["most", "mostly", "majority", "predominantly", "dominant", "typically", "generally",
                  "usually", "commonly", "overwhelmingly", "prevalent", "the norm", "nearly all", "almost all"]
# the neutral wording the prompt offers for exactly-half-or-fewer
NEUTRAL_WORDING = ["five of ten videos", "half of the videos", "recurs across five videos", "is present in"]


def majority_guidance():
    [line] = [l for l in SYSTEM_PROMPT.splitlines() if l.startswith("- Majority wording")]
    return line


def test_the_prompt_states_the_more_than_half_rule_in_general_terms():
    g = majority_guidance()
    assert "MORE THAN HALF" in g
    assert "Exactly half is not a majority (5 of 10 fails, 6 of 10 passes)" in g
    assert "distinct content items" in g
    assert "never from how strong or prominent the pattern seems" in g
    assert "all the videos analysed, or the classified videos if the claim rests on classifications" in g
    assert "checked by code" in g


def test_every_word_the_prompt_names_is_majority_language_to_the_validator():
    g = majority_guidance().lower()
    for word in MAJORITY_WORDS:
        assert word in g, f"the prompt does not name {word!r}"
        assert _MAJORITY_RE.search(word), f"the validator does not treat {word!r} as majority language"


def test_the_guidance_is_general_not_a_special_case_for_one_word():
    g = majority_guidance().lower()
    assert sum(word in g for word in MAJORITY_WORDS) == len(MAJORITY_WORDS)  # all of them, equally
    assert len(re.findall(r"\bdominant\b", g)) == 1  # named once, in the same list as the rest


def test_the_neutral_wording_the_prompt_offers_is_accepted_by_the_validator():
    g = majority_guidance()
    for phrase in NEUTRAL_WORDING:
        assert phrase in g
        assert not _MAJORITY_RE.search(phrase), f"{phrase!r} would itself be treated as majority language"
    for phrase in ("recurring", "recurs across five videos", "appears in", "is present across five items"):
        assert not _MAJORITY_RE.search(phrase)


def test_the_guidance_stays_brief():
    assert len(majority_guidance()) < 900  # one bullet; not a wall of rules
    assert SYSTEM_PROMPT.count("\n- Majority wording") == 1


# --- prompt guidance on reference namespaces (must match what the validator enforces) ---------------------------

from research.synthesis_validation import _FIELDS_BY_KIND  # noqa: E402

PREFIX_BY_KIND = {"content": "C#", "comment": "M#", "interpretation": "I#"}


def namespace_table():
    """{prefix: [fields]} parsed from the prompt's namespace lines."""
    table = {}
    for line in SYSTEM_PROMPT.splitlines():
        m = re.match(r"^\s{4}(C#|M#|I#|STATS) \(([^)]*)\): (.*)$", line)
        if m:
            table[m.group(1)] = [part.strip().split(" ")[0] for part in m.group(3).split(", ")]
    return table


def test_the_prompt_lists_each_namespace_with_exactly_the_fields_the_validator_allows():
    table = namespace_table()
    assert set(table) == {"C#", "M#", "I#", "STATS"}
    for kind, prefix in PREFIX_BY_KIND.items():
        assert set(table[prefix]) == {f.value for f in _FIELDS_BY_KIND[kind]}, prefix
    assert table["STATS"] == ["statistics"]


def test_the_prompt_says_comment_text_belongs_to_the_comment_reference_never_its_parent():
    assert "For comment_text always cite the M# comment reference, never its parent C# content reference" in SYSTEM_PROMPT
    assert "For a video's title, description, transcript or metrics cite the C# reference" in SYSTEM_PROMPT
    assert "Never convert a reference between namespaces" in SYSTEM_PROMPT


def test_the_prompt_tells_the_model_to_use_separate_entries_for_content_and_comment_claims():
    assert "separate evidence entries, e.g. C2 for the title and M16 for the comment" in SYSTEM_PROMPT
    assert "Do not attach comment evidence to C2 merely because the comment is on that video" in SYSTEM_PROMPT


def test_the_namespace_prefixes_match_the_references_the_pack_actually_generates():
    from synthesis_fixtures import build_pack

    pack = build_pack()
    generated = {ref[0] + "#": kind for ref, kind in ((r, t.kind) for r, t in pack.refs.items())}
    assert generated == {"C#": "content", "I#": "interpretation", "M#": "comment"}
    assert "STATS" in {k for k, _ in pack.sources}  # and the fourth namespace


def test_the_old_flat_field_list_is_gone_so_fields_are_only_listed_per_namespace():
    assert "  Fields: title, description" not in SYSTEM_PROMPT  # the list that never tied a field to a ref type
