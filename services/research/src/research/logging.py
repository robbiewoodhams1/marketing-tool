"""Logging setup shared by all research stages."""

from __future__ import annotations

import logging

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)


def get_logger(name: str) -> logging.Logger:
    """Return a logger namespaced under `research`, e.g. `research.search`."""
    return logging.getLogger(name if name.startswith("research") else f"research.{name}")
