import pytest

from research.classification import (
    ClassificationInput,
    ClassificationInputError,
    ClassificationResult,
    ClassificationSchemaError,
    FieldResult,
)

VID = "dQw4w9WgXcQ"


def field(value="x", conf=0.9, ev="quote"):
    return {"value": value, "confidence": conf, "evidence": ev}


def valid_dict(**overrides):
    d = {
        "topic": field("sole trader tax"),
        "audience": field("UK freelancers"),
        "pain_point": field("fear of HMRC"),
        "hook": field("Stop doing this"),
        "hook_type": field("mistake"),
        "format": field("tutorial"),
        "emotion": field("fear"),
        "cta": {"value": None, "confidence": None, "evidence": None},
    }
    d.update(overrides)
    return d


# --- input ---


def test_valid_input():
    i = ClassificationInput(VID, " Title ", "desc", "text", views=1, likes=0, comments_count=2)
    assert i.title == "Title"


def test_missing_transcript_and_description():
    i = ClassificationInput(VID, "Title")
    assert i.description is None and i.transcript is None


def test_blank_optional_text_becomes_none():
    i = ClassificationInput(VID, "Title", "   ", "")
    assert i.description is None and i.transcript is None


@pytest.mark.parametrize("title", ["", "   ", None])
def test_blank_title_rejected(title):
    with pytest.raises(ClassificationInputError):
        ClassificationInput(VID, title)


@pytest.mark.parametrize("vid", ["", "short", "has spaces!!", "x" * 12, None])
def test_invalid_video_id_rejected(vid):
    with pytest.raises(ClassificationInputError):
        ClassificationInput(vid, "Title")


def test_negative_metric_rejected():
    with pytest.raises(ClassificationInputError):
        ClassificationInput(VID, "Title", views=-1)


# --- output ---


def test_valid_result_roundtrip():
    r = ClassificationResult.from_dict(valid_dict())
    assert r.hook_type.value == "mistake"
    assert r.cta.value is None
    assert ClassificationResult.from_dict(r.to_dict()) == r


def test_null_field_needs_no_confidence_or_evidence():
    assert FieldResult(None).value is None


@pytest.mark.parametrize("conf", [-0.01, 1.01, "high", True])
def test_confidence_out_of_range_rejected(conf):
    with pytest.raises(ClassificationSchemaError):
        ClassificationResult.from_dict(valid_dict(topic=field(conf=conf)))


@pytest.mark.parametrize(
    "name,value", [("hook_type", "clickbait"), ("format", "vlog"), ("emotion", "joy")]
)
def test_invalid_enum_rejected(name, value):
    with pytest.raises(ClassificationSchemaError):
        ClassificationResult.from_dict(valid_dict(**{name: field(value)}))


def test_label_without_evidence_rejected():
    with pytest.raises(ClassificationSchemaError):
        ClassificationResult.from_dict(valid_dict(topic=field(ev="  ")))


def test_label_without_confidence_rejected():
    with pytest.raises(ClassificationSchemaError):
        ClassificationResult.from_dict(valid_dict(topic=field(conf=None)))


def test_malformed_shapes_rejected():
    with pytest.raises(ClassificationSchemaError):
        ClassificationResult.from_dict(valid_dict(topic="just a string"))
    with pytest.raises(ClassificationSchemaError):
        ClassificationResult.from_dict(valid_dict(topic={**field(), "why": "x"}))
    with pytest.raises(ClassificationSchemaError):
        ClassificationResult.from_dict([])


def test_missing_and_unknown_fields_rejected():
    d = valid_dict()
    del d["cta"]
    with pytest.raises(ClassificationSchemaError):
        ClassificationResult.from_dict(d)
    with pytest.raises(ClassificationSchemaError):
        ClassificationResult.from_dict({**valid_dict(), "extra": field()})
