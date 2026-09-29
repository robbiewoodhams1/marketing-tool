"""ImageProvider/VideoProvider protocol shape and result dataclasses.

Production V1 never imports these (see the last test below); the media
service (`research.media`) depends only on the protocol, never on a
concrete provider."""

from research.media_providers import (
    GeneratedImage,
    GeneratedVideo,
    ImageProvider,
    MediaProviderAPIError,
    MediaProviderResponseError,
    VideoProvider,
)


class FakeImageProvider:
    model = "fake-image-model"

    def __init__(self):
        self.calls = []

    def generate_image(self, prompt: str, *, aspect_ratio: str = "9:16", **options):
        self.calls.append((prompt, aspect_ratio, options))
        return GeneratedImage(data=b"fake-bytes", mime_type="image/png", width=1080, height=1920)


class FakeVideoProvider:
    model = "fake-video-model"

    def generate_video(self, prompt: str, *, aspect_ratio: str = "9:16", **options):
        return GeneratedVideo(data=b"fake-video-bytes", mime_type="video/mp4", duration_seconds=8.0)


def test_a_conforming_image_provider_can_be_used_as_the_protocol():
    provider: ImageProvider = FakeImageProvider()
    result = provider.generate_image("a mechanic saving a job as a template", aspect_ratio="9:16")
    assert isinstance(result, GeneratedImage)
    assert result.mime_type == "image/png" and result.data == b"fake-bytes"
    assert provider.calls == [("a mechanic saving a job as a template", "9:16", {})]


def test_a_conforming_video_provider_can_be_used_as_the_protocol():
    provider: VideoProvider = FakeVideoProvider()
    result = provider.generate_video("scene 1: hook")
    assert isinstance(result, GeneratedVideo) and result.duration_seconds == 8.0


def test_generated_image_defaults_are_absent_not_invented():
    image = GeneratedImage(data=b"x", mime_type="image/png")
    assert image.width is None and image.height is None and image.metadata == {}


def test_error_hierarchy_carries_status_and_keeps_raw_for_diagnostics_only():
    api_err = MediaProviderAPIError("boom", status=500)
    assert api_err.status == 500
    resp_err = MediaProviderResponseError("no image in response", raw={"weird": True})
    assert resp_err.raw == {"weird": True}
    assert "weird" not in str(resp_err)  # raw is never folded into the message


def test_production_v1_does_not_import_or_reference_any_media_provider():
    import research.production as production_cli
    import research.production_pack as production_pack
    import research.production_schema as production_schema
    import research.production_validation as production_validation

    for module in (production_cli, production_pack, production_schema, production_validation):
        assert "ImageProvider" not in dir(module)
        assert "VideoProvider" not in dir(module)
