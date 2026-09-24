import logging
from types import SimpleNamespace

import pytest
import requests
from youtube_transcript_api import (
    AgeRestricted,
    IpBlocked,
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

from research.transcript_provider import YouTubeTranscriptApiProvider
from research.transcripts import (
    RawTranscript,
    TranscriptConfigError,
    TranscriptLanguageUnavailableError,
    TranscriptNetworkError,
    TranscriptProviderError,
    TranscriptsDisabledError,
    TranscriptUnavailableError,
    VideoUnavailableError,
    YouTubeTranscript,
    YouTubeTranscripts,
)


class FakeProvider:
    """Answers from a catalogue of RawTranscript or exceptions, recording calls."""

    def __init__(self, catalogue=None):
        self.catalogue = catalogue or {}
        self.calls = []

    def fetch(self, video_id, language, allow_fallback):
        self.calls.append((video_id, language, allow_fallback))
        outcome = self.catalogue.get(video_id, VideoUnavailableError("unknown"))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def raw(*segments, language="en", generated=False):
    return RawTranscript(list(segments), language, generated)


def service(catalogue, **kwargs):
    provider = FakeProvider(catalogue)
    return YouTubeTranscripts(provider, **kwargs), provider


# --- service ---------------------------------------------------------------


def test_single_transcript():
    svc, _ = service({"a": raw("Hello there")})
    assert svc.get_transcript("a") == YouTubeTranscript("a", "Hello there", "en", False)


def test_multiple_transcripts_in_input_order():
    svc, _ = service({"a": raw("A"), "b": raw("B"), "c": raw("C")})
    assert [t.video_id for t in svc.get_transcripts(["c", "a", "b"])] == ["c", "a", "b"]


def test_duplicates_fetched_once_first_seen_order():
    svc, provider = service({"a": raw("A"), "b": raw("B")})
    result = svc.get_transcripts(["b", "a", "b", " a "])
    assert [t.video_id for t in result] == ["b", "a"]
    assert [c[0] for c in provider.calls] == ["b", "a"]


def test_whitespace_stripped():
    svc, provider = service({"a": raw("A")})
    assert svc.get_transcripts(["  a\n"])[0].video_id == "a"
    assert provider.calls[0][0] == "a"


def test_empty_input_makes_no_calls():
    svc, provider = service({})
    assert svc.get_transcripts([]) == []
    assert provider.calls == []


@pytest.mark.parametrize("bad", ["", "   ", "\t"])
def test_blank_id_rejected_before_any_call(bad):
    svc, provider = service({"a": raw("A")})
    with pytest.raises(ValueError):
        svc.get_transcripts(["a", bad])
    with pytest.raises(ValueError):
        svc.get_transcript(bad)
    assert provider.calls == []


def test_malformed_id_is_not_truncated():
    svc, provider = service({})
    svc.get_transcripts(["abc def"])
    assert provider.calls[0][0] == "abc def"


def test_unavailable_is_skipped_not_fatal(caplog):
    svc, _ = service(
        {
            "a": raw("A"),
            "b": TranscriptsDisabledError("off"),
            "c": raw("C"),
            "d": VideoUnavailableError("gone"),
            "e": TranscriptLanguageUnavailableError("no en"),
        }
    )
    with caplog.at_level(logging.WARNING, logger="research.transcripts"):
        result = svc.get_transcripts(list("abcde"))
    assert [t.video_id for t in result] == ["a", "c"]
    assert "TranscriptsDisabledError" in caplog.text


def test_get_transcript_raises_specific_unavailable_reason():
    svc, _ = service({"a": TranscriptsDisabledError("off")})
    with pytest.raises(TranscriptsDisabledError):
        svc.get_transcript("a")
    assert issubclass(TranscriptsDisabledError, TranscriptUnavailableError)


def test_empty_transcript_is_unavailable():
    svc, _ = service({"a": raw("", "  "), "b": raw("B")})
    assert [t.video_id for t in svc.get_transcripts(["a", "b"])] == ["b"]


@pytest.mark.parametrize(
    "error", [TranscriptProviderError("boom"), TranscriptNetworkError("down")]
)
def test_provider_and_network_errors_propagate(error, caplog):
    svc, _ = service({"a": raw("A"), "b": error})
    with caplog.at_level(logging.ERROR, logger="research.transcripts"):
        with pytest.raises(type(error)):
            svc.get_transcripts(["a", "b"])
    assert type(error).__name__ in caplog.text


def test_segments_joined_and_whitespace_normalised():
    svc, _ = service({"a": raw("Today we're going to", " talk about\nbookkeeping ", "", "now.")})
    assert svc.get_transcript("a").text == "Today we're going to talk about bookkeeping now."


@pytest.mark.parametrize("segments", ["just a string", [1, 2], None, ["ok", None]])
def test_malformed_segments_rejected(segments):
    svc, _ = service({"a": RawTranscript(segments, "en", False)})
    with pytest.raises(TranscriptProviderError):
        svc.get_transcript("a")


def test_missing_language_rejected():
    svc, _ = service({"a": RawTranscript(["x"], "", False)})
    with pytest.raises(TranscriptProviderError):
        svc.get_transcript("a")


def test_language_settings_passed_to_provider():
    svc, provider = service({"a": raw("A")})
    svc.get_transcript("a")
    assert provider.calls == [("a", "en", False)]
    svc, provider = service({"a": raw("Hola", language="es")}, language=" es ", allow_fallback=True)
    result = svc.get_transcript("a")
    assert provider.calls == [("a", "es", True)]
    assert result.language == "es"


@pytest.mark.parametrize("bad", ["", "  "])
def test_blank_language_is_config_error(bad):
    with pytest.raises(TranscriptConfigError):
        YouTubeTranscripts(FakeProvider(), language=bad)


# --- library adapter ---------------------------------------------------------


class FakeTranscript:
    def __init__(self, code, generated=False, texts=("hi",), error=None):
        self.language_code, self.is_generated = code, generated
        self._texts, self._error = texts, error

    def fetch(self):
        if self._error:
            raise self._error
        return SimpleNamespace(
            snippets=[SimpleNamespace(text=t) for t in self._texts],
            language_code=self.language_code,
            is_generated=self.is_generated,
        )


class FakeApi:
    def __init__(self, transcripts=None, error=None):
        self.transcripts, self.error = transcripts or [], error

    def list(self, video_id):
        if self.error:
            raise self.error
        return self.transcripts


def fetch(api, language="en", fallback=False):
    return YouTubeTranscriptApiProvider(api).fetch("vid", language, fallback)


def test_adapter_returns_segments():
    result = fetch(FakeApi([FakeTranscript("en", texts=("a", "b"))]))
    assert result == RawTranscript(["a", "b"], "en", False)


def test_adapter_prefers_manual_over_generated():
    api = FakeApi([FakeTranscript("en", True, ("auto",)), FakeTranscript("en-GB", False, ("manual",))])
    assert fetch(api).segments == ["manual"]


def test_adapter_prefers_exact_code_then_regional():
    api = FakeApi([FakeTranscript("en-US", texts=("us",)), FakeTranscript("en", texts=("plain",))])
    assert fetch(api).segments == ["plain"]
    assert fetch(FakeApi([FakeTranscript("en-GB")])).language == "en-GB"


def test_adapter_region_request_does_not_match_other_region():
    with pytest.raises(TranscriptLanguageUnavailableError):
        fetch(FakeApi([FakeTranscript("en-US")]), language="en-GB")


def test_adapter_english_missing_errors_without_fallback():
    with pytest.raises(TranscriptLanguageUnavailableError, match="available: de"):
        fetch(FakeApi([FakeTranscript("de")]))


def test_adapter_english_missing_falls_back_when_allowed():
    api = FakeApi([FakeTranscript("de", True, ("auto",)), FakeTranscript("fr", False, ("manuel",))])
    result = fetch(api, fallback=True)
    assert (result.language, result.segments) == ("fr", ["manuel"])


def test_adapter_fallback_with_no_transcripts_at_all():
    with pytest.raises(TranscriptLanguageUnavailableError):
        fetch(FakeApi([]), fallback=True)


@pytest.mark.parametrize(
    "error, expected",
    [
        (TranscriptsDisabled("vid"), TranscriptsDisabledError),
        (VideoUnavailable("vid"), VideoUnavailableError),
        (AgeRestricted("vid"), VideoUnavailableError),
        (NoTranscriptFound("vid", ["en"], "x"), TranscriptLanguageUnavailableError),
        (IpBlocked("vid"), TranscriptProviderError),
        (requests.ConnectionError("secret-url"), TranscriptNetworkError),
        (requests.Timeout("secret-url"), TranscriptNetworkError),
        (RuntimeError("weird"), TranscriptProviderError),
    ],
)
def test_adapter_maps_library_errors(error, expected):
    with pytest.raises(expected) as info:
        fetch(FakeApi(error=error))
    assert type(info.value) is expected
    assert "secret-url" not in str(info.value)


def test_adapter_maps_errors_raised_during_fetch():
    with pytest.raises(TranscriptNetworkError):
        fetch(FakeApi([FakeTranscript("en", error=requests.ConnectionError())]))
