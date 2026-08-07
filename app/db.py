from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._write_lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    submission_key TEXT NOT NULL,
                    team_id TEXT NOT NULL DEFAULT '',
                    repo_url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress TEXT NOT NULL DEFAULT '',
                    project_title TEXT NOT NULL DEFAULT '',
                    commit_sha TEXT NOT NULL DEFAULT '',
                    total_score REAL,
                    overall_confidence REAL,
                    manual_review_recommended INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
                CREATE INDEX IF NOT EXISTS idx_jobs_submission_key ON jobs(submission_key);
                """
            )
            connection.execute(
                "UPDATE jobs SET status='queued', progress='Recovered after restart' WHERE status='processing'"
            )

    @staticmethod
    def submission_key(team_id: str, repo_url: str) -> str:
        return f"{team_id.strip().lower()}|{repo_url.strip().lower().rstrip('/')}"

    def create_or_get_job(self, team_id: str, repo_url: str, force: bool = False) -> tuple[dict[str, Any], bool]:
        key = self.submission_key(team_id, repo_url)
        with self._write_lock, self._connect() as connection:
            if not force:
                row = connection.execute(
                    "SELECT * FROM jobs WHERE submission_key=? ORDER BY created_at DESC LIMIT 1", (key,)
                ).fetchone()
                if row:
                    return self._row(row), False

            job_id = uuid.uuid4().hex
            now = utcnow()
            connection.execute(
                """
                INSERT INTO jobs (
                    id, submission_key, team_id, repo_url, status, progress, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', 'Waiting to start', ?, ?)
                """,
                (job_id, key, team_id.strip(), repo_url.strip(), now, now),
            )
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return self._row(row), True

    def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {
            "status", "progress", "project_title", "commit_sha", "total_score",
            "overall_confidence", "manual_review_recommended", "result_json", "error",
        }
        clean = {key: value for key, value in fields.items() if key in allowed}
        if not clean:
            return
        clean["updated_at"] = utcnow()
        assignments = ", ".join(f"{key}=?" for key in clean)
        values = list(clean.values()) + [job_id]
        with self._write_lock, self._connect() as connection:
            connection.execute(f"UPDATE jobs SET {assignments} WHERE id=?", values)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return self._row(row) if row else None

    def list_jobs(self, limit: int = 250) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._row(row) for row in rows]

    def queued_job_ids(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM jobs WHERE status='queued' ORDER BY created_at"
            ).fetchall()
            return [row["id"] for row in rows]

    def clear_jobs(self) -> int:
        with self._write_lock, self._connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            connection.execute("DELETE FROM jobs")
            return int(count)

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["manual_review_recommended"] = bool(data["manual_review_recommended"])
        if data.get("result_json"):
            try:
                data["result"] = json.loads(data["result_json"])
            except json.JSONDecodeError:
                data["result"] = None
        else:
            data["result"] = None
        return data
