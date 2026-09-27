"""Transport observability: logging, timing and failure phase for Anthropic requests.

Every test talks to a server on 127.0.0.1 that the test itself starts. Nothing
reaches the real Anthropic API or any other network host.
"""

import json
import logging
import re
import socket
import threading

import pytest
from synthesis_fixtures import build_pack, content_row, insight, output

import research.llm as llm
from research.classification import ClassificationInput
from research.config import Settings
from research.llm import (
    PHASE_BODY,
    PHASE_CONNECTING,
    PHASE_ERROR_BODY,
    PHASE_WAITING,
    AnthropicProvider,
    LLMAPIError,
    LLMNetworkError,
    _cause_chain,
    _usage_summary,
)

KEY = "sk-ant-TESTSECRET-0123456789"
PROMPT_MARKER = "TRANSCRIPT-MARKER-XYZ"        # evidence content sent to the model
RESPONSE_MARKER = "RESPONSE-MARKER-QRS"        # content the model returns
ERROR_BODY_MARKER = "ERROR-BODY-MARKER-JKL"    # content of an API error body
TIMEOUT = 0.4


# --- a loopback-only server ----------------------------------------------------------------------


def read_request(conn):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(65536)
        if not chunk:
            break
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    length = int(re.search(rb"(?i)content-length:\s*(\d+)", head).group(1))
    while len(rest) < length:
        rest += conn.recv(65536)
    return head.decode("latin-1"), rest


class Loopback:
    """Accepts one connection, records the request, then runs `behaviour`."""

    def __init__(self, behaviour):
        self.stop = threading.Event()
        self.head, self.body = "", b""
        self._srv = socket.socket()
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.url = f"http://127.0.0.1:{self._srv.getsockname()[1]}/v1/messages"
        self._thread = threading.Thread(target=self._run, args=(behaviour,), daemon=True)
        self._thread.start()

    def _run(self, behaviour):
        try:
            conn, _ = self._srv.accept()
            with conn:
                self.head, self.body = read_request(conn)
                behaviour(conn, self.stop)
        except OSError:
            pass
        finally:
            self._srv.close()

    def close(self):
        self.stop.set()
        self._thread.join(timeout=2)


def http_response(status, body, reason="OK"):
    return (f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body


def answer(status=200, body=b"", reason="OK"):
    return lambda conn, stop: conn.sendall(http_response(status, body, reason))


def stall_after_reading(conn, stop):
    stop.wait(5)  # accepted and read, but never replies: a long non-streaming generation


def stall_in_body(conn, stop):
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\nConnection: close\r\n\r\n{\"partial\":")
    stop.wait(5)


def stall_in_error_body(conn, stop):
    conn.sendall(b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 1000\r\nConnection: close\r\n\r\npartial")
    stop.wait(5)


@pytest.fixture
def server(monkeypatch):
    started = []

    def start(behaviour):
        srv = Loopback(behaviour)
        started.append(srv)
        monkeypatch.setattr(llm, "ANTHROPIC_URL", srv.url)
        return srv

    yield start
    for srv in started:
        srv.close()


@pytest.fixture
def logs(caplog):
    caplog.set_level(logging.DEBUG, logger="research.llm")

    class View:
        @property
        def records(self):
            return [r for r in caplog.records if r.name == "research.llm"]

        @property
        def messages(self):
            return [r.getMessage() for r in self.records]

        def texts(self):
            """Every message plus every formatted traceback: all that could ever be printed."""
            fmt = logging.Formatter()
            out = []
            for r in self.records:
                out.append(r.getMessage())
                if r.exc_info:
                    out.append(fmt.formatException(r.exc_info))
            return out

        def one(self, needle):
            """The single summary line containing `needle` (traceback records excluded)."""
            [found] = [m for m in self.messages if needle in m and "failure traceback" not in m]
            return found

    return View()


def synthesis_pack():
    rows = [content_row(1, transcript=f"{PROMPT_MARKER} opening words"),
            content_row(2, title="Second video", transcript="More text")]
    return build_pack(content=rows, comments=[], interpretations=[])


def provider(timeout=TIMEOUT, **kw):
    return AnthropicProvider(KEY, model="claude-fable-5-1", timeout=timeout, **kw)


def model_reply(usage=True):
    out = output(insight(title=f"Insight {RESPONSE_MARKER}"))
    body = {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(out)}]}
    if usage:
        body["usage"] = {"input_tokens": 1234, "output_tokens": 5678, "cache_read_input_tokens": 0,
                         "note": RESPONSE_MARKER}
    return json.dumps(body).encode()


