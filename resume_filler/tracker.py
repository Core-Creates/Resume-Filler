"""SQLite application tracker.

Delivers the CSV functionality an old commit message promised but never shipped,
in a form that survives repeated runs. The unique index on the posting URL is
what stops the tool from applying to the same job twice.
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .models import ApplicationResult, JobPosting

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    url            TEXT NOT NULL,
    title          TEXT NOT NULL DEFAULT '',
    company        TEXT NOT NULL DEFAULT '',
    location       TEXT NOT NULL DEFAULT '',
    source         TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL,
    fields_filled  INTEGER NOT NULL DEFAULT 0,
    fields_review  INTEGER NOT NULL DEFAULT 0,
    message        TEXT NOT NULL DEFAULT '',
    applied_at     TEXT NOT NULL,
    follow_up_at   TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_applications_url ON applications(url);
"""


class Tracker:
    """A small persistent record of every posting the tool has processed."""

    def __init__(self, database_path: str | Path = "applications.db") -> None:
        self.path = Path(database_path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(SCHEMA)
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def already_applied(self, url: str) -> bool:
        """True when this URL has been recorded with a non-failed status."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT status FROM applications WHERE url = ?", (url,)
            ).fetchone()
        return bool(row) and row["status"] not in {"failed", "skipped"}

    def record(self, result: ApplicationResult, follow_up_at: str = "") -> None:
        """Insert or update the record for one application."""
        posting: JobPosting = result.posting
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO applications
                    (url, title, company, location, source, status,
                     fields_filled, fields_review, message, applied_at, follow_up_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    status        = excluded.status,
                    fields_filled = excluded.fields_filled,
                    fields_review = excluded.fields_review,
                    message       = excluded.message,
                    applied_at    = excluded.applied_at
                """,
                (
                    posting.url,
                    posting.title,
                    posting.company,
                    posting.location,
                    posting.source,
                    result.status.value,
                    result.filled_count,
                    result.review_count,
                    result.message,
                    now,
                    follow_up_at,
                ),
            )
            connection.commit()
        logger.debug("Recorded %s as %s", posting.url, result.status.value)

    def all_records(self) -> list[dict[str, object]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM applications ORDER BY applied_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def export_csv(self, destination: str | Path) -> Path:
        """Write the full history to CSV for spreadsheet review."""
        records = self.all_records()
        out_path = Path(destination).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        columns = [
            "applied_at",
            "company",
            "title",
            "location",
            "status",
            "fields_filled",
            "fields_review",
            "url",
            "source",
            "message",
            "follow_up_at",
        ]
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for record in records:
                writer.writerow(record)
        logger.info("Exported %d record(s) to %s", len(records), out_path)
        return out_path

    def summary(self) -> dict[str, int]:
        """Count of records by status."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS total FROM applications GROUP BY status"
            ).fetchall()
        return {row["status"]: row["total"] for row in rows}
