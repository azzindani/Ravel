"""Document profiles: the declarative half of parsing.

A **profile** is everything Ravel knows about a *kind of document* — how to tell it
apart, what its structure markers look like, how to read its identity, what counts as
boilerplate. It lives in `registry/profiles/<id>@<version>.yaml`, not in Python.

Why this is a file and not code
-------------------------------
`ABSORPTION.md` §4 records the failure this prevents. The knowledge needed to parse
Indonesian regulations existed in `06_ID_Legal/core/legal_vocab.py` — and the parser,
living in a notebook, hardcoded `PERATURAN MENTERI KEUANGAN` instead. The result was
10.66% `Unknown` regulation_type across 748,558 rows. Knowledge that lives in code is
knowledge that does not reach the person who needs it.

So: the engine is generic and the corpus knowledge is data. A new document family is a
new YAML file, reviewed like code, versioned like code, hashed into `config_hash` like
code — but requiring none.

Two layers, deliberately
------------------------
`*Spec` models are pure data validated from YAML. `Profile` is the compiled runtime
object built from a spec: regexes compiled once, alternations expanded, lookups
indexed. Keeping them apart means the spec stays hashable and serializable while the
runtime stays fast, and neither has to compromise for the other.
"""

from __future__ import annotations

import hashlib
import json
import re
import string
from dataclasses import dataclass, field
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Levels deeper than this cannot be expressed in the canonical format.
MAX_LEVEL = 9

_FLAGS = {
    "ignorecase": re.IGNORECASE,
    "multiline": re.MULTILINE,
    "dotall": re.DOTALL,
    "verbose": re.VERBOSE,
}


def _digest(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UnitSpec(Base):
    """One structural marker: what it looks like and how deep it sits."""

    name: str
    level: int = Field(ge=1, le=MAX_LEVEL)
    pattern: str
    ignorecase: bool = True
    """Match the marker case-insensitively.

    ! Set this False for any marker that is also an ordinary word. `PENJELASAN` heads
    the explanatory annex of an Indonesian regulation, and `Penjelasan realisasi ini
    karena ...` is a sentence. Matched case-insensitively the sentence became a
    heading, and on the real corpus that produced 2,310 chunks locating themselves at
    `Penjelasan` and another 1,112 at the sentence itself — an ambiguous citation is
    one a reader cannot follow.
    """

    marker_only: bool = False
    """Nothing may follow the marker on the same line.

    ! Set this for any unit whose name commonly opens a sentence. In Indonesian
    regulations `Pasal 9 dihapus.` is prose, not a heading; without this flag the
    chunker splits the document at every amendment clause.
    """

    role: Literal["chapter", "article"] | None = None
    """What this unit is to a reader's citation.

    `chapter` units name the grouping a chunk sits in (`BAB III - KETENTUAN UMUM`);
    `article` units name the thing cited (`Pasal 5`). Declared rather than inferred from
    `level`, because depth says how a marker nests and not what a reader calls it.
    """

    region: str | None = None
    """Label for the part of the document this marker opens, until another one does.

    `PENJELASAN` opens the explanatory annex; every pasal after it belongs to the annex
    and not to the body, which is what lets one `Pasal 5` be told from the other.
    """

    article: str | None = None
    """Fixed label for a unit that is cited by name, not by number (`Pembukaan`)."""

    label: str | None = None
    """How a citation names this unit, built from the pattern's named groups: `Pasal
    {number}`. Whitespace inside a captured value is dropped, because OCR spaces digits
    (`Pasal 3 2` is pasal 32) and glues words (`BABIV`); the label is what a reader cites,
    so it is repaired there and the heading text, which is not stored in `body`, is not."""

    max_title_chars: int | None = None
    prose_tail: list[str] | None = None
    """Per-unit overrides of `structure.max_title_chars` and `structure.prose_tail`. A
    chapter title is routinely longer than 80 characters and ends in `.` or `,` in this
    corpus; a clause that opens `Pasal` is not, which is why the defaults are not looser."""


class MatchSpec(Base):
    """How the router decides a profile applies to a document."""

    mime: list[str] = Field(default_factory=list)
    content: list[str] = Field(default_factory=list)
    min_hits: int = Field(default=1, ge=0)
    priority: int = 0
    """Tie-breaker. The generic fallback sets this negative so anything specific wins."""


class StructureSpec(Base):
    max_line_chars: int = Field(default=120, ge=1)
    max_title_chars: int = Field(default=80, ge=0)
    prose_tail: list[str] = Field(default_factory=lambda: [".", ":", ";", ","])
    units: list[UnitSpec] = Field(default_factory=list)
    sections: list[UnitSpec] = Field(default_factory=list)
    subunits: dict[str, str] = Field(default_factory=dict)
    opening: str | None = None
    """`article` label for what precedes the first marker: the title block of a regulation
    is its `Pembukaan`, and a chunk with no label at all cannot be cited."""

    @model_validator(mode="after")
    def _names_unique(self) -> Self:
        names = [u.name for u in (*self.units, *self.sections)]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"duplicate structural unit names: {sorted(duplicates)}")
        return self


