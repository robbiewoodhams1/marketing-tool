import json
import random

import pytest
from synthesis_fixtures import (
    build_pack,
    content_row,
    interpretation_row,
    result,
    standard_comments,
    standard_content,
    standard_interpretations,
)

from research.classification import FieldResult
from research.evidence_pack import (
    MIN_CONTENT_FOR_SYNTHESIS,
    EvidenceTooLargeError,
    InsufficientEvidenceError,
    PackBounds,
    PackLevel,
)


def by_ref(pack, ref):
    return next(c for c in pack.prompt["content"] if c["ref"] == ref)


# --- content selection, references, determinism ----------------------------------------------


def test_every_content_item_is_included_with_core_metadata():
    pack = build_pack()
    assert [c["ref"] for c in pack.prompt["content"]] == ["C1", "C2", "C3", "C4"]
    c1 = by_ref(pack, "C1")
    assert c1["title"] == "MTD for sole traders: 5 things to know"
    assert c1["creator"] == "AccountsCo"
    assert c1["url"].endswith("vid00000001")
    assert c1["published_at"] == "2026-01-01T00:00:00+00:00"
    assert c1["raw_metrics"] == {"views": 1200, "likes": 60, "comments_count": 10,
                                 "shares": None, "saves": None}
    assert c1["transcript_excerpt"]["text"].startswith("Stop what you are doing")
    assert by_ref(pack, "C3")["description_excerpt"]["text"] == "Ranked by price and features."
    assert pack.content_total == 4 and len(pack.content_ids) == 4


def test_pack_is_deterministic_regardless_of_input_order():
    a = build_pack()
    rng = random.Random(7)
    content, comments, interps = standard_content(), standard_comments(), standard_interpretations()
    for rows in (content, comments, interps):
        rng.shuffle(rows)
    b = build_pack(content, comments, interps)
    assert a.prompt_json() == b.prompt_json()
    assert a.manifest == b.manifest
    assert a.refs == b.refs and a.content_ids == b.content_ids


def test_references_are_opaque_and_no_database_ids_reach_the_model():
    pack = build_pack()
    text = pack.prompt_json()
    for row in standard_content() + standard_comments() + standard_interpretations():
        assert row["id"] not in text
    assert set(pack.refs) == {"C1", "C2", "C3", "C4", "I1", "I2", "I3", "M1", "M2", "M3", "M4"}
    assert pack.refs["C2"].id == "content-2" and pack.refs["C2"].content_id == "content-2"
    assert pack.refs["I2"].id == "interp-2" and pack.refs["I2"].content_id == "content-2"
    assert pack.refs["M3"].kind == "comment" and pack.refs["M3"].content_id == "content-2"


# --- classification selection and coverage -------------------------------------------------------


def test_latest_classification_wins_newest_first_then_id():
    old = interpretation_row(10, 1, created="2026-09-01T00:00:00+00:00", model="old",
                             res=result(topic=FieldResult("old-topic", 0.1, "e")))
    new = interpretation_row(11, 1, created="2026-09-27T00:00:00+00:00", model="new",
                             res=result(topic=FieldResult("new-topic", 0.9, "e")))
    tie_a = interpretation_row(20, 2, created="2026-09-27T00:00:00+00:00", model="tie-a")
    tie_b = interpretation_row(21, 2, created="2026-09-27T00:00:00+00:00", model="tie-b")
    for order in ([old, new, tie_a, tie_b], [tie_b, tie_a, new, old]):
        pack = build_pack(interpretations=list(order))
        c1 = by_ref(pack, "C1")["classification"]
        assert c1["model"] == "new" and c1["fields"]["topic"]["value"] == "new-topic"
        assert by_ref(pack, "C2")["classification"]["model"] == "tie-b"  # id 21 > 20
        assert "interp-10" not in pack.interpretation_ids  # no mixing of versions per content


