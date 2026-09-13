"""Corpus definitions: what to build, from where, with which profile.

A profile describes a *kind of document* (`PROFILES.md`). A **corpus** describes a
concrete body of them: where the files are, where they authoritatively live, which
profile to apply, and what tolerance for failure is acceptable.

Profiles are reusable across corpora; corpora are not reusable at all. Keeping them
in separate files means a second Indonesian corpus reuses `id_regulation@1.0` without
copying a line of it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from runtime.shards import MAX_BLOCKS, MAX_DOCS


def _digest(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourcesSpec(Base):
    root: str
    include: list[str] = Field(default_factory=lambda: ["**/*"])
    skip_hidden: bool = True


class ProvenanceSpec(Base):
    """Where the sources authoritatively live.

    ! This is the field whose absence cost the incumbent corpus everything. All
    748,558 of its rows have no `source_url`, because the pipeline that built it
    never carried one — and a citation cannot be back-filled (`ABSORPTION.md` §3).

    `base_url` is a real, resolvable location for the exact bytes that were hashed,
    not a guess. `hf://datasets/owner/name/` is a better citation than a portal
    search URL constructed from parsed metadata: the portal link may 404, may point
    at a revised text, and was never verified — while the dataset holds the precise
    bytes whose sha256 is recorded beside them.

    Constructing a plausible URL from a document's own metadata is exactly what
    `CHUNKING.md` §4 forbids. If a corpus genuinely has per-document canonical URLs,
    supply them in a sidecar (`url_map`); do not invent a template that usually works.
    """

    base_url: str
    """Prefix joined with each source's path relative to `sources.root`."""

    url_map: str | None = None
    """Optional JSONL of {path, url} overrides, for sources with known canonical URLs."""

    note: str = ""
    """What `base_url` points at and why it is authoritative. Goes into the bundle."""

    @model_validator(mode="after")
    def _plausible(self) -> Self:
        if "://" not in self.base_url:
            raise ValueError(f"base_url must be a URI, got {self.base_url!r}")
        return self

    def url_for(self, relative_path: str) -> str:
        return f"{self.base_url.rstrip('/')}/{relative_path.lstrip('/')}"


class ExtractSpec(Base):
    profile: str = "generic"
    """Profile ref, or `auto` to let the router choose per document."""

    extractors: list[str] = Field(default_factory=lambda: ["native", "text"])
    """Preference order. The first that `supports()` a document wins."""

    native_text_ratio: float = Field(default=0.9, ge=0.0, le=1.0)
    """Below this share of pages carrying text, a PDF goes to the OCR/VLM path."""

    shard_target_mb: int = Field(default=256, ge=1)
    """Estimated size of a sealed shard on disk. Shapes the artifact, not the run."""

    shard_max_blocks: int = Field(default=MAX_BLOCKS, ge=0)
    """Blocks buffered before sealing, whatever the estimated size. 0 disables.

    ! The bound that actually protects the run. Buffered documents cost far more
    resident memory than the parquet they compress into, so a size target alone will
    happily hold gigabytes before it seals. Lower this on a small box.
    """

    shard_max_docs: int = Field(default=MAX_DOCS, ge=0)
    """Documents buffered before sealing. 0 disables."""

    failure_tolerance: float = Field(default=0.02, ge=0.0, le=1.0)
    """Share of documents that may fail before the run is considered broken.

    ! Asserted, never assumed. A bundle silently missing 400 of 30,000 documents is
    the failure this exists to prevent (`LOOPHOLES.md` §3).
    """


