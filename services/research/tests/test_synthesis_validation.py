import pytest
from synthesis_fixtures import build_pack, ev, insight, output

from research.synthesis import SynthesisOutput, SynthesisValidationError
from research.synthesis_validation import normalize_whitespace, validate_synthesis

PACK = build_pack()  # C1-C4, I1-I3 (content 1-3 classified), M1-M4; coverage 0.75

Q_C1 = "860,000 sole traders are now required to use Making Tax Digital"
Q_C2 = "Making Tax Digital explained in 5 minutes"


def obs(text, *evidence):
    return {"text": text, "evidence": list(evidence)}


def check(*insights, pack=PACK):
    return validate_synthesis(SynthesisOutput.from_dict(output(*insights)), pack)


def rejects(match, *insights, pack=PACK):
    with pytest.raises(SynthesisValidationError, match=match) as info:
        check(*insights, pack=pack)
    return info.value


def one_content(**kw):
    """An insight whose evidence is all from content item 1 (a single content item)."""
    return insight(observations=[
        obs("C1 opens with the requirement.", ev("C1", "transcript", Q_C1)),
        obs("C1 is titled as a list of things to know.", ev("C1", "title", "5 things to know")),
    ], **kw)


# --- valid --------------------------------------------------------------------------------------


def test_a_valid_insight_is_accepted_and_resolved_to_real_database_ids():
    [v] = check(insight()).insights
    assert [(e.role, e.observation_index, e.content_id, e.comment_id, e.interpretation_id, e.field)
            for e in v.evidence] == [
        ("supports", 0, "content-1", None, None, "transcript"),
        ("supports", 1, "content-2", None, None, "title")]
    assert v.evidence[0].quote == Q_C1  # the verified quote is what gets stored
    assert v.support["content_count"] == 2
    assert v.support["distinct_creators"] == 1  # both videos are by AccountsCo
    assert v.support["share_of_content"] == 0.5 and v.support["content_total"] == 4
    assert v.support["classification_coverage"] == 0.75 and v.support["computed_by"] == "code"


def test_zero_insights_validate():
    result = validate_synthesis(SynthesisOutput.from_dict(output(reason="Nothing supported.")), PACK)
    assert result.insights == () and result.no_insights_reason == "Nothing supported."


def test_comments_and_classifications_resolve_to_their_own_targets():
    i = insight(
        pattern_type="content_comment_pattern",
        statement="Among the analysed videos, commenters on C1 and C2 echo the requirement framing.",
        observations=[
            obs("A C1 comment expresses fear of penalties.", ev("M1", "comment_text", "terrified of the penalties")),
            obs("A C2 comment praises the explanation.", ev("M3", "comment_text", "clear explanation of MTD"),
                ev("C2", "title", Q_C2)),
        ])
    [v] = check(i).insights
    kinds = [(e.comment_id, e.content_id, e.interpretation_id) for e in v.evidence]
    assert kinds == [("comment-1", None, None), ("comment-3", None, None), (None, "content-2", None)]
    assert v.support["comment_count"] == 2 and v.support["content_count"] == 2


# --- quotes --------------------------------------------------------------------------------------


def test_a_fabricated_quote_is_rejected_not_repaired():
    fake = insight(observations=[
        obs("x", ev("C1", "transcript", "Sole traders will lose all their money")),
        obs("y", ev("C2", "title", Q_C2))])
    rejects("not verbatim", fake)


def test_whitespace_differences_are_normalised_and_the_normalised_quote_is_stored():
    messy = "860,000   sole traders\nare now\t required"
    [v] = check(insight(observations=[
        obs("a", ev("C1", "transcript", messy)), obs("b", ev("C2", "title", Q_C2))])).insights
    assert v.evidence[0].quote == "860,000 sole traders are now required"


def test_normalisation_is_only_whitespace_case_and_words_must_match_exactly():
    assert normalize_whitespace("  a \n b\t\tc  ") == "a b c"
    for wrong in ("860,000 SOLE TRADERS are now required", "860,000 sole trader are now required",
                  "sole traders are required now"):
        rejects("not verbatim", insight(observations=[
            obs("a", ev("C1", "transcript", wrong)), obs("b", ev("C2", "title", Q_C2))]))


def test_ellipses_are_not_verbatim():
    for quote in ("Stop what ... changes", "Stop what… changes"):
        rejects("ellipsis", insight(observations=[
            obs("a", ev("C1", "transcript", quote)), obs("b", ev("C2", "title", Q_C2))]))


def test_quotes_are_checked_against_the_excerpt_the_model_actually_saw():
    from synthesis_fixtures import content_row
    from research.evidence_pack import PackBounds, PackLevel

    rows = [content_row(1, transcript="OPENING words " + "filler " * 200 + "SECRET-TAIL"),
            content_row(2, title="Second title")]
    pack = build_pack(content=rows, comments=[], interpretations=[],
                      bounds=PackBounds(ladder=(PackLevel(60, 50, 0, 0),)))
    tail = insight(observations=[obs("a", ev("C1", "transcript", "SECRET-TAIL")),
                                 obs("b", ev("C2", "title", "Second title"))])
    rejects("not verbatim", tail, pack=pack)  # in the database, but never shown to the model


