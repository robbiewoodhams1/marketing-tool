import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from research.metadata import YouTubeMetadata, YouTubeVideoMetadata
from research.youtube import (
    YouTubeAuthError,
    YouTubeClient,
    YouTubeConfigError,
    YouTubeNetworkError,
    YouTubeQuotaError,
    YouTubeResponseError,
)

KEY = "test-key-not-real"


def api_item(video_id, **overrides):
    item = {
        "kind": "youtube#video",
        "etag": "abc",
        "id": video_id,
        "snippet": {
            "publishedAt": "2024-03-05T14:30:00Z",
            "channelId": "UC123",
            "title": f"Title &amp; {video_id}",
            "description": "A description",
            "channelTitle": "Some Channel",
        },
        "contentDetails": {"duration": "PT1H2M3S", "dimension": "2d"},
        "statistics": {"viewCount": "1500", "likeCount": "40", "commentCount": "7"},
    }
    item.update(overrides)
    return item


class FakeTransport:
    """Answers videos.list from a catalogue, like YouTube: unknown ids are omitted."""

    def __init__(self, catalogue=None, response=None):
        self.catalogue = catalogue if catalogue is not None else {}
        self.response = response
        self.urls = []

    def requested_ids(self, i):
        return parse_qs(urlparse(self.urls[i]).query)["id"][0].split(",")

    def __call__(self, url):
        self.urls.append(url)
        if isinstance(self.response, Exception):
            raise self.response
        if self.response is not None:
            return self.response
        ids = parse_qs(urlparse(url).query)["id"][0].split(",")
        items = [self.catalogue[i] for i in ids if i in self.catalogue]
        return 200, json.dumps({"kind": "youtube#videoListResponse", "items": items}).encode()


def setup(*ids, **kw):
    transport = FakeTransport({i: api_item(i) for i in ids}, **kw)
    return YouTubeMetadata(YouTubeClient(KEY, transport=transport)), transport


def test_single_video_maps_all_fields():
    meta, t = setup("abc123")
    [v] = meta.get_metadata(["abc123"])
    assert v == YouTubeVideoMetadata(
        video_id="abc123",
        url="https://www.youtube.com/watch?v=abc123",
        title="Title & abc123",
        channel_title="Some Channel",
        published_at=datetime(2024, 3, 5, 14, 30, tzinfo=timezone.utc),
        description="A description",
        duration=timedelta(hours=1, minutes=2, seconds=3),
        view_count=1500,
        like_count=40,
        comment_count=7,
    )
    q = parse_qs(urlparse(t.urls[0]).query)
    assert urlparse(t.urls[0]).path == "/youtube/v3/videos"
    assert q["part"] == ["snippet,contentDetails,statistics"]
    assert q["key"] == [KEY]


def test_multiple_videos_use_one_request():
    meta, t = setup("a", "b", "c")
    assert len(meta.get_metadata(["a", "b", "c"])) == 3
    assert len(t.urls) == 1
    assert t.requested_ids(0) == ["a", "b", "c"]


def test_input_order_preserved_regardless_of_api_order():
    meta, t = setup("a", "b", "c")
    # API returns in its own (reversed) order
    t.catalogue = dict(reversed(list(t.catalogue.items())))
    assert [v.video_id for v in meta.get_metadata(["b", "c", "a"])] == ["b", "c", "a"]


def test_duplicates_and_whitespace_are_collapsed():
    meta, t = setup("a", "b")
    result = meta.get_metadata([" a ", "b", "a", "b "])
    assert [v.video_id for v in result] == ["a", "b"]
    assert t.requested_ids(0) == ["a", "b"]


def test_empty_input_makes_no_request():
    meta, t = setup()
    assert meta.get_metadata([]) == []
    assert t.urls == []


def test_blank_id_rejected_before_any_request():
    meta, t = setup("a")
    with pytest.raises(ValueError):
        meta.get_metadata(["a", "   "])
    assert t.urls == []


def test_unavailable_videos_are_omitted_not_invented(caplog):
    meta, _ = setup("a", "c")
    with caplog.at_level(logging.INFO, logger="research"):
        result = meta.get_metadata(["a", "gone", "c"])
    assert [v.video_id for v in result] == ["a", "c"]
    assert "1 requested videos were unavailable" in caplog.text


def test_all_unavailable_returns_empty_list():
    meta, _ = setup()
    assert meta.get_metadata(["gone"]) == []


