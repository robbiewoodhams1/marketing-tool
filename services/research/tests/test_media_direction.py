"""MediaDirection: parsing, prompt rendering, and the run-identity snapshot."""

from __future__ import annotations

from research.media_direction import MediaDirection


def test_an_empty_direction_is_empty_renders_no_lines_and_snapshots_to_none():
    direction = MediaDirection()
    assert direction.is_empty()
    assert direction.to_prompt_lines() == ()
    assert direction.to_snapshot() is None


def test_from_row_of_none_is_the_same_as_an_empty_direction():
    assert MediaDirection.from_row(None) == MediaDirection()


def test_a_direction_with_only_some_fields_set_is_not_empty():
    direction = MediaDirection(target_subject="A UK sole-trader mechanic")
    assert not direction.is_empty()


def test_from_row_reads_only_known_fields_and_treats_blank_strings_as_empty():
    row = {
        "target_subject": "A mechanic",
        "appearance": "",  # blank string from an empty form field - not real content
        "setting": None,
        "some_unrelated_column": "ignored",
    }
    direction = MediaDirection.from_row(row)
    assert direction.target_subject == "A mechanic"
    assert direction.appearance is None
    assert direction.setting is None


def test_to_prompt_lines_only_includes_filled_in_fields_labelled_and_in_a_fixed_order():
    direction = MediaDirection(
        additional_direction="Keep it upbeat", target_subject="A mechanic", avoid="Do not show a competitor logo",
    )
    lines = direction.to_prompt_lines()
    assert lines == (
        "Subject to depict: A mechanic",
        "Do NOT include: Do not show a competitor logo",
        "Additional direction: Keep it upbeat",
    )


def test_to_snapshot_only_includes_filled_in_fields():
    direction = MediaDirection(target_subject="A mechanic", lighting="Warm evening light")
    assert direction.to_snapshot() == {"target_subject": "A mechanic", "lighting": "Warm evening light"}


def test_two_directions_with_the_same_content_snapshot_identically():
    a = MediaDirection(target_subject="A mechanic")
    b = MediaDirection(target_subject="A mechanic")
    assert a.to_snapshot() == b.to_snapshot()


def test_two_directions_with_different_content_snapshot_differently():
    a = MediaDirection(target_subject="A mechanic")
    b = MediaDirection(target_subject="A plumber")
    assert a.to_snapshot() != b.to_snapshot()