# --- references and fields ------------------------------------------------------------------------


@pytest.mark.parametrize("ref,field", [("C9", "title"), ("M9", "comment_text"), ("I9", "classification"),
                                       ("C5", "transcript")])
def test_unknown_references_are_rejected(ref, field):
    rejects("unknown reference", insight(observations=[
        obs("a", ev(ref, field, "anything")), obs("b", ev("C2", "title", Q_C2))]))


@pytest.mark.parametrize("ref,field", [("M1", "title"), ("I1", "transcript"), ("C1", "comment_text"),
                                       ("C1", "classification"), ("M1", "classification")])
def test_a_field_not_valid_for_that_kind_of_reference_is_rejected(ref, field):
    rejects("cannot be cited", insight(observations=[
        obs("a", ev(ref, field, "x")), obs("b", ev("C2", "title", Q_C2))]))


def test_a_field_with_no_supplied_text_is_rejected():
    rejects("no supplied", insight(observations=[
        obs("a", ev("C1", "description", "anything")), obs("b", ev("C2", "title", Q_C2))]))  # C1 has none
    rejects("no supplied", insight(observations=[
        obs("a", ev("C4", "transcript", "anything")), obs("b", ev("C2", "title", Q_C2))]))  # C4 has none


def test_stats_can_only_be_cited_as_statistics():
    rejects("STATS can only", insight(observations=[
        obs("a", ev("STATS", "title", "x")), obs("b", ev("C2", "title", Q_C2))]))


def test_real_database_ids_are_never_valid_references():
    from research.synthesis import SynthesisSchemaError

    with pytest.raises(SynthesisSchemaError):
        SynthesisOutput.from_dict(output(insight(observations=[
            obs("a", ev("content-1", "title", "x"))])))


# --- evidence requirements ---------------------------------------------------------------------------


def test_an_insight_with_only_statistics_has_no_supporting_evidence():
    only_stats = insight(pattern_type="performance_signal", observations=[
        obs("a", ev("STATS", "statistics", "metrics.views.median: 3300.0"))])
    rejects("no supporting evidence", only_stats)


def test_every_observation_needs_evidence_at_the_schema_level():
    from research.synthesis import SynthesisSchemaError

    with pytest.raises(SynthesisSchemaError, match="needs evidence"):
        SynthesisOutput.from_dict(output(insight(observations=[obs("a")])))


def test_a_recurring_pattern_with_one_content_item_is_rejected():
    e = rejects("at least two distinct content items", one_content())
    assert "found 1" in str(e)


def test_comments_and_classifications_count_for_their_own_content_item_only():
    same_video = insight(observations=[
        obs("a", ev("C1", "title", "5 things to know")),
        obs("b", ev("M1", "comment_text", "terrified of the penalties")),   # a comment on C1
        obs("c", ev("I1", "classification", "hook_type: fear")),           # C1's classification
    ])
    rejects("found 1", same_video)


def test_a_recurring_pattern_with_two_content_items_is_accepted():
    two = insight(observations=[
        obs("a", ev("C1", "title", "5 things to know")),
        obs("b", ev("M3", "comment_text", "clear explanation of MTD"))])  # a comment on C2
    assert check(two).insights[0].support["content_count"] == 2


@pytest.mark.parametrize("ptype", ["recurring_theme", "hook_pattern", "format_pattern"])
def test_every_recurring_pattern_type_needs_two_content_items(ptype):
    rejects("two distinct", one_content(pattern_type=ptype))


def test_content_comment_pattern_needs_both_sides():
    only_content = insight(pattern_type="content_comment_pattern")  # C1 + C2, no comments
    rejects("both content-side evidence", only_content)
    only_comments = insight(pattern_type="content_comment_pattern", observations=[
        obs("a", ev("M1", "comment_text", "terrified of the penalties")),
        obs("b", ev("M3", "comment_text", "clear explanation of MTD"))])
    rejects("both content-side evidence", only_comments)


def test_performance_signal_must_cite_the_computed_statistics():
    signal = dict(
        pattern_type="performance_signal",
        statement="Among the analysed videos, the video with 20000 views is well above the median "
                  "of 3300.0 views; this shows association, not cause.",
        observations=[obs("C3 has 20000 views.", ev("C3", "metrics", "views: 20000")),
                      obs("C1 has 1200 views.", ev("C1", "metrics", "views: 1200"))],
        explanation="The views of C3 and C1 differ from the dataset median.",
        limitations="Four videos only; views do not establish cause.")
    rejects("must cite the computed statistics", insight(**signal))
    with_stats = dict(signal, observations=signal["observations"] + [
        obs("The dataset median views is 3300.0.", ev("STATS", "statistics", "metrics.views.median: 3300.0"))])
    [v] = check(insight(**with_stats)).insights
    assert v.support["statistics_cited"] == ["metrics.views.median: 3300.0"]
    assert all(e.content_id in ("content-3", "content-1") for e in v.evidence)  # STATS has no db target