# --- success ------------------------------------------------------------------------------------------


def test_a_successful_request_logs_start_and_completion(server, logs):
    srv = server(answer(200, model_reply()))
    result = provider().synthesize_research(synthesis_pack())
    assert result.insights[0].title == f"Insight {RESPONSE_MARKER}"  # the response was really used

    start = logs.one("synthesis request start")
    assert re.search(r"started=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+00:00", start)
    assert "model=claude-fable-5-1" in start
    assert f"timeout={TIMEOUT}s" in start
    assert "streaming=off" in start
    assert f"request_bytes={len(srv.body)}" in start and len(srv.body) > 500
    assert f"max_tokens={llm.SYNTHESIS_MAX_TOKENS}" in start

    done = logs.one("synthesis request completed")
    assert "status=200" in done and re.search(r"elapsed=\d+\.\ds", done)
    assert f"timeout={TIMEOUT}s" in done
    assert "stop_reason=end_turn" in done
    assert "input_tokens=1234" in done and "output_tokens=5678" in done
    assert "cache_read_input_tokens=0" in done
    assert f"response_bytes={len(model_reply())}" in done

    timings = logs.one("transport timings")
    assert re.search(r"connect=\d\.\d\ds headers_after=\d\.\d\ds body_read=\d\.\d\ds", timings)
    assert [r.levelno for r in logs.records if "start" in r.getMessage() or "completed" in r.getMessage()] == [
        logging.INFO, logging.INFO]  # visible at the default INFO level


def test_the_request_still_carries_the_key_but_it_is_never_logged(server, logs):
    srv = server(answer(200, model_reply()))
    provider().synthesize_research(synthesis_pack())
    assert f"x-api-key: {KEY}".lower() in srv.head.lower()  # really sent...
    assert not any(KEY in t for t in logs.texts())            # ...never logged


def test_usage_is_optional_and_only_numbers_are_extracted():
    assert _usage_summary(b"not json") == "usage=unavailable"
    assert _usage_summary(b"[1,2]") == "usage=unavailable"
    assert _usage_summary(b'{"content": []}') == "usage=unavailable"
    body = json.dumps({"stop_reason": "max_tokens", "usage": {"output_tokens": 9, "input_tokens": True,
                                                              "service_tier": "secret text"}}).encode()
    assert _usage_summary(body) == "stop_reason=max_tokens output_tokens=9"


# --- failures and phases --------------------------------------------------------------------------------


def assert_failed(exc, logs, phase):
    assert type(exc) is LLMNetworkError                       # the existing contract
    assert str(exc) == "request failed: TimeoutError"         # message text unchanged
    assert exc.phase == phase and exc.elapsed is not None and exc.elapsed >= TIMEOUT - 0.05
    assert isinstance(exc.__cause__, TimeoutError)            # the original is still chained
    line = logs.one("request failed")
    assert re.search(r"elapsed=0\.[4-9]\ds", line) or re.search(r"elapsed=\d+\.\ds", line)
    assert f"timeout={TIMEOUT}s" in line
    assert "exception=LLMNetworkError" in line
    assert "message=request failed: TimeoutError" in line
    assert f"phase={phase}" in line
    assert "cause_chain=[LLMNetworkError: request failed: TimeoutError <- TimeoutError: timed out]" in line
    [tb] = [r for r in logs.records if "failure traceback" in r.getMessage()]
    assert tb.levelno == logging.DEBUG  # the traceback is a DEBUG-level record
    text = tb.getMessage()
    assert "Traceback (most recent call last)" in text and "LLMNetworkError: request failed: TimeoutError" in text
    assert "TimeoutError: timed out" in text  # the original exception is in the traceback chain
    [err] = [r for r in logs.records if r.getMessage().startswith("Anthropic synthesis request failed:")]
    assert err.levelno == logging.ERROR


