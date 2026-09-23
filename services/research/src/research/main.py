"""Entry point for the research service."""

from __future__ import annotations

from research import __version__
from research.config import Settings
from research.logging import configure_logging, get_logger


def main() -> int:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    log = get_logger("research.main")
    log.info("Research service %s is running (foundation only)", __version__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