def test_null_metrics_can_be_cited_as_unavailable_but_not_as_zero():
    unavailable = insight(observations=[
        obs("a", ev("C1", "metrics", "shares: unavailable")), obs("b", ev("C2", "title", Q_C2))])
    check(unavailable)
    rejects("not verbatim", insight(observations=[
        obs("a", ev("C1", "metrics", "shares: 0")), obs("b", ev("C2", "title", Q_C2))]))


def test_counter_evidence_is_verified_and_stored_with_its_role():
    with_counter = insight(counter_evidence=[ev("C4", "title", "Tax return mistakes")])
    [v] = check(with_counter).insights
    [counter] = [e for e in v.evidence if e.role == "counter"]
    assert (counter.content_id, counter.observation_index) == ("content-4", None)
    assert v.support["counter_evidence_count"] == 1
    rejects("not verbatim", insight(counter_evidence=[ev("C4", "title", "invented counterpoint")]))
    rejects("unknown reference", insight(counter_evidence=[ev("C8", "title", "x")]))


def test_identical_evidence_items_are_stored_once():
    dup = insight(observations=[
        obs("a", ev("C1", "transcript", Q_C1), ev("C1", "transcript", Q_C1)),
        obs("b", ev("C2", "title", Q_C2))])
    assert len(check(dup).insights[0].evidence) == 2


# --- prose: numbers, scope, advice, coverage ----------------------------------------------------------


def test_a_fabricated_numeric_claim_is_rejected():
    e = rejects("numeric claim 9999", insight(
        statement="Among the analysed videos, 9999 videos frame Making Tax Digital as a requirement."))
    assert "evidence pack" in str(e)
    rejects("numeric claim 73", insight(explanation="73 percent of the analysed videos say this."))
    rejects("numeric claim 47", insight(limitations="Only 47 videos were analysed."))
    rejects("numeric claim 9999", insight(observations=[
        obs("C1 has 9999 views.", ev("C1", "title", "5 things to know")),
        obs("b", ev("C2", "title", Q_C2))]))


def test_numbers_from_the_pack_and_our_own_counts_are_accepted():
    ok = insight(
        statement="Among the analysed videos, 2 of 4 videos frame Making Tax Digital as a requirement "
                  "(50% of the analysed videos); the median views are 3300.",
        explanation="C3 has 20,000 views and C1 has 1200 views.",
        limitations="Sample of 4 videos from 3 creators; 3 of 4 are classified.")
    check(ok)


def test_percentages_must_match_a_computed_proportion():
    rejects("numeric claim 91", insight(
        statement="Among the analysed videos, 91% of videos frame Making Tax Digital as a requirement."))


def test_ref_labels_are_not_mistaken_for_numbers():
    check(insight(explanation="C1 and C2 both name Making Tax Digital; see M1 and I1."))


def test_a_claim_beyond_the_dataset_scope_is_rejected():
    for bad_statement in (
        "Among the analysed videos, the market prefers Making Tax Digital explainers.",
        "Among the analysed videos, all sole traders fear Making Tax Digital.",
        "Among the analysed videos, MTD is always framed as a requirement.",
    ):
        rejects("exceeds the dataset scope", insight(statement=bad_statement))


def test_a_statement_must_be_scoped_to_the_analysed_dataset():
    rejects("scoped to the analysed dataset", insight(
        statement="Two videos frame Making Tax Digital as a requirement for sole traders."))


def test_marketing_advice_is_out_of_scope():
    for advice in (
        "Among the analysed videos, TradeFlow should make more list videos.",
        "Among the analysed videos, we recommend leading with the penalty.",
        "Among the analysed videos, consider opening with a requirement.",
        "Among the analysed videos, creators could benefit from a stronger hook.",
    ):
        rejects("advice is out of scope", insight(statement=advice))
    rejects("advice is out of scope", insight(explanation="You should copy this hook."))
    rejects("advice is out of scope", insight(title="Make more explainers"))


def test_limitations_may_discuss_generalisation_because_they_are_not_claims():
    check(insight(limitations="These four videos cannot speak for the market, and never for all creators."))


def test_a_pattern_resting_on_partial_classification_must_say_so():
    resting_on_classification = dict(
        pattern_type="hook_pattern",
        observations=[obs("C1 is classified as a fear hook.", ev("I1", "classification", "hook_type: fear")),
                      obs("C2 is classified as a fear hook.", ev("I2", "classification", "hook_type: fear"))])
    rejects("classification is incomplete", insight(**resting_on_classification))
    ok = insight(statement="Among the classified videos, two use a fear hook.", **resting_on_classification)
    [v] = check(ok).insights
    assert v.support["interpretation_count"] == 2
    assert {e.interpretation_id for e in v.evidence} == {"interp-1", "interp-2"}


