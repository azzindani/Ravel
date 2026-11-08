"""The lexical arm as a stage: fit one BM25 vocabulary over the whole corpus, then write one
sparse vector per chunk.

Vera needs the vocabulary even where it never scans a `sparse` column: its relevance and
domain gates weigh a query term by the IDF this corpus gave it, and it refuses to start
without the artifact. The vectors are the fallback arm for a deployment without
`pg_search`; shipping the vocabulary without them would leave that arm answering from a
column of NULLs, so the stage writes both or neither.

Two passes, because the second needs the first's result and neither needs a GPU:

1. **fit** streams every chunk body once and keeps document frequencies. One vocabulary
   for the corpus, never one per shard (`ABSORPTION.md` §7: a thousand internally
   consistent vocabularies, none comparable). It is saved before anything else is written,
   and a re-run loads it instead of fitting again.
2. **transform** turns each chunk shard into one sparse shard of the same rows, written to
   a temporary name and renamed, so a killed run leaves whole shards and nothing else and a
   re-run skips what is done.

What is fitted is the chunk `body`, the text Postgres full-text search indexes too; not the
embedding header, which the lexical arm must not match on.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from enrich.sparse import Bm25Vectorizer

__all__ = ["SparseReport", "VOCAB_NAME", "build_sparse", "locate_sparse"]

#: The file Vera is told to load (`BM25_VOCAB`). Same name in the workspace and the bundle.
VOCAB_NAME = "corpus.bm25.json"

SCHEMA = pa.schema([("chunk_id", pa.string()), ("sparse", pa.string())])
BATCH_ROWS = 8192


@dataclass
class SparseReport:
    directory: Path
    fitted_now: bool = False
    documents: int = 0
    dim: int = 0
    shards: int = 0
    skipped: int = 0
    rows: int = 0
    vocab_sha256: str = ""
    notes: list[str] = field(default_factory=list)


def locate_sparse(workspace: Path | str, corpus_id: str, variant: str, generation: str) -> Path:
    """Where the sparse arm of one chunk generation lives. Keyed by the chunk generation: a
    vocabulary describes exactly the chunks it was fitted over and no others."""
    return Path(workspace) / "sparse" / corpus_id / variant / generation


def _shards(chunk_dir: Path) -> list[Path]:
    return sorted(chunk_dir.glob("part-*.parquet"))


def _bodies(shards: list[Path]) -> Iterator[str]:
    for shard in shards:
        for batch in pq.ParquetFile(str(shard)).iter_batches(
            batch_size=BATCH_ROWS, columns=["body"]
        ):
            yield from (text or "" for text in batch.column("body").to_pylist())


def build_sparse(
    chunk_dir: Path | str,
    out_dir: Path | str,
    *,
    max_features: int = 20_000,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> SparseReport:
    """Fit (once) and transform every chunk shard of one chunk generation."""
    chunk_dir = Path(chunk_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    shards = _shards(chunk_dir)
    if not shards:
        raise FileNotFoundError(f"no chunk shards in {chunk_dir} · run `ravel chunk` first")

    report = SparseReport(directory=out)
    vocab_path = out / VOCAB_NAME
    if vocab_path.exists():
        vectorizer = Bm25Vectorizer.load(vocab_path)
    else:
        if on_progress:
            on_progress("fit", 0, len(shards))
        vectorizer = Bm25Vectorizer.fit(_bodies(shards), max_features=max_features)
        tmp = vocab_path.with_suffix(".tmp")
        vectorizer.save(tmp)
        os.replace(tmp, vocab_path)
        report.fitted_now = True
    report.documents = vectorizer.n_docs
    report.dim = vectorizer.dim
    # ! Hashed from the file as written, which is the identity Vera and the manifest check.
    report.vocab_sha256 = Bm25Vectorizer.load(vocab_path).sha256()

    for index, shard in enumerate(shards):
        target = out / shard.name
        if target.exists():
            report.skipped += 1
            report.rows += int(pq.ParquetFile(str(target)).metadata.num_rows)
            continue
        ids: list[str] = []
        literals: list[str] = []
        for batch in pq.ParquetFile(str(shard)).iter_batches(
            batch_size=BATCH_ROWS, columns=["id", "body"]
        ):
            for chunk_id, body in zip(
                batch.column("id").to_pylist(), batch.column("body").to_pylist(), strict=True
            ):
                ids.append(chunk_id)
                literals.append(vectorizer.to_sparsevec(vectorizer.document(body or "")))
        tmp = target.with_suffix(".tmp")
        pq.write_table(
            pa.table({"chunk_id": ids, "sparse": literals}, schema=SCHEMA),
            str(tmp),
            compression="zstd",
        )
        os.replace(tmp, target)
        report.shards += 1
        report.rows += len(ids)
        if on_progress:
            on_progress("transform", index + 1, len(shards))
    return report
