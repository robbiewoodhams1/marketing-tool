"""SYNTHESIS_EFFORT -> output_config.effort, for controlled experiments.

Nothing here reaches the network: requests are captured by a fake transport.
"""

import json
import logging

import pytest
from synthesis_fixtures import GOOD_OUTPUT, build_pack, seeded_db

import research.llm as llm
from research import synthesize
from research.config import Settings
from research.llm import EFFORT_LEVELS, AnthropicProvider, LLMConfigError, parse_effort
from research.synthesis_prompt import PROMPT_VERSION, SYSTEM_PROMPT, synthesis_output_json_schema

JOB = "d8aee101-d48c-4be5-9247-57b33b56fe65"


def capture():
    """(transport, payloads): a transport that records the request and returns a valid answer."""
    payloads = []

    def transport(url, headers, data):
        payloads.append(json.loads(data))
        body = {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(GOOD_OUTPUT)}]}
        return 200, json.dumps(body).encode()

    return transport, payloads


def request_for(env=None, model="claude-sonnet-5"):
    """The synthesis payload produced with SYNTHESIS_EFFORT etc. taken from `env`."""
    transport, payloads = capture()
    settings = Settings.from_env({"LLM_API_KEY": "k", "SYNTHESIS_MODEL": model, **(env or {})})
    AnthropicProvider.for_synthesis(settings, transport=transport).synthesize_research(build_pack())
    [payload] = payloads
    return payload


SCHEMA_FORMAT = {"type": "json_schema", "schema": synthesis_output_json_schema()}


# --- 1-2. the value reaches the request ------------------------------------------------------------


@pytest.mark.parametrize("effort", ["low", "medium"])
def test_the_effort_from_the_environment_is_sent_as_output_config_effort(effort):
    payload = request_for({"SYNTHESIS_EFFORT": effort})
    assert payload["output_config"]["effort"] == effort


@pytest.mark.parametrize("effort", EFFORT_LEVELS)
def test_every_supported_level_is_accepted_and_sent_unchanged(effort):
    assert request_for({"SYNTHESIS_EFFORT": effort})["output_config"]["effort"] == effort


def test_the_supported_levels_are_the_documented_five():
    assert EFFORT_LEVELS == ("low", "medium", "high", "xhigh", "max")


@pytest.mark.parametrize("raw,expected", [("MEDIUM", "medium"), ("  low  ", "low"), ("High", "high"), ("XHIGH", "xhigh")])
def test_case_and_surrounding_whitespace_are_ignored(raw, expected):
    assert request_for({"SYNTHESIS_EFFORT": raw})["output_config"]["effort"] == expected


# --- 3. unset: the existing behaviour is preserved --------------------------------------------------


@pytest.mark.parametrize("env", [{}, {"SYNTHESIS_EFFORT": ""}, {"SYNTHESIS_EFFORT": "   "}])
def test_unset_or_blank_sends_no_effort_at_all(env):
    payload = request_for(env)
    assert payload["output_config"] == {"format": SCHEMA_FORMAT}  # exactly what it was before this setting
    assert "effort" not in json.dumps(payload["output_config"]) and "thinking" not in payload


def test_unset_produces_the_identical_request_to_a_provider_built_without_the_parameter():
    transport, payloads = capture()
    AnthropicProvider("k", model="claude-sonnet-5", transport=transport).synthesize_research(build_pack())
    assert request_for({}) == payloads[0]


def test_settings_reads_the_variable_and_unset_is_none():
    assert Settings.from_env({"SYNTHESIS_EFFORT": "medium"}).synthesis_effort == "medium"
    assert Settings.from_env({}).synthesis_effort is None
    assert Settings.from_env({"SYNTHESIS_EFFORT": ""}).synthesis_effort is None
    assert Settings().synthesis_effort is None


# --- 4. invalid values fail clearly, at configuration time -------------------------------------------------


@pytest.mark.parametrize("bad", ["extreme", "mid", "0", "none", "disabled", "med ium", "highest", "1"])
def test_an_invalid_effort_is_a_clear_configuration_error(bad):
    with pytest.raises(LLMConfigError) as info:
        AnthropicProvider.for_synthesis(Settings.from_env({"LLM_API_KEY": "k", "SYNTHESIS_EFFORT": bad}))
    message = str(info.value)
    assert "SYNTHESIS_EFFORT must be one of low, medium, high, xhigh, max" in message
    assert repr(bad) in message  # names the offending value


def test_an_invalid_effort_is_rejected_before_any_request_is_built():
    transport, payloads = capture()
    with pytest.raises(LLMConfigError):
        AnthropicProvider.for_synthesis(
            Settings.from_env({"LLM_API_KEY": "k", "SYNTHESIS_EFFORT": "extreme"}), transport=transport)
    assert payloads == []


