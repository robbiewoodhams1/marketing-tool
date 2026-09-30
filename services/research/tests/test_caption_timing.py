"""Pure caption segmentation/timing (research.caption_timing) - no ffmpeg,
no Pillow, no database: just text in, `CaptionSegment`s out."""

from __future__ import annotations

import pytest

from research.caption_timing import (
    DEFAULT_MAX_CAPTION_CHARS,
    CaptionError,
    CaptionSegment,
    segment_caption_text,
)


def total_chars(segments) -> int:
    return sum(len(s.text) for s in segments)


# --- basic shape / ordering / contiguity -----------------------------------------------------------


def test_a_short_sentence_becomes_one_segment_spanning_the_whole_duration():
    segments = segment_caption_text("Stop rebuilding the same quote.", 4.0)
    assert len(segments) == 1
    [seg] = segments
    assert seg.index == 0
    assert seg.text == "Stop rebuilding the same quote."
    assert seg.start_seconds == 0.0
    assert seg.end_seconds == 4.0


def test_segments_are_ordered_and_indexed_from_zero():
    segments = segment_caption_text(
        "This is the first sentence here. This is the second sentence too. And a third one now.", 12.0,
    )
    assert [s.index for s in segments] == list(range(len(segments)))


def test_the_first_segment_starts_at_zero_and_the_last_ends_at_the_full_duration():
    segments = segment_caption_text(
        "Same job, same customer, same quote you're typing out again from zero every single time.", 9.0,
    )
    assert segments[0].start_seconds == 0.0
    assert segments[-1].end_seconds == 9.0


def test_segments_never_overlap_and_are_contiguous():
    segments = segment_caption_text(
        "Same job, same customer, same quote you're typing out again from zero every single time.", 9.0,
    )
    for a, b in zip(segments, segments[1:]):
        assert a.end_seconds == b.start_seconds


def test_every_word_of_the_input_appears_exactly_once_unmodified():
    text = "Stop rebuilding the same quote. Build it once, reuse it forever."
    segments = segment_caption_text(text, 6.0)
    reconstructed = " ".join(s.text for s in segments)
    assert reconstructed.split() == text.split()


def test_deterministic_output_for_the_same_input():
    a = segment_caption_text("Same job, same customer type quote you're typing out again from zero?", 5.0)
    b = segment_caption_text("Same job, same customer type quote you're typing out again from zero?", 5.0)
    assert a == b


# --- multiple sentences / sentence boundaries --------------------------------------------------------


def test_multiple_sentences_prefer_sentence_boundaries():
    text = "Stop rebuilding the same quote. Build it once and reuse it forever!"
    segments = segment_caption_text(text, 6.0)
    texts = [s.text for s in segments]
    assert "Stop rebuilding the same quote." in texts
    assert "Build it once and reuse it forever!" in texts


def test_a_question_mark_ends_a_sentence_too():
    text = "Same job, same customer type quote you're typing out again from zero?"
    segments = segment_caption_text(text, 5.0)
    assert segments[-1].text.endswith("?")


# --- long sentences: wrapping, max length, no single-word tails --------------------------------------


def test_a_long_sentence_is_split_into_multiple_readable_segments():
    text = (
        "This narration is deliberately long so that it must be split into more than one readable "
        "caption segment instead of being shown as one single overwhelming block of text on screen."
    )
    segments = segment_caption_text(text, 20.0)
    assert len(segments) > 1


def test_no_segment_exceeds_a_generous_maximum_readable_length():
    text = (
        "This narration is deliberately long so that it must be split into more than one readable "
        "caption segment instead of being shown as one single overwhelming block of text on screen "
        "because nobody can read an entire paragraph in the time it takes to say it out loud."
    )
    segments = segment_caption_text(text, 25.0)
    # A small tolerance above the wrap target for the short-tail merge (see
    # segment_caption_text's max_merge_chars) - never wildly long.
    assert all(len(s.text) <= DEFAULT_MAX_CAPTION_CHARS + 15 for s in segments)


def test_avoids_a_lone_trailing_single_word_when_it_can_be_merged_back():
    # "extra" alone would be an awkward final caption; it should be folded
    # back into the previous chunk since the combined line is still short.
    text = "Stop doing this thing over and over again for no reason extra"
    segments = segment_caption_text(text, 6.0)
    assert len(segments[-1].text.split()) > 1


def test_a_single_word_longer_than_the_wrap_target_is_still_its_own_segment():
    long_word = "supercalifragilisticexpialidocious" * 2  # deliberately longer than max_chars
    segments = segment_caption_text(f"Say {long_word} now.", 4.0)
    assert any(long_word in s.text for s in segments)


# --- punctuation / apostrophes / numbers / quotes ----------------------------------------------------


def test_apostrophes_are_preserved_inside_words():
    segments = segment_caption_text("You're typing it out again, it's exhausting.", 4.0)
    joined = " ".join(s.text for s in segments)
    assert "You're" in joined and "it's" in joined


def test_numbers_are_preserved_as_ordinary_words():
    segments = segment_caption_text("Save 6 hours a week on the same 3.5x quote every time.", 5.0)
    joined = " ".join(s.text for s in segments)
    assert "6" in joined and "3.5x" in joined


def test_embedded_quotation_marks_do_not_crash_or_drop_text():
    text = 'He said "stop rebuilding the same quote" and meant it.'
    segments = segment_caption_text(text, 5.0)
    joined = " ".join(s.text for s in segments)
    assert joined.split() == text.split()


# --- short / long narration -------------------------------------------------------------------------


def test_a_very_short_single_word_narration_over_a_short_duration():
    segments = segment_caption_text("Quotes.", 0.6)
    assert len(segments) == 1
    assert segments[0].start_seconds == 0.0
    assert segments[0].end_seconds == pytest.approx(0.6)


def test_long_narration_over_a_long_duration_still_covers_it_exactly():
    text = (
        "Every single day you open up a blank document and start typing out the same quote for the "
        "same type of customer, copying the same line items, the same terms, the same signature block, "
        "instead of just reusing a template you already built the very first time you did this job."
    )
    segments = segment_caption_text(text, 42.0)
    assert segments[0].start_seconds == 0.0
    assert segments[-1].end_seconds == 42.0


# --- invalid input -----------------------------------------------------------------------------------


def test_empty_text_raises_caption_error():
    with pytest.raises(CaptionError, match="empty"):
        segment_caption_text("", 4.0)


def test_whitespace_only_text_raises_caption_error():
    with pytest.raises(CaptionError, match="empty"):
        segment_caption_text("   \n\t ", 4.0)


def test_zero_duration_raises_caption_error():
    with pytest.raises(CaptionError, match="positive"):
        segment_caption_text("Stop rebuilding the same quote.", 0.0)


def test_negative_duration_raises_caption_error():
    with pytest.raises(CaptionError, match="positive"):
        segment_caption_text("Stop rebuilding the same quote.", -1.0)


def test_caption_segment_is_a_plain_frozen_dataclass():
    seg = CaptionSegment(index=0, text="hi", start_seconds=0.0, end_seconds=1.0)
    assert seg.index == 0 and seg.text == "hi"
    with pytest.raises(Exception):
        seg.text = "changed"  # frozen
