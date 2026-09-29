"""Upload generated media bytes to Supabase Storage and return a reference.

Binary media is never persisted in Postgres (Part 7 of this chapter's spec):
`research.media_store` stores only the `storage_bucket`/`storage_path`/
`storage_url` this module returns. This is a different Supabase API from
PostgREST (used by `research.persistence.SupabaseDatabase`) - the Storage REST
API - over the same service-role credential. Raw HTTP, no vendor SDK, an
injectable http_client for tests, mirroring `SupabaseDatabase`'s own approach.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from research.logging import get_logger

log = get_logger("research.media_storage")

DEFAULT_BUCKET = "media-assets"

_EXTENSION_BY_MIME_TYPE = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "video/mp4": "mp4",
    "audio/wav": "wav",
}


class StorageError(Exception):
    """A Supabase Storage operation failed."""


class StorageConfigError(StorageError):
    """Supabase URL or service-role key is not configured."""


@dataclass(frozen=True)
class StoredObject:
    bucket: str
    path: str
    public_url: str


def extension_for_mime_type(mime_type: str) -> str:
    return _EXTENSION_BY_MIME_TYPE.get(mime_type, "bin")


def scene_asset_path(*, production_id: str, media_run_id: str, scene_number: int, extension: str) -> str:
    """One path per generation attempt (a short random suffix, not a hash of
    content): idempotency is the database's job (run_key + the asset rows),
    not the storage path's."""
    return f"{production_id}/{media_run_id}/scene-{scene_number}-{uuid.uuid4().hex[:8]}.{extension}"


def assembly_output_path(*, production_id: str, run_key: str, extension: str) -> str:
    """One path per assembly attempt, in the same bucket/production folder as
    the scene assets it was built from, but under its own `assembly/`
    subfolder (there is no scene number for the finished, combined output).
    Same idempotency principle as `scene_asset_path`: a short random suffix,
    not a hash of content - the database (run_key + the assembly rows) is
    the source of truth, not the storage path."""
    return f"{production_id}/assembly/{run_key[:16]}-{uuid.uuid4().hex[:8]}.{extension}"


class SupabaseStorage:
    """Uploads to one bucket over the Storage REST API. Errors become `StorageError`."""

    def __init__(
        self,
        url: str | None,
        service_role_key: str | None,
        *,
        bucket: str = DEFAULT_BUCKET,
        timeout: float = 60.0,
        http_client: Any = None,  # an httpx.Client; injectable for tests
    ):
        if not url or not service_role_key:
            raise StorageConfigError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        import httpx

        self._base = url.rstrip("/")
        self._secret = service_role_key
        self._bucket = bucket
        self._client = http_client or httpx.Client(timeout=timeout)

    def upload(self, path: str, data: bytes, *, content_type: str) -> StoredObject:
        url = f"{self._base}/storage/v1/object/{self._bucket}/{path}"
        headers = {
            "Authorization": f"Bearer {self._secret}",
            "apikey": self._secret,
            "Content-Type": content_type,
            "x-upsert": "false",  # each attempt gets its own path; never silently overwrite
        }
        try:
            response = self._client.post(url, headers=headers, content=data)
        except Exception as exc:
            raise StorageError(f"Supabase Storage upload failed: {type(exc).__name__}") from None
        if response.status_code not in (200, 201):
            detail = response.text[:300].replace(self._secret, "***")
            raise StorageError(f"Supabase Storage upload failed: HTTP {response.status_code}: {detail}")
        return StoredObject(
            bucket=self._bucket, path=path,
            public_url=f"{self._base}/storage/v1/object/public/{self._bucket}/{path}",
        )

    def download(self, path: str) -> bytes:
        """Reads an object back by its storage path (not its public URL) -
        used by Assembly to fetch already-generated scene videos. Uses the
        same authenticated endpoint as `upload`, so it works regardless of
        whether the bucket happens to be public."""
        url = f"{self._base}/storage/v1/object/{self._bucket}/{path}"
        headers = {"Authorization": f"Bearer {self._secret}", "apikey": self._secret}
        try:
            response = self._client.get(url, headers=headers)
        except Exception as exc:
            raise StorageError(f"Supabase Storage download failed: {type(exc).__name__}") from None
        if response.status_code != 200:
            detail = response.text[:300].replace(self._secret, "***")
            raise StorageError(f"Supabase Storage download failed: HTTP {response.status_code}: {detail}")
        return response.content

    @property
    def bucket(self) -> str:
        return self._bucket
