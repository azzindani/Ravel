"""Shard writing: many documents per file, never one file per document.

! Object stores and HF datasets both degrade badly at millions of small objects.
30,000 documents written as 30,000 files is 30,000 uploads and 30,000 listings on
every resume; the same data as ~30 parquet shards is trivially fast
(`EXECUTION.md` §4, `INTERFACES.md` §5).

The shard is also the **commit unit**. Documents accumulate in memory, the shard is
sealed to disk, and only then are its documents marked done in the ledger. A crash
mid-shard loses at most one shard's work and leaves nothing half-recorded.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from canon import CanonicalDoc, write_shard

#: Rough bytes-per-character for estimating a document's on-disk weight before it is
#: written. Only used to decide when to seal — being wrong costs a differently sized
#: shard, never correctness.
BYTES_PER_CHAR = 1.4

#: Blocks buffered before a shard is sealed regardless of its estimated size.
#:
#: ! This is the memory bound, and it is the cap that actually binds. `target_bytes`
#: estimates the *compressed parquet* weight of a shard; the buffered `CanonicalDoc`
#: objects that produce it cost roughly sixty times that, because a pydantic `Block`
#: with a bbox and an attrs dict is ~1 KB of Python object for ~30 bytes on disk.
#: The first ID_Legal run held **5 GB resident** to seal a 41 MB shard — fine on a
#: workstation, an OOM kill on an 8 GB Kaggle box, which is an environment this is
#: required to run in (`EXECUTION.md` §2). Counting blocks tracks that cost directly;
#: counting documents does not, because documents vary by three orders of magnitude.
MAX_BLOCKS = 500_000

#: Documents buffered before sealing. A second, coarser bound for corpora of tiny
#: documents, where the block cap would never be reached but per-document overhead
#: still accumulates.
MAX_DOCS = 5_000


def _write_canon(items: list[Any], path: str) -> int:
    return write_shard(items, path)


def _weigh_canon(doc: Any) -> tuple[int, int]:
    return _estimate(doc), len(doc.blocks)


@dataclass
class SealedShard:
    path: Path
    docs: int
    bytes: int
    keys: list[str]


@dataclass
class ShardWriter:
    """Accumulates canonical documents and seals them into sized parquet shards."""

    directory: Path
    target_bytes: int = 256 * 1024 * 1024
    max_blocks: int = MAX_BLOCKS
    max_docs: int = MAX_DOCS
    prefix: str = "part"
    on_seal: Callable[[SealedShard], None] | None = None

    write: Callable[[list[Any], str], int] = _write_canon
    """How a buffer becomes a file. Swapped for chunks, vectors and anything else that
    shards — the sealing, resume and commit-unit logic is the same for all of them,
    and a second copy of it is a second place for the ledger to be written before its
    artifact is durable."""

    weigh: Callable[[Any], tuple[int, int]] = _weigh_canon
    """`(estimated bytes, units)` for one item, where units feed `max_blocks`."""

    _buffer: list[Any] = field(default_factory=list, init=False)
    _keys: list[str] = field(default_factory=list, init=False)
    _pending_bytes: int = field(default=0, init=False)
    _pending_blocks: int = field(default=0, init=False)
    _index: int = field(default=0, init=False)
    sealed: list[SealedShard] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        # Resume into a fresh shard rather than overwriting an existing one.
        existing = sorted(self.directory.glob(f"{self.prefix}-*.parquet"))
        self._index = len(existing)

    def add(self, item: Any, key: str) -> SealedShard | None:
        """Buffer one item under a stage key, sealing when a bound binds.

        ! Sealed BEFORE the item is buffered when it would not fit, not after.

        Sealing afterwards makes the real bound `cap + one item`, which is fine until
        one item is enormous. The ID_Legal corpus has a 3,359-page scan that extracts
        to 479,355 blocks — 570x the median document — so a 250k cap checked after the
        fact would have held 729k blocks at its peak, nearly triple what was asked for.
        Checking first makes the bound `max(cap, one item)`: still not free, because a
        single document has to fit in memory to be written at all, but it is the
        tightest bound this design can honestly offer, and it no longer degrades
        exactly when the corpus throws something unusual at it.
        """
        size, units = self.weigh(item)
        sealed = None
        if self._buffer and self._would_exceed(size, units):
            sealed = self.seal()

        self._buffer.append(item)
        self._keys.append(key)
        self._pending_bytes += size
        self._pending_blocks += units

        if self._full():
            return self.seal()
        return sealed

    def _would_exceed(self, size: int, units: int) -> bool:
        return (
            self._pending_bytes + size > self.target_bytes
            or (self.max_blocks > 0 and self._pending_blocks + units > self.max_blocks)
        )

    def _full(self) -> bool:
        """Whichever bound binds first wins.

        Size shapes the artifact; blocks and documents bound the process. A run that
        produces slightly small shards is a non-event — a run that is OOM-killed at
        hour six loses everything buffered since the last seal.
        """
        return (
            self._pending_bytes >= self.target_bytes
            or (self.max_blocks > 0 and self._pending_blocks >= self.max_blocks)
            or (self.max_docs > 0 and len(self._buffer) >= self.max_docs)
        )

    def seal(self) -> SealedShard | None:
        """Write the buffer to disk. Returns None when there is nothing to write."""
        if not self._buffer:
            return None
        path = self.directory / f"{self.prefix}-{self._index:05d}.parquet"
        rows = self.write(self._buffer, str(path))
        shard = SealedShard(
            path=path, docs=rows, bytes=path.stat().st_size, keys=list(self._keys)
        )
        self.sealed.append(shard)
        self._index += 1
        self._buffer.clear()
        self._keys.clear()
        self._pending_bytes = 0
        self._pending_blocks = 0
        if self.on_seal:
            self.on_seal(shard)
        return shard

    @property
    def pending(self) -> int:
        return len(self._buffer)

    @property
    def total_docs(self) -> int:
        return sum(s.docs for s in self.sealed) + len(self._buffer)

    def __enter__(self) -> ShardWriter:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, *_: object) -> None:
        # ! Seal on the way out even when the body raised: the documents already
        # extracted are real work, and a partial shard is a resumable checkpoint.
        # Only an interrupt that would corrupt the file should discard it.
        if exc_type is None or issubclass(exc_type, KeyboardInterrupt):
            self.seal()


def _estimate(doc: CanonicalDoc) -> int:
    chars = sum(len(b.text) for b in doc.blocks)
    chars += sum(len(c.text) for t in doc.tables for c in t.cells)
    return int(chars * BYTES_PER_CHAR) + 512


def generation_of(*parts: str) -> str:
    """A short, stable id for one artifact *configuration*.

    ! Artifacts live under this, and the reason is a defect that appeared three times
    in one afternoon before it was named. `ShardWriter` resumes into a fresh shard
    index rather than overwriting — right for resume, wrong when the keys change.
    Re-running a stage after a version or config change therefore *appends* new shards
    beside the stale ones, and the next stage reads both: 931 canonical documents
    became 1,862, and a forced re-chunk left two generations of chunks in one
    directory. Nothing is overwritten, nothing raises, and the count is simply wrong.

    Keying the directory by configuration makes a superseded artifact set inert rather
    than invisible — still on disk, still addressable, no longer in the path anything
    reads.
    """
    material = "\0".join(parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def newest_generation(base: Path) -> Path | None:
    """The most recently written generation under `base`, or None.

    A reasonable default for a workspace, and never a substitute for naming one: a
    reader that wants a specific artifact set must say which.
    """
    if not base.is_dir():
        return None
    found = [d for d in base.iterdir() if d.is_dir() and any(d.glob("part-*.parquet"))]
    return max(found, key=lambda d: d.stat().st_mtime) if found else None


def clear_shards(directory: Path, prefix: str = "part") -> int:
    """Remove a generation's shards so a forced rerun replaces them.

    ! Only ever called for `--force`, and only inside one generation directory.
    Forcing means "recompute this exact configuration", and `ShardWriter` resumes into
    a fresh index rather than overwriting — so without this, forcing *adds* a second
    copy of everything it just recomputed. A generation directory prevents a config
    change from mixing artifact sets; it cannot prevent a rerun of the same config
    from duplicating itself.
    """
    if not directory.is_dir():
        return 0
    shards = list(directory.glob(f"{prefix}-*.parquet"))
    for shard in shards:
        shard.unlink()
    return len(shards)
