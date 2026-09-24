import json
import logging
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from research.comments import YouTubeComment, YouTubeComments
from research.youtube import (
    YouTubeAPIError,
    YouTubeAuthError,
    YouTubeClient,
    YouTubeCommentsUnavailableError,
    YouTubeNetworkError,
    YouTubeQuotaError,
    YouTubeResponseError,
)

KEY = "test-key-not-real"


def thread(video_id, n, **snippet):
    top = {
        "videoId": video_id,
        "textDisplay": f"comment {n} on {video_id}",
        "textOriginal": "SHOULD NOT BE USED",
        "authorDisplayName": "Some Person",
        "likeCount": n,
        "publishedAt": "2024-03-05T14:30:00Z",
        "updatedAt": "2024-03-06T09:00:00Z",
    }
    top.update(snippet)
    return {
        "kind": "youtube#commentThread",
        "id": f"{video_id}-c{n}",
        "snippet": {
            "videoId": video_id,
            "totalReplyCount": 2,
            "topLevelComment": {"id": f"{video_id}-c{n}", "snippet": top},
        },
        "replies": {
            "comments": [{"id": f"{video_id}-c{n}.r1", "snippet": {"textDisplay": "a reply"}}]
        },
    }


def error_body(status, reason, message="msg"):
    body = {"error": {"code": status, "message": message, "errors": [{"reason": reason}]}}
    return status, json.dumps(body).encode()


class FakeTransport:
    """Serves commentThreads.list pages of up to maxResults from per-video lists.

    `videos` maps id -> list of thread items, or an (status, body) error tuple.
    Page tokens are just the offset, so pagination is exercised for real.
    """

    def __init__(self, videos=None, response=None):
        self.videos = videos or {}
        self.response = response
        self.urls = []

    def query(self, i):
        return {k: v[0] for k, v in parse_qs(urlparse(self.urls[i]).query).items()}

    def __call__(self, url):
        self.urls.append(url)
        if isinstance(self.response, Exception):
            raise self.response
        if self.response is not None:
            return self.response
        q = self.query(len(self.urls) - 1)
        data = self.videos.get(q["videoId"], error_body(404, "videoNotFound"))
        if isinstance(data, tuple):
            return data
        start = int(q.get("pageToken", 0))
        end = start + int(q["maxResults"])
        body = {"items": data[start:end]}
        if end < len(data):
            body["nextPageToken"] = str(end)
        return 200, json.dumps(body).encode()


def setup(videos=None, **kw):
    transport = FakeTransport(videos, **kw)
    return YouTubeComments(YouTubeClient(KEY, transport=transport)), transport


def threads(video_id, count):
    return [thread(video_id, n) for n in range(1, count + 1)]


def test_single_comment_maps_fields_and_request():
    comments, t = setup({"v": [thread("v", 12)]})
    [c] = comments.get_comments(["v"])
    assert c == YouTubeComment(
        video_id="v",
        comment_id="v-c12",
        text="comment 12 on v",
        published_at=datetime(2024, 3, 5, 14, 30, tzinfo=timezone.utc),
        like_count=12,
        updated_at=datetime(2024, 3, 6, 9, 0, tzinfo=timezone.utc),
        type="top_level",
    )
    assert not hasattr(c, "author_name")
    assert c.published_at.tzinfo is not None and c.updated_at.tzinfo is not None
    q = t.query(0)
    assert urlparse(t.urls[0]).path == "/youtube/v3/commentThreads"
    assert q["part"] == "snippet"  # no replies requested
    assert q["textFormat"] == "plainText"
    assert q["key"] == KEY


def test_multiple_comments_keep_youtube_order_not_sorted_by_likes():
    comments, _ = setup({"v": [thread("v", 3), thread("v", 9), thread("v", 1)]})
    assert [c.like_count for c in comments.get_comments(["v"])] == [3, 9, 1]


