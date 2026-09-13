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
from dataclasses import dataclass, field
from typing import Any, Self

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
    extract: dict[str, Any] = Field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

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
        return _digest(
            {
                "match": self.match.model_dump(mode="json"),
                "structure": self.structure.model_dump(mode="json"),
                "extract": self.extract,
            }
        )


# ---------------------------------------------------------------------------
# Compiled runtime
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Unit:
    """A compiled structural marker."""

    name: str
    level: int
    pattern: re.Pattern[str]
    marker_only: bool


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
            if unit.marker_only or not self._is_title(rest):
                return None
            return unit
        return None

    def _is_title(self, rest: str) -> bool:
        limits = self.spec.structure
        return len(rest) <= limits.max_title_chars and not rest.endswith(
            tuple(limits.prose_tail)
        )

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
