"""Writing the bundle: the layout of `BUNDLE.md` §4, and what verifies it.

Two hashes, two jobs
--------------------
A bundle carries a **recipe** and an **inventory**, and conflating them is how a corpus
ends up unverifiable in a way nobody notices.

- The *recipe* is `BundleManifest.fingerprint()` — model, pooling, instructions, chunker,
  clustering. It excludes `run_id` and `created_at` so that rebuilding the same corpus
  twice produces the same fingerprint. It answers **"are these two bundles the same
  build?"**
- The *inventory* is a sha256 per file plus the row counts. It changes on every write,
  because it describes bytes rather than intent. It answers **"did this bundle arrive
  intact?"**

A single hash cannot do both. One that covers the files cannot compare two builds; one
that covers the recipe cannot detect a truncated shard. The incumbent corpus had neither,
which is why "is this the corpus we think it is" was answerable only by re-running the
job that produced it (`ABSORPTION.md` §18.2).

Why vectors are a separate file set
-----------------------------------
`BUNDLE.md` §4, and it is not tidiness. A re-embed replaces `vectors/` and nothing else —
the chunks, their provenance and their ids are unchanged, so a chunk someone cited last
month still resolves. Writing embeddings into the chunk rows would make a re-embed a
rewrite of the entire corpus, which is both expensive and a chance to lose provenance
that is currently impossible to take.

Immutability
------------
`CLAUDE.md` §7.10: a published bundle is never edited. This writer refuses to open a
version directory that already holds a manifest, because "just re-run it into the same
directory" is how two generations of shards end up side by side and the next stage reads
both — a failure this project has already had once, at a cost of 931 documents becoming
1,862 (`runtime/shards.py`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from bundle.manifest import BundleManifest
from bundle.schema import render_indexes, render_schema

__all__ = [
    "BUNDLE_VERSION",
    "BundleWriter",
    "BundleLayoutError",
    "sha256_file",
    "vector_schema",
    "write_clusters",
    "write_domains",
    "write_edges",
    "write_signals",
    "write_vectors",
]

BUNDLE_VERSION = "1"

#: `halfvec` on Vera's side. Named here so the mapping from a manifest string to a
#: storage type is one table rather than a chain of ifs in three modules.
_DTYPES = {
    "float16": (pa.float16(), np.float16),
    "float32": (pa.float32(), np.float32),
    "float64": (pa.float64(), np.float64),
}


class BundleLayoutError(RuntimeError):
    """A bundle that would be written somewhere it must not be."""


def sha256_file(path: Path, *, chunk: int = 1 << 20) -> str:
    """Hash a file without reading it into memory. Shards are 256MB-1GB."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


# -- the tables that are not chunks -----------------------------------------------------


def vector_schema(dim: int, dtype: str = "float16") -> pa.Schema:
    """`(chunk_id, embedding)` at the manifest's width and precision.

    ! Fixed-size list, not a variable list. The width is a property of the corpus, and a
    fixed-size list makes a short row a write error rather than a row that loads and means
    something else. `CLAUDE.md` §8 says never hardcode the dimension; this is the other
    half of that rule — having read it from the manifest, enforce it.
    """
    if dtype not in _DTYPES:
        raise BundleLayoutError(
            f"unknown vector dtype {dtype!r}; expected one of {sorted(_DTYPES)}"
        )
    return pa.schema(
        [
            ("chunk_id", pa.string()),
            ("embedding", pa.list_(_DTYPES[dtype][0], dim)),
        ]
    )