def test_classification_provenance_and_confidence_and_evidence_are_supplied():
    c = by_ref(build_pack(), "C1")["classification"]
    assert (c["ref"], c["model"], c["prompt_version"], c["schema_version"]) == (
        "I1", "haiku", "classification-v2", "classification-v1")
    assert c["fields"]["hook_type"] == {"value": "fear", "confidence": 0.88, "evidence": "now required"}
    assert c["fields"]["cta"] == {"value": None, "confidence": None, "evidence": None}


def test_missing_classifications_are_included_as_raw_evidence_and_coverage_recorded():
    pack = build_pack()  # content 4 has no classification
    assert by_ref(pack, "C4")["classification"] is None
    assert by_ref(pack, "C4")["title"] == "Tax return mistakes"  # still evidence
    cov = pack.prompt["coverage"]
    assert (cov["content_total"], cov["classified_content_count"],
            cov["classification_coverage"]) == (4, 3, 0.75)
    assert cov["unclassified_content_refs"] == ["C4"]
    assert any("INCOMPLETE" in n and "3 of 4" in n for n in cov["notes"])
    assert pack.classification_coverage == 0.75
    m = pack.manifest["coverage"]
    assert (m["content_total"], m["classified_content_count"], m["classification_coverage"]) == (4, 3, 0.75)


def test_synthesis_can_run_with_no_classifications_at_all():
    pack = build_pack(interpretations=[])
    assert pack.classified_content_count == 0 and pack.classification_coverage == 0.0
    assert all(c["classification"] is None for c in pack.prompt["content"])
    assert pack.interpretation_ids == ()


def test_full_classification_omits_the_incomplete_note():
    interps = standard_interpretations() + [interpretation_row(4, 4)]
    cov = build_pack(interpretations=interps).prompt["coverage"]
    assert cov["classification_coverage"] == 1.0
    assert not any("INCOMPLETE" in n for n in cov["notes"])


def test_malformed_latest_classification_is_unusable_recorded_and_not_replaced_by_an_older_one():
    good_old = interpretation_row(1, 1, created="2026-09-01T00:00:00+00:00")
    bad_new = interpretation_row(2, 1, created="2026-09-02T00:00:00+00:00", res={"topic": "x"})
    pack = build_pack(interpretations=[good_old, bad_new])
    assert by_ref(pack, "C1")["classification"] is None
    assert pack.manifest["unusable_interpretation_ids"] == ["interp-2"]
    assert pack.classified_content_count == 0


def test_only_classification_interpretations_are_used():
    other = interpretation_row(9, 1, analysis_type="synthesis")
    assert build_pack(interpretations=[other]).classified_content_count == 0


# --- NULL metrics never become zero -----------------------------------------------------------


def test_null_metrics_stay_null_and_are_flagged_unavailable():
    pack = build_pack()
    c1, c4 = by_ref(pack, "C1"), by_ref(pack, "C4")
    assert c1["raw_metrics"]["shares"] is None and c1["raw_metrics"]["saves"] is None
    assert c1["metrics_available"] == {"views": True, "likes": True, "comments_count": True,
                                       "shares": False, "saves": False}
    assert c4["raw_metrics"]["likes"] is None and c4["raw_metrics"]["comments_count"] is None
    assert "shares: unavailable" in c1["metrics_text"] and "likes: unavailable" in c4["metrics_text"]
    assert "shares: 0" not in json.dumps(pack.prompt)
    assert c4["computed_metrics"] == {"like_rate": None, "views_vs_dataset_median": 0.0909}
    # and a genuine zero stays a zero
    zero = build_pack(content=[content_row(1, likes=0), content_row(2, likes=None)])
    assert by_ref(zero, "C1")["raw_metrics"]["likes"] == 0
    assert by_ref(zero, "C2")["raw_metrics"]["likes"] is None