def test_full_classification_coverage_does_not_require_the_word():
    from synthesis_fixtures import interpretation_row, standard_interpretations

    full = build_pack(interpretations=standard_interpretations() + [interpretation_row(4, 4)])
    assert full.classification_coverage == 1.0
    check(insight(observations=[
        obs("a", ev("I1", "classification", "hook_type: fear")),
        obs("b", ev("I2", "classification", "hook_type: fear"))]), pack=full)


def test_rejection_names_the_offending_insight():
    e = rejects("insight 1:", insight(), one_content())
    assert e.insight_index == 1


# --- classification: dependent claims, supplementary use, unsupported generalisation -----------------------
#
# The exact shape of a real run: 10 videos, only ONE classified (coverage 0.1).

from synthesis_fixtures import content_row, interpretation_row, result  # noqa: E402

from research.classification import FieldResult  # noqa: E402

T2 = "Will HMRC DESTROY Sole Traders in 2026? This is what to do!"
T8 = "Your Sole Trader Tax Just Got Harder - Making Tax Digital"
FEAR_EVIDENCE = ("Hook emphasizes mandatory compliance and requirements; creates urgency by "
                 "stating 860,000 sole traders are now required")


def sparse_pack():
    rows = [content_row(n, published_at=f"2026-01-{n:02d}T00:00:00+00:00", title=f"Video title {n}",
                        creator=f"Creator {n}") for n in range(1, 11)]
    rows[1]["title"] = T2
    rows[7]["title"] = T8
    classification = interpretation_row(
        1, 8, res=result(hook_type=FieldResult("fear", 0.88, FEAR_EVIDENCE)))
    return build_pack(content=rows, comments=[], interpretations=[classification])


SPARSE = sparse_pack()  # C2 and C8 are the titles above; I1 classifies C8

LIMITATIONS = ("Classification coverage is only 1 of 10 items (10%); the assessment of C2 and C8 "
               "titles as 'fear framing' is a qualitative reading of raw text, not a model "
               "classification, and 10 videos is a small sample.")
EXPLANATION = ("Two titles clearly show fear framing and one independent classification supports it, "
               "but only one of the three items was formally classified, so the classification adds "
               "support without carrying the claim.")

C2_OBS = obs("C2's title frames the MTD change as a threat to sole traders.",
             ev("C2", "title", "Will HMRC DESTROY Sole Traders in 2026?"))
C8_OBS = obs("C8's title says the sole trader tax situation just got harder.",
             ev("C8", "title", "Your Sole Trader Tax Just Got Harder"))
I1_OBS = obs("The classification of C8 records a fear hook.",
             ev("I1", "classification", "hook_type: fear"))


def failing_insight_0(**over):
    """The insight from the real run that validation wrongly rejected."""
    base = dict(
        title="MTD-deadline videos use fear/threat framing in their titles",
        pattern_type="hook_pattern",
        statement="Among the analysed videos, content about the upcoming Making Tax Digital (MTD) "
                  "deadline recurringly frames the topic as a threat or crisis in its title, "
                  "rather than as neutral information.",
        observations=[C2_OBS, C8_OBS, I1_OBS],
        explanation=EXPLANATION,
        confidence=0.55,
        confidence_rationale="Two raw titles agree and one classification agrees; small sample.",
        limitations=LIMITATIONS,
    )
    base.update(over)
    return insight(**base)


def check_sparse(*insights):
    return check(*insights, pack=SPARSE)


def rejects_sparse(match, *insights):
    return rejects(match, *insights, pack=SPARSE)


def test_the_fixture_matches_the_real_run():
    assert (SPARSE.content_total, SPARSE.classified_content_count, SPARSE.classification_coverage) == (10, 1, 0.1)
    assert SPARSE.sources[("C2", "title")] == T2 and SPARSE.sources[("C8", "title")] == T8
    assert SPARSE.refs["I1"].content_id == SPARSE.refs["C8"].content_id  # I1 classifies C8


# The real failing case ------------------------------------------------------------------------------


def test_the_real_insight_0_is_accepted_a_raw_claim_with_a_supplementary_classification():
    [v] = check_sparse(failing_insight_0()).insights
    assert "classified" not in v.insight.statement  # and it does not need to say so
    assert v.support["content_count"] == 2                  # C2 and C8: I1 is C8's classification
    assert v.support["interpretation_count"] == 1 and v.support["classification_coverage"] == 0.1
    assert [e.field for e in v.evidence] == ["title", "title", "classification"]
    assert v.evidence[2].interpretation_id == "interp-1"    # the classification is still cited, and traceable


