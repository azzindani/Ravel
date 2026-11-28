"""Phase A, driven: a corpus of source files into canonical shards.

    scan/hash -> probe -> route -> extract -> structure -> shard -> ledger

Resume is not a feature bolted on; it is the execution model (`EXECUTION.md` §1).
Every document has a content-addressed key, the ledger records which keys are done,
and a run processes the set difference. Kill it at any instant and rerun: it picks up
where it stopped, having lost at most the documents in the unsealed shard.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from canon import CANON_VERSION, CanonicalDoc, Severity, validate
from extract.base import ExtractionEmpty, ExtractionFailed, Extractor, Probe
from extract.docling_pdf import DoclingExtractor
from extract.native import NativeExtractor
from extract.native import probe as probe_pdf
from extract.structure import Structurer
from extract.text import TextExtractor, probe_text
from extract.xml import XmlExtractor
from runtime.ledger import Ledger, Status
from runtime.shards import (
    SealedShard,
    ShardWriter,
    clear_shards,
    generation_of,
    newest_generation,
)
from sources import SourceFile, guess_mime, read_list, scan
from spec import Profile, Registry, default_registry
from spec.corpus import CorpusSpec

STAGE = "extract"

#: How much of a document the router reads to score profiles. Enough to carry a
#: title block and a preamble; small enough to stay free.
ROUTE_SAMPLE_CHARS = 4000


@dataclass
class DocResult:
    key: str
    path: str
    doc: CanonicalDoc | None = None
    profile: str | None = None
    error: str | None = None
    quarantine: bool = False
    deferred: bool = False
    empty: bool = False
    """The document states it holds nothing (`ExtractionEmpty`): quarantined, not failed."""


@dataclass
class RunReport:
    """What a run actually did. Counts first, so a corpus is never silently partial."""

    corpus: str
    considered: int = 0
    skipped_done: int = 0
    duplicates: int = 0
    """Sources whose bytes were already extracted in this same run."""

    extracted: int = 0
    failed: int = 0
    deferred: int = 0
    empty: int = 0
    """Documents that state they hold nothing (`ExtractionEmpty`): ✗ failures, ✗ corpus."""
    invalid: int = 0
    shards: int = 0
    bytes_written: int = 0
    seconds: float = 0.0
    by_extractor: dict[str, int] = field(default_factory=dict)
    by_profile: dict[str, int] = field(default_factory=dict)
    errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def attempted(self) -> int:
        return self.extracted + self.failed

    @property
    def failure_rate(self) -> float:
        """Failed, as a share of the corpus this run read or found already read.

        ! Deferred documents are excluded: they are a missing stage, not a fault. They are
        reported separately and block bundling on their own terms. Documents done in an
        earlier run are counted: a rerun retries only the failures, and a rate over those
        alone would be 100% for a corpus within its tolerance.
        """
        total = self.attempted + self.skipped_done
        return self.failed / total if total else 0.0

    @property
    def coverage(self) -> float:
        """Share of considered documents that actually entered the corpus."""
        # ! `empty` is left out: a document with nothing to read is not one the corpus lacks.
        total = (
            self.extracted + self.failed + self.deferred + self.skipped_done + self.duplicates
        )
        covered = self.extracted + self.skipped_done + self.duplicates
        return covered / total if total else 0.0

    def within(self, tolerance: float) -> bool:
        return self.failure_rate <= tolerance


def probe_any(path: Path) -> Probe:
    """Cheap look at a file, whatever it is."""
    mime = guess_mime(path)
    if mime == "application/pdf":
        return probe_pdf(path)
    if mime.startswith("text/") or mime.endswith("+xml"):
        return probe_text(path)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return Probe(mime=mime, pages=None, text_ratio=0.0, chars=size)


def read_sample(path: Path, probe: Probe, limit: int = ROUTE_SAMPLE_CHARS) -> str:
    """Text for the router to score profiles against."""
    if probe.mime == "application/pdf":
        try:
            import pymupdf

            with pymupdf.open(path) as pdf:
                out: list[str] = []
                for page in pdf:
                    out.append(page.get_text("text"))
                    if sum(map(len, out)) >= limit:
                        break
                return "".join(out)[:limit]
        except Exception:  # noqa: BLE001 — routing must never be the thing that fails
            return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def stage_key(
    source: SourceFile, *, extractor: str, version: str, profile: Profile, corpus_hash: str
) -> str:
    """The content-addressed key for one document's extraction.

    ! Every parameter that changes the output is in here. A key that omits one lets
    a stale artifact be reused while the manifest claims otherwise — the highest
    priority failure in `LOOPHOLES.md` §1. Adding a knob without adding it here is a
    review-blocking bug.

    `corpus_hash` is `CorpusSpec.extraction_hash` and the profile contributes its
    `structure_hash` — in both cases the parts that change what a document contains,
    and only those. See those docstrings for why the distinction is load-bearing
    rather than tidy. Chunking keys on the full hashes, because it reads the rest.
    """
    material = json.dumps(
        {
            "sha256": source.sha256,
            "extractor": f"{extractor}@{version}",
            "profile": profile.ref,
            "profile_hash": profile.structure_hash,
            "corpus": corpus_hash,
        },
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()[:32]


class Extraction:
    """Runs one corpus through Phase A."""

    def __init__(
        self,
        corpus: CorpusSpec,
        *,
        workspace: Path,
        registry: Registry | None = None,
    ) -> None:
        self.corpus = corpus
        self.workspace = Path(workspace)
        self.registry = registry or default_registry()
        # ! Annotated, not inferred. Two concrete extractor classes join to
        # `object`, which silently makes `.supports()` and `.version`
        # unresolvable — the registry is a protocol registry and has to say so.
        self.extractors: dict[str, Extractor] = {
            "native": NativeExtractor(),
            "text": TextExtractor(),
            "xml": XmlExtractor(),
            # Built from the corpus, because its pipelines are the corpus's choice;
            # cheap to construct, since docling itself is imported only on first use.
            "docling": DoclingExtractor(corpus.extract.docling),
        }
        self._structurers: dict[str, Structurer] = {}

    # -- paths --------------------------------------------------------------
    @property
    def generation(self) -> str:
        """Identifies one extraction configuration, end to end.

        ! Canonical shards are written under this, and the reason is a defect that
        cost a corpus its correctness silently. `ShardWriter` resumes into a *fresh*
        shard index rather than overwriting — right for resume, wrong when the keys
        change. Bumping the extractor version and re-running therefore appended 17 new
        shards beside the 17 stale ones, and the next stage read every document twice:
        931 documents became 1,862, half of them carrying the heading_path bug the
        re-run existed to fix. Nothing was overwritten, nothing errored, and the count
        was simply wrong.

        A generation directory makes a superseded extraction inert instead of
        invisible. The old artifacts stay on disk and stay addressable; they are just
        no longer in the path anything reads.
        """
        material = json.dumps(
            {
                "corpus": self.corpus.extraction_hash,
                "extractors": {
                    name: ex.version
                    for name, ex in sorted(self.extractors.items())
                    if name in self.corpus.extract.extractors
                },
                "profiles": {
                    p.ref: p.structure_hash for p in sorted(self.registry, key=lambda x: x.ref)
                },
                "canon": CANON_VERSION,
            },
            sort_keys=True,
        )
        return generation_of(material)

    @property
    def canon_dir(self) -> Path:
        return self.workspace / "canon" / self.corpus.id / self.generation

    @property
    def ledger_path(self) -> Path:
        return self.workspace / "state" / self.corpus.id / "ledger.db"

    # -- pieces -------------------------------------------------------------
    def structurer_for(self, profile: Profile) -> Structurer:
        if profile.ref not in self._structurers:
            self._structurers[profile.ref] = Structurer(profile)
        return self._structurers[profile.ref]

    def profile_for(self, path: Path, probe: Probe) -> Profile | None:
        configured = self.corpus.extract.profile
        if configured != "auto":
            return self.registry.get(configured)
        return self.registry.route(mime=probe.mime, sample=read_sample(path, probe))

    def extractor_for(self, path: Path, probe: Probe) -> Extractor | None:
        for name in self.corpus.extract.extractors:
            extractor = self.extractors.get(name)
            if extractor and extractor.supports(path, probe):
                return extractor
        return None

    # -- one document -------------------------------------------------------
    def process(self, source: SourceFile, root: Path, corpus_hash: str) -> DocResult:
        path = root / source.path
        probe = probe_any(path)

        profile = self.profile_for(path, probe)
        if profile is None:
            return DocResult(
                key="",
                path=source.path,
                quarantine=True,
                error=f"no profile matches {probe.mime}",
            )

        extractor = self.extractor_for(path, probe)
        if extractor is None:
            reason = (
                f"no text layer ({probe.text_ratio:.0%} of pages) · awaiting the OCR/VLM path"
            )
            return DocResult(key="", path=source.path, deferred=True, error=reason)

        key = stage_key(
            source,
            extractor=extractor.id,
            version=extractor.version,
            profile=profile,
            corpus_hash=corpus_hash,
        )

        try:
            doc = extractor.extract(
                path,
                title=path.stem,
                url=self.corpus.url_for(source.path),
                profile=profile,
            )
        except ExtractionEmpty as exc:
            return DocResult(
                key=key, path=source.path, quarantine=True, empty=True, error=str(exc)
            )
        except ExtractionFailed as exc:
            return DocResult(key=key, path=source.path, quarantine=True, error=str(exc))
        except Exception as exc:  # noqa: BLE001 — transient; retryable, not quarantined
            return DocResult(key=key, path=source.path, error=f"{type(exc).__name__}: {exc}")

        if doc.source.sha256 != source.sha256:
            # ! A document assembled from linked files is identified by all of them
            # (`xml._assembly_digest`), so its key is too: two folders whose linking files
            # hold the same bytes are two texts, ✗ one duplicate.
            key = stage_key(
                source.model_copy(update={"sha256": doc.source.sha256}),
                extractor=extractor.id,
                version=extractor.version,
                profile=profile,
                corpus_hash=corpus_hash,
            )

        doc = self.structurer_for(profile).apply(doc)

        errors = [i for i in validate(doc) if i.severity is Severity.ERROR]
        if errors:
            # ! An invalid canonical document never enters the corpus. A crash beats
            # an empty success (`LOOPHOLES.md` §2).
            return DocResult(
                key=key,
                path=source.path,
                quarantine=True,
                error=f"invalid canonical document: {errors[0].message}",
            )

        return DocResult(key=key, path=source.path, doc=doc, profile=profile.ref)

    # -- the run ------------------------------------------------------------
    def run(
        self,
        *,
        limit: int = 0,
        force: bool = False,
        largest_first: bool = False,
        on_progress: Callable[[RunReport], None] | None = None,
    ) -> RunReport:
        root = Path(self.corpus.sources.root)
        report = RunReport(corpus=self.corpus.id)
        started = time.time()

        listed = self.corpus.sources.snapshot
        files, scanned = scan(
            root,
            self.corpus.sources.include,
            skip_hidden=self.corpus.sources.skip_hidden,
            only=read_list(root, listed) if listed else None,
        )
        # ! A snapshot that names a file it does not have is a partial corpus. Counted as
        # failures, so the tolerance check sees them.
        for problem in scanned.unreadable:
            report.failed += 1
            report.errors.append((problem, "unreadable source"))
        if largest_first:
            # The largest files are the hardest — the most pages, the most scans — so a
            # trial that takes them first meets the worst case before the average one.
            # Ordering decides which files a `limit` keeps, never what a document says.
            files = sorted(files, key=lambda f: (-f.bytes, f.path))
        if limit:
            files = files[:limit]
        report.considered = len(files)
        corpus_hash = self.corpus.extraction_hash

        with Ledger(self.ledger_path) as ledger:
            if force:
                # ! Forcing recomputes this generation, so its existing shards are
                # superseded. Left in place they would be read alongside the new ones.
                clear_shards(self.canon_dir)
            done = set() if force else ledger.done_keys(STAGE)

            def commit(shard: SealedShard) -> None:
                ledger.record_many(
                    (key, STAGE, Status.DONE, None, str(shard.path)) for key in shard.keys
                )
                report.shards += 1
                report.bytes_written += shard.bytes

            with ShardWriter(
                self.canon_dir,
                target_bytes=self.corpus.extract.shard_target_mb * 1024 * 1024,
                max_blocks=self.corpus.extract.shard_max_blocks,
                max_docs=self.corpus.extract.shard_max_docs,
                on_seal=commit,
            ) as writer:
                seen: set[str] = set()
                for source in files:
                    result = self.process(source, root, corpus_hash)

                    if result.key and result.key in done:
                        report.skipped_done += 1
                        continue

                    if result.doc is None:
                        if result.deferred:
                            report.deferred += 1
                        elif result.empty:
                            report.empty += 1
                        else:
                            report.failed += 1
                            report.errors.append((result.path, result.error or "unknown"))
                        if result.key:
                            ledger.record(
                                result.key,
                                STAGE,
                                Status.DEFERRED
                                if result.deferred
                                else Status.QUARANTINED
                                if result.quarantine
                                else Status.FAILED,
                                detail=result.error,
                            )
                        continue

                    # ! Two paths can hold identical bytes, and identical bytes are one
                    # document. Checked here rather than earlier so a failure is still
                    # counted as a failure: `done` is read once at the start and cannot
                    # see this run's own work, which is how 931 documents became 1,862.
                    if result.key in seen:
                        report.duplicates += 1
                        continue
                    seen.add(result.key)

                    writer.add(result.doc, result.key)
                    report.extracted += 1
                    name = result.doc.extraction.extractor
                    report.by_extractor[name] = report.by_extractor.get(name, 0) + 1
                    if result.profile:
                        report.by_profile[result.profile] = (
                            report.by_profile.get(result.profile, 0) + 1
                        )
                    if on_progress:
                        on_progress(report)

        report.seconds = time.time() - started
        return report


def iter_canon(
    workspace: Path | str, corpus_id: str, generation: str | None = None
) -> Iterator[CanonicalDoc]:
    """Every canonical document of one extraction generation, shard by shard.

    ! A generation must be named. Reading the corpus directory as a whole would mix
    superseded extractions with current ones — the defect `Extraction.generation`
    exists to prevent. When `generation` is None the newest one is used, which is the
    right default for a workspace but never a substitute for asking.
    """
    from canon import iter_shard

    base = Path(workspace) / "canon" / corpus_id
    directory = base / generation if generation else newest_generation(base)
    if directory is None:
        return
    for shard in sorted(directory.glob("part-*.parquet")):
        yield from iter_shard(str(shard))