def test_computed_statistics_only_use_metrics_that_exist():
    s = build_pack().prompt["statistics"]
    assert s["content_count"] == 4 and s["distinct_creators"] == 3
    assert s["metrics"]["views"] == {"available": 4, "min": 300, "max": 20000, "median": 3300.0}
    assert s["metrics"]["likes"]["available"] == 3  # the NULL is not a zero
    assert s["metrics"]["likes"]["min"] == 60 and s["metrics"]["likes"]["max"] == 400
    assert s["metrics"]["comments_count"]["available"] == 3
    assert s["metrics"]["shares"] == {"available": 0, "min": None, "max": None, "median": None}
    assert s["metrics"]["saves"]["available"] == 0
    assert s["like_rate"]["available"] == 3
    assert s["like_rate"]["min"] == 0.02 and s["like_rate"]["max"] == 0.05
    assert s["like_rate"]["median"] == 0.037
    assert s["classification"]["classified_content_count"] == 3
    assert s["classification"]["coverage"] == 0.75
    assert s["classification"]["value_counts_among_classified"]["hook_type"] == {"fear": 3}
    assert s["comments"]["available"] == 4


def test_like_rate_and_views_vs_median_are_computed_by_code():
    c1 = by_ref(build_pack(), "C1")["computed_metrics"]
    assert c1["like_rate"] == round(60 / 1200, 4) == 0.05
    assert c1["views_vs_dataset_median"] == round(1200 / 3300, 4)


def test_statistics_text_is_the_verbatim_source_for_stat_quotes():
    pack = build_pack()
    text = pack.sources[("STATS", "statistics")]
    assert "metrics.views.median: 3300.0" in text
    assert "metrics.shares.median: unavailable" in text
    assert text == pack.prompt["statistics_text"]


def test_numbers_visible_in_the_pack_are_collected_for_claim_checking():
    pack = build_pack()
    assert {1200.0, 20000.0, 3300.0, 860000.0, 4.0, 0.75}.issubset(pack.numbers)


# --- comments -----------------------------------------------------------------------------------


def test_comments_are_selected_deterministically_by_likes_and_referenced_stably():
    comments = [
        comment(1, 1, "low", likes=1), comment(2, 1, "high", likes=50),
        comment(3, 1, "none", likes=None), comment(4, 1, "   ", likes=99),  # blank: skipped
        comment(5, 1, "mid", likes=10),
    ]
    pack = build_pack(comments=comments)
    assert [c["text"] for c in pack.prompt["comments"]] == ["high", "mid", "low", "none"]
    assert [c["ref"] for c in pack.prompt["comments"]] == ["M1", "M2", "M3", "M4"]
    assert pack.comment_ids == ("comment-2", "comment-5", "comment-1", "comment-3")
    assert pack.prompt["comments"][0] == {
        "ref": "M1", "content_ref": "C1", "text": "high", "likes": 50, "type": "top_level"}


def comment(n, content_n, text, likes=0):
    from synthesis_fixtures import comment_row

    return comment_row(n, content_n, text, likes)


def test_comments_per_content_are_capped_and_the_cut_is_recorded():
    comments = [comment(n, 1, f"comment {n}", likes=100 - n) for n in range(1, 8)]
    level = PackLevel(3000, 800, 3, 500)
    pack = build_pack(comments=comments, bounds=PackBounds(ladder=(level,)))
    assert len(pack.prompt["comments"]) == 3
    assert pack.manifest["truncation"]["comments_not_shown"] == 4
    entry = pack.manifest["content"][0]
    assert (entry["comments_available"], entry["comments_selected"]) == (7, 3)
    assert any("Only 3 of 7 comments" in n for n in pack.prompt["coverage"]["notes"])


# --- bounds and truncation --------------------------------------------------------------------