def test_the_supplementary_classification_stays_visible_as_derived_evidence_not_dropped():
    [v] = check_sparse(failing_insight_0()).insights
    assert {(e.content_id, e.interpretation_id) for e in v.evidence} == {
        ("content-2", None), ("content-8", None), (None, "interp-1")}


# B: raw-evidence claim + supplementary classification --------------------------------------------------------


def test_B_the_users_own_wording_is_valid():
    b = failing_insight_0(
        statement="Among the analysed videos, two MTD videos use threat language in their titles; "
                  "the formally classified example was also identified as a fear hook.")
    check_sparse(b)


def test_B_a_raw_claim_may_cite_a_classification_without_saying_classified():
    for statement in (
        "Among the analysed videos, two titles frame the MTD change as a threat.",
        "Among the analysed videos, MTD titles recurringly use threat language.",
    ):
        check_sparse(failing_insight_0(statement=statement))


def test_B_but_the_incomplete_classification_coverage_must_be_acknowledged():
    silent = dict(limitations="Small sample of ten videos.", explanation="Both titles use threat language.",
                  confidence_rationale="Two agreeing titles.")
    e = rejects_sparse("must acknowledge the incomplete classification coverage", failing_insight_0(**silent))
    assert "only 1 of 10" in str(e)
    # mentioning classification is not enough: it must say the coverage is partial
    rejects_sparse("acknowledge the incomplete", failing_insight_0(
        **dict(silent, explanation="The classification supports this.")))
    # acknowledging anywhere in limitations, rationale or explanation is accepted
    check_sparse(failing_insight_0(**dict(silent, limitations="Small sample.",
                 confidence_rationale="Only 1 of 10 videos is classified.")))
    check_sparse(failing_insight_0(**dict(silent, explanation=EXPLANATION)))
    check_sparse(failing_insight_0(**dict(silent, limitations="Classification is partial; small sample.")))


def test_B_full_classification_coverage_needs_no_acknowledgement():
    from synthesis_fixtures import standard_interpretations

    rows = [content_row(n, published_at=f"2026-01-{n:02d}T00:00:00+00:00") for n in (1, 2)]
    rows[0]["title"], rows[1]["title"] = T2, T8
    interps = [interpretation_row(1, 1), interpretation_row(2, 2)]
    full = build_pack(content=rows, comments=[], interpretations=interps)
    assert full.classification_coverage == 1.0 and standard_interpretations()
    check(insight(statement="Among the analysed videos, two titles frame the change as a threat.",
                  observations=[obs("a", ev("C1", "title", "Will HMRC DESTROY Sole Traders in 2026?")),
                                obs("b", ev("C2", "title", "Your Sole Trader Tax Just Got Harder")),
                                obs("c", ev("I1", "classification", "hook_type: fear"))],
                  limitations="Two videos only.", explanation="Both titles use threat language.",
                  confidence_rationale="Two agreeing titles."), pack=full)


# A: classification-dependent claims ---------------------------------------------------------------------------------------


def dependent(**over):
    """Only one raw item (C2) plus a classification (of C8): the pattern rests on the classification."""
    base = dict(
        statement="Among the classified videos, fear hooks were observed.",
        observations=[I1_OBS, obs("C2's title frames the change as a threat.", ev("C2", "title", "Will HMRC DESTROY Sole Traders in 2026?"))],
        limitations=LIMITATIONS, explanation=EXPLANATION)
    base.update(over)
    return insight(**base)


def test_A_a_statement_scoped_to_the_classified_videos_remains_valid():
    [v] = check_sparse(dependent()).insights
    assert v.support["content_count"] == 2 and v.support["interpretation_count"] == 1
    check_sparse(dependent(statement="Among the classified videos, one fear hook was observed."))


def test_A_a_classification_dependent_claim_must_say_classified():
    e = rejects_sparse("classification is incomplete", dependent(
        statement="Among the analysed videos, fear hooks were observed."))
    assert "'classified'" in str(e)


def test_A_the_original_two_classification_case_is_still_rejected_without_classified():
    from synthesis_fixtures import interpretation_row as ir

    pack = build_pack(interpretations=[ir(1, 1), ir(2, 2), ir(3, 3)])  # 3 of 4 classified
    only_classifications = dict(observations=[
        obs("C1 is classified as a fear hook.", ev("I1", "classification", "hook_type: fear")),
        obs("C2 is classified as a fear hook.", ev("I2", "classification", "hook_type: fear"))])
    rejects("classification is incomplete", insight(**only_classifications), pack=pack)
    check(insight(statement="Among the classified videos, two use a fear hook.", **only_classifications), pack=pack)


# C: unsupported generalisation -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("statement", [
    "The dataset predominantly uses fear-based hooks.",
    "Most videos use fear-based hooks.",
])
def test_C_the_unscoped_generalisations_are_rejected(statement):
    rejects_sparse("scoped to the analysed dataset", failing_insight_0(statement=statement))


