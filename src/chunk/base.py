"""The chunker registry, and the one place provenance is filled in.

A chunker is a pure function `canonical document → [chunk]`, registered by id with a
declared version (`CHUNKING.md` §2). A corpus definition names one; the variant harness
sweeps them by id. Neither imports a chunker directly.

! Chunkers decide **grouping only**. They choose which blocks belong together and what
to call the result; they never assemble a `Chunk` themselves. `Builder` does that, and
it is the single place that reads provenance off the canonical document and computes a
chunk id. This is deliberate: "never emit a chunk without full provenance"
(`CLAUDE.md` §7.4) is a rule six chunkers can each forget, and a structure only one
piece of code can get wrong. Vera enforces provenance immutability with a database
trigger; Ravel must never be the reason it fires.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from canon import Block, BlockType, CanonicalDoc
from canon.render import render_table
from chunk.models import PATH_SEP, Chunk, chunk_id
from spec import Profile

#: Block types that carry document content. Page furniture and captions are excluded
#: from bodies — a running header repeated on 400 pages is noise that would otherwise
#: dominate keyword statistics for the whole corpus.
BODY_TYPES = frozenset(
    {BlockType.PARAGRAPH, BlockType.LIST_ITEM, BlockType.FORMULA}
)

_WORD = re.compile(r"\w+|[^\w\s]")

#: Tokens per whitespace-delimited piece, for the default estimator. Measured against
#: Qwen3's tokenizer on Indonesian legal prose, which is sub-word heavy: `menyelenggarakan`
#: is one word and four tokens.
TOKENS_PER_WORD = 1.35


def estimate_tokens(text: str) -> int:
    """A tokenizer-free token estimate.

    ! An estimate on purpose. Loading a tokenizer here would put a model download on
    the path of a stage whose whole value is being cheap and re-runnable, and would
    tie chunk boundaries to one embedder — so re-chunking would be required to change
    models, collapsing the Phase B boundary this project is built around. The true
    count is recorded at embed time, where the tokenizer already exists.

    It runs ~10% high on Indonesian legal text, which is the safe direction: chunks
    come out slightly under budget rather than over it.
    """
    return int(len(_WORD.findall(text)) * TOKENS_PER_WORD)


TokenCounter = Callable[[str], int]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ChunkConfig:
    """Knobs shared by every chunker, plus per-chunker `params`.

    `config_hash` folds all of it into the chunk id, so two variants of one corpus
    produce disjoint id spaces and cannot silently reuse each other's rows
    (`LOOPHOLES.md` §1).
    """

    max_tokens: int = 512
    """Upper bound on a chunk body. A structural unit larger than this is split into
    `part n of m` pieces rather than truncated."""

    min_tokens: int = 16
    """Below this a chunk is merged forward rather than emitted.

    A retrieved fragment reading only `Pasal 12` helps nobody, and hundreds of them
    dilute every similarity score in the corpus.
    """

    overlap_tokens: int = 0
    """Overlap between size-split pieces. 0 by default: clean boundaries, no duplicated
    text, smaller corpus. `CHUNKING.md` §7 leaves this a per-corpus variant dimension."""

    carry_parent: bool = True
    """Prepend a unit's own lead-in text to each of its sub-units.

    An `ayat` is routinely meaningless alone — the pasal's opening line carries the
    subject the ayat only qualifies.
    """

    carry_parent_max_tokens: int = 64
    """Longest lead-in worth carrying. Above this the unit is split instead.

    ! Measured, not guessed. Without a cap, a `Mengingat` section whose lead-in is its
    whole enumerated list of statutes was prepended to all 40 of its sub-units, and 40
    chunks came out with near-identical 150-token preambles and their actual content
    pushed past the budget. Carrying is for a one-line subject, not for a section.
    """

    include_tables: bool = True
    """Emit each table as its own chunk, with its caption and heading path."""

    params: dict[str, Any] = field(default_factory=dict)
    """Chunker-specific settings. Validated by the chunker, hashed like everything else."""

    @property
    def config_hash(self) -> str:
        payload = {
            "max_tokens": self.max_tokens,
            "min_tokens": self.min_tokens,
            "overlap_tokens": self.overlap_tokens,
            "carry_parent": self.carry_parent,
            "carry_parent_max_tokens": self.carry_parent_max_tokens,
            "include_tables": self.include_tables,
            "params": self.params,
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Building chunks
# ---------------------------------------------------------------------------
@dataclass
class Builder:
    """Turns a group of blocks into a `Chunk`, with provenance and a deterministic id.

    ! The only constructor of `Chunk` in the codebase. Everything a chunk asserts about
    where it came from is read here, from the canonical document, once.
    """

    doc: CanonicalDoc
    chunker: str
    version: str
    config: ChunkConfig
    profile: Profile | None = None
    count_tokens: TokenCounter = estimate_tokens

    def body_of(self, blocks: Sequence[Block]) -> str:
        """Verbatim text of a block group, in reading order."""
        return "\n\n".join(b.text.strip() for b in blocks if b.text.strip())

    def make(
        self,
        blocks: Sequence[Block],
        *,
        body: str | None = None,
        locator: str | None = None,
        identifier: str | None = None,
        part: tuple[int, int] = (1, 1),
        attrs: dict[str, Any] | None = None,
        extra_block_ids: Sequence[str] = (),
    ) -> Chunk | None:
        """One chunk, or None when there is nothing worth emitting.

        `body` may be supplied when the chunker composed text the blocks alone do not
        give — a carried parent lead-in, or a rendered table. It must still be text
        that appears in the document; composing is allowed, inventing is not.

        `extra_block_ids` names blocks whose text reached the body without being in
        `blocks` — a carried lead-in, most often. ! Without it the debugging path is
        broken exactly where it is most needed: a reader questioning the first sentence
        of a chunk would find no block to check it against.
        """
        text = (body if body is not None else self.body_of(blocks)).strip()
        if not text:
            return None
        attrs = attrs or {}

        block_ids = [*extra_block_ids, *(b.id for b in blocks)]
        heading_path = self._heading_path(blocks)
        # A locator the chunker did not supply falls back to structure, never to an
        # index — "block 412" is not something a human can check against a source.
        full_locator = locator or heading_path or ""
        page = next((b.page for b in blocks if b.page is not None), None)

        return Chunk(
            id=chunk_id(
                doc_id=self.doc.doc_id,
                chunker=self.chunker,
                version=self.version,
                config_hash=self.config.config_hash,
                locator=f"{full_locator}#{part[0]}/{part[1]}",
                # ! The document's own coordinates — every constituent block, not
                # just the first. Anchoring on the first alone still collided on the
                # real corpus: a carried lead-in makes sibling chunks share their
                # opening blocks, so what distinguishes them is the tail.
                anchor=",".join(block_ids) or str(attrs.get("table_id", "")),
                body=text,
            ),
            doc_id=self.doc.doc_id,
            body=text,
            token_count=self.count_tokens(text),
            part=part,
            block_ids=block_ids,
            source_title=self.doc.source.title,
            source_url=self._url(),
            source_sha256=self.doc.source.sha256,
            locator_page=page,
            locator_section=locator or None,
            heading_path=heading_path or None,
            identifier=identifier,
            chunker=self.chunker,
            chunker_version=self.version,
            config_hash=self.config.config_hash,
            profile=self.profile.ref if self.profile else None,
            attrs=attrs,
        )

    def split(
        self,
        blocks: Sequence[Block],
        *,
        locator: str | None = None,
        identifier: str | None = None,
        attrs: dict[str, Any] | None = None,
        lead: str = "",
        lead_block_ids: Sequence[str] = (),
    ) -> Iterator[Chunk]:
        """Emit a block group, splitting it into parts if it exceeds `max_tokens`.

        Splits fall on block boundaries. A block is the smallest verbatim unit the
        canonical format defines, and splitting inside one would cut mid-sentence for
        no gain that a smaller `max_tokens` would not give more honestly.
        """
        groups = self._by_budget(blocks, lead=lead)
        total = len(groups)
        for n, group in enumerate(groups, start=1):
            body = self.body_of(group)
            if lead and n == 1:
                body = f"{lead}\n\n{body}" if body else lead
            chunk = self.make(
                group,
                body=body,
                locator=locator,
                identifier=identifier,
                part=(n, total),
                attrs=attrs,
                extra_block_ids=lead_block_ids if (lead and n == 1) else (),
            )
            if chunk:
                yield chunk

    def _by_budget(self, blocks: Sequence[Block], *, lead: str = "") -> list[list[Block]]:
        budget = self.config.max_tokens
        spent = self.count_tokens(lead) if lead else 0
        groups: list[list[Block]] = [[]]
        for block in blocks:
            cost = self.count_tokens(block.text)
            # A single oversized block goes in alone rather than being dropped or cut.
            if groups[-1] and spent + cost > budget:
                groups.append([])
                spent = 0
                if self.config.overlap_tokens:
                    carried = self._overlap(groups[-2])
                    groups[-1].extend(carried)
                    spent += sum(self.count_tokens(b.text) for b in carried)
            groups[-1].append(block)
            spent += cost
        return [g for g in groups if g]

    def _overlap(self, previous: Sequence[Block]) -> list[Block]:
        carried: list[Block] = []
        spent = 0
        for block in reversed(previous):
            cost = self.count_tokens(block.text)
            if spent + cost > self.config.overlap_tokens:
                break
            carried.insert(0, block)
            spent += cost
        return carried

    def tables(self) -> Iterator[Chunk]:
        """One chunk per table, rendered as a markdown grid.

        A table's cells are the truth (`CANONICAL_FORMAT.md`); the grid is a lossy
        rendering of them, which is the right trade for retrieval — but it is why the
        chunk carries the table id in `attrs`, so the exact cells stay reachable.
        """
        if not self.config.include_tables:
            return
        for table in self.doc.tables:
            grid = render_table(table).strip()
            if not grid:
                continue
            anchor = next(
                (b for b in self.doc.blocks if b.ref == table.id), None
            )
            blocks = [anchor] if anchor else []
            heading = PATH_SEP.join(anchor.heading_path) if anchor else ""
            locator = table.caption or heading or table.id
            chunk = self.make(
                blocks,
                body=grid,
                locator=locator,
                attrs={"table_id": table.id, "kind": "table"},
            )
            if chunk:
                yield chunk

    def _heading_path(self, blocks: Sequence[Block]) -> str:
        for block in blocks:
            if block.heading_path:
                return PATH_SEP.join(block.heading_path)
        return ""

    def _url(self) -> str:
        url = self.doc.source.url
        if not url:
            # ! Never synthesized (`CHUNKING.md` §4.1). A plausible URL that was never
            # verified is worse than a missing one: it looks like a citation.
            raise ValueError(
                f"{self.doc.source.path}: no source_url · a corpus definition must "
                f"supply provenance.base_url before its documents can be chunked"
            )
        return url


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class ChunkFn(Protocol):
    def __call__(self, doc: CanonicalDoc, builder: Builder) -> Iterable[Chunk]: ...


@dataclass(frozen=True, slots=True)
class Entry:
    id: str
    version: str
    fn: ChunkFn
    doc: str = ""

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


_REGISTRY: dict[str, Entry] = {}


def chunker(chunk_id_: str, *, version: str) -> Callable[[ChunkFn], ChunkFn]:
    """Register a chunking function under an id.

    ! Bump `version` whenever the output changes. It is in every chunk id, so a bump
    invalidates the old rows instead of mixing two chunkers' output in one table.
    """

    def register(fn: ChunkFn) -> ChunkFn:
        if chunk_id_ in _REGISTRY and _REGISTRY[chunk_id_].fn is not fn:
            raise ValueError(f"chunker {chunk_id_!r} is already registered")
        _REGISTRY[chunk_id_] = Entry(
            id=chunk_id_, version=version, fn=fn, doc=(fn.__doc__ or "").strip()
        )
        return fn

    return register


def get(chunk_id_: str) -> Entry:
    if chunk_id_ not in _REGISTRY:
        raise KeyError(f"no chunker {chunk_id_!r} · known: {sorted(_REGISTRY)}")
    return _REGISTRY[chunk_id_]


def available() -> list[Entry]:
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]
