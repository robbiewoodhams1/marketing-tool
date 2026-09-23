from datetime import datetime, timezone

import pytest

from research.search import MAX_QUERY_LENGTH, YouTubeSearch
from research.youtube import YouTubeQuotaError, YouTubeSearchResult


def video(video_id):
    return YouTubeSearchResult(
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        title=f"Title {video_id}",
        channel_id="UC1",
        channel_title="Channel",
        published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


class FakeClient:
    def __init__(self, results=(), error=None):
        self.results, self.error, self.calls = list(results), error, []

    def search_videos(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if self.error:
            raise self.error
        return self.results


def test_valid_query_calls_client_and_passes_results_through():
    client = FakeClient([video("a"), video("b")])
    results = YouTubeSearch(client).search("  email marketing  ", max_candidates=5)
    assert results == [video("a"), video("b")]
    assert len(client.calls) == 1
    query, kwargs = client.calls[0]
    assert query == "email marketing"
    assert kwargs["max_results"] == 5


def test_empty_results_give_empty_list():
    assert YouTubeSearch(FakeClient([])).search("q") == []


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
def test_blank_query_rejected_without_calling_client(query):
    client = FakeClient([video("a")])
    with pytest.raises(ValueError):
        YouTubeSearch(client).search(query)
    assert client.calls == []


def test_overlong_query_rejected():
    client = FakeClient()
    with pytest.raises(ValueError):
        YouTubeSearch(client).search("x" * (MAX_QUERY_LENGTH + 1))
    assert client.calls == []
    YouTubeSearch(client).search("x" * MAX_QUERY_LENGTH)  # boundary is allowed


@pytest.mark.parametrize("limit", [0, -1, 51])
def test_invalid_candidate_limit_rejected_without_calling_client(limit):
    client = FakeClient()
    with pytest.raises(ValueError):
        YouTubeSearch(client).search("q", max_candidates=limit)
    assert client.calls == []


def test_candidate_limit_bounds_accepted():
    client = FakeClient()
    search = YouTubeSearch(client)
    search.search("q", max_candidates=1)
    search.search("q", max_candidates=50)
    assert [c[1]["max_results"] for c in client.calls] == [1, 50]


def test_date_filters_passed_through_unchanged():
    client = FakeClient()
    after = datetime(2024, 1, 1, tzinfo=timezone.utc)
    before = datetime(2024, 6, 1)  # naive: normalisation is the client's job
    YouTubeSearch(client).search("q", published_after=after, published_before=before)
    kwargs = client.calls[0][1]
    assert kwargs["published_after"] is after
    assert kwargs["published_before"] is before


def test_duplicates_removed_preserving_first_seen_order():
    client = FakeClient([video("b"), video("a"), video("b"), video("c"), video("a")])
    results = YouTubeSearch(client).search("q")
    assert [v.video_id for v in results] == ["b", "a", "c"]


def test_client_errors_propagate():
    client = FakeClient(error=YouTubeQuotaError("quota"))
    with pytest.raises(YouTubeQuotaError):
        YouTubeSearch(client).search("q")
