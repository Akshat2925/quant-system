"""Structured logging setup using structlog.

Call configure_logging() once at application startup. After that, every
module gets a structlog logger via `structlog.get_logger()` and all log
records include:
- timestamp (ISO 8601, UTC)
- log level
- module/function
- all keyword args passed to the log call

In production, logs are JSON-formatted for ingestion by any log aggregator
(Loki, CloudWatch, etc.). In development, they are human-readable.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    """Configure structlog + stdlib logging for the entire application.

    Args:
        level       : log level string ("DEBUG", "INFO", "WARNING", etc.)
        json_output : True for JSON logs (production), False for console (dev)
    """
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