def test_multiple_videos_in_input_order_each_associated():
    comments, _ = setup({"a": threads("a", 2), "b": threads("b", 2), "c": threads("c", 1)})
    result = comments.get_comments(["c", "a", "b"])
    assert [(c.video_id, c.comment_id) for c in result] == [
        ("c", "c-c1"), ("a", "a-c1"), ("a", "a-c2"), ("b", "b-c1"), ("b", "b-c2"),
    ]


def test_duplicates_and_whitespace_collapsed():
    comments, t = setup({"a": threads("a", 1), "b": threads("b", 1)})
    result = comments.get_comments([" a ", "b", "a", "b\n"])
    assert [c.video_id for c in result] == ["a", "b"]
    assert len(t.urls) == 2
    assert t.query(0)["videoId"] == "a"


def test_empty_input_makes_no_request():
    comments, t = setup()
    assert comments.get_comments([]) == []
    assert t.urls == []


@pytest.mark.parametrize("bad", ["", "  "])
def test_blank_id_rejected_before_any_request(bad):
    comments, t = setup({"a": threads("a", 1)})
    with pytest.raises(ValueError):
        comments.get_comments(["a", bad])
    assert t.urls == []


@pytest.mark.parametrize("bad", [0, -1, 1001])
def test_invalid_maximum_rejected(bad):
    comments, t = setup({"a": threads("a", 1)})
    with pytest.raises(ValueError):
        comments.get_comments(["a"], max_comments_per_video=bad)
    assert t.urls == []


def test_invalid_order_rejected():
    comments, _ = setup({"a": threads("a", 1)})
    with pytest.raises(ValueError):
        comments.get_comments(["a"], order="likes")


def test_order_is_passed_through():
    comments, t = setup({"a": threads("a", 1)})
    comments.get_comments(["a"], order="time")
    assert t.query(0)["order"] == "time"


def test_comments_disabled_yields_nothing_and_batch_continues(caplog):
    comments, _ = setup(
        {
            "a": threads("a", 1),
            "b": error_body(403, "commentsDisabled", "disabled comments"),
            "c": threads("c", 1),
        }
    )
    with caplog.at_level(logging.WARNING, logger="research.youtube"):
        result = comments.get_comments(["a", "b", "c"])
    assert [c.video_id for c in result] == ["a", "c"]
    assert "unavailable for b" in caplog.text
    assert KEY not in caplog.text


def test_client_raises_specific_error_for_disabled_comments():
    client = YouTubeClient(KEY, transport=FakeTransport({"b": error_body(403, "commentsDisabled")}))
    with pytest.raises(YouTubeCommentsUnavailableError):
        client.get_comment_threads("b")


def test_video_not_found_yields_nothing():
    comments, _ = setup({})
    assert comments.get_comments(["gone"]) == []


def test_no_comments_yields_nothing():
    comments, t = setup({"a": []})
    assert comments.get_comments(["a"]) == []
    assert len(t.urls) == 1


def test_pagination_250_is_100_100_50():
    comments, t = setup({"v": threads("v", 400)})
    result = comments.get_comments(["v"], max_comments_per_video=250)
    assert len(result) == 250
    assert [int(t.query(i)["maxResults"]) for i in range(len(t.urls))] == [100, 100, 50]
    assert "pageToken" not in t.query(0)
    assert t.query(1)["pageToken"] == "100"
    assert [c.comment_id for c in result] == [f"v-c{n}" for n in range(1, 251)]


def test_stops_exactly_at_maximum_without_extra_request():
    comments, t = setup({"v": threads("v", 300)})
    assert len(comments.get_comments(["v"], max_comments_per_video=100)) == 100
    assert len(t.urls) == 1


def test_stops_when_youtube_has_no_more_pages():
    comments, t = setup({"v": threads("v", 130)})
    assert len(comments.get_comments(["v"], max_comments_per_video=500)) == 130
    assert len(t.urls) == 2


def test_extra_items_beyond_cap_are_dropped():
    class Greedy(FakeTransport):
        def __call__(self, url):
            self.urls.append(url)
            return 200, json.dumps({"items": threads("v", 10), "nextPageToken": "x"}).encode()

    comments = YouTubeComments(YouTubeClient(KEY, transport=Greedy()))
    assert len(comments.get_comments(["v"], max_comments_per_video=4)) == 4


