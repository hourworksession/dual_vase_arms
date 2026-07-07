"""Project-wide logger configured for human-readable console output."""
from __future__ import annotations

import logging

from rich.logging import RichHandler


def get_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        log.setLevel(logging.INFO)
        log.addHandler(RichHandler(rich_tracebacks=True, show_path=False))
    return log
