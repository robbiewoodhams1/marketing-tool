import json
import logging
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from research.config import Settings
from research.youtube import (
    YouTubeAPIError,
    YouTubeAuthError,
    YouTubeClient,
    YouTubeConfigError,
    YouTubeNetworkError,
    YouTubeQuotaError,
    YouTubeResponseError,
)

KEY = "test-key-not-real"


def item(video_id, title, channel="Some Channel", published="2024-03-05T14:30:00Z"):
    return {
        "kind": "youtube#searchResult",
        "etag": "abc",
        "id": {"kind": "youtube#video", "videoId": video_id},
        "snippet": {
            "publishedAt": published,
            "channelId": "UC123",
            "title": title,
            "description": "ignored",
            "channelTitle": channel,
            "liveBroadcastContent": "none",
        },
    }


def ok(items):
    return 200, json.dumps({"kind": "youtube#searchListResponse", "items": items}).encode()


def error(status, message, reason):
    body = {"error": {"code": status, "message": message,
                      "errors": [{"reason": reason}]}}
    return status, json.dumps(body).encode()


class FakeTransport:
    def __init__(self, response):
        self.response, self.urls = response, []

    def __call__(self, url):
        self.urls.append(url)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def client(response):
    transport = FakeTransport(response)
    return YouTubeClient(KEY, transport=transport), transport


def test_successful_search_maps_fields():
    c, _ = client(ok([item("dQw4w9WgXcQ", "Never &amp; Gonna &#39;Give&#39; Up")]))
    [r] = c.search_videos("rick")
    assert r.video_id == "dQw4w9WgXcQ"
    assert r.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert r.title == "Never & Gonna 'Give' Up"
    assert r.channel_id == "UC123"
    assert r.channel_title == "Some Channel"
    assert r.published_at == datetime(2024, 3, 5, 14, 30, tzinfo=timezone.utc)


def test_multiple_results_preserve_order():
    c, _ = client(ok([item("a", "A"), item("b", "B"), item("c", "C")]))
    assert [r.video_id for r in c.search_videos("q", max_results=3)] == ["a", "b", "c"]


def test_empty_results():
    c, _ = client(ok([]))
    assert c.search_videos("nothing") == []


def test_request_params():
    c, t = client(ok([]))
    c.search_videos(
        "pain points",
        max_results=5,
        published_after=datetime(2024, 1, 1, tzinfo=timezone.utc),
        published_before=datetime(2024, 2, 1),  # naive -> treated as UTC
    )
    parsed = urlparse(t.urls[0])
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert parsed.path == "/youtube/v3/search"
    assert q["q"] == "pain points"
    assert q["type"] == "video" and q["part"] == "snippet"
    assert q["maxResults"] == "5"
    assert q["publishedAfter"] == "2024-01-01T00:00:00Z"
    assert q["publishedBefore"] == "2024-02-01T00:00:00Z"
    assert q["key"] == KEY


def test_missing_api_key_fails_on_use_not_construction():
    c = YouTubeClient.from_settings(Settings.from_env({}), transport=FakeTransport(ok([])))
    with pytest.raises(YouTubeConfigError):
        c.search_videos("q")


def test_settings_key_is_used():
    s = Settings.from_env({"YOUTUBE_API_KEY": KEY})
    t = FakeTransport(ok([]))
    YouTubeClient.from_settings(s, transport=t).search_videos("q")
    assert f"key={KEY}" in t.urls[0]


@pytest.mark.parametrize("bad", [dict(max_results=0), dict(max_results=51)])
def test_invalid_max_results(bad):
    c, _ = client(ok([]))
    with pytest.raises(ValueError):
        c.search_videos("q", **bad)


def test_blank_query_rejected():
    c, _ = client(ok([]))
    with pytest.raises(ValueError):
        c.search_videos("  ")


def test_invalid_key_is_auth_error():
    c, _ = client(error(400, "API key not valid. Please pass a valid API key.", "badRequest"))
    with pytest.raises(YouTubeAuthError):
        c.search_videos("q")


def test_forbidden_is_auth_error():
    c, _ = client(error(403, "Access Not Configured", "accessNotConfigured"))
    with pytest.raises(YouTubeAuthError):
        c.search_videos("q")


def test_quota_exceeded_is_quota_error():
    c, _ = client(error(403, "The request cannot be completed because you have "
                             "exceeded your quota.", "quotaExceeded"))
    with pytest.raises(YouTubeQuotaError):
        c.search_videos("q")


def test_http_429_is_quota_error():
    c, _ = client((429, b"Too Many Requests"))
    with pytest.raises(YouTubeQuotaError):
        c.search_videos("q")


def test_server_error_is_api_error_with_status():
    c, _ = client(error(500, "Backend Error", "backendError"))
    with pytest.raises(YouTubeAPIError) as exc:
        c.search_videos("q")
    assert exc.value.status == 500


def test_non_json_error_body_is_api_error():
    c, _ = client((502, b"<html>Bad gateway</html>"))
    with pytest.raises(YouTubeAPIError):
        c.search_videos("q")


def test_network_failure_propagates():
    c, _ = client(YouTubeNetworkError("Could not reach YouTube API: TimeoutError"))
    with pytest.raises(YouTubeNetworkError):
        c.search_videos("q")


@pytest.mark.parametrize(
    "response",
    [
        (200, b"not json"),
        (200, b"[]"),
        (200, json.dumps({"kind": "x"}).encode()),
        (200, json.dumps({"items": "nope"}).encode()),
        ok([{"id": {"kind": "youtube#channel", "channelId": "UC1"},
             "snippet": item("x", "t")["snippet"]}]),
        ok([{"id": {"videoId": "x"}}]),
        ok([item("x", "t", published="yesterday")]),
    ],
)
def test_malformed_responses(response):
    c, _ = client(response)
    with pytest.raises(YouTubeResponseError):
        c.search_videos("q")


def test_logging_never_contains_api_key(caplog):
    with caplog.at_level(logging.INFO, logger="research"):
        client(ok([item("a", "A")]))[0].search_videos("q")
        with pytest.raises(YouTubeAuthError):
            client(error(400, "API key not valid", "badRequest"))[0].search_videos("q")
    assert "started" in caplog.text and "returned 1 results" in caplog.text
    assert "failed" in caplog.text
    assert KEY not in caplog.text
