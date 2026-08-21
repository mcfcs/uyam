"""Structured logging configuration for Uyám.

Emits key=value structured log lines. Never logs:
  - raw usernames
  - proxy credentials
  - Reddit client secrets or HMAC keys
  - full post/comment text
  - full environment dumps
"""

from __future__ import annotations

import logging
import os
import sys


class _FlushStreamHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Flush after every record so Streamlit can tail collect.log live."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


class StructuredFormatter(logging.Formatter):
    """Formats log records as: LEVEL timestamp event key=value ..."""

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, self.datefmt)
        base = f"{record.levelname} {ts} {record.getMessage()}"

        extras = []
        skip = frozenset(
            {
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "message",
                "asctime",
                "taskName",
                "threadId",
                "processId",
            }
        )
        for key, val in record.__dict__.items():
            if key.startswith("_") or key in skip:
                continue
            extras.append(f"{key}={val!r}")

        if extras:
            return f"{base} {' '.join(extras)}"
        return base


def configure_logging(
    level: str = "INFO",
    log_file: str | None = None,
) -> None:
    """Configure root logger with structured formatting."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    # Streamlit redirects the child stdout/stderr into collect.log; a second
    # FileHandler on the same path would duplicate every line.
    if os.environ.get("UYAM_LOG_TO_STDOUT_ONLY"):
        log_file = None

    formatter = StructuredFormatter(datefmt="%Y-%m-%dT%H:%M:%S%z")

    stdout_handler = _FlushStreamHandler(sys.stderr)
    stdout_handler.setFormatter(formatter)
    root.addHandler(stdout_handler)

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("prawcore").setLevel(logging.WARNING)
    logging.getLogger("praw").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("patchright").setLevel(logging.WARNING)