def test_a_timeout_while_waiting_for_the_response_is_logged_with_its_phase(server, logs):
    server(stall_after_reading)
    with pytest.raises(LLMNetworkError) as info:
        provider().synthesize_research(synthesis_pack())
    assert_failed(info.value, logs, PHASE_WAITING)
    assert not any("completed" in m for m in logs.messages)


def test_a_timeout_while_reading_the_body_is_told_apart_from_waiting(server, logs):
    server(stall_in_body)
    with pytest.raises(LLMNetworkError) as info:
        provider().synthesize_research(synthesis_pack())
    assert_failed(info.value, logs, PHASE_BODY)


def test_a_timeout_reading_an_error_body_is_a_network_error_not_a_bare_timeout(server, logs):
    server(stall_in_error_body)
    with pytest.raises(LLMNetworkError) as info:  # previously this escaped as a raw TimeoutError
        provider().synthesize_research(synthesis_pack())
    assert_failed(info.value, logs, PHASE_ERROR_BODY)


def test_a_refused_connection_is_the_connecting_phase(logs, monkeypatch):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    monkeypatch.setattr(llm, "ANTHROPIC_URL", f"http://127.0.0.1:{port}/v1/messages")
    with pytest.raises(LLMNetworkError) as info:
        provider().synthesize_research(synthesis_pack())
    exc = info.value
    assert type(exc) is LLMNetworkError and exc.phase == PHASE_CONNECTING
    assert str(exc).startswith("request failed: ")
    line = logs.one("request failed")
    assert f"phase={PHASE_CONNECTING}" in line
    assert "exception=LLMNetworkError" in line
    assert "<- ConnectionRefusedError: [Errno" in line  # urllib's `.reason` is followed


def test_an_http_error_status_keeps_its_api_error_contract_and_logs_only_the_status(server, logs):
    body = json.dumps({"error": {"type": "invalid_request_error", "message": f"bad {ERROR_BODY_MARKER}"}}).encode()
    server(answer(400, body, "Bad Request"))
    with pytest.raises(LLMAPIError) as info:
        provider().synthesize_research(synthesis_pack())
    assert info.value.status == 400
    assert f"Anthropic API error (HTTP 400): bad {ERROR_BODY_MARKER}" == str(info.value)  # unchanged
    warning = logs.one("returned HTTP 400")
    assert "elapsed=" in warning and f"response_bytes={len(body)}" in warning
    assert not any(ERROR_BODY_MARKER in t for t in logs.texts())  # the body is not logged


# --- nothing sensitive is ever logged --------------------------------------------------------------------


def test_no_secret_prompt_evidence_or_response_body_appears_in_any_log(server, logs):
    forbidden = [KEY, PROMPT_MARKER, RESPONSE_MARKER, ERROR_BODY_MARKER, "Evidence pack (JSON)",
                 "untrusted", "Authorization", "opening words", "Making Tax Digital"]
    scenarios = [
        (answer(200, model_reply()), None),
        (stall_after_reading, LLMNetworkError),
        (stall_in_body, LLMNetworkError),
        (answer(529, json.dumps({"error": {"message": ERROR_BODY_MARKER}}).encode(), "Overloaded"), LLMAPIError),
    ]
    for behaviour, expected in scenarios:
        server(behaviour)
        if expected is None:
            provider().synthesize_research(synthesis_pack())
        else:
            with pytest.raises(expected):
                provider().synthesize_research(synthesis_pack())
    assert len(logs.records) >= 10
    for text in logs.texts():
        for needle in forbidden:
            assert needle not in text, f"{needle!r} leaked into a log: {text[:200]}"
    # header names never appear in the summary lines (traceback source lines are not summaries)
    assert not any("x-api-key" in m.lower() for m in logs.messages)


def test_a_key_inside_an_exception_message_is_redacted(logs):
    def leaky(url, headers, body):
        raise RuntimeError(f"connection to host failed while using {KEY}")

    p = AnthropicProvider(KEY, model="m", timeout=5, transport=leaky)
    with pytest.raises(RuntimeError) as info:
        p.synthesize_research(synthesis_pack())
    assert KEY in str(info.value)  # the exception itself is untouched...
    assert not any(KEY in t for t in logs.texts())  # ...but the logs, tracebacks included, are redacted
    assert "***" in logs.one("request failed")
    [tb] = [m for m in logs.messages if "failure traceback" in m]
    assert "RuntimeError: connection to host failed while using ***" in tb