class IdentitySpec(Base):
    """Patterns that read a document's own identity out of its text.

    `{types}` in any pattern expands to an alternation of `types`, so the list is
    maintained in one place and every pattern stays in sync with it.
    """

    types: list[str] = Field(default_factory=list)

    abbreviations: dict[str, str] = Field(default_factory=dict)
    """Full document type → the short form a citation uses (`UNDANG-UNDANG` → `UU`).

    ! Here rather than in the chunker for the same reason the types list is. A chunk's
    `identifier` is what Vera's exact-match bypass keys on, so the short forms decide
    whether a query naming `UU 28/2007` finds the document. That is corpus knowledge,
    and corpus knowledge that lives in Python is knowledge the next corpus cannot
    reuse (`ABSORPTION.md` §4).
    """

    authority: dict[str, int] = Field(default_factory=dict)
    """Document type → its rank in the legal hierarchy, highest first.

    ! Data, not a model. Indonesian regulation is a strict ladder set by UU 12/2011, so
    "how binding is this instrument" is a lookup — and a lookup that belongs in the
    registry rather than in a scorer, because the ladder is a property of the corpus and
    a different jurisdiction has a different one (`CLAUDE.md` §12).

    Vera's `SCORING.md` consumes this as one of six ranking factors, normalised as
    `authority = rank / 10`. Ravel's job is to compute it at ingest, where it is free,
    rather than leaving the engine to re-derive it per query from a string comparison.
    """

    title: str | None = None
    citation: str | None = None
    cross_ref: str | None = None
    enacting_body: str | None = None
    """Named group `body`: who issued the document, read from its title block.

    `{types}` expands as in the other patterns. The text is what sits between the type
    and the number (`PERATURAN BUPATI` `BANYUWANGI` `NOMOR 26`), so a regional
    regulation says which region without a list of every region.
    """

    about: str | None = None
    """Named group `about`: the subject line (`TENTANG ...`), searched in the title block."""

    dates: list[str] = Field(default_factory=list)
    """Patterns for the document's own date, most specific first.

    Named groups `day`, `month`, `year`. The first match that also validates wins, so a
    loose fallback can sit last without outranking a precise one. The notebook this was
    ported from kept 60 patterns in one list and took the first hit, which in practice
    meant the first date of any kind in the text.
    """

    months: list[str] = Field(default_factory=list)
    """Month names in calendar order. A numeric month is rendered as `months[n - 1]`."""

    month_aliases: dict[str, str] = Field(default_factory=dict)
    """Other spellings of a month -> the name in `months` (`Pebruari` -> `Februari`)."""

    flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _known_flags(self) -> Self:
        unknown = set(self.flags) - set(_FLAGS)
        if unknown:
            raise ValueError(
                f"unknown regex flags: {sorted(unknown)} · known: {sorted(_FLAGS)}"
            )
        return self


class CleanupSpec(Base):
    boilerplate: list[str] = Field(default_factory=list)
    min_indexable_chars: int = Field(default=0, ge=0)
    drop_truncated_duplicates: bool = False
    """Drop a block that ends in an ellipsis when the next block begins with the same words.

    A page break leaves the last words of a clause printed twice, the first time cut off
    (`e. menyiapkan...`, then `e. menyiapkan, pengolahan dan penyajian data`). The cut copy
    is repeated in full by the block after it, so removing it loses no word; a truncated
    block that is NOT followed by its own completion is kept, which a pattern alone cannot
    tell and is why this is a rule over neighbours and not another regex."""


