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
        )