def test_the_provider_constructor_validates_too_and_parse_effort_is_the_single_rule():
    with pytest.raises(LLMConfigError, match="effort must be one of"):
        AnthropicProvider("k", effort="bogus")
    assert AnthropicProvider("k", effort="LOW")._effort == "low"
    assert parse_effort(None) is None and parse_effort("") is None and parse_effort("max") == "max"


def test_the_cli_exits_2_with_a_clear_message_and_makes_no_request(monkeypatch, capsys):
    monkeypatch.setenv("LLM_API_KEY", "llm-secret")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "sr-secret")
    monkeypatch.setenv("SYNTHESIS_EFFORT", "extreme")
    called = []
    monkeypatch.setattr(synthesize, "SupabaseDatabase", lambda *a: called.append("db"))
    monkeypatch.setattr(llm, "_urllib_transport", lambda t: called.append("network"))
    assert synthesize.main(["--job-id", JOB]) == 2
    err = capsys.readouterr().err
    assert "SYNTHESIS_EFFORT must be one of low, medium, high, xhigh, max (got 'extreme')" in err
    assert "secret" not in err and called == []


# --- 5. the structured-output schema is untouched alongside effort ---------------------------------------


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_the_json_schema_is_still_present_and_unchanged_next_to_effort(effort):
    output_config = request_for({"SYNTHESIS_EFFORT": effort})["output_config"]
    assert set(output_config) == {"format", "effort"}
    assert output_config["format"] == SCHEMA_FORMAT


def test_effort_changes_nothing_else_in_the_request():
    base, medium = request_for({}), request_for({"SYNTHESIS_EFFORT": "medium"})
    for key in ("model", "max_tokens", "system", "messages"):
        assert medium[key] == base[key]
    assert medium["max_tokens"] == llm.SYNTHESIS_MAX_TOKENS == 16000
    assert medium["system"] == SYSTEM_PROMPT and PROMPT_VERSION == "synthesis-v3"
    without_effort = {k: v for k, v in medium["output_config"].items() if k != "effort"}
    assert without_effort == base["output_config"]
    assert set(medium) == set(base) == {"model", "max_tokens", "system", "messages", "output_config"}


def test_classification_is_unaffected_by_the_setting():
    from research.classification import ClassificationInput

    seen = []

    def transport(url, headers, data):
        seen.append(json.loads(data))
        body = {"content": [{"type": "tool_use", "name": "record_classification", "input": {
            k: {"value": None, "confidence": None, "evidence": None}
            for k in ("topic", "audience", "pain_point", "hook", "hook_type", "format", "emotion", "cta")}}]}
        return 200, json.dumps(body).encode()

    settings = Settings.from_env({"LLM_API_KEY": "k", "SYNTHESIS_EFFORT": "low"})
    AnthropicProvider.from_settings(settings, transport=transport).classify_content(
        ClassificationInput("dQw4w9WgXcQ", "Title", None, "Hello"))
    assert "output_config" not in seen[0]  # classification never sends effort


def test_the_effort_is_visible_in_the_request_log_for_experiments(caplog):
    caplog.set_level(logging.INFO, logger="research.llm")
    request_for({"SYNTHESIS_EFFORT": "low"})
    request_for({})
    starts = [r.getMessage() for r in caplog.records if "synthesis request start" in r.getMessage()]
    assert "effort=low" in starts[0] and "effort=default" in starts[1]


# --- the real entry point, environment variable to request (no network) ----------------------------------


@pytest.mark.parametrize("setting,expected", [("medium", "medium"), ("low", "low"), (None, None)])
def test_running_the_cli_with_the_variable_set_or_unset(monkeypatch, capsys, setting, expected):
    db = seeded_db()
    db.tables["research_jobs"][0]["id"] = JOB
    for row in db.tables["content"]:
        row["research_job_id"] = JOB
    transport, payloads = capture()
    monkeypatch.setenv("LLM_API_KEY", "llm-secret")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "sr-secret")
    monkeypatch.delenv("SYNTHESIS_EFFORT", raising=False)
    if setting:
        monkeypatch.setenv("SYNTHESIS_EFFORT", setting)
    monkeypatch.setenv("SYNTHESIS_MODEL", "claude-sonnet-5")
    monkeypatch.setattr(synthesize, "SupabaseDatabase", lambda url, key: db)
    monkeypatch.setattr(llm, "_urllib_transport", lambda timeout: transport)  # no network
    assert synthesize.main(["--job-id", JOB]) == 0
    [payload] = payloads
    assert payload["model"] == "claude-sonnet-5"
    assert payload["output_config"].get("effort") == expected
    assert payload["output_config"]["format"] == SCHEMA_FORMAT
    assert len(db.tables["syntheses"]) == 1  # (the fake database: nothing real was written)