@pytest.mark.parametrize("statement", [
    "Among the analysed videos, the dataset predominantly uses fear-based hooks.",
    "Among the analysed videos, most videos use fear-based hooks.",
    "Among the analysed videos, the majority of titles frame the change as a threat.",
    "Among the analysed videos, fear-based hooks are typically used.",
    "Among the analysed videos, threat framing is overwhelmingly common.",
])
def test_C_scoped_majority_claims_are_rejected_when_the_evidence_covers_2_of_10(statement):
    e = rejects_sparse("majority claim", failing_insight_0(statement=statement))
    assert "covers 2 of 10" in str(e)


def test_C_a_majority_claim_resting_on_one_classification_is_rejected():
    e = rejects_sparse("majority claim", dependent(
        statement="Among the classified videos, most use fear hooks."))
    assert "classified videos" in str(e) and "covers 1 of 1" in str(e)
    rejects_sparse("majority claim", dependent(
        title="Fear hooks predominate", statement="Among the classified videos, fear hooks were observed."))


def test_C_a_majority_claim_is_allowed_only_when_the_evidence_really_covers_a_majority():
    rows = [content_row(n, published_at=f"2026-01-{n:02d}T00:00:00+00:00", title=f"Threat title {n}")
            for n in range(1, 11)]
    pack = build_pack(content=rows, comments=[], interpretations=[])

    def titles(count):
        return [obs(f"C{n} uses threat language.", ev(f"C{n}", "title", f"Threat title {n}"))
                for n in range(1, count + 1)]

    def majority(count):
        return insight(statement="Among the analysed videos, most titles use threat language.",
                       observations=titles(count), limitations="Ten videos only.")

    check(majority(6), pack=pack)                                  # 6 of 10: more than half
    rejects("covers 5 of 10", majority(5), pack=pack)              # exactly half is not a majority
    rejects("covers 2 of 10", majority(2), pack=pack)


def test_C_majority_words_are_only_checked_in_the_claim_not_in_limitations():
    check_sparse(failing_insight_0(limitations=LIMITATIONS + " This is not the majority of the dataset."))


def test_C_recurring_language_is_not_a_majority_claim():
    check_sparse(failing_insight_0(statement="Among the analysed videos, two titles recurringly frame the change as a threat."))


# What must NOT have been weakened -------------------------------------------------------------------------------------------------


def test_quote_verification_still_applies_to_the_real_shape():
    fake = failing_insight_0(observations=[
        obs("a", ev("C2", "title", "HMRC will destroy every sole trader")), C8_OBS, I1_OBS])
    rejects_sparse("not verbatim", fake)
    rejects_sparse("not verbatim", failing_insight_0(observations=[
        C2_OBS, C8_OBS, obs("c", ev("I1", "classification", "hook_type: love"))]))


def test_reference_validation_still_applies_to_the_real_shape():
    rejects_sparse("unknown reference", failing_insight_0(observations=[
        C2_OBS, C8_OBS, obs("c", ev("I2", "classification", "hook_type: fear"))]))  # only I1 exists
    rejects_sparse("unknown reference", failing_insight_0(observations=[
        C2_OBS, obs("b", ev("C99", "title", "x")), I1_OBS]))


def test_a_raw_pattern_still_needs_two_distinct_content_items():
    # C8's title plus C8's own classification is ONE content item, however many rows cite it
    one_item = failing_insight_0(observations=[C8_OBS, I1_OBS])
    rejects_sparse("at least two distinct content items", one_item)


def test_numbers_are_now_checked_on_these_insights_and_fabricated_ones_are_rejected():
    rejects_sparse("numeric claim 47", failing_insight_0(
        limitations=LIMITATIONS + " 47 of the 10 videos use the same framing."))
    rejects_sparse("numeric claim 9999", failing_insight_0(
        statement="Among the analysed videos, 9999 titles frame the change as a threat."))


def test_dataset_scope_and_advice_checks_still_apply_to_the_real_shape():
    rejects_sparse("advice", failing_insight_0(statement="Among the analysed videos, creators should use threat titles."))
    rejects_sparse("exceeds the dataset scope", failing_insight_0(
        statement="Among the analysed videos, the market always frames the change as a threat."))


def test_performance_signals_still_need_statistics_even_with_a_classification():
    signal = failing_insight_0(
        pattern_type="performance_signal",
        statement="Among the analysed videos, threat-framed titles have more views.",
        observations=[obs("C2 has views.", ev("C2", "metrics", "views: 2000")),
                      obs("C8 has views.", ev("C8", "metrics", "views: 8000")), I1_OBS])
    rejects_sparse("must cite the computed statistics", signal)


# --- majority wording: the exact boundary from a real run (5 of 10 is not a majority) -------------------------------

import re as _re  # noqa: E402

from research.synthesis_validation import _MAJORITY_RE  # noqa: E402