class ChunkSpec(Base):
    """Phase B defaults for this corpus. A variant overrides any of them.

    These sit in the corpus rather than in code because the right chunk size is a
    property of the documents, not of the pipeline: a corpus of one-line clauses and
    a corpus of ten-page sections do not share a budget.
    """

    chunker: str = "heading"
    """Registered chunker id. `unit` for documents whose profile declares citable
    structural units; `heading` for everything else; `token` is the baseline."""

    max_tokens: int = Field(default=512, ge=32)
    min_tokens: int = Field(default=16, ge=0)
    overlap_tokens: int = Field(default=0, ge=0)
    carry_parent: bool = True
    carry_parent_max_tokens: int = Field(default=64, ge=0)
    include_tables: bool = True
    params: dict[str, Any] = Field(default_factory=dict)
    """Chunker-specific settings, passed through and hashed like everything else."""

    shard_target_mb: int = Field(default=128, ge=1)
    shard_max_chunks: int = Field(default=200_000, ge=0)
    """Chunks buffered before sealing. The Phase B counterpart of `shard_max_blocks`;
    a chunk is far lighter than a document, so this number is far larger."""

    def to_config(self) -> Any:
        from chunk.base import ChunkConfig

        return ChunkConfig(
            max_tokens=self.max_tokens,
            min_tokens=self.min_tokens,
            overlap_tokens=self.overlap_tokens,
            carry_parent=self.carry_parent,
            carry_parent_max_tokens=self.carry_parent_max_tokens,
            include_tables=self.include_tables,
            params=dict(self.params),
        )


class CorpusSpec(Base):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str
    description: str = ""
    sources: SourcesSpec
    provenance: ProvenanceSpec
    extract: ExtractSpec = Field(default_factory=ExtractSpec)
    chunk: ChunkSpec = Field(default_factory=ChunkSpec)

    @property
    def config_hash(self) -> str:
        """Every field. Identifies the definition; goes in the manifest."""
        return _digest(self.model_dump(mode="json"))

    @property
    def extraction_hash(self) -> str:
        """Only the fields that change what a canonical document *contains*.

        ! This, not `config_hash`, is what belongs in an extraction cache key.

        `LOOPHOLES.md` §1 is about keys that omit a parameter which changes the
        output — a stale artifact reused while the manifest claims otherwise. Hashing
        the whole spec avoids that, but overshoots in the other direction: raising
        `shard_max_blocks` cannot alter a single byte of a canonical document, yet a
        whole-spec key would discard 931 extracted documents and spend another
        eighteen minutes reproducing them identically. A cache that is invalidated by
        edits which provably cannot change the result trains people to stop trusting
        it.

        So the split is explicit and the burden is on the *content* side: a field
        listed here must be one the extractor or structurer reads. `base_url` is
        here because it becomes `doc.source.url`, which is stored in the document.
        Sharding, tolerance and the source glob are not — they decide how work is
        packaged and which files are considered, never what a document says.

        ! Adding a knob that reaches the extractor or the structurer without adding
        it here is a review-blocking bug, exactly as `LOOPHOLES.md` §1 describes.
        """
        return _digest(
            {
                "provenance": {
                    "base_url": self.provenance.base_url,
                    "url_map": self.provenance.url_map,
                },
                "extract": {
                    "profile": self.extract.profile,
                    "extractors": self.extract.extractors,
                    "native_text_ratio": self.extract.native_text_ratio,
                },
            }
        )

    @classmethod
    def load(cls, path: Path | str) -> CorpusSpec:
        path = Path(path)
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{path.name}: expected a mapping at the top level")
        spec = cls.model_validate(raw)
        if path.stem != spec.id:
            raise ValueError(
                f"{path.name}: declares id {spec.id!r}, so the file must be {spec.id}.yaml"
            )
        return spec

    @classmethod
    def find(cls, corpus_id: str, root: Path | str | None = None) -> CorpusSpec:
        base = Path(root) if root else Path(__file__).resolve().parents[2] / "corpora"
        path = base / f"{corpus_id}.yaml"
        if not path.is_file():
            known = sorted(p.stem for p in base.glob("*.yaml")) if base.is_dir() else []
            raise FileNotFoundError(f"no corpus {corpus_id!r} in {base} · known: {known}")
        return cls.load(path)