class ScoringSpec(Base):
    """The vocabulary the query-independent ranking factors are computed from.

    ! This block exists because `generic@1.0.yaml` claims, in its own description,
    that "everything the `id_regulation` profile does, this does with different data
    and no code changes" — and that was not true. `enrich/factors.py` carried the
    annex and operative patterns as module-level regexes and the legal vocabulary as
    a 26-word frozenset, all Indonesian. A generic corpus got `legal_term_density`
    of ~0 on every row and a `completeness` that had quietly degraded to length
    alone, with nothing in the output saying so.

    That is the same rule `identity.authority` already follows, and for the same
    reason `ABSORPTION.md` §4 gives: corpus knowledge that lives in Python is
    knowledge the next corpus cannot reuse.

    ! Every field defaults to empty, and empty means the factor reports `None`
    rather than a confident zero. A profile that declares no vocabulary is making no
    claim about its corpus, which is different from claiming its corpus scores zero.
    """

    authority_scale: int = Field(default=10, ge=1)
    """What `identity.authority`'s ranks are divided by.

    ! A scale, not a maximum. Indonesian regulation tops out at 9 against a scale of
    10, deliberately: normalising against the highest rank *present* would make the
    same instrument score differently in a corpus that merely lacks a constitution.
    A corpus whose ladder runs 1-5 declares 5 here and its top instrument reaches
    1.0, exactly as UU does against 10.
    """

    annex: list[str] = Field(default_factory=list)
    """Patterns marking a chunk as annex-like: schedules, forms, appendices.

    Matched against `article` then `chapter`, anchored at the start. An annex is
    rarely the answer to a question about obligations.
    """

    operative: list[str] = Field(default_factory=list)
    """Patterns marking a chunk as operative text — the clause itself."""

    annex_score: float = Field(default=0.2, ge=0.0, le=1.0)
    operative_score: float = Field(default=1.0, ge=0.0, le=1.0)
    labelled_score: float = Field(default=0.6, ge=0.0, le=1.0)
    """A chunk that carries a label matching neither list. It is located but not
    classified, which is a weaker claim than either and a stronger one than nothing."""

    terms: list[str] = Field(default_factory=list)
    """The domain vocabulary whose density is the readable half of `completeness`.

    ! A whole provision states an obligation and a fragment usually does not — but
    "states an obligation" is spelled `wajib` in one corpus, `MUST` in another and
    `shall` in a third. The claim generalises; the words never do.
    """

    stopwords: list[str] = Field(default_factory=list)
    """Function words the engine drops before measuring term overlap.

    ! Read by Vera, not by Ravel: it is the input to the relevance floor
    (`SCORING.md` §3), and it is declared here so the engine stops compiling in a
    stop list that describes one language.
    """

    min_term_chars: int = Field(default=3, ge=1)
    """A token must be LONGER than this to count · at 3 the engine keeps 4+ characters.

    ! Strictly greater, not "at least". The distinction is one character and it moves
    Vera's relevance floor, which is a threshold fitted against one tokenisation --
    applying it to another is a cut nothing measured (`SCORING.md` §3). Documented
    here in the units the engine actually uses rather than the ones the name suggests.
    """

    def resolved(self, authority: dict[str, int]) -> dict[str, Any]:
        """The whole vocabulary, as one JSON document for `corpus_meta`.

        ! Takes `identity.authority` as an argument rather than reaching for it. The
        ladder lives under `identity` because it is how a document says what it *is*;
        it is consumed as a ranking factor. Both are true, and duplicating the table
        under `scoring:` so this method could read one object would create the exact
        drift this block exists to remove.

        Keys are uppercased here, once, rather than per candidate on every request in
        the engine.
        """
        return {
            "authority": {k.upper(): v for k, v in authority.items()},
            "authority_scale": self.authority_scale,
            "annex": list(self.annex),
            "operative": list(self.operative),
            "annex_score": self.annex_score,
            "operative_score": self.operative_score,
            "labelled_score": self.labelled_score,
            "terms": [t.lower() for t in self.terms],
            "stopwords": [w.lower() for w in self.stopwords],
            "min_term_chars": self.min_term_chars,
        }

    @model_validator(mode="after")
    def _annex_outranks_nothing(self) -> Self:
        # ! An annex scoring above an operative clause inverts the one thing this
        # factor exists to do. It is a legible thing to want for some corpus, and it
        # is not something to acquire by mistyping two numbers.
        if self.annex and self.operative and self.annex_score >= self.operative_score:
            raise ValueError(
                f"annex_score {self.annex_score} >= operative_score "
                f"{self.operative_score}: a schedule would outrank the clause it "
                "belongs to"
            )
        return self