def mtd_pack():
    rows = [content_row(n, published_at=f"2026-01-{n:02d}T00:00:00+00:00",
                        title=f"Making Tax Digital update {n}", creator=f"Creator {n}")
            for n in range(1, 11)]
    return build_pack(content=rows, comments=[], interpretations=[])


MTD = mtd_pack()


def mtd_evidence(refs):
    return [obs(f"C{r} foregrounds Making Tax Digital.", ev(f"C{r}", "title", f"Making Tax Digital update {r}"))
            for r in refs]


def mtd_insight(refs, *, title, statement):
    return insight(
        title=title, statement=statement, observations=mtd_evidence(refs),
        explanation="The cited titles all foreground Making Tax Digital.",
        confidence_rationale="Direct titles, but a small sample.",
        limitations="Only 10 videos were analysed and none is classified.")


FIVE = [2, 3, 6, 8, 10]      # the run's five items: C2, C3, C6, C8, C10
SIX = [2, 3, 6, 8, 10, 1]


def test_the_real_run_insight_0_is_still_rejected_at_exactly_half():
    e = rejects(
        "majority claim", mtd_insight(
            FIVE,
            title="Making Tax Digital (MTD) is the dominant recurring topic across independently created videos",
            statement="Among the 10 videos analysed, at least five items from different creators "
                      "(C2, C3, C6, C8, C10) foreground Making Tax Digital as a central topic, "
                      "indicating it is a major shared talking point in this dataset."),
        pack=MTD)
    assert "'dominant'" in str(e) and "covers 5 of 10" in str(e)


def test_the_same_five_items_are_valid_with_neutral_wording():
    for title, statement in (
        ("Making Tax Digital recurs across five of the ten videos",
         "Among the 10 videos analysed, five items from different creators (C2, C3, C6, C8, C10) "
         "foreground Making Tax Digital as a central topic, so it recurs across half of this dataset."),
        ("Making Tax Digital appears in five of ten titles",
         "Among the analysed videos, Making Tax Digital is present across five items from different creators."),
    ):
        [v] = check(mtd_insight(FIVE, title=title, statement=statement), pack=MTD).insights
        assert v.support["content_count"] == 5 and v.support["share_of_content"] == 0.5


def test_six_of_ten_is_a_majority_so_the_same_wording_becomes_valid():
    [v] = check(mtd_insight(
        SIX, title="Making Tax Digital is the dominant topic",
        statement="Among the analysed videos, Making Tax Digital is the dominant topic of six titles."), pack=MTD).insights
    assert v.support["content_count"] == 6


@pytest.mark.parametrize("word", ["most", "mostly", "majority", "predominantly", "dominant", "typically",
                                  "generally", "usually", "commonly", "overwhelmingly", "prevalent",
                                  "the norm", "nearly all", "almost all"])
def test_every_majority_word_has_the_same_boundary_at_five_and_six_of_ten(word):
    assert _MAJORITY_RE.search(word)
    statement = f"Among the analysed videos, Making Tax Digital is {word} the topic of the cited titles."
    rejects("covers 5 of 10", mtd_insight(FIVE, title="Making Tax Digital titles", statement=statement), pack=MTD)
    check(mtd_insight(SIX, title="Making Tax Digital titles", statement=statement), pack=MTD)


def test_the_boundary_is_by_distinct_content_items_not_by_how_many_rows_cite_them():
    many_rows_five_items = insight(
        title="Making Tax Digital titles",
        statement="Among the analysed videos, Making Tax Digital is the dominant topic.",
        observations=mtd_evidence(FIVE) + [
            obs("Again C2.", ev("C2", "title", "Making Tax Digital update 2")),
            obs("Again C3.", ev("C3", "title", "Making Tax Digital update 3")),
            obs("Again C6.", ev("C6", "title", "Making Tax Digital update 6"))],
        limitations="Only 10 videos were analysed and none is classified.")
    rejects("covers 5 of 10", many_rows_five_items, pack=MTD)  # 8 evidence rows, still 5 items


def test_validator_rules_the_prompt_change_relies_on_are_unchanged():
    import inspect

    from research import synthesis_validation as sv

    src = inspect.getsource(sv._check_prose)
    assert "supporting * 2 <= population" in src and "supporting < 2" in src  # strictly more than half, and >= 2
    assert _re.search(r"dominant\|dominat", sv._MAJORITY_RE.pattern)


# --- reference namespaces: which fields each kind of reference may cite -------------------------------------------
#
# C# content: title, description, transcript, metrics | M# comment: comment_text
# I# classification: classification                    | STATS: statistics

COMPANION = obs("C1 is titled as a list.", ev("C1", "title", "5 things to know"))  # a second content item


def with_companion(*evidence):
    """One observation holding the evidence under test, plus a companion that supplies the
    second content item a recurring pattern needs, so only the reference/field pair varies."""
    return insight(observations=[obs("The evidence under test.", *evidence), COMPANION])


