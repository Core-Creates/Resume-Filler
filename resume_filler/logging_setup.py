"""Logging configuration.

Replaces the original popup based feedback, which blocked on a GUI toolkit that
is no longer distributable and gave no record of what happened during a run.
"""

from __future__ import annotations

import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%H:%M:%S"

# Selenium and urllib3 are extremely chatty at DEBUG.
NOISY_LOGGERS = ("selenium", "urllib3", "pdfminer", "requests")


def configure_logging(verbose: bool = False) -> None:
    """Send application logs to stderr so stdout stays usable for reports."""
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
