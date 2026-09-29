"""Shared fixtures for the media-generation tests. Nothing here touches the network."""

from __future__ import annotations

import copy
import re

from opportunity_fixtures import PRODUCT  # noqa: F401  (re-exported for convenience)
from test_persistence import FakeDatabase

from research.media_providers import GeneratedImage
from research.persistence import PersistenceError

PRODUCTION_ID = "prod-1"
PRODUCTION_RUN_ID = "prodrun-1"


def scene_row(number=1, **kw):
    row = {
        "number": number,
        "duration_seconds": 6,
        "purpose": f"Scene {number} purpose",
        "voiceover": f"Scene {number} voiceover line.",
        "on_screen_text": f"Scene {number} text",
        "visual_direction": f"Scene {number} visual direction, showing quotes and templates.",
        "product_capability_refs": ["quotes"] if number == 1 else ["reusable_templates"],
        "transition": None,
    }
    row.update(kw)
    return row


def production_run_row(**kw):
    # Media generation always resolves its product from the REAL registry
    # (there is no --product flag: it is inherited from the production, which
    # inherited it from its opportunity), so this must be a real, registered
    # product key. "quotes"/"reusable_templates" (used by scene_row below)
    # are real TradeFlow capabilities too, so no further fixture changes are
    # needed for this to resolve correctly end to end.
    row = {"id": PRODUCTION_RUN_ID, "product_key": "tradeflow", "product_version": "tradeflow-v1"}
    row.update(kw)
    return row


def production_row(**kw):
    row = {
        "id": PRODUCTION_ID,
        "production_run_id": PRODUCTION_RUN_ID,
        "opportunity_id": "opp-1",
        "title": "Stop rebuilding the same quote",
        "hook": "Still typing the same job details in?",
        "target_audience": "UK sole traders",
        "objective": "activation",
        "platform": "tiktok",
        "estimated_duration_seconds": 16,
        "scenes": [scene_row(1), scene_row(2)],
        "script": "hook\n\nvo1\n\nvo2\n\ncta",
        "cta": "Build your first template today.",
        "caption": "Stop rebuilding the same quote every time.",
        "hashtags": ["soletrader"],
        "product_capability_refs": ["quotes", "reusable_templates"],
        "limitations": "Illustrative example.",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    row.update(kw)
    return row


def good_image(**kw):
    fields = dict(data=b"\x89PNG-fake-bytes", mime_type="image/png", width=1080, height=1920, metadata={})
    fields.update(kw)
    return GeneratedImage(**fields)


class FakeImageProvider:
    """An ImageProvider that never touches the network.

    `side_effects`, if given, is consumed one entry per call (a `GeneratedImage`
    is returned, an `Exception` instance is raised); once exhausted, `image`/
    `error` (a single fixed outcome) apply to every further call.
    """

    def __init__(self, image=None, model="fake-image-model", error=None, side_effects=None):
        self.model = model
        self._image = image or good_image()
        self._error = error
        self._side_effects = list(side_effects) if side_effects else None
        self.calls: list[tuple[str, str, dict]] = []

    def generate_image(self, prompt, *, aspect_ratio="9:16", **options):
        self.calls.append((prompt, aspect_ratio, options))
        if self._side_effects:
            outcome = self._side_effects.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        if self._error:
            raise self._error
        return self._image


class FakeStorage:
    """A SupabaseStorage stand-in that never touches the network."""

    def __init__(self, fail=False):
        self.fail = fail
        self.uploads: list[tuple[str, bytes, str]] = []

    def upload(self, path, data, *, content_type):
        from research.media_storage import StorageError, StoredObject

        self.uploads.append((path, data, content_type))
        if self.fail:
            raise StorageError("simulated storage failure")
        return StoredObject(bucket="media-assets", path=path, public_url=f"https://example.supabase.co/storage/v1/object/public/media-assets/{path}")


_HEXKEY_RE = re.compile(r"^[0-9a-f]{64}$")


class RpcFakeDatabase(FakeDatabase):
    """FakeDatabase plus an emulation of `save_media_run` / `save_media_asset`."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for t in ("productions", "production_runs", "media_runs", "media_assets"):
            self.tables.setdefault(t, [])
        self.rpc_calls = []

    def rpc(self, function, params):
        self.rpc_calls.append((function, params))
        if function == "save_media_run":
            return self._save_media_run(params["payload"])
        if function == "save_media_asset":
            return self._save_media_asset(params["payload"])
        raise AssertionError(f"unexpected rpc {function}")

    def _save_media_run(self, payload):
        if not _HEXKEY_RE.match(payload["run_key"]):
            raise PersistenceError("check violation: run_key format")
        for existing in self.tables["media_runs"]:
            if existing["run_key"] == payload["run_key"]:
                return {"status": "exists", "run_id": existing["id"]}
        self._n += 1
        run_id = f"mrun-{self._n}"
        self.tables["media_runs"].append({"id": run_id, **copy.deepcopy(payload)})
        return {"status": "created", "run_id": run_id}

    def _save_media_asset(self, payload):
        run = next((r for r in self.tables["media_runs"] if r["id"] == payload["media_run_id"]), None)
        if run is None:
            raise PersistenceError(f"save_media_asset: media_run {payload['media_run_id']} does not exist")
        if run["production_id"] != payload["production_id"]:
            raise PersistenceError("save_media_asset: production_id does not match the media run's own")
        if payload["status"] == "completed":
            if not payload.get("storage_path") or payload.get("error_message"):
                raise PersistenceError("check violation: completed asset needs storage, no error")
        elif payload["status"] == "failed":
            if payload.get("storage_path") or not payload.get("error_message"):
                raise PersistenceError("check violation: failed asset needs error, no storage")
        else:
            raise PersistenceError("check violation: asset status")
        self._n += 1
        asset_id = f"masset-{self._n}"
        self.tables["media_assets"].append({"id": asset_id, **copy.deepcopy(payload)})
        return {"status": "created", "asset_id": asset_id}


def seeded_db(**kw):
    """A database holding one production (via its production_run), ready for
    media generation."""
    db = RpcFakeDatabase()
    db.tables["production_runs"].append(production_run_row())
    db.tables["productions"].append(production_row(**kw))
    return db
