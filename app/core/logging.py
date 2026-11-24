"""
app/core/logging.py
JSON-structured logging for production; coloured console logs in dev.
"""

import logging
import sys

from app.core.config import settings


def setup_logging() -> None:
    level = logging.DEBUG if settings.DEBUG else logging.INFO

    if settings.DEBUG:
        # Human-readable for local dev
        fmt = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
        logging.basicConfig(
            level=level,
            format=fmt,
            datefmt="%H:%M:%S",
            stream=sys.stdout,
        )
    else:
        # JSON lines for log aggregators (Datadog, CloudWatch, etc.)
        try:
            import json_log_formatter

            formatter = json_log_formatter.JSONFormatter()
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(formatter)
            root = logging.getLogger()
            root.setLevel(level)
            root.handlers = [handler]
        except ImportError:
            # Fallback if json_log_formatter not installed
            logging.basicConfig(level=level, stream=sys.stdout)

    # Quiet noisy third-party loggers
    for noisy in ("httpx", "httpcore", "openai", "anthropic", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging initialised — level=%s debug=%s", logging.getLevelName(level), settings.DEBUG
    )