#: The chunk fields a profile may name in `expected.coverage` and `derive.embed_text`.
#: Listed here, not read off `Chunk`, because `chunk` imports `spec`; a test asserts every
#: name is a real `Chunk` field, so the two cannot drift apart.
CHUNK_FIELDS = frozenset(
    {
        "body",
        "identifier",
        "regulation_type",
        "enacting_body",
        "regulation_number",
        "year",
        "about",
        "effective_date",
        "chapter",
        "article",
        "heading_path",
        "locator_section",
        "source_title",
    }
)


class ChunkDefaults(Base):
    """What this kind of document wants from the chunker. Every field is optional.

    ! None means "not stated", not "zero". Precedence is engine default < profile <
    corpus < variant, and a field the profile leaves out must fall through to the next
    layer rather than overwrite it with a default of its own.
    """

    chunker: str | None = None
    max_tokens: int | None = Field(default=None, ge=32)
    min_tokens: int | None = Field(default=None, ge=0)
    overlap_tokens: int | None = Field(default=None, ge=0)
    carry_parent: bool | None = None
    carry_parent_max_tokens: int | None = Field(default=None, ge=0)
    include_tables: bool | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class Rewrite(Base):
    pattern: str
    replace: str = ""


class EmbedText(Base):
    """How a chunk is presented to the embedder: context first, then the body.

    A pasal of thirty tokens means little alone; the law, subject and chapter it sits in
    are what place it. The context is built from fields the chunk already carries, so it
    costs no extra extraction -- and it is *derived*: `body` is never touched.
    """

    context: list[str] = Field(default_factory=list)
    """Parts joined by `separator`. A part is dropped when every field in it is empty."""
    separator: str = " › "
    skip_values: list[str] = Field(default_factory=lambda: ["N/A"])
    """Field values that count as empty (`N/A` is a label, not information)."""

    @model_validator(mode="after")
    def _known_fields(self) -> Self:
        for part in self.context:
            for _, name, _, _ in string.Formatter().parse(part):
                if name and name not in CHUNK_FIELDS:
                    raise ValueError(
                        f"embed_text names {name!r} · known: {sorted(CHUNK_FIELDS)}"
                    )
        return self


class DeriveSpec(Base):
    """Text derived from a chunk for the index layers. Never stored in `body`."""

    embed_text: EmbedText | None = None
    search_normalize: list[Rewrite] = Field(default_factory=list)
    """Rewrites applied to `body` to make `search_text` for the keyword index. Fixes the
    damage OCR does to tokens a query will contain (`Pasal5`, `1O0`) in the copy that is
    *matched*, while the copy that is *cited* stays as the source printed it."""


class ExpectedSpec(Base):
    """The contract the knowledge-base build is checked against.

    Floors, not targets: a build below one is reported, and the profile author decides
    whether the profile or the corpus is wrong. Measured on a real sample before they
    are written down, so they describe what is achievable and not what is hoped for.
    """

    coverage: dict[str, float] = Field(default_factory=dict)
    """Chunk field -> least share of chunks that must carry it (0..1)."""
    max_row_tokens: int | None = Field(default=None, ge=1)
    """Chunks above this are over budget. The embedder's `max_length` is the natural value."""
    oversize_share: float = Field(default=0.0, ge=0, le=1)
    """Share of chunks allowed above `max_row_tokens`. A single block is the smallest unit a
    chunk is built from, so one unbroken run-on paragraph can exceed any budget; 0 would
    fail a whole corpus for it, and this says how many such rows are tolerable."""

    @model_validator(mode="after")
    def _known(self) -> Self:
        for name, floor in self.coverage.items():
            if name not in CHUNK_FIELDS:
                raise ValueError(
                    f"expected.coverage names {name!r} · known: {sorted(CHUNK_FIELDS)}"
                )
            if not 0 <= floor <= 1:
                raise ValueError(f"expected.coverage[{name!r}] = {floor}: must be within 0..1")
        return self


class PipelineSpec(Base):
    """What the layers after extraction do for this kind of document (`PIPELINE.md`)."""

    chunk: ChunkDefaults = Field(default_factory=ChunkDefaults)
    derive: DeriveSpec = Field(default_factory=DeriveSpec)
    expected: ExpectedSpec = Field(default_factory=ExpectedSpec)


