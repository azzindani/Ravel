"""The extractor interface — Phase A's plugin boundary.

Every extractor is a pure function of (source file, config) and produces exactly one
canonical document. The router (EXTRACTION.md §2) picks between them per document,
and records which one ran so a corpus can be audited by extraction path.

! An extractor must never return valid-but-empty output silently. A crash is better
than an empty success (LOOPHOLES.md §2); the validator's text-ratio floor is the
backstop, not the first line of defence.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from canon import Block, BlockType, CanonicalDoc
from extract.quality import TextLayer, TextQuality


class ExtractionFailed(RuntimeError):
    """Deterministic failure: this document cannot be extracted by this extractor.

    Distinct from a transient error (OOM, CUDA fault) so the runtime can quarantine
    immediately instead of retrying forever (EXTRACTION.md §4).
    """


@dataclass(frozen=True, slots=True)
class Probe:
    """What a cheap look at a file says about how to extract it."""

    mime: str
    pages: int | None
    text_ratio: float
    """Share of pages carrying an extractable text layer. 1.0 = born-digital.

    ! Coverage, not quality. 13.3% of real source PDFs score 1.0 here and carry a text
    layer that is damaged (`ABSORPTION.md` §16) — usually somebody else's old OCR, baked
    in. Route on `layer`, not on this.
    """
    chars: int
    quality: TextQuality | None = None
    """The three-way verdict. `None` when the file was never opened (wrong mime, or the
    PDF would not parse), which `layer` reports as `ABSENT`."""

    @property
    def layer(self) -> TextLayer:
        """What to do with this file: extract it, or re-read the page."""
        return self.quality.layer if self.quality else TextLayer.ABSENT

    @property
    def native_ok(self) -> bool:
        """! The gate extractors should use. `text_ratio >= x` is the gate that let the
        incumbent corpus in."""
        return self.layer is TextLayer.CLEAN


@runtime_checkable
class Extractor(Protocol):
    id: str
    version: str

    def supports(self, path: Path, probe: Probe) -> bool: ...

    def extract(self, path: Path, *, title: str, url: str | None = None) -> CanonicalDoc: ...


@dataclass
class Registry:
    """Extractors by id. The router consults this; nothing imports an extractor
    directly, so a corpus definition can name one in config (INTERFACES.md §6)."""

    _items: dict[str, Extractor] = field(default_factory=dict)

    def register(self, extractor: Extractor) -> Extractor:
        key = f"{extractor.id}@{extractor.version}"
        if key in self._items:
            raise ValueError(f"extractor {key} is already registered")
        self._items[key] = extractor
        self._items.setdefault(extractor.id, extractor)  # bare id = latest registered
        return extractor

    def get(self, ref: str) -> Extractor:
        if ref not in self._items:
            raise KeyError(f"no extractor {ref!r} · known: {sorted(self.ids())}")
        return self._items[ref]

    def ids(self) -> list[str]:
        return sorted({e.id for e in self._items.values()})

    def __iter__(self) -> Iterator[Extractor]:
        return iter(dict.fromkeys(self._items.values()))


registry = Registry()


class BlockBuilder:
    """Accumulates blocks while maintaining the heading stack.

    ! `heading_path` must be materialized on EVERY block so a chunker is a linear
    scan, never a tree walk (CANONICAL_FORMAT.md §3). Every extractor builds its
    blocks through here so that invariant holds by construction rather than by each
    extractor remembering to.
    """

    def __init__(self) -> None:
        self._blocks: list[Block] = []
        self._stack: list[tuple[int, str]] = []

    def add(
        self,
        btype: BlockType,
        text: str = "",
        *,
        level: int | None = None,
        page: int | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        confidence: float | None = None,
        ref: str | None = None,
        attrs: dict[str, Any] | None = None,
    ) -> Block:
        if btype is BlockType.HEADING:
            level = level or 1
            # ! Pop by LEVEL, not by depth. Indexing the stack by depth assumed the
            # levels seen so far were 1, 2, 3…, and profiles deliberately assign fixed
            # levels with gaps so two documents chunked at different depths still
            # produce comparable paths: `id_regulation` numbers bab 3 and pasal 6.
            # A document opening at level 3 therefore left the stack one deep, so the
            # next level-3 heading did not displace its sibling — real regulations
            # came out with `Menimbang › Mengingat › BAB I`, three siblings nested as
            # ancestors, on every document in the corpus.
            while self._stack and self._stack[-1][0] >= level:
                self._stack.pop()
            self._stack.append((level, text))

        block = Block(
            id=f"b{len(self._blocks):05d}",
            type=btype,
            text=text,
            level=level,
            heading_path=[t for _, t in self._stack],
            page=page,
            bbox=bbox,
            reading_order=len(self._blocks),
            confidence=confidence,
            ref=ref,
            attrs=attrs or {},
        )
        self._blocks.append(block)
        return block

    @property
    def blocks(self) -> list[Block]:
        return self._blocks

    def __len__(self) -> int:
        return len(self._blocks)
