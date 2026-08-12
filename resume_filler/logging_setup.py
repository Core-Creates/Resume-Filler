"""Logging configuration.

Replaces the original popup based feedback, which blocked on a GUI toolkit that
is no longer distributable and gave no record of what happened during a run.
"""

from __future__ import annotations

import contextlib
import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%H:%M:%S"

# Selenium and urllib3 are extremely chatty at DEBUG.
NOISY_LOGGERS = ("selenium", "urllib3", "pdfminer", "requests")


def configure_console_encoding() -> None:
    """Make stdout and stderr tolerate non-ASCII text.

    Real application forms use characters the Windows console default (cp1252)
    cannot encode, such as the heavy asterisk many ATS use to mark a required
    field. Printing a plan containing one raises UnicodeEncodeError and takes
    the whole run down, which is a crash caused purely by rendering.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            # A redirected or already-detached stream is not worth failing over.
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")


def configure_logging(verbose: bool = False) -> None:
    """Send application logs to stderr so stdout stays usable for reports."""
    configure_console_encoding()
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