def write_vectors(
    path: str | Path,
    ids: Sequence[str],
    matrix: np.ndarray,
    *,
    dim: int,
    dtype: str = "float16",
) -> int:
    """One vector shard. Returns the row count.

    The dimension check is the point of this function. A bundle and a database are married
    by dimension (`EMBEDDING.md` §2), and a mismatch discovered at `COPY` time has already
    cost the GPU-days that produced the vectors.
    """
    array = np.asarray(matrix)
    if array.ndim != 2:
        raise BundleLayoutError(f"expected a 2-D (rows, dim) array, got shape {array.shape}")
    if array.shape[1] != dim:
        raise BundleLayoutError(
            f"the manifest declares {dim} dimensions and these vectors are "
            f"{array.shape[1]}-dimensional. These are different spaces, not a reshape "
            f"(CLAUDE.md §7.3)."
        )
    if len(ids) != array.shape[0]:
        raise BundleLayoutError(
            f"{len(ids):,} chunk ids for {array.shape[0]:,} vectors — a vector shard whose "
            f"ids do not line up cannot be joined to chunks at load, and the failure is a "
            f"silent mis-join rather than an error"
        )

    schema = vector_schema(dim, dtype)
    flat = pa.array(np.ascontiguousarray(array, dtype=_DTYPES[dtype][1]).reshape(-1))
    table = pa.Table.from_arrays(
        [
            pa.array(list(ids), type=pa.string()),
            pa.FixedSizeListArray.from_arrays(flat, dim),
        ],
        schema=schema,
    )
    pq.write_table(table, str(path), compression="zstd")
    return table.num_rows


DOMAIN_SCHEMA = pa.schema(
    [
        ("domain_id", pa.string()),
        ("description", pa.large_string()),
        ("anchor_index", pa.int32()),
        ("anchor", pa.list_(pa.float32())),
        ("threshold", pa.float32()),
        ("calibration", pa.string()),  # JSON: the evidence behind the threshold
        ("row_count", pa.int64()),
    ]
)


def write_domains(path: str | Path, rows: Iterable[dict[str, Any]]) -> int:
    """Layer 1.

    ! `description` and `calibration` are columns, not notes in a README. A threshold
    without the distributions it was chosen from is a magic number, and the next person to
    touch it — six months on, without the eval set to hand — can only guess whether moving
    it is safe (`CLUSTERING.md` §4).
    """
    materialised = [
        {**row, "calibration": json.dumps(row.get("calibration") or {}, sort_keys=True)}
        for row in rows
    ]
    table = pa.Table.from_pylist(materialised, schema=DOMAIN_SCHEMA)
    pq.write_table(table, str(path), compression="zstd")
    return table.num_rows


CLUSTER_SCHEMA = pa.schema(
    [
        ("cluster_id", pa.int32()),
        ("domain_id", pa.string()),
        ("generation", pa.int32()),
        ("centroid", pa.list_(pa.float32())),
        ("row_count", pa.int64()),
        ("mean_distance", pa.float32()),
    ]
)


def write_clusters(
    path: str | Path,
    centroids: np.ndarray,
    sizes: Sequence[int],
    *,
    domain_id: str,
    generation: int,
    mean_distances: Sequence[float] | None = None,
) -> int:
    """Layer 2, with the per-cluster row counts `CLUSTERING.md` §5 requires.

    `row_count` is written because Vera's sequential loading is sized against it: a
    routing structure that does not say how big its clusters are cannot be budgeted for,
    and the discovery that one holds 40% of the corpus should happen here rather than
    under a query.
    """
    matrix = np.asarray(centroids, dtype=np.float32)
    if matrix.shape[0] != len(sizes):
        raise BundleLayoutError(
            f"{matrix.shape[0]} centroids and {len(sizes)} row counts — the cluster table "
            f"would describe a structure that was not built"
        )
    distances = list(mean_distances or [0.0] * len(sizes))
    table = pa.Table.from_pylist(
        [
            {
                "cluster_id": i,
                "domain_id": domain_id,
                "generation": generation,
                "centroid": matrix[i].tolist(),
                "row_count": int(sizes[i]),
                "mean_distance": float(distances[i]),
            }
            for i in range(matrix.shape[0])
        ],
        schema=CLUSTER_SCHEMA,
    )
    pq.write_table(table, str(path), compression="zstd")
    return table.num_rows


EDGE_SCHEMA = pa.schema(
    [
        ("src_chunk_id", pa.string()),
        ("src_doc_id", pa.string()),
        ("kind", pa.string()),
        ("target", pa.string()),
        ("text", pa.large_string()),
        ("start", pa.int32()),
        ("end", pa.int32()),
        ("resolved_doc_id", pa.string()),
    ]
)


