"""Shared fixtures for the Assembly tests. Nothing here touches the network
or a real ffmpeg process."""

from __future__ import annotations

import copy
import re

from media_fixtures import PRODUCTION_ID, RpcFakeDatabase, production_row, production_run_row, scene_row

from research.ffmpeg_engine import CommandResult
from research.persistence import PersistenceError

_HEXKEY_RE = re.compile(r"^[0-9a-f]{64}$")


def completed_video_asset_row(scene_number: int, *, asset_id: str, created_at: str, duration_seconds=6.0, **kw):
    """A `media_assets` row shaped exactly as a real completed video attempt
    would be - seeded directly (bypassing research.media entirely), since
    Assembly only ever READS already-generated assets, never creates them."""
    row = {
        "id": asset_id,
        "media_run_id": "mrun-video-1",
        "production_id": PRODUCTION_ID,
        "scene_number": scene_number,
        "asset_type": "video",
        "status": "completed",
        "provider": "veo",
        "model": "veo-3.1-fast-generate-preview",
        "prompt": f"scene {scene_number} prompt",
        "generation_config": {"aspect_ratio": "9:16"},
        "product_capability_refs": [],
        "provider_metadata": {},
        "storage_bucket": "media-assets",
        "storage_path": f"{PRODUCTION_ID}/mrun-video-1/scene-{scene_number}-abcd1234.mp4",
        "storage_url": f"https://example.supabase.co/storage/v1/object/public/media-assets/{PRODUCTION_ID}/mrun-video-1/scene-{scene_number}-abcd1234.mp4",
        "mime_type": "video/mp4",
        "width": None,
        "height": None,
        "duration_seconds": duration_seconds,
        "cost_amount": None,
        "cost_currency": None,
        "error_message": None,
        "created_at": created_at,
    }
    row.update(kw)
    return row


def completed_voice_asset_row(scene_number: int, *, asset_id: str, created_at: str, duration_seconds=6.0, **kw):
    """A `media_assets` row shaped exactly as a real completed voiceover
    attempt would be (see `research.voice`) - seeded directly, since
    Assembly only ever READS already-generated assets, never creates them."""
    row = {
        "id": asset_id,
        "media_run_id": "mrun-voice-1",
        "production_id": PRODUCTION_ID,
        "scene_number": scene_number,
        "asset_type": "audio",
        "audio_subtype": "voiceover",
        "status": "completed",
        "provider": "gemini",
        "model": "gemini-2.5-flash-preview-tts",
        "prompt": f"Scene {scene_number} voiceover line.",
        "generation_config": {"voice": "Kore", "language": "en-US"},
        "product_capability_refs": [],
        "provider_metadata": {},
        "storage_bucket": "media-assets",
        "storage_path": f"{PRODUCTION_ID}/mrun-voice-1/scene-{scene_number}-ffff0000.wav",
        "storage_url": f"https://example.supabase.co/storage/v1/object/public/media-assets/{PRODUCTION_ID}/mrun-voice-1/scene-{scene_number}-ffff0000.wav",
        "mime_type": "audio/wav",
        "width": None,
        "height": None,
        "duration_seconds": duration_seconds,
        "cost_amount": None,
        "cost_currency": None,
        "error_message": None,
        "created_at": created_at,
    }
    row.update(kw)
    return row