class ProfileSpec(Base):
    """The whole file. Validated from YAML; hashed into every downstream cache key."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(pattern=r"^\d+\.\d+$")
    title: str
    description: str = ""
    match: MatchSpec = Field(default_factory=MatchSpec)
    structure: StructureSpec = Field(default_factory=StructureSpec)
    identity: IdentitySpec = Field(default_factory=IdentitySpec)
    cleanup: CleanupSpec = Field(default_factory=CleanupSpec)
    scoring: ScoringSpec = Field(default_factory=ScoringSpec)
    pipeline: PipelineSpec = Field(default_factory=PipelineSpec)
    extract: dict[str, Any] = Field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def scoring_vocabulary(self) -> dict[str, Any]:
        """What goes into `corpus_meta.scoring_vocabulary` · the engine's copy of this
        profile's ranking tables, so it stops carrying its own."""
        return self.scoring.resolved(self.identity.authority)

    @property
    def config_hash(self) -> str:
        """Hash of the fully resolved spec.

        ! Every stage that reads *any* part of a profile folds this into its cache
        key. A pattern edited without a version bump therefore invalidates the
        artifacts it produced, instead of silently reusing them (LOOPHOLES.md §1).
        """
        return _digest(self.model_dump(mode="json"))

    @property
    def structure_hash(self) -> str:
        """Hash of only the parts that change a *canonical document*.

        ! Extraction keys on this, not on `config_hash`, and the distinction earns its
        keep the first time a profile is corrected. Fixing the `identity.citation`
        pattern — which recovered 37 uncited documents and corrected 21 that were
        citing the regulation they implement rather than themselves — changes nothing
        about how a document is split into blocks and headings. Under a whole-spec key
        it would still have discarded 931 extracted documents and spent nineteen
        minutes reproducing them byte for byte.

        `match` and `structure` are here because the router and the structurer read
        them. `identity` and `cleanup` are not: they are read at chunk time, and
        chunking keys on the full `config_hash`.
        """
        # ! `role`, `region`, `article` and `opening` are labels read at chunk time. Left
        # in, correcting a label would discard every extracted document to reproduce the
        # same blocks -- the cost this hash exists to avoid.
        label_fields = {"role", "region", "article", "label"}
        structure = self.structure.model_dump(
            mode="json",
            exclude={
                "opening": True,
                "units": {"__all__": label_fields},
                "sections": {"__all__": label_fields},
            },
        )
        return _digest(
            {
                "match": self.match.model_dump(mode="json"),
                "structure": structure,
                "extract": self.extract,
            }
        )


# ---------------------------------------------------------------------------
# Compiled runtime
# ---------------------------------------------------------------------------


def _squash(text: str | None) -> str | None:
    """Collapse whitespace and trim edge punctuation; None when nothing is left."""
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip(" \t\n.:;,-")
    return cleaned or None


@dataclass(frozen=True, slots=True)
class Unit:
    """A compiled structural marker."""

    name: str
    level: int
    pattern: re.Pattern[str]
    marker_only: bool
    role: str | None = None
    region: str | None = None
    article: str | None = None
    label: str | None = None
    max_title_chars: int | None = None
    prose_tail: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class Facts:
    """What a document says about itself, as far as its profile can read.

    Every field is None when the profile did not find it. Never a guess: an empty
    `regulation_type` is a gap somebody can count, and `Unknown` is a claim.
    """

    regulation_type: str | None = None
    enacting_body: str | None = None
    regulation_number: str | None = None
    year: int | None = None
    about: str | None = None
    effective_date: str | None = None