def write_edges(path: str | Path, rows: Iterable[dict[str, Any]]) -> int:
    """The graph, as an edge list.

    ! `start` and `end` are columns. `ABSORPTION.md` §6 found the incumbent's entity
    payloads already carried character positions and that is the one thing worth keeping
    unchanged — a mention with a position can be highlighted and re-verified; a mention
    without one is an assertion.

    `resolved_doc_id` is nullable and stays null when the cited instrument is not in this
    corpus. A dangling citation is a fact about the corpus, not an error: roughly half of
    what Indonesian regulations cite is not in any given collection, and filling it with a
    guess would put a wrong edge in a graph whose whole value is that its edges are real.
    """
    table = pa.Table.from_pylist(list(rows), schema=EDGE_SCHEMA)
    pq.write_table(table, str(path), compression="zstd")
    return table.num_rows


SIGNAL_SCHEMA = pa.schema(
    [
        ("chunk_id", pa.string()),
        ("data", pa.large_string()),  # JSON, loaded into JSONB
    ]
)


def write_signals(path: str | Path, rows: Iterable[tuple[str, dict[str, Any]]]) -> int:
    """Ranking factors and counts, as JSON.

    Open-ended on purpose: the enricher set changes between variants, and a table whose
    columns change per variant is not a schema. Disposable by design (`CLAUDE.md` §5.4) —
    dropping and rebuilding this never touches `chunks`.
    """
    table = pa.Table.from_pylist(
        [
            {"chunk_id": chunk_id, "data": json.dumps(data, sort_keys=True, ensure_ascii=False)}
            for chunk_id, data in rows
        ],
        schema=SIGNAL_SCHEMA,
    )
    pq.write_table(table, str(path), compression="zstd")
    return table.num_rows


# -- the bundle itself --------------------------------------------------------------------