def test_default_maximum_is_100():
    comments, t = setup({"v": threads("v", 300)})
    assert len(comments.get_comments(["v"])) == 100


def test_html_entities_decoded():
    comments, _ = setup({"v": [thread("v", 1, textDisplay="Tom &amp; Jerry&#39;s &quot;tips&quot;")]})
    assert comments.get_comments(["v"])[0].text == "Tom & Jerry's \"tips\""


def test_text_preserved_verbatim():
    raw = "  wot a   load of  RUBBISH lol!!  \nsecond line 😂 "
    comments, _ = setup({"v": [thread("v", 1, textDisplay=raw)]})
    assert comments.get_comments(["v"])[0].text == raw


def test_replies_are_not_returned():
    comments, _ = setup({"v": [thread("v", 1)]})
    result = comments.get_comments(["v"])
    assert [c.comment_id for c in result] == ["v-c1"]
    assert all(c.type == "top_level" for c in result)


def test_missing_optional_fields_are_none():
    item = thread("v", 1)
    snippet = item["snippet"]["topLevelComment"]["snippet"]
    del snippet["likeCount"], snippet["updatedAt"], snippet["videoId"]
    comments, _ = setup({"v": [item]})
    [c] = comments.get_comments(["v"])
    assert c.like_count is None and c.updated_at is None and c.video_id == "v"


def test_zero_likes_is_zero_not_none():
    comments, _ = setup({"v": [thread("v", 1, likeCount=0)]})
    assert comments.get_comments(["v"])[0].like_count == 0


@pytest.mark.parametrize(
    "body",
    [
        {"nope": []},
        {"items": "x"},
        {"items": [{"snippet": {}}]},
        {"items": [thread("v", 1)], "nextPageToken": 5},
        {"items": [thread("v", 1, publishedAt="not a date")]},
        {"items": [thread("v", 1, textDisplay=None)]},
    ],
)
def test_malformed_response(body):
    comments, _ = setup(response=(200, json.dumps(body).encode()))
    with pytest.raises(YouTubeResponseError):
        comments.get_comments(["v"])


def test_non_json_response():
    comments, _ = setup(response=(200, b"<html>"))
    with pytest.raises(YouTubeResponseError):
        comments.get_comments(["v"])


def test_quota_error_propagates():
    comments, _ = setup({"v": error_body(403, "quotaExceeded")})
    with pytest.raises(YouTubeQuotaError):
        comments.get_comments(["v"])


def test_auth_error_propagates():
    comments, _ = setup({"v": error_body(400, "keyInvalid", "API key not valid")})
    with pytest.raises(YouTubeAuthError):
        comments.get_comments(["v"])


def test_other_api_error_propagates_with_status():
    comments, _ = setup({"v": error_body(500, "backendError")})
    with pytest.raises(YouTubeAPIError) as info:
        comments.get_comments(["v"])
    assert info.value.status == 500


def test_network_error_propagates_and_stops_batch():
    comments, t = setup(response=YouTubeNetworkError("down"))
    with pytest.raises(YouTubeNetworkError):
        comments.get_comments(["a", "b"])
    assert len(t.urls) == 1


def test_error_on_later_page_is_not_swallowed():
    class Flaky(FakeTransport):
        def __call__(self, url):
            if len(self.urls) == 1:
                self.urls.append(url)
                return error_body(403, "quotaExceeded")
            return super().__call__(url)

    comments = YouTubeComments(YouTubeClient(KEY, transport=Flaky({"v": threads("v", 300)})))
    with pytest.raises(YouTubeQuotaError):
        comments.get_comments(["v"], max_comments_per_video=200)


def test_missing_api_key():
    from research.youtube import YouTubeConfigError

    with pytest.raises(YouTubeConfigError):
        YouTubeComments(YouTubeClient(None, transport=FakeTransport())).get_comments(["v"])
