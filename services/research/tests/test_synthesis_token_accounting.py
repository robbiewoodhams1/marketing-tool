"""How token usage and truncation are (and are not) visible today.

These are characterisation tests for a diagnosis: they document what the
current code reports about thinking versus final-text tokens. They assert no
optimisation and no behaviour change.
"""

import json

import pytest
from synthesis_fixtures import GOOD_OUTPUT, FakeSynthesisLLM, build_pack, seeded_db

from research import synthesize
from research.config import Settings
from research.llm import SYNTHESIS_MAX_TOKENS, AnthropicProvider, LLMResponseError, _usage_summary

THINKING = {"type": "thinking", "thinking": "", "signature": "sig"}  # display defaults to "omitted"
PARTIAL_TEXT = '{"insights": [{"title": "PARTIAL-TEXT-MARKER", "pattern_type": "hook_pat'


def truncated_body(usage=None):
    return json.dumps({
        "stop_reason": "max_tokens",
        "content": [THINKING, {"type": "text", "text": PARTIAL_TEXT}],
        "usage": usage or {"input_tokens": 34324, "output_tokens": 16000},
    }).encode()


def test_the_request_sets_neither_thinking_nor_effort_so_the_models_defaults_apply():
    seen = {}

    def transport(url, headers, data):
        seen["payload"] = json.loads(data)
        return 200, json.dumps({"stop_reason": "end_turn",
                                "content": [{"type": "text", "text": json.dumps(GOOD_OUTPUT)}]}).encode()

    AnthropicProvider("k", model="claude-sonnet-5", transport=transport).synthesize_research(build_pack())
    payload = seen["payload"]
    assert "thinking" not in payload                       # Sonnet 5 then runs ADAPTIVE thinking by default
    assert set(payload["output_config"]) == {"format"}     # no `effort`: the default (`high`) applies
    assert "temperature" not in payload and "tools" not in payload
    assert payload["max_tokens"] == SYNTHESIS_MAX_TOKENS   # one budget for thinking AND the reply


def test_the_logged_output_tokens_is_the_apis_single_figure_thinking_included():
    body = truncated_body()
    summary = _usage_summary(body)
    assert summary == "stop_reason=max_tokens input_tokens=34324 output_tokens=16000"
    assert "thinking" not in summary  # we do not, and from this figure cannot, separate the two


def test_a_separate_thinking_figure_would_not_be_logged_because_only_four_usage_fields_are_read():
    usage = {"input_tokens": 10, "output_tokens": 16000,
             "output_tokens_details": {"thinking_tokens": 11000}}  # hypothetical extra field
    assert _usage_summary(truncated_body(usage)) == "stop_reason=max_tokens input_tokens=10 output_tokens=16000"


def test_thinking_blocks_are_ignored_when_the_answer_is_parsed():
    body = json.dumps({"stop_reason": "end_turn", "content": [
        THINKING, {"type": "redacted_thinking", "data": "x"},
        {"type": "text", "text": json.dumps(GOOD_OUTPUT)}]}).encode()
    out = AnthropicProvider("k", transport=lambda *a: (200, body)).synthesize_research(build_pack())
    assert out.to_dict() == GOOD_OUTPUT  # the thinking blocks did not interfere


def test_a_truncation_error_carries_the_partial_blocks_but_not_in_its_message():
    with pytest.raises(LLMResponseError, match="truncated") as info:
        AnthropicProvider("k", transport=lambda *a: (200, truncated_body())).synthesize_research(build_pack())
    exc = info.value
    assert exc.stop_reason == "max_tokens"
    assert [b["type"] for b in exc.raw] == ["thinking", "text"]       # available on the exception object...
    assert exc.raw[1]["text"] == PARTIAL_TEXT                          # ...including how much JSON was written
    assert "PARTIAL-TEXT-MARKER" not in str(exc) and "thinking" not in str(exc)


def test_nothing_about_the_truncated_content_reaches_the_cli_output(monkeypatch, capsys):
    # so the thinking-versus-JSON split of a truncated run is lost unless the exception is inspected
    error = LLMResponseError("synthesis was truncated (max_tokens reached); no partial result is used",
                             raw=[THINKING, {"type": "text", "text": PARTIAL_TEXT}], stop_reason="max_tokens")
    db = seeded_db()
    job = "d8aee101-d48c-4be5-9247-57b33b56fe65"
    db.tables["research_jobs"][0]["id"] = job
    for row in db.tables["content"]:
        row["research_job_id"] = job
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "s")
    monkeypatch.setattr(synthesize, "SupabaseDatabase", lambda url, key: db)
    monkeypatch.setattr(synthesize.AnthropicProvider, "for_synthesis", lambda s: FakeSynthesisLLM(error=error))
    assert synthesize.main(["--job-id", job]) == 3
    err = capsys.readouterr().err
    assert "LLMResponseError: synthesis was truncated" in err
    assert "PARTIAL-TEXT-MARKER" not in err and "thinking" not in err.lower().replace("truncated", "")