@dataclass
class BundleWriter:
    """One version directory, written once and then never touched again."""

    root: Path
    manifest: BundleManifest
    provenance_complete: bool = True

    checksums: dict[str, str] = field(default_factory=dict, init=False)
    counts: dict[str, int] = field(default_factory=dict, init=False)
    _sealed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        if (self.root / "manifest.json").exists():
            raise BundleLayoutError(
                f"{self.root} already holds a manifest. A published bundle is never edited "
                f"(CLAUDE.md §7.10) — write a new version directory. Re-running into an "
                f"existing one leaves two generations of shards where the next stage reads "
                f"both."
            )
        for name in ("chunks", "vectors", "signals"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    # -- recording -----------------------------------------------------------------

    def record(self, relative: str, rows: int) -> None:
        """Register a file this bundle owns, hashing it now rather than at seal time.

        Now, because the alternative is a seal step that re-reads every shard in the
        bundle — hundreds of gigabytes for a checksum that could have been taken while the
        bytes were still warm.
        """
        path = self.root / relative
        if not path.exists():
            raise BundleLayoutError(f"{relative} was recorded but does not exist")
        self.checksums[relative] = sha256_file(path)
        self.counts[relative] = rows

    def shard_path(self, kind: str, index: int) -> Path:
        return self.root / kind / f"part-{index:05d}.parquet"

    def rows_of(self, kind: str) -> int:
        prefix = f"{kind}/"
        return sum(n for name, n in self.counts.items() if name.startswith(prefix))

    def write_reference(self, vector: np.ndarray, text: str) -> None:
        """The canary (`BUNDLE.md` §3): a known string and the vector this model gave it.

        ! Written at the embedder's native precision, not the corpus storage precision.
        The canary tests whether Vera's query-side embedder is the same embedder — a
        question about the model, not about the disk format — and rounding it to fp16
        first would fold the storage error into the comparison it exists to make.
        """
        path = self.root / "reference.npy"
        np.save(path, np.asarray(vector, dtype=np.float32))
        self.record("reference.npy", 1)
        (self.root / "reference.txt").write_text(text, encoding="utf-8")
        self.record("reference.txt", 1)

    def write_failed(self, entries: Iterable[dict[str, Any]]) -> int:
        """What did not make it, and why (`BUNDLE.md` §4).

        ! Always written, even when empty. An absent `failed.jsonl` is ambiguous between
        "nothing failed" and "nothing was recorded", and the load-time completeness check
        has to tell those apart to be worth running (`LOOPHOLES.md`).
        """
        path = self.root / "failed.jsonl"
        written = 0
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for entry in entries:
                handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
                written += 1
        self.record("failed.jsonl", written)
        return written

    # -- sealing --------------------------------------------------------------------

    def seal(self, *, clustering: dict[str, Any] | None = None) -> Path:
        """Write `schema.sql` and `manifest.json`. The bundle is finished at this point.

        ! The manifest is written **last**. Its presence is what marks a directory as a
        published bundle, and it is what `__post_init__` refuses to overwrite — so a run
        that dies mid-shard leaves an unsealed directory that can be cleared and retried,
        rather than a bundle that looks complete and is missing its tail.
        """
        if self._sealed:
            raise BundleLayoutError(f"{self.root} has already been sealed")

        # ! Two files, and the split is not cosmetic. `schema.sql` creates the tables;
        # `indexes.sql` is applied after the load, because index maintenance during `COPY`
        # turns a 20-minute load into a six-hour one (`BUNDLE.md` §6). Emitting one file
        # would leave the ordering to whoever applies it, and the natural thing to do with
        # a file called schema.sql is to apply all of it first.
        schema = render_schema(
            self.manifest,
            provenance_complete=self.provenance_complete,
            with_indexes=False,
        )
        (self.root / "schema.sql").write_text(schema, encoding="utf-8", newline="\n")
        self.record("schema.sql", 0)
        (self.root / "indexes.sql").write_text(
            render_indexes(self.manifest), encoding="utf-8", newline="\n"
        )
        self.record("indexes.sql", 0)

        chunk_rows = self.rows_of("chunks")
        vector_rows = self.rows_of("vectors")
        if chunk_rows and chunk_rows != self.manifest.chunk_count:
            raise BundleLayoutError(
                f"the manifest claims {self.manifest.chunk_count:,} chunks and "
                f"{chunk_rows:,} were written. A bundle whose manifest disagrees with its "
                f"own shards fails its load preflight, so fail here where the cause is "
                f"still visible."
            )
        if vector_rows and chunk_rows and vector_rows != chunk_rows:
            raise BundleLayoutError(
                f"{vector_rows:,} vectors for {chunk_rows:,} chunks. The join at load time "
                f"would drop the difference silently."
            )

        document = {
            "bundle_version": BUNDLE_VERSION,
            "manifest_sha256": self.manifest.fingerprint(),
            **self.manifest.to_dict(),
            "clustering": clustering or {},
            "inventory": {
                "counts": dict(sorted(self.counts.items())),
                "checksums": dict(sorted(self.checksums.items())),
                "files": len(self.checksums),
            },
            "provenance_complete": self.provenance_complete,
        }
        path = self.root / "manifest.json"
        path.write_text(
            json.dumps(document, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
            newline="\n",
        )
        self._sealed = True
        return path


def read_bundle(root: Path) -> dict[str, Any]:
    """Load a sealed bundle's manifest document."""
    path = Path(root) / "manifest.json"
    if not path.exists():
        raise BundleLayoutError(
            f"{root} has no manifest.json — it is an unsealed or abandoned build "
            f"directory, not a bundle"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def iter_vectors(
    path: str | Path, *, batch_size: int = 4096
) -> Iterator[tuple[str, np.ndarray]]:
    """Stream a vector shard back. Phase C and the loader both read millions of these."""
    for batch in pq.ParquetFile(str(path)).iter_batches(batch_size=batch_size):
        ids = batch.column("chunk_id").to_pylist()
        matrix = np.stack([np.asarray(v) for v in batch.column("embedding").to_pylist()])
        for i, chunk_id in enumerate(ids):
            yield chunk_id, matrix[i]