def test_cause_chain_lists_every_cause_once_and_redacts_the_key():
    try:
        try:
            try:
                raise TimeoutError("timed out")
            except TimeoutError as a:
                raise OSError(f"socket {KEY}") from a
        except OSError as b:
            raise LLMNetworkError("request failed: OSError") from b
    except LLMNetworkError as top:
        chain = _cause_chain(top, KEY)
    assert chain == ["LLMNetworkError: request failed: OSError", "OSError: socket ***", "TimeoutError: timed out"]
    import urllib.error

    wrapped = urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
    assert _cause_chain(wrapped) == [
        "URLError: <urlopen error [Errno 61] Connection refused>",
        "ConnectionRefusedError: [Errno 61] Connection refused"]
    assert _cause_chain(ValueError("solo")) == ["ValueError: solo"]
    loop = ValueError("a")
    loop.__cause__ = loop
    assert _cause_chain(loop) == ["ValueError: a"]  # a cycle cannot hang the logger


# --- the existing contract and configuration are unchanged ---------------------------------------------------


def test_an_injected_transport_error_propagates_unchanged_and_is_logged(logs):
    error = LLMNetworkError("down")

    def down(url, headers, body):
        raise error

    with pytest.raises(LLMNetworkError) as info:
        AnthropicProvider(KEY, model="m", timeout=7, transport=down).synthesize_research(synthesis_pack())
    assert info.value is error and str(error) == "down" and error.phase is None
    line = logs.one("request failed")
    assert "phase=unknown" in line and "timeout=7s" in line and "message=down" in line


def test_unexpected_exceptions_keep_their_type_and_are_logged(logs):
    def boom(url, headers, body):
        raise RuntimeError("unexpected")

    with pytest.raises(RuntimeError, match="unexpected"):
        AnthropicProvider(KEY, model="m", transport=boom).synthesize_research(synthesis_pack())
    assert "exception=RuntimeError" in logs.one("request failed")


def test_the_timeout_and_token_settings_are_unchanged_by_this_change():
    assert AnthropicProvider.for_synthesis(Settings(llm_api_key="k"))._timeout == 180.0
    assert AnthropicProvider.from_settings(Settings(llm_api_key="k"))._timeout == 60.0
    assert llm.SYNTHESIS_MAX_TOKENS == 16000


def test_classification_requests_log_at_debug_only_but_failures_always(caplog, logs):
    ok = json.dumps({"content": [{"type": "tool_use", "name": "record_classification", "input": {
        k: {"value": None, "confidence": None, "evidence": None}
        for k in ("topic", "audience", "pain_point", "hook", "hook_type", "format", "emotion", "cta")}}]}).encode()
    p = AnthropicProvider(KEY, model="m", transport=lambda u, h, b: (200, ok))
    p.classify_content(ClassificationInput("dQw4w9WgXcQ", "Title", None, "Hello"))
    starts = [r for r in logs.records if "classification request" in r.getMessage()]
    assert starts and all(r.levelno == logging.DEBUG for r in starts)  # quiet at the default INFO level

    def down(url, headers, body):
        raise LLMNetworkError("down")

    with pytest.raises(LLMNetworkError):
        AnthropicProvider(KEY, model="m", transport=down).classify_content(
            ClassificationInput("dQw4w9WgXcQ", "Title", None, "Hello"))
    failure = [r for r in logs.records if "classification request failed" in r.getMessage()]
    assert failure and failure[0].levelno == logging.ERROR


def test_the_real_transport_still_speaks_plain_urllib_semantics(server):
    srv = server(answer(200, b'{"ok": true}'))
    status, body = llm._urllib_transport(2.0)(srv.url, {"content-type": "application/json", "x-test": "1"}, b"{}")
    assert (status, body) == (200, b'{"ok": true}')
    assert "POST /v1/messages" in srv.head and "x-test: 1" in srv.head.lower() and srv.body == b"{}"
    server(answer(503, b'{"error": "busy"}', "Service Unavailable"))
    status, body = llm._urllib_transport(2.0)(llm.ANTHROPIC_URL, {}, b"{}")
    assert (status, body) == (503, b'{"error": "busy"}')  # HTTP errors are still returned, not raised