def test_missing_statistics_are_none_not_zero():
    item = api_item("a", statistics={"viewCount": "0"})  # likes hidden, comments off
    meta = YouTubeMetadata(YouTubeClient(KEY, transport=FakeTransport({"a": item})))
    [v] = meta.get_metadata(["a"])
    assert v.view_count == 0
    assert v.like_count is None
    assert v.comment_count is None


def test_missing_statistics_block_and_content_details():
    item = api_item("a")
    del item["statistics"], item["contentDetails"]
    del item["snippet"]["description"]
    meta = YouTubeMetadata(YouTubeClient(KEY, transport=FakeTransport({"a": item})))
    [v] = meta.get_metadata(["a"])
    assert (v.view_count, v.like_count, v.comment_count) == (None, None, None)
    assert v.duration is None and v.description is None


@pytest.mark.parametrize(
    "iso, expected",
    [
        ("PT45S", timedelta(seconds=45)),
        ("PT5M", timedelta(minutes=5)),
        ("PT1H", timedelta(hours=1)),
        ("PT1H2M3S", timedelta(hours=1, minutes=2, seconds=3)),
        ("P1DT2H", timedelta(days=1, hours=2)),
        ("P0D", timedelta(0)),
        ("PT0S", timedelta(0)),
    ],
)
def test_duration_parsing(iso, expected):
    item = api_item("a", contentDetails={"duration": iso})
    meta = YouTubeMetadata(YouTubeClient(KEY, transport=FakeTransport({"a": item})))
    assert meta.get_metadata(["a"])[0].duration == expected


def test_published_timestamp_is_timezone_aware_utc():
    item = api_item("a")
    item["snippet"]["publishedAt"] = "2023-12-31T23:59:59Z"
    meta = YouTubeMetadata(YouTubeClient(KEY, transport=FakeTransport({"a": item})))
    published = meta.get_metadata(["a"])[0].published_at
    assert published == datetime(2023, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    assert published.utcoffset() == timedelta(0)


def test_large_input_split_into_batches_of_50():
    ids = [f"v{i:03d}" for i in range(120)]
    meta, t = setup(*ids)
    result = meta.get_metadata(ids)
    assert [v.video_id for v in result] == ids
    assert len(t.urls) == 3
    assert [len(t.requested_ids(i)) for i in range(3)] == [50, 50, 20]


def test_exactly_50_ids_is_one_batch():
    ids = [f"v{i}" for i in range(50)]
    meta, t = setup(*ids)
    meta.get_metadata(ids)
    assert len(t.urls) == 1


def test_quota_error_propagates():
    body = {"error": {"code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}}
    meta, _ = setup(response=(403, json.dumps(body).encode()))
    with pytest.raises(YouTubeQuotaError):
        meta.get_metadata(["a"])


def test_auth_error_propagates():
    body = {"error": {"code": 400, "message": "API key not valid.", "errors": [{"reason": "badRequest"}]}}
    meta, _ = setup(response=(400, json.dumps(body).encode()))
    with pytest.raises(YouTubeAuthError):
        meta.get_metadata(["a"])


def test_network_error_propagates():
    meta, _ = setup(response=YouTubeNetworkError("down"))
    with pytest.raises(YouTubeNetworkError):
        meta.get_metadata(["a"])


def test_missing_api_key_raises_config_error():
    meta = YouTubeMetadata(YouTubeClient(None, transport=FakeTransport()))
    with pytest.raises(YouTubeConfigError):
        meta.get_metadata(["a"])


@pytest.mark.parametrize(
    "response",
    [
        (200, b"not json"),
        (200, json.dumps({"kind": "x"}).encode()),
        (200, json.dumps({"items": [{"id": "a"}]}).encode()),
        (200, json.dumps({"items": [api_item("a", statistics={"viewCount": "many"})]}).encode()),
        (200, json.dumps({"items": [api_item("a", contentDetails={"duration": "1:02:03"})]}).encode()),
    ],
)
def test_malformed_responses_raise_response_error(response):
    meta, _ = setup(response=response)
    with pytest.raises(YouTubeResponseError):
        meta.get_metadata(["a"])


def test_logging_never_contains_api_key(caplog):
    with caplog.at_level(logging.INFO, logger="research"):
        setup("a")[0].get_metadata(["a"])
    assert "returned 1 videos" in caplog.text
    assert KEY not in caplog.text