@dataclass(frozen=True, slots=True)
class Profile:
    """A profile with everything compiled. Built once, consulted per line."""

    spec: ProfileSpec
    units: tuple[Unit, ...]
    subunits: dict[str, re.Pattern[str]]
    boilerplate: tuple[re.Pattern[str], ...]
    content_match: tuple[re.Pattern[str], ...]
    abbreviations: dict[str, str] = field(default_factory=dict)
    title: re.Pattern[str] | None = None
    citation: re.Pattern[str] | None = None
    cross_ref: re.Pattern[str] | None = None
    enacting_body: re.Pattern[str] | None = None
    about: re.Pattern[str] | None = None
    dates: tuple[re.Pattern[str], ...] = ()
    rewrites: tuple[tuple[re.Pattern[str], str], ...] = ()
    _types_sorted: tuple[str, ...] = field(default=())

    # -- construction -------------------------------------------------------
    @classmethod
    def compile(cls, spec: ProfileSpec) -> Profile:
        flags = 0
        for name in spec.identity.flags:
            flags |= _FLAGS[name]

        # Longest first, so "PERATURAN DAERAH KABUPATEN" wins over "PERATURAN DAERAH".
        types = tuple(sorted(spec.identity.types, key=len, reverse=True))
        alternation = "|".join(re.escape(t) for t in types)

        def build(pattern: str | None) -> re.Pattern[str] | None:
            if not pattern:
                return None
            return re.compile(pattern.format(types=alternation), flags)

        return cls(
            spec=spec,
            units=tuple(
                Unit(
                    u.name,
                    u.level,
                    re.compile(u.pattern, re.IGNORECASE if u.ignorecase else 0),
                    u.marker_only,
                    u.role,
                    u.region,
                    u.article,
                    u.label,
                    u.max_title_chars,
                    None if u.prose_tail is None else tuple(u.prose_tail),
                )
                for u in (*spec.structure.units, *spec.structure.sections)
            ),
            subunits={k: re.compile(v) for k, v in spec.structure.subunits.items()},
            boilerplate=tuple(re.compile(p, re.IGNORECASE) for p in spec.cleanup.boilerplate),
            content_match=tuple(re.compile(p) for p in spec.match.content),
            abbreviations={k.upper(): v for k, v in spec.identity.abbreviations.items()},
            title=build(spec.identity.title),
            citation=build(spec.identity.citation),
            cross_ref=build(spec.identity.cross_ref),
            enacting_body=build(spec.identity.enacting_body),
            about=build(spec.identity.about),
            dates=tuple(re.compile(d, flags) for d in spec.identity.dates),
            rewrites=tuple(
                (re.compile(r.pattern), r.replace)
                for r in spec.pipeline.derive.search_normalize
            ),
            _types_sorted=types,
        )

    # -- identity -----------------------------------------------------------
    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def version(self) -> str:
        return self.spec.version

    @property
    def ref(self) -> str:
        return self.spec.ref

    @property
    def config_hash(self) -> str:
        return self.spec.config_hash

    @property
    def structure_hash(self) -> str:
        return self.spec.structure_hash

    # -- structure ----------------------------------------------------------
    def structural(self, line: str) -> Unit | None:
        """The structural unit a line announces, or None.

        Errs toward None. A missed heading understates an extractor's recall; a
        false one corrupts both the chunker and any measurement built on it.
        """
        text = line.strip()
        if not text or len(text) > self.spec.structure.max_line_chars:
            return None
        for unit in self.units:
            match = unit.pattern.match(text)
            if not match:
                continue
            rest = text[match.end() :].strip()
            if not rest:
                return unit
            if unit.marker_only or not self._is_title(rest, unit):
                return None
            return unit
        return None

    def _is_title(self, rest: str, unit: Unit) -> bool:
        limits = self.spec.structure
        longest = (
            unit.max_title_chars if unit.max_title_chars is not None else limits.max_title_chars
        )
        tail = unit.prose_tail if unit.prose_tail is not None else tuple(limits.prose_tail)
        return len(rest) <= longest and not rest.endswith(tail)

    def subunit(self, line: str) -> tuple[str, str, str] | None:
        """`(kind, value, marker)` for a sub-unit such as an ayat or huruf.

        `value` is the bare capture (`2`); `marker` is the marker as the document
        writes it, trailing punctuation stripped (`(2)`, `a`).

        ! Both, because they are needed for different things and using one for the
        other is silently wrong. A citation reads `Pasal 9 ayat (2)` — with the
        parentheses, which are part of how the unit is written — while sorting and
        cross-reference matching need the bare `2`.
        """
        for kind, pattern in self.subunits.items():
            if match := pattern.match(line):
                value = match.group(1) if match.groups() else match.group(0)
                marker = match.group(0).strip().rstrip(".:)").lstrip()
                if match.group(0).strip().startswith("("):
                    marker = f"({value})"
                return kind, value, marker
        return None

    # -- identity extraction ------------------------------------------------
    def document_type(self, text: str) -> str | None:
        """Longest matching type, or None. Never guesses."""
        upper = text.upper()
        return next((t for t in self._types_sorted if t in upper), None)

    def abbreviate(self, document_type: str) -> str:
        """Short form of a document type, or the type unchanged when none is declared."""
        return self.abbreviations.get(document_type.upper(), document_type)

    @property
    def scoring(self) -> ScoringSpec:
        """The vocabulary the ranking factors are computed from."""
        return self.spec.scoring

    def authority_of(self, document_type: str | None) -> int | None:
        """Where this instrument sits in the legal hierarchy, or None if unranked.

        ! None, never 0. An unranked type means "the profile does not know", and a
        scorer must be able to tell that apart from "ranked lowest" — otherwise every
        unrecognised document silently becomes the least authoritative thing in the
        corpus, which is a claim the profile never made.
        """
        if not document_type:
            return None
        return self.spec.identity.authority.get(document_type.upper())

    def cite(self, text: str) -> str | None:
        """The document's own citation in short form, or None.

        Returns None rather than a guess. An `identifier` that is wrong is worse than
        one that is absent: the exact-match path trusts it completely.
        """
        if self.citation is None:
            return None
        match = self.citation.search(text)
        if not match:
            return None
        groups = match.groupdict()
        kind, number, year = groups.get("type"), groups.get("number"), groups.get("year")
        if not (kind and number and year):
            return None
        # Some numbering schemes already carry the year (`123/PMK.03/2019`);
        # appending it again would produce a citation nobody writes.
        if number.endswith(f"/{year}"):
            return f"{self.abbreviate(kind)} {number}"
        return f"{self.abbreviate(kind)} {number}/{year}"

    def describe(self, head: str, whole: str | None = None) -> Facts:
        """The document's own facts. `head` is its title block; `whole` is all of it.

        Type, number and year come from the citation, the body and subject from the
        title block, and the date from anywhere in the document -- a regulation states
        when it was issued at its foot, not its head.
        """
        kind = number = None
        year: int | None = None
        if self.citation and (match := self.citation.search(head)):
            groups = match.groupdict()
            kind, number = groups.get("type"), groups.get("number")
            if (groups.get("year") or "").isdigit():
                year = int(groups["year"])
        if number and year and number.endswith(f"/{year}"):
            number = number[: -len(f"/{year}")]
        body = about = None
        if self.enacting_body and (match := self.enacting_body.search(head)):
            body = _squash(match.groupdict().get("body"))
        if self.about and (match := self.about.search(head)):
            about = _squash(match.groupdict().get("about"))
        kind = _squash(kind)
        return Facts(
            regulation_type=kind.upper() if kind else None,
            enacting_body=body,
            regulation_number=number,
            year=year,
            about=about,
            effective_date=self._date(whole if whole is not None else head),
        )

    def _date(self, text: str) -> str | None:
        months = self.spec.identity.months
        names = {m.lower(): m for m in months}
        names.update({k.lower(): v for k, v in self.spec.identity.month_aliases.items()})
        for pattern in self.dates:
            for match in pattern.finditer(text):
                groups = match.groupdict()
                day, month, year = groups.get("day"), groups.get("month"), groups.get("year")
                if not (day and month and year):
                    continue
                if month.isdigit() and 1 <= int(month) <= len(months):
                    month = months[int(month) - 1]
                elif month.lower() in names:
                    month = names[month.lower()]
                else:
                    continue
                if 1 <= int(day) <= 31 and 1900 <= int(year) <= 2100:
                    return f"{int(day)} {month} {year}"
        return None

    def is_boilerplate(self, text: str) -> bool:
        return any(p.match(text) for p in self.boilerplate)

    # -- routing ------------------------------------------------------------
    def score(self, *, mime: str, sample: str) -> int | None:
        """How well this profile fits a document, or None if it does not apply.

        ! None, not a negative number. `priority` is deliberately negative on the
        generic fallback, so "negative means no match" would make the fallback
        filter itself out and leave HTML and plain text with no profile at all.
        """
        if self.spec.match.mime and mime not in self.spec.match.mime:
            return None
        hits = sum(1 for p in self.content_match if p.search(sample))
        if hits < self.spec.match.min_hits:
            return None
        return hits * 10 + self.spec.match.priority

    def extract_options(self, extractor_id: str) -> dict[str, Any]:
        value = self.spec.extract.get(extractor_id, {})
        return dict(value) if isinstance(value, dict) else {}

    def option(self, key: str, default: Any = None) -> Any:
        return self.spec.extract.get(key, default)
