"""Phase B, third stage driven: chunk shards to vector shards.

    chunk shards -> embed_text -> batch by bytes -> embed -> check -> vector shard -> ledger

The expensive half of the pipeline (a GPU, hours), so the shape is chosen for what goes
wrong on a GPU box: the run dies, the card runs out of memory on the one 30 KB chunk, the
instance is reclaimed. Interruption is the normal path (`EXECUTION.md` §1).

* **The resume unit is a window of `shard_rows` vectors.** A shard is sealed on disk
  before its keys reach the ledger, never the other way round, so a resumed run cannot skip
  work that was never written.
* **Every key includes the embedder's fingerprint and the embed template.** A different
  model, pooling, padding side, instruction or template is a different key and a different
  directory: one variant can never reuse another's vectors (`CLAUDE.md` §7.7).
* **Every window is checked before it is written**: the right width, finite, unit norm when
  the manifest says normalised. A model that returns NaN for one pathological chunk, or a
  pooling bug that returns unit-norm nonsense of the wrong width, is a stack trace here and
  not a corpus.
* **Nothing is skipped.** An unindexable document is embedded too (a few percent of the
  work): the bundle joins vectors to chunks one-to-one and refuses a chunk without one, and
  `indexable` already keeps the document out of search (`QUALITY.md` §6).

`body` is never touched. What the model saw is `embed_text`, a function of the chunk and the
profile, and the template is recorded beside the vectors (`EMBEDDING.md` §5).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from chunk.derive import embed_text
from chunk.store import iter_chunk_range
from embed.progress import Progress, Tracker
from embed.spec import Embedder
from runtime.batching import batched, batched_by_tokens
from runtime.ledger import Ledger, Status
from runtime.shards import generation_of, newest_generation
from spec import Profile, Registry, default_registry
from spec.corpus import CorpusSpec, EmbedSpec

__all__ = [
    "EmbedReport",
    "Embedding",
    "check_vectors",
    "locate_vectors",
    "template_hash_of",
    "window_key",
]

STAGE = "embed"


def window_key(
    *,
    shard: str,
    start: int,
    end: int,
    fingerprint: str,
    template: str,
    chunk_generation: str,
) -> str:
    """The content-addressed key for one window of vectors.

    Everything that changes a vector is in here. `chunk_generation` is too, for the reason
    `chunk.run.stage_key` carries `generation`: shard names repeat across generations, and
    a re-chunked corpus must not find its old vectors "already done".
    """
    material = json.dumps(
        {
            "shard": shard,
            "rows": [start, end],
            "embedder": fingerprint,
            "template": template,
            "chunks": chunk_generation,
        },
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def check_vectors(
    matrix: np.ndarray[Any, Any],
    *,
    dim: int,
    rows: int,
    normalized: bool,
    tolerance: float = 1e-2,
) -> None:
    """Refuse a window that cannot be right.

    ! Asserts the invariant rather than replaying the construction (`CLAUDE.md` §15): the
    shape the manifest promised, finite numbers, and unit norm if it said normalised. A
    pooling or padding bug returns a perfectly shaped, perfectly unit-norm matrix of
    garbage, which this cannot catch and the preflight round trip exists for; what this does
    catch is the loud half of the failures, before they are written.
    """
    if matrix.shape != (rows, dim):
        raise ValueError(f"expected {rows} vectors of {dim} dimensions, got {matrix.shape}")
    if not np.isfinite(matrix).all():
        bad = int((~np.isfinite(matrix).all(axis=1)).sum())
        raise ValueError(f"{bad} of {rows} vectors contain NaN or infinity")
    if normalized:
        norms = np.linalg.norm(matrix.astype(np.float32), axis=1)
        off = np.abs(norms - 1.0) > tolerance
        if off.any():
            raise ValueError(
                f"{int(off.sum())} of {rows} vectors are not unit length "
                f"(norm {float(norms[off][0]):.4f}), but the manifest says normalised"
            )


def template_hash_of(profile: Profile | None) -> str:
    """Hash of the `embed_text` template alone (what `Embedding.template_hash` is)."""
    derived = profile.spec.pipeline.derive.embed_text if profile else None
    template = derived.model_dump(mode="json") if derived else None
    return hashlib.sha256(json.dumps(template, sort_keys=True).encode()).hexdigest()[:16]


def locate_vectors(
    corpus: CorpusSpec,
    *,
    workspace: Path | str,
    variant: str = "default",
    chunk_generation: str | None = None,
    registry: Registry | None = None,
) -> tuple[Path, Path]:
    """`(chunk directory, vector directory)` for a corpus, without loading a model.

    The bundle builder needs to find what `Embedding` wrote, and the directory name is a hash
    of the embedder, the template and the chunk generation; computing it here from the config
    keeps one definition of that name (`Embedding.vector_dir` is the other caller).
    """
    from embed.factory import spec_of

    if corpus.embed is None:
        raise ValueError(f"{corpus.id}: no `embed:` section")
    base = Path(workspace) / "chunks" / corpus.id / variant
    found = base / chunk_generation if chunk_generation else newest_generation(base)
    if found is None or not found.is_dir():
        raise FileNotFoundError(f"no chunks at {base} · run `ravel chunk` first")
    ref = corpus.extract.profile
    reg = registry or default_registry()
    try:
        profile = reg.get(ref) if ref else None
    except KeyError:
        profile = None
    generation = generation_of(
        found.name, spec_of(corpus.embed).fingerprint(), template_hash_of(profile)
    )
    return found, Path(workspace) / "vectors" / corpus.id / variant / generation


@dataclass
class EmbedReport:
    corpus: str
    variant: str
    windows: int = 0
    skipped_done: int = 0
    vectors: int = 0
    shards: int = 0
    bytes_written: int = 0
    characters: int = 0
    seconds: float = 0.0
    longest: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def per_second(self) -> float:
        return self.vectors / self.seconds if self.seconds else 0.0


class Embedding:
    """Runs one corpus, one variant, through the embedding stage."""

    def __init__(
        self,
        corpus: CorpusSpec,
        *,
        workspace: Path | str,
        embedder: Embedder,
        config: EmbedSpec | None = None,
        variant: str = "default",
        chunk_generation: str | None = None,
        registry: Registry | None = None,
        worker: tuple[int, int] | None = None,
    ) -> None:
        if worker is not None:
            index, of = worker
            if of < 1 or not 0 <= index < of:
                raise ValueError(f"worker {index}/{of}: need 0 <= index < count")
        self.worker = worker
        cfg = config or corpus.embed
        if cfg is None:
            raise ValueError(f"{corpus.id}: no `embed:` section, and none was passed")
        self.corpus = corpus
        self.config = cfg
        self.embedder = embedder
        self.workspace = Path(workspace)
        self.variant = variant
        self.registry = registry or default_registry()
        self.profile = self._pinned()

        base = self.workspace / "chunks" / corpus.id / variant
        found = base / chunk_generation if chunk_generation else newest_generation(base)
        if found is None or not found.is_dir():
            raise FileNotFoundError(f"no chunks at {base} · run `ravel chunk` first")
        self.chunk_dir = found

        spec = embedder.spec
        if spec.dim != cfg.dim:
            raise ValueError(
                f"{spec.id}: the embedder declares {spec.dim} dimensions, the corpus "
                f"config {cfg.dim}"
            )

    def _pinned(self) -> Profile | None:
        ref = self.corpus.extract.profile
        if not ref:
            return None
        try:
            return self.registry.get(ref)
        except KeyError:
            return None

    # -- identity -------------------------------------------------------------
    @property
    def template_hash(self) -> str:
        """What the model is shown, apart from the body: the profile's `embed_text` template.

        ! `search_normalize` and the rest of `derive` are not here: they feed the keyword
        index, and changing them must not re-embed anything.
        """
        return template_hash_of(self.profile)

    @property
    def output_generation(self) -> str:
        return generation_of(
            self.chunk_dir.name, self.embedder.spec.fingerprint(), self.template_hash
        )

    @property
    def vector_dir(self) -> Path:
        return (
            self.workspace / "vectors" / self.corpus.id / self.variant / self.output_generation
        )

    @property
    def worker_tag(self) -> str:
        """`-w2of5` for worker 2 of 5, empty for a single process."""
        return f"-w{self.worker[0]}of{self.worker[1]}" if self.worker else ""

    @property
    def ledger_path(self) -> Path:
        # ! One ledger per worker. Two processes writing one SQLite file over a synced object
        # store is how a ledger is corrupted; each worker's windows are disjoint, so each
        # needs only its own record (and reads the shared one, below, for earlier work).
        name = f"ledger-embed{self.worker_tag}.db" if self.worker else "ledger.db"
        return self.workspace / "state" / self.corpus.id / name

    def _stage(self) -> str:
        return f"{STAGE}:{self.variant}"

    # -- one window -------------------------------------------------------------
    @property
    def progress_path(self) -> Path:
        name = f"embed-progress-{self.variant}{self.worker_tag}.json"
        return self.workspace / "state" / self.corpus.id / name

    def owns(self, window_index: int) -> bool:
        """Whether this process embeds the window at this position in corpus order.

        Static round-robin, so workers need no coordination and cannot collide: window k is
        worker `k % count`'s. A window's file name is a function of (shard, start), so a
        window embedded twice (a changed worker count) overwrites itself with identical bytes.
        """
        return self.worker is None or window_index % self.worker[1] == self.worker[0]

    def count_rows(self) -> tuple[int, int]:
        """(chunks, windows) this process will cover, from the parquet footers alone."""
        import pyarrow.parquet as pq

        rows = self.config.shard_rows
        total = windows = index = 0
        for shard in sorted(self.chunk_dir.glob("part-*.parquet")):
            n = int(pq.ParquetFile(str(shard)).metadata.num_rows)
            for begin in range(0, n, rows):
                if self.owns(index):
                    total += min(rows, n - begin)
                    windows += 1
                index += 1
        return total, windows

    def embed_texts(
        self,
        texts: list[str],
        on_batch: Callable[[int, int, float], None] | None = None,
    ) -> np.ndarray[Any, Any]:
        """Embed in batches bounded by payload size, longest first, in the input order.

        Longest first so an out-of-memory fails in the first minute and not the fourth hour;
        results go back to their own positions, so the order the chunks were read in is the
        order the vectors come out in. `on_batch(items, characters)` is called after each
        batch, which is what makes progress visible between sealed windows.
        """
        lengths_of = getattr(self.embedder, "token_lengths", None)
        tokens = self.config.batch_tokens
        if tokens > 0 and lengths_of is not None:
            # True token lengths, longest first, filled to a padded-token budget: the padding
            # in a batch is the gap between neighbours. By bytes, a third of the GPU's work
            # was padding on this corpus (`ravel embed` benchmark, 2026-11-08).
            lengths = lengths_of(texts)
            order = sorted(range(len(texts)), key=lambda i: lengths[i], reverse=True)
            batches = batched_by_tokens(
                order, lengths, budget=tokens, max_items=self.config.batch_items
            )
        else:
            order = sorted(range(len(texts)), key=lambda i: len(texts[i]), reverse=True)
            batches = None
        out = np.empty((len(texts), self.embedder.spec.dim), dtype=np.float32)

        def run_batch(batch: list[int]) -> tuple[np.ndarray[Any, Any], float]:
            began = time.perf_counter()
            vectors = np.asarray(
                self.embedder.embed_documents([texts[i] for i in batch]), dtype=np.float32
            )
            took = time.perf_counter() - began
            if vectors.shape[0] != len(batch):
                raise ValueError(
                    f"sent {len(batch)} texts, received {vectors.shape[0]} vectors"
                )
            return vectors, took

        def land(batch: list[int], vectors: np.ndarray[Any, Any], took: float) -> None:
            out[batch] = vectors
            if on_batch is not None:
                on_batch(len(batch), sum(len(texts[i]) for i in batch), took)

        if batches is None:
            batches = batched(
                order,
                text_of=lambda i: texts[i],
                max_bytes=self.config.batch_bytes,
                max_items=self.config.batch_items,
                max_square=self.config.batch_square,
            )
        width = self.config.concurrency if getattr(self.embedder, "thread_safe", False) else 1
        if width <= 1:
            for batch in batches:
                land(batch, *run_batch(batch))
            return out

        # ! Several requests in flight, landed in submission order. A serving engine batches
        # across requests; one at a time leaves it idle between round trips. At most
        # `width` are outstanding, so a slow server is a slow run, not a pile of memory, and
        # an exception surfaces at the batch that raised it instead of at the end.
        with ThreadPoolExecutor(max_workers=width) as pool:
            pending: deque[tuple[list[int], Future[tuple[np.ndarray[Any, Any], float]]]] = (
                deque()
            )
            for batch in batches:
                pending.append((batch, pool.submit(run_batch, batch)))
                if len(pending) >= width:
                    done_batch, future = pending.popleft()
                    land(done_batch, *future.result())
            while pending:
                done_batch, future = pending.popleft()
                land(done_batch, *future.result())
        return out

    # -- the run ------------------------------------------------------------------
    def run(
        self,
        *,
        limit: int = 0,
        force: bool = False,
        on_progress: Callable[[EmbedReport], None] | None = None,
        on_status: Callable[[Progress], None] | None = None,
        status_every: float = 5.0,
    ) -> EmbedReport:
        report = EmbedReport(corpus=self.corpus.id, variant=self.variant)
        started = time.time()
        self.vector_dir.mkdir(parents=True, exist_ok=True)

        total, windows_total = self.count_rows()
        tracker = Tracker(
            corpus=self.corpus.id,
            variant=self.variant,
            total=total,
            windows_total=windows_total,
            path=self.progress_path,
            every=status_every,
            device=getattr(self.embedder, "device_status", None),
            on_update=on_status,
        )
        tracker.emit(force=True)
        try:
            return self._run(
                report, started, tracker, limit=limit, force=force, on_progress=on_progress
            )
        except BaseException as exc:
            tracker.finish("failed", f"{type(exc).__name__}: {exc}"[:300])
            raise

    def _run(
        self,
        report: EmbedReport,
        started: float,
        tracker: Tracker,
        *,
        limit: int,
        force: bool,
        on_progress: Callable[[EmbedReport], None] | None,
    ) -> EmbedReport:
        spec = self.embedder.spec
        rows = self.config.shard_rows
        window_index = [0]

        if force and self.worker:
            raise ValueError(
                "--force with --worker would delete the shards other workers wrote; "
                "force from a single process"
            )
        with Ledger(self.ledger_path) as ledger:
            if force:
                ledger.forget(self._stage())
                for stale in self.vector_dir.glob("part-*.parquet"):
                    stale.unlink()
            done = ledger.done_keys(self._stage())
            shared = self.workspace / "state" / self.corpus.id / "ledger.db"
            if self.worker and shared.exists():  # windows an earlier single process sealed
                with Ledger(shared) as earlier:
                    done |= earlier.done_keys(self._stage())

            def embed_window(shard: Path, begin: int, end: int, key: str) -> bool:
                """Embed one window. False once `limit` has been reached."""
                with tracker.phase("read"):
                    chunks = list(iter_chunk_range(str(shard), begin, end))
                with tracker.phase("prepare"):
                    texts = [embed_text(c, self.profile) for c in chunks]
                matrix = self.embed_texts(texts, on_batch=tracker.advance)
                with tracker.phase("write"):
                    check_vectors(
                        matrix, dim=spec.dim, rows=len(chunks), normalized=spec.normalize
                    )
                    # ! Imported here: `bundle` imports `embed.spec`, and this module is
                    # loaded by `embed/__init__`, so a top-level import is a cycle.
                    from bundle.writer import write_vectors

                    path = self.vector_dir / f"{shard.stem}-{begin:08d}.parquet"
                    write_vectors(
                        path,
                        [c.id for c in chunks],
                        matrix,
                        dim=spec.dim,
                        dtype=self.config.vector_dtype,
                    )
                    # ! After the shard is durable, never before.
                    ledger.record_many([(key, self._stage(), Status.DONE, None, str(path))])
                tracker.seal(len(chunks))
                report.vectors += len(chunks)
                report.shards += 1
                report.bytes_written += path.stat().st_size
                report.characters += sum(len(t) for t in texts)
                report.longest = max(report.longest, max(len(t) for t in texts))
                if on_progress:
                    on_progress(report)
                return not (limit and report.vectors >= limit)

            for shard in sorted(self.chunk_dir.glob("part-*.parquet")):
                total = int(pq.ParquetFile(str(shard)).metadata.num_rows)
                for begin in range(0, total, rows):
                    end = min(begin + rows, total)
                    index = window_index[0]
                    window_index[0] += 1
                    if not self.owns(index):
                        continue
                    key = window_key(
                        shard=shard.name,
                        start=begin,
                        end=end,
                        fingerprint=spec.fingerprint(),
                        template=self.template_hash,
                        chunk_generation=self.chunk_dir.name,
                    )
                    report.windows += 1
                    # ! A sealed window is skipped without reading its rows. A resume used to
                    # rebuild every earlier chunk object just to throw it away, and that cost
                    # grows with progress: the later a box is reclaimed, the longer its
                    # replacement idles before the first new vector.
                    if key in done:
                        report.skipped_done += 1
                        tracker.skip(end - begin)
                        continue
                    if not embed_window(shard, begin, end, key):
                        report.seconds = time.time() - started
                        tracker.finish("done", f"stopped at --limit {limit:,}")
                        return report

        self._write_manifest(report)
        report.seconds = time.time() - started
        tracker.finish("done")
        return report

    def _write_manifest(self, report: EmbedReport) -> None:
        """What these vectors are, next to the vectors (`BUNDLE.md` §3 reads it back)."""
        (self.vector_dir / "embed.json").write_text(
            json.dumps(
                {
                    "embedder": self.embedder.spec.manifest(),
                    "template_hash": self.template_hash,
                    "chunk_generation": self.chunk_dir.name,
                    "vector_dtype": self.config.vector_dtype,
                    "shard_rows": self.config.shard_rows,
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
