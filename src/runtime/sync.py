"""A workspace that survives its machine: a local directory mirrored to a remote URI.

The rule this serves is `CLAUDE.md` §7.5: never checkpoint to local disk only when the run can
happen on a rented GPU. An instance that is reclaimed takes its disk with it, and a day of
embedding with it. The stages keep working against a local directory, which is fast and what
every library underneath wants; this module is the durable half. It pulls what a resumed run
needs before the run, and pushes what the run produced as it goes.

Three properties make that safe:

* **Shards are immutable, so a push is "add what is missing".** A shard is sealed before its
  ledger key is written (`embed/run.py`), and never rewritten, so uploading it again when the
  size matches is wasted work and uploading it when the size differs means the earlier upload
  was cut short. Either way the rule is the same: upload unless remote has this size.
* **The ledger is pushed as a snapshot, not as a file.** A SQLite database in WAL mode is
  several files that are only consistent together; copying the live one can upload a torn
  database. The snapshot goes through SQLite's own backup, and goes *after* the shards it
  describes, so a ledger on the remote never names a shard the remote does not have.
* **Pull never overwrites local work.** The ledger comes down only when there is none locally.
  A local ledger is by construction at least as new as any snapshot this machine pushed.

Every path is a URI (`CLAUDE.md` §9), so `s3://`, `hf://`, `gs://` and `file://` are the same
code; the tests run it on fsspec's in-memory filesystem.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from uris import resolve

__all__ = ["AsyncPusher", "RemoteMirror", "SyncReport"]

#: What a resumed run needs, relative to the workspace root. `state` holds the ledger
#: snapshot and the progress file; the rest are the sealed artifacts of each stage.
PREFIXES = ("canon", "chunks", "vectors", "state")
#: Never uploaded as files: a live SQLite database is pushed as a snapshot instead.
LEDGER = "ledger.db"
_SKIP_SUFFIXES = (".db", ".db-wal", ".db-shm", ".tmp")


@dataclass
class SyncReport:
    files: int = 0
    bytes: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: SyncReport) -> SyncReport:
        self.files += other.files
        self.bytes += other.bytes
        self.skipped += other.skipped
        self.errors += other.errors
        return self


class RemoteMirror:
    """One workspace directory and the remote prefix that backs it."""

    def __init__(
        self,
        local: Path | str,
        remote: str,
        *,
        prefixes: Iterable[str] = PREFIXES,
        fs: Any = None,
    ) -> None:
        self.local = Path(local)
        self.prefixes = tuple(prefixes)
        if fs is not None:  # the seam tests use: an in-memory filesystem
            self.fs, root = fs, remote
        else:
            self.fs, root = resolve(remote)
        self.root = str(self.fs._strip_protocol(root)).replace("\\", "/").rstrip("/")

    # -- paths ---------------------------------------------------------------------
    def _remote(self, rel: str) -> str:
        return f"{self.root}/{rel}".replace("\\", "/")

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.local).as_posix()

    def _local_files(self, prefix: str) -> Iterable[Path]:
        base = self.local / prefix
        if not base.is_dir():
            return
        for path in sorted(base.rglob("*")):
            if path.is_file() and not path.name.endswith(_SKIP_SUFFIXES):
                yield path

    def _remote_files(self, prefix: str) -> dict[str, int]:
        base = self._remote(prefix)
        if not self.fs.exists(base):
            return {}
        found = self.fs.find(base, detail=True)
        out: dict[str, int] = {}
        for path, info in found.items():
            stripped = str(self.fs._strip_protocol(path)).replace("\\", "/")
            rel = stripped[len(self.root) :].lstrip("/")
            if info.get("type") == "file" and not rel.endswith(_SKIP_SUFFIXES):
                out[rel] = int(info.get("size") or 0)
        return out

    # -- push ----------------------------------------------------------------------
    def push(self, prefixes: Iterable[str] | None = None) -> SyncReport:
        """Upload every local artifact the remote lacks (or holds at another size)."""
        report = SyncReport()
        for prefix in tuple(prefixes) if prefixes is not None else self.prefixes:
            remote = self._remote_files(prefix)
            for path in self._local_files(prefix):
                rel = self._rel(path)
                size = path.stat().st_size
                if remote.get(rel) == size:
                    report.skipped += 1
                    continue
                try:
                    self._put(path, rel)
                except PermissionError as exc:
                    # ! A file the run is rewriting at this instant (the progress file, on
                    # Windows, where a file cannot be opened while it is replaced). Never a
                    # shard: those are sealed before they are listed. Leave it for the next
                    # push, which `AsyncPusher.close` guarantees there is.
                    report.errors.append(f"{rel}: {exc}")
                    continue
                report.files += 1
                report.bytes += size
        return report

    def _put(self, path: Path, rel: str) -> None:
        target = self._remote(rel)
        self.fs.makedirs(target.rsplit("/", 1)[0], exist_ok=True)
        # ! Written under a temporary name and moved into place, so a connection that dies
        # mid-upload leaves a stray file nobody lists, never a short shard that looks whole.
        staging = target + ".part"
        self.fs.put_file(str(path), staging)
        self.fs.mv(staging, target)

    def push_ledger(self, corpus_id: str, name: str = LEDGER) -> bool:
        """Snapshot the ledger through SQLite's backup and upload it. False if there is none.

        Call after `push`: the snapshot names shards, and the remote must already have them.
        """
        source = self.local / "state" / corpus_id / name
        if not source.exists():
            return False
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / name
            src = sqlite3.connect(source)
            dst = sqlite3.connect(snapshot)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            self._put(snapshot, f"state/{corpus_id}/{name}")
        return True

    # -- pull ----------------------------------------------------------------------
    def pull(self, prefixes: Iterable[str] | None = None) -> SyncReport:
        """Download what is missing locally. Existing local files are never overwritten,
        except one of a different size (a download cut short), which is fetched again."""
        report = SyncReport()
        for prefix in tuple(prefixes) if prefixes is not None else self.prefixes:
            for rel, size in self._remote_files(prefix).items():
                target = self.local / rel
                if target.exists() and target.stat().st_size == size:
                    report.skipped += 1
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                staging = target.with_name(target.name + ".part")
                self.fs.get_file(self._remote(rel), str(staging))
                os.replace(staging, target)
                report.files += 1
                report.bytes += size
        return report

    def pull_ledger(self, corpus_id: str, name: str = LEDGER) -> bool:
        """Fetch the remote ledger snapshot only when there is no local one."""
        target = self.local / "state" / corpus_id / name
        remote = self._remote(f"state/{corpus_id}/{name}")
        if target.exists() or not self.fs.exists(remote):
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(target.name + ".part")
        self.fs.get_file(remote, str(staging))
        os.replace(staging, target)
        return True


class AsyncPusher:
    """Pushes in a background thread so a window's upload overlaps the next window's compute.

    One worker, so pushes never overlap each other and the ledger snapshot always follows the
    shards before it. `close()` waits for the queue: a run is not finished until its last
    window is on the remote.
    """

    def __init__(
        self, mirror: RemoteMirror, corpus_id: str, ledgers: Iterable[str] = (LEDGER,)
    ) -> None:
        self.mirror = mirror
        self.corpus_id = corpus_id
        self.ledgers = tuple(ledgers)
        self.report = SyncReport()
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._pending: list[Future[SyncReport]] = []

    def _job(self) -> SyncReport:
        report = self.mirror.push(("vectors", "chunks", "canon", "state"))
        for name in self.ledgers:
            self.mirror.push_ledger(self.corpus_id, name)
        return report

    def submit(self) -> None:
        self._pending.append(self._pool.submit(self._job))

    def close(self) -> SyncReport:
        self.submit()  # the last window, and the final ledger
        for future in self._pending:
            self.report.merge(future.result())
        self._pool.shutdown()
        return self.report
