"""Phase B, first stage driven: canonical shards into chunk shards.

    read canon shards -> route profile -> chunk -> shard -> ledger

The same execution model as Phase A, for the same reason: interruption is the normal
path (`EXECUTION.md` §1). The difference is what a rerun costs. Re-extracting 931
documents takes eighteen minutes of PDF parsing; re-chunking them takes seconds,
because the expensive half already happened and is cached behind the artifact
boundary. That asymmetry is the entire point of the Phase A/B split, and it is what
makes a chunking experiment cheap enough to actually run (`VARIANTS.md`).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from canon import CanonicalDoc
from chunk.base import Builder, ChunkConfig, get
from chunk.models import Chunk
from chunk.store import write_chunks
from extract.run import iter_canon
from runtime.ledger import Ledger, Status
from runtime.shards import (
    SealedShard,
    ShardWriter,
    clear_shards,
    generation_of,
    newest_generation,
)
from spec import Profile, Registry, default_registry
from spec.corpus import CorpusSpec

STAGE = "chunk"


def stage_key(
    doc_id: str,
    *,
    chunker: str,
    version: str,
    config_hash: str,
    profile: Profile | None,
    generation: str | None = None,
) -> str:
    """The content-addressed key for one document's chunking.

    Every input that changes the chunks is in here — the document itself, which
    chunker, its version, its full configuration, and the profile whose patterns the
    chunker reads. A variant that changed `max_tokens` and reused another variant's
    rows would be `LOOPHOLES.md` §1 at corpus scale.

    ! `generation` is in here because `doc_id` is not enough. A doc_id is the sha256 of
    the *source bytes*, so it is identical across two extractions of the same file —
    and a fixed extractor bug produces a completely different canonical document under
    the same doc_id. Without the generation, re-chunking a corrected extraction found
    every key already done and wrote nothing at all, leaving the old chunks in place
    and reporting success.
    """
    material = json.dumps(
        {
            "doc": doc_id,
            "generation": generation,
            "chunker": f"{chunker}@{version}",
            "config": config_hash,
            "profile": profile.ref if profile else None,
            "profile_hash": profile.config_hash if profile else None,
        },
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()[:32]


@dataclass
class RunReport:
    corpus: str
    variant: str = "default"
    documents: int = 0
    skipped_done: int = 0
    duplicates: int = 0
    chunked: int = 0
    chunks: int = 0
    empty: int = 0
    failed: int = 0
    shards: int = 0
    bytes_written: int = 0
    seconds: float = 0.0
    tokens: list[int] = field(default_factory=list)
    with_identifier: int = 0
    with_section: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def chunks_per_doc(self) -> float:
        return self.chunks / self.chunked if self.chunked else 0.0

    @property
    def median_tokens(self) -> int:
        if not self.tokens:
            return 0
        ordered = sorted(self.tokens)
        return ordered[len(ordered) // 2]

    @property
    def identifier_rate(self) -> float:
        """! Reported, never assumed.

        `identifier` is what Vera's exact-match bypass keys on. A corpus where it is
        mostly null looks healthy by every other measure and silently loses every
        query that names a document by number.
        """
        return self.with_identifier / self.chunks if self.chunks else 0.0

    @property
    def section_rate(self) -> float:
        return self.with_section / self.chunks if self.chunks else 0.0


class Chunking:
    """Runs one corpus, one variant, through the chunking stage."""

    def __init__(
        self,
        corpus: CorpusSpec,
        *,
        workspace: Path,
        config: ChunkConfig | None = None,
        chunker: str | None = None,
        variant: str = "default",
        generation: str | None = None,
        registry: Registry | None = None,
    ) -> None:
        self.corpus = corpus
        self.workspace = Path(workspace)
        self.variant = variant
        self.registry = registry or default_registry()
        self.entry = get(chunker or corpus.chunk.chunker)
        self.config = config or corpus.chunk.to_config()

        # ! Resolved once, here, to a concrete generation name — never left as a
        # placeholder meaning "whatever is newest". `output_generation` hashes this
        # value, and hashing the word "latest" makes every canonical generation map to
        # the same chunk directory: re-extracting and re-chunking then appended the new
        # rows beside the old ones, which is precisely the defect generations exist to
        # prevent. A fix that names a moving target has not fixed anything.
        self.generation = generation or self._newest_canon()

    # -- paths --------------------------------------------------------------
    @property
    def output_generation(self) -> str:
        """Identifies this chunking configuration over this canonical generation.

        ! A variant name is not enough. Re-running one variant after changing the
        chunker or its config appends new shards beside the old ones — a forced
        re-chunk left two generations of rows in one directory, and the stage that
        reads them would have seen every chunk twice (`runtime.shards.generation_of`).
        """
        return generation_of(
            self.generation or "none",
            self.entry.ref,
            self.config.config_hash,
        )

    def _newest_canon(self) -> str | None:
        found = newest_generation(self.workspace / "canon" / self.corpus.id)
        return found.name if found else None

    @property
    def chunk_dir(self) -> Path:
        # Variants live side by side, and each variant's generations do too. Two
        # chunkings of one corpus are not interchangeable: a bundle built from a mix
        # of them is unverifiable.
        return (
            self.workspace / "chunks" / self.corpus.id / self.variant / self.output_generation
        )

    @property
    def ledger_path(self) -> Path:
        return self.workspace / "state" / self.corpus.id / "ledger.db"

    def profile_for(self, doc: CanonicalDoc) -> Profile | None:
        """The profile the document was extracted with, not one chosen afresh.

        ! Recorded at extraction, read back here. Re-routing at chunk time would let a
        document be structured by one profile and chunked by another — the headings
        would say pasal and the chunker would be looking for clauses.
        """
        ref = doc.extraction.params.get("profile")
        if not ref:
            return None
        return self.registry.get(str(ref))

    def chunks_for(self, doc: CanonicalDoc) -> list[Chunk]:
        profile = self.profile_for(doc)
        builder = Builder(
            doc=doc,
            chunker=self.entry.id,
            version=self.entry.version,
            config=self.config,
            profile=profile,
        )
        return list(self.entry.fn(doc, builder))

    # -- the run ------------------------------------------------------------
    def run(
        self,
        *,
        limit: int = 0,
        force: bool = False,
        on_progress: Callable[[RunReport], None] | None = None,
    ) -> RunReport:
        report = RunReport(corpus=self.corpus.id, variant=self.variant)
        started = time.time()

        with Ledger(self.ledger_path) as ledger:
            if force:
                # ! Forcing recomputes this generation, so its existing shards are
                # superseded. Left in place they would be read alongside the new ones.
                clear_shards(self.chunk_dir)
            done = set() if force else ledger.done_keys(self._stage())

            def commit(shard: SealedShard) -> None:
                ledger.record_many(
                    (key, self._stage(), Status.DONE, None, str(shard.path))
                    for key in shard.keys
                )
                report.shards += 1
                report.bytes_written += shard.bytes

            with ShardWriter(
                self.chunk_dir,
                target_bytes=self.corpus.chunk.shard_target_mb * 1024 * 1024,
                max_blocks=self.corpus.chunk.shard_max_chunks,
                max_docs=0,
                on_seal=commit,
                write=write_chunks,
                weigh=lambda c: (len(c.body) + 512, 1),
            ) as writer:
                seen: set[str] = set()
                for doc in iter_canon(self.workspace, self.corpus.id, self.generation):
                    if limit and report.documents >= limit:
                        break
                    report.documents += 1
                    key = self._key(doc)

                    if key in done:
                        report.skipped_done += 1
                        continue
                    # ! One document chunked twice is one document in the corpus twice.
                    # `done` is read once at the start, so it cannot see this run's own
                    # work — 931 documents became 1,862 the first time this was missing.
                    if key in seen:
                        report.duplicates += 1
                        continue
                    seen.add(key)

                    try:
                        chunks = self.chunks_for(doc)
                    except Exception as exc:  # noqa: BLE001 — one document must not kill a run
                        report.failed += 1
                        report.errors.append((doc.source.path, f"{type(exc).__name__}: {exc}"))
                        ledger.record(key, self._stage(), Status.FAILED, detail=str(exc)[:500])
                        continue

                    if not chunks:
                        # ! Recorded, not ignored. A document that produced nothing is
                        # missing from the corpus, and silent incompleteness is the
                        # failure `LOOPHOLES.md` §3 exists to prevent.
                        report.empty += 1
                        ledger.record(
                            key,
                            self._stage(),
                            Status.QUARANTINED,
                            detail="chunker produced no chunks",
                        )
                        continue

                    for chunk in chunks:
                        writer.add(chunk, key)
                        report.tokens.append(chunk.token_count)
                        report.with_identifier += chunk.identifier is not None
                        report.with_section += chunk.locator_section is not None
                    report.chunked += 1
                    report.chunks += len(chunks)
                    if on_progress:
                        on_progress(report)

        report.seconds = time.time() - started
        return report

    def _stage(self) -> str:
        # Variants share a ledger file but never a stage namespace, so one variant's
        # completed work can never satisfy another's resume.
        return f"{STAGE}:{self.variant}"

    def _key(self, doc: CanonicalDoc) -> str:
        return stage_key(
            doc.doc_id,
            chunker=self.entry.id,
            version=self.entry.version,
            config_hash=self.config.config_hash,
            profile=self.profile_for(doc),
            generation=self.generation,
        )


def iter_chunk_shards(
    workspace: Path | str,
    corpus_id: str,
    variant: str = "default",
    generation: str | None = None,
) -> Iterator[Chunk]:
    """Every chunk of one variant generation, streamed shard by shard."""
    from chunk.store import iter_chunks

    base = Path(workspace) / "chunks" / corpus_id / variant
    directory = base / generation if generation else newest_generation(base)
    if directory is None:
        return
    for shard in sorted(directory.glob("part-*.parquet")):
        yield from iter_chunks(str(shard))
