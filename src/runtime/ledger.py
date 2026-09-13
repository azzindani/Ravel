"""The ledger: what has been done, so a run can be resumed rather than restarted.

! Interruption is the normal path, not an error path (`EXECUTION.md` §1). Kaggle
dies at nine hours, a Vast box gets reclaimed, a laptop lid closes. A run that cannot
be killed at any instant and resumed with zero lost work is broken.

This is SQLite because it is boring, durable and already installed. It is
deliberately **a cache of what exists, not the source of truth** (`STACK.md` §4):
every entry is derivable by listing the artifacts, so losing this file costs a
re-listing, not a re-run. That is why there is no distributed queue here and no
orchestrator — the work is content-addressed, so "what still needs doing" is just
set subtraction.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    key        TEXT PRIMARY KEY,   -- the content-addressed stage key
    stage      TEXT NOT NULL,
    doc_id     TEXT,
    status     TEXT NOT NULL,      -- done | failed | quarantined
    shard      TEXT,               -- where the artifact landed, once sealed
    detail     TEXT,               -- failure reason, kept verbatim
    attempts   INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS entries_stage_idx  ON entries (stage, status);
CREATE INDEX IF NOT EXISTS entries_doc_idx    ON entries (doc_id);
"""


class Status(StrEnum):
    DONE = "done"
    FAILED = "failed"
    """Something broke. Retryable, and counted against the corpus tolerance."""

    QUARANTINED = "quarantined"
    """Deterministically unprocessable by this configuration. Never retried."""

    DEFERRED = "deferred"
    """Waiting on a stage that does not exist yet — a scanned page with no OCR path.

    ! Separate from FAILED on purpose, and counted just as loudly. A deferred
    document is not an error, so it must not fail an otherwise healthy run; but it
    is also NOT in the corpus, so burying it inside a failure count (or worse,
    ignoring it) is the silent-incompleteness failure of `LOOPHOLES.md` §3.
    """


@dataclass(frozen=True, slots=True)
class Entry:
    key: str
    stage: str
    doc_id: str | None
    status: Status
    shard: str | None
    detail: str | None
    attempts: int


class Ledger:
    """Append-mostly record of completed work, keyed by stage key."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        # WAL so a reader (`ravel status`) never blocks a running build.
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)

    # -- reads --------------------------------------------------------------
    def done_keys(self, stage: str) -> set[str]:
        rows = self._db.execute(
            "SELECT key FROM entries WHERE stage = ? AND status = 'done'", (stage,)
        )
        return {row["key"] for row in rows}

    def keys_with_status(self, stage: str, status: Status) -> set[str]:
        rows = self._db.execute(
            "SELECT key FROM entries WHERE stage = ? AND status = ?", (stage, status.value)
        )
        return {row["key"] for row in rows}

    def counts(self, stage: str) -> dict[str, int]:
        rows = self._db.execute(
            "SELECT status, COUNT(*) AS n FROM entries WHERE stage = ? GROUP BY status",
            (stage,),
        )
        return {row["status"]: row["n"] for row in rows}

    def failures(self, stage: str, limit: int = 0) -> list[Entry]:
        sql = (
            "SELECT * FROM entries WHERE stage = ? AND status != 'done' "
            "ORDER BY updated_at DESC"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [_entry(row) for row in self._db.execute(sql, (stage,))]

    def attempts(self, key: str) -> int:
        row = self._db.execute("SELECT attempts FROM entries WHERE key = ?", (key,)).fetchone()
        return row["attempts"] if row else 0

    # -- writes -------------------------------------------------------------
    def record(
        self,
        key: str,
        stage: str,
        status: Status,
        *,
        doc_id: str | None = None,
        shard: str | None = None,
        detail: str | None = None,
    ) -> None:
        self._db.execute(
            """
            INSERT INTO entries
                (key, stage, doc_id, status, shard, detail, attempts, updated_at)
            VALUES (?,?,?,?,?,?,1,?)
            ON CONFLICT(key) DO UPDATE SET
                status = excluded.status,
                shard = excluded.shard,
                detail = excluded.detail,
                doc_id = COALESCE(excluded.doc_id, entries.doc_id),
                attempts = entries.attempts + 1,
                updated_at = excluded.updated_at
            """,
            (key, stage, doc_id, status.value, shard, detail, _now()),
        )

    def record_many(
        self, entries: Iterable[tuple[str, str, Status, str | None, str | None]]
    ) -> int:
        """Mark a batch done in one transaction.

        ! Called only AFTER a shard is sealed on disk. Marking work done before its
        artifact is durable is how a resumed run skips something that was never
        written (`EXECUTION.md` §4).
        """
        rows = [
            (key, stage, doc_id, status.value, shard, None, _now())
            for key, stage, status, doc_id, shard in entries
        ]
        if not rows:
            return 0
        with self.transaction():
            self._db.executemany(
                """
                INSERT INTO entries
                    (key, stage, doc_id, status, shard, detail, attempts, updated_at)
                VALUES (?,?,?,?,?,?,1,?)
                ON CONFLICT(key) DO UPDATE SET
                    status = excluded.status,
                    shard = excluded.shard,
                    attempts = entries.attempts + 1,
                    updated_at = excluded.updated_at
                """,
                rows,
            )
        return len(rows)

    def forget(self, stage: str, *, status: Status | None = None) -> int:
        """Drop entries so they are recomputed. Used when a stage version changes."""
        if status:
            cur = self._db.execute(
                "DELETE FROM entries WHERE stage = ? AND status = ?", (stage, status.value)
            )
        else:
            cur = self._db.execute("DELETE FROM entries WHERE stage = ?", (stage,))
        return cur.rowcount

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self._db.execute("BEGIN")
        try:
            yield
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        else:
            self._db.execute("COMMIT")

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _entry(row: sqlite3.Row) -> Entry:
    return Entry(
        key=row["key"],
        stage=row["stage"],
        doc_id=row["doc_id"],
        status=Status(row["status"]),
        shard=row["shard"],
        detail=row["detail"],
        attempts=row["attempts"],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