# (ref, field, a verbatim quote from what the pack supplied for it)
VALID_REFERENCES = [
    ("C2", "title", "Making Tax Digital explained in 5 minutes"),
    ("C2", "transcript", "Requirement one is digital records"),
    ("C2", "metrics", "views: 5400"),
    ("C3", "description", "Ranked by price and features."),
    ("M1", "comment_text", "terrified of the penalties"),
    ("M3", "comment_text", "clear explanation of MTD"),
    ("I1", "classification", "hook_type: fear"),
    ("STATS", "statistics", "metrics.views.median: 3300.0"),
]


@pytest.mark.parametrize("ref,field,quote", VALID_REFERENCES)
def test_valid_reference_and_field_pairs_are_accepted(ref, field, quote):
    # I# evidence rests on classification, and coverage is partial, so acknowledge it (a separate rule)
    extra = dict(limitations="Only 3 of 4 videos are classified, so classification is partial; small sample.")
    [v] = check(insight(observations=[obs("The evidence under test.", ev(ref, field, quote)), COMPANION,
                                      obs("A second content item.", ev("C2", "title", "Making Tax Digital explained in 5 minutes"))],
                        **extra)).insights
    assert v.support["content_count"] >= 2


def test_one_observation_can_hold_content_title_evidence_and_separate_comment_evidence():
    # C2 for the title and M3 for the comment (a comment ON C2): two entries, two namespaces
    both = insight(
        pattern_type="content_comment_pattern",
        statement="Among the analysed videos, C2's title and its viewers' comments both frame Making Tax Digital as a requirement.",
        observations=[
            obs("C2 is titled as an explanation and a viewer calls it clear.",
                ev("C2", "title", "Making Tax Digital explained in 5 minutes"),
                ev("M3", "comment_text", "clear explanation of MTD")),
            obs("C1 has a fearful comment.", ev("C1", "title", "5 things to know"),
                ev("M1", "comment_text", "terrified of the penalties"))])
    [v] = check(both).insights
    rows = [(e.content_id, e.comment_id, e.field) for e in v.evidence]
    assert ("content-2", None, "title") in rows and (None, "comment-3", "comment_text") in rows
    assert v.support["comment_count"] == 2 and v.support["content_count"] == 2


# (ref, field): the ref's namespace does not allow that field
INVALID_REFERENCES = [
    ("C2", "comment_text"),      # the failure from the real run: a comment field on a CONTENT ref
    ("C2", "classification"),    # a classification field on a content ref
    ("C2", "statistics"),
    ("M1", "title"),             # a content field on a COMMENT ref
    ("M1", "transcript"),
    ("M1", "description"),
    ("M1", "metrics"),
    ("M1", "classification"),
    ("I1", "comment_text"),      # a comment field on a classification ref
    ("I1", "title"),
    ("I1", "transcript"),
]


@pytest.mark.parametrize("ref,field", INVALID_REFERENCES)
def test_a_field_from_another_namespace_is_rejected(ref, field):
    kind = {"C": "content", "M": "comment", "I": "interpretation"}[ref[0]]
    e = rejects("cannot be cited", with_companion(ev(ref, field, "anything")))
    assert f"field {field!r} cannot be cited from {ref} ({kind})" in str(e)


def test_the_exact_error_from_the_real_run_is_still_raised():
    e = rejects("cannot be cited", insight(observations=[
        obs("a", ev("C2", "title", "Making Tax Digital explained in 5 minutes"),
            ev("C2", "comment_text", 'All part of "Digital Enslavement"')),
        COMPANION]))
    assert "observations[0].evidence[1]" in str(e)
    assert "field 'comment_text' cannot be cited from C2 (content)" in str(e)


def test_stats_can_only_be_cited_as_statistics_and_other_refs_cannot_cite_statistics():
    rejects("STATS can only be cited", with_companion(ev("STATS", "title", "x")))
    rejects("STATS can only be cited", with_companion(ev("STATS", "comment_text", "x")))
    rejects("cannot be cited", with_companion(ev("M1", "statistics", "x")))


def test_a_comment_quote_cannot_be_smuggled_in_under_its_parent_content_reference():
    # the comment text is real and is a comment on C2, but it is only citable as M3.comment_text
    quote = "clear explanation of MTD"
    check(with_companion(ev("M3", "comment_text", quote)))
    rejects("cannot be cited", with_companion(ev("C2", "comment_text", quote)))
    rejects("not verbatim", with_companion(ev("C2", "title", quote)))  # nor as C2's title


def test_a_reference_is_never_converted_between_namespaces_by_the_validator():
    # there is no fallback: a wrong-namespace pair is rejected, not repaired to the right ref
    for ref, field in INVALID_REFERENCES:
        with pytest.raises(SynthesisValidationError):
            check(with_companion(ev(ref, field, "terrified of the penalties")))