def test_transcript_excerpt_is_the_opening_and_truncation_is_recorded():
    long = "OPENING " + "x" * 5000
    pack = build_pack(content=[content_row(1, transcript=long), content_row(2)],
                      bounds=PackBounds(ladder=(PackLevel(100, 50, 5, 100),)))
    t = by_ref(pack, "C1")["transcript_excerpt"]
    assert t["text"].startswith("OPENING ") and len(t["text"]) <= 100
    assert t["truncated"] is True and t["chars_total"] == len(long)
    assert pack.sources[("C1", "transcript")] == t["text"]  # quotes are checked against what the model saw
    m = pack.manifest["content"][0]["transcript"]
    assert m == {"available": True, "chars_total": len(long), "chars_included": len(t["text"]),
                 "truncated": True}
    assert pack.manifest["truncation"]["transcripts_truncated"] == 1
    assert "opening" in pack.manifest["selection_rules"]["transcript"]


def test_missing_transcript_and_description_are_null_not_empty():
    c4 = by_ref(build_pack(), "C4")
    assert c4["transcript_excerpt"] is None and c4["description_excerpt"]["text"] == "Avoid common errors"
    assert ("C4", "transcript") not in build_pack().sources


def test_pack_degrades_along_a_fixed_ladder_and_records_the_level():
    big = [content_row(n, transcript="word " * 2000, description="d " * 500) for n in (1, 2, 3)]
    comments = [comment(n, 1 + n % 3, "c " * 100, likes=n) for n in range(1, 40)]
    generous = build_pack(content=big, comments=comments, interpretations=[])
    assert generous.manifest["bounds"]["level_index"] == 0
    tight = build_pack(content=big, comments=comments, interpretations=[],
                       bounds=PackBounds(budget_chars=16_000))
    idx = tight.manifest["bounds"]["level_index"]
    assert idx == 3  # levels 0-2 measure 28514 / 25920 / 20520 chars; level 3 is 15975
    tried = tight.manifest["bounds"]["levels_tried"]
    assert [t["level"] for t in tried] == list(range(idx + 1))
    assert not any(t["fits"] for t in tried[:-1]) and tried[-1]["fits"]
    assert tight.manifest["bounds"]["measured_chars"] <= 16_000
    assert len(tight.prompt["content"]) == 3  # content is degraded, never dropped
    assert build_pack(content=big, comments=comments, interpretations=[],
                      bounds=PackBounds(budget_chars=16_000)).prompt_json() == tight.prompt_json()


def test_pack_that_cannot_fit_fails_loudly_instead_of_dropping_content():
    with pytest.raises(EvidenceTooLargeError):
        build_pack(bounds=PackBounds(budget_chars=500))


def test_manifest_records_exactly_what_the_model_saw():
    pack = build_pack()
    m = pack.manifest
    assert m["pack_version"] == "pack-v1"
    assert m["selected_content_ids"] == ["content-1", "content-2", "content-3", "content-4"]
    assert m["selected_interpretation_ids"] == ["interp-1", "interp-2", "interp-3"]
    assert m["selected_comment_ids"] == ["comment-1", "comment-2", "comment-3", "comment-4"]
    assert m["bounds"]["level"] == {"transcript_chars": 3000, "description_chars": 800,
                                    "comments_per_content": 20, "comment_chars": 500}
    assert set(m["selection_rules"]) == {"content", "transcript", "description", "comments", "classification"}
    assert [c["ref"] for c in m["content"]] == ["C1", "C2", "C3", "C4"]
    assert m["content"][3]["interpretation_id"] is None
    assert m["content"][0]["metrics_available"]["shares"] is False
    assert [i["ref"] for i in m["interpretations"]] == ["I1", "I2", "I3"]
    json.dumps(m)  # storable as jsonb


# --- small / empty ------------------------------------------------------------------------------


@pytest.mark.parametrize("rows", [[], [content_row(1)]])
def test_too_little_evidence_raises_before_any_llm_call(rows):
    assert MIN_CONTENT_FOR_SYNTHESIS == 2
    with pytest.raises(InsufficientEvidenceError):
        build_pack(content=rows)


def test_two_content_items_are_enough_even_without_comments_or_classification():
    pack = build_pack(content=[content_row(1), content_row(2)], comments=[], interpretations=[])
    assert pack.content_total == 2 and pack.prompt["comments"] == []