class AssemblyFakeDatabase(RpcFakeDatabase):
    """RpcFakeDatabase plus an emulation of `save_media_assembly`."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.tables.setdefault("media_assemblies", [])

    def rpc(self, function, params):
        if function == "save_media_assembly":
            self.rpc_calls.append((function, params))
            return self._save_media_assembly(params["payload"])
        return super().rpc(function, params)

    def _save_media_assembly(self, payload):
        if not _HEXKEY_RE.match(payload["run_key"]):
            raise PersistenceError("check violation: run_key format")
        if not payload.get("source_asset_ids"):
            raise PersistenceError("check violation: source_asset_ids must not be empty")
        voice_ids = payload.get("source_voice_asset_ids") or []
        if voice_ids and len(voice_ids) != payload["source_scene_count"]:
            raise PersistenceError("check violation: source_voice_asset_ids must be paired with every scene")
        if payload.get("audio_policy") not in (None, "mute-v1", "voiceover-sync-v1"):
            raise PersistenceError("check violation: audio_policy")
        if payload.get("caption_policy") not in (None, "captions-burned-v1"):
            raise PersistenceError("check violation: caption_policy")
        if payload["status"] == "completed":
            if (payload.get("caption_manifest") is None) != (payload.get("caption_policy") is None):
                raise PersistenceError("check violation: caption_manifest requires caption_policy and vice versa")
        if payload["status"] == "completed":
            if not payload.get("output_storage_path") or payload.get("error_message"):
                raise PersistenceError("check violation: completed assembly needs output, no error")
        elif payload["status"] == "failed":
            if payload.get("output_storage_path") or not payload.get("error_message"):
                raise PersistenceError("check violation: failed assembly needs error, no output")
        else:
            raise PersistenceError("check violation: assembly status")
        self._n += 1
        assembly_id = f"masm-{self._n}"
        row = {
            "id": assembly_id,
            "completed_at": f"2026-01-02T00:00:00.{self._n:06d}+00:00" if payload["status"] == "completed" else None,
            **copy.deepcopy(payload),
        }
        self.tables["media_assemblies"].append(row)
        return {"status": "created", "assembly_id": assembly_id}


def seeded_assembly_db(scene_numbers=(1, 2, 3, 4, 5, 6), **video_kwargs):
    """A database holding one production with `scene_numbers` scenes, each
    with one completed video asset ready to assemble."""
    db = AssemblyFakeDatabase()
    db.tables["production_runs"].append(production_run_row())
    db.tables["productions"].append(production_row(scenes=[scene_row(n) for n in scene_numbers]))
    for n in scene_numbers:
        db.tables["media_assets"].append(
            completed_video_asset_row(n, asset_id=f"vasset-{n}", created_at=f"2026-01-01T00:00:{n:02d}+00:00", **video_kwargs)
        )
    return db


def seeded_assembly_db_with_voice(
    scene_numbers=(1, 2, 3, 4, 5, 6), video_kwargs=None, voice_kwargs=None, voice_scene_numbers=None
):
    """Like `seeded_assembly_db`, plus one completed voiceover asset per
    scene (Assembly V2's minimum requirement). `voice_scene_numbers`
    defaults to the same set as `scene_numbers`; pass a narrower tuple to
    simulate a scene missing its voiceover."""
    db = seeded_assembly_db(scene_numbers, **(video_kwargs or {}))
    for n in voice_scene_numbers if voice_scene_numbers is not None else scene_numbers:
        db.tables["media_assets"].append(
            completed_voice_asset_row(
                n, asset_id=f"aasset-{n}", created_at=f"2026-01-01T01:00:{n:02d}+00:00", **(voice_kwargs or {})
            )
        )
    return db


class FakeAssembler:
    """A FFmpegAssembler stand-in that never touches ffmpeg or the disk."""

    def __init__(self, output=None, error=None):
        from research.ffmpeg_engine import AssembledOutput

        self._output = output or AssembledOutput(
            data=b"fake-mp4-bytes", mime_type="video/mp4", output_duration_seconds=36.0,
            metadata={"width": 1280, "height": 720, "input_count": 6},
        )
        self._error = error
        self.calls: list[list[bytes]] = []

    def assemble(self, clips):
        self.calls.append(list(clips))
        if self._error:
            raise self._error
        return self._output


class FakeAssemblerV2:
    """An `assemble_with_voiceover`-only FFmpegAssembler stand-in that never
    touches ffmpeg or the disk. By default fabricates one `SyncedSceneResult`
    per input scene, using `duration_seconds`/`video_extend`/`audio_pad`
    hints per scene number rather than actually measuring anything."""

    def __init__(self, output=None, error=None, scene_durations=None, burn_error=None, burned_output=None):
        from research.ffmpeg_engine import AssembledOutputV2, SyncedSceneResult, sync_strategy_for

        self._error = error
        self._scene_durations = scene_durations or {}  # scene_number -> (video_s, voice_s)
        self.calls: list[list] = []
        if output is not None:
            self._output = output
        else:
            self._output = None  # built lazily per-call from self._scene_durations, see assemble_with_voiceover
        self._SyncedSceneResult = SyncedSceneResult
        self._sync_strategy_for = sync_strategy_for
        self._AssembledOutputV2 = AssembledOutputV2
        self._burn_error = burn_error
        self._burned_output = burned_output
        self.burn_calls: list[tuple] = []

    def assemble_with_voiceover(self, scenes):
        self.calls.append(list(scenes))
        if self._error:
            raise self._error
        if self._output is not None:
            return self._output
        results = []
        for scene in scenes:
            video_s, voice_s = self._scene_durations.get(scene.scene_number, (6.0, 6.0))
            results.append(self._SyncedSceneResult(
                scene_number=scene.scene_number, video_duration_seconds=video_s, voice_duration_seconds=voice_s,
                scene_duration_seconds=max(video_s, voice_s), sync_strategy=self._sync_strategy_for(video_s, voice_s),
            ))
        total = sum(r.scene_duration_seconds for r in results)
        return self._AssembledOutputV2(
            data=b"fake-synced-mp4-bytes", mime_type="video/mp4", output_duration_seconds=total,
            scene_results=tuple(results), metadata={"width": 1080, "height": 1920, "input_count": len(scenes)},
        )

    def burn_captions(self, video_bytes, cues, *, width, height):
        from research.ffmpeg_engine import AssembledOutput

        self.burn_calls.append((video_bytes, list(cues), width, height))
        if self._burn_error:
            raise self._burn_error
        if self._burned_output is not None:
            return self._burned_output
        duration = max((c.end_seconds for c in cues), default=0.0)
        return AssembledOutput(
            data=b"fake-captioned-mp4-bytes", mime_type="video/mp4", output_duration_seconds=duration,
            metadata={"width": width, "height": height, "caption_count": len(cues)},
        )


class FakeAssemblyStorage:
    """A SupabaseStorage stand-in for Assembly: downloads return canned clip
    bytes keyed by storage_path, uploads never touch the network."""

    def __init__(self, clips_by_path=None, download_error=None, upload_error=None):
        self._clips_by_path = clips_by_path or {}
        self._download_error = download_error
        self._upload_error = upload_error
        self.downloads: list[str] = []
        self.uploads: list[tuple[str, bytes, str]] = []

    def download(self, path):
        self.downloads.append(path)
        if self._download_error:
            raise self._download_error
        return self._clips_by_path.get(path, b"fake-source-clip-bytes")

    def upload(self, path, data, *, content_type):
        from research.media_storage import StoredObject

        self.uploads.append((path, data, content_type))
        if self._upload_error:
            raise self._upload_error
        return StoredObject(
            bucket="media-assets", path=path,
            public_url=f"https://example.supabase.co/storage/v1/object/public/media-assets/{path}",
        )


def fake_command_result(returncode=0, stdout=b"", stderr=b"") -> CommandResult:
    return CommandResult(returncode=returncode, stdout=stdout, stderr=stderr)
