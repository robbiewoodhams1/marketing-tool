"""Environment-based configuration.

Nothing here is required to start the service yet. Future stages read their
credentials from the environment via this object; values are never hardcoded.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    log_level: str = "INFO"
    # Optional for now; later stages will validate the ones they need.
    supabase_url: str | None = None
    supabase_key: str | None = None
    # Secret, server-side only: bypasses RLS. Used by research.persistence.
    supabase_service_role_key: str | None = None
    youtube_api_key: str | None = None
    llm_api_key: str | None = None
    # Optional model override for the LLM provider (default set in research.llm).
    llm_model: str | None = None
    # Optional model for synthesis (falls back to llm_model, then the default).
    synthesis_model: str | None = None
    # Optional thinking effort for synthesis (`output_config.effort`). Unset means
    # "send nothing": the model's own default applies. Validated in research.llm.
    synthesis_effort: str | None = None
    # Optional model for opportunity creation (falls back to llm_model, then the default).
    opportunity_model: str | None = None
    # Optional thinking effort for opportunity creation. Same semantics as synthesis_effort.
    opportunity_effort: str | None = None
    # Optional model for production (falls back to llm_model, then the default).
    production_model: str | None = None
    # Optional thinking effort for production. Same semantics as synthesis_effort.
    production_effort: str | None = None
    # Secret, media generation only: the Gemini API's own credential (not llm_api_key -
    # media generation is a different vendor from the Anthropic-backed stages above).
    gemini_api_key: str | None = None
    # Optional provider/model overrides for media generation (defaults set in
    # research.gemini / research.media). MEDIA_VIDEO_PROVIDER has no V1
    # implementation; it is read so a future provider needs no config changes.
    media_image_provider: str | None = None
    media_image_model: str | None = None
    media_video_provider: str | None = None
    media_video_model: str | None = None
    # Supabase Storage bucket generated assets are uploaded to.
    media_storage_bucket: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        return cls(
            log_level=env.get("RESEARCH_LOG_LEVEL", "INFO").upper(),
            supabase_url=env.get("SUPABASE_URL") or None,
            supabase_key=env.get("SUPABASE_KEY") or None,
            supabase_service_role_key=env.get("SUPABASE_SERVICE_ROLE_KEY") or None,
            youtube_api_key=env.get("YOUTUBE_API_KEY") or None,
            llm_api_key=env.get("LLM_API_KEY") or None,
            llm_model=env.get("LLM_MODEL") or None,
            synthesis_model=env.get("SYNTHESIS_MODEL") or None,
            synthesis_effort=env.get("SYNTHESIS_EFFORT") or None,
            opportunity_model=env.get("OPPORTUNITY_MODEL") or None,
            opportunity_effort=env.get("OPPORTUNITY_EFFORT") or None,
            production_model=env.get("PRODUCTION_MODEL") or None,
            production_effort=env.get("PRODUCTION_EFFORT") or None,
            gemini_api_key=env.get("GEMINI_API_KEY") or None,
            media_image_provider=env.get("MEDIA_IMAGE_PROVIDER") or None,
            media_image_model=env.get("MEDIA_IMAGE_MODEL") or None,
            media_video_provider=env.get("MEDIA_VIDEO_PROVIDER") or None,
            media_video_model=env.get("MEDIA_VIDEO_MODEL") or None,
            media_storage_bucket=env.get("MEDIA_STORAGE_BUCKET") or None,
        )
