"""How regulations relate to each other: revokes, amends, is based on, merely mentions.

Why this and not NER
--------------------
A regulation says what it replaces in so many words -- `Pada saat Peraturan Bupati ini
mulai berlaku, Peraturan Bupati Lahat Nomor 15 Tahun 2017 ... dicabut dan dinyatakan tidak
berlaku` -- and what it changes in its title. The target is named by type, number and year,
which is exactly what the profile already knows how to read. So the graph is built from
citations plus the verb beside each one, not from a model guessing entity spans, and every
edge carries the sentence it was read from.

What is data and what is code
-----------------------------
The verbs (`mencabut`, `diubah`), the region where a legal basis lives (after `Mengingat`)
and the phrases that turn a citation into history (`sebagaimana telah diubah dengan`) are
the profile's `relations:` section. This module knows only the mechanism: find citations,
bind each to its nearest cue, demote the ones that are context, resolve the target against
the corpus. A corpus in another language or jurisdiction brings its own section.

Honest limits
-------------
* **Absence is not validity.** A document with no `revoked_by` edge has *no known*
  revocation. The revoker may not be in the corpus, so the status is never "in force".
* **A target outside the corpus stays an edge**, resolution `unresolved`: the claim "this
  revokes Perda 11/1979" is true and citable even when 11/1979 is not here to be marked.
* **Ambiguity is reported, not resolved by luck.** `Peraturan Daerah Nomor 5 Tahun 2010`
  exists in many regions; with no region in the citation and no unique candidate, the edge
  is `ambiguous` and links to nothing.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from chunk.models import Chunk
from spec.models import Profile, RelationRule

__all__ = [
    "DocIdentity",
    "DocStatus",
    "Index",
    "Relation",
    "RelationExtractor",
    "derive_status",
    "identity_of",
    "resolve",
]

SUPERSEDING = frozenset({"revokes", "replaces"})
"""Relations that end a document's life: a regulation that replaces another has revoked it
whether or not the text says `dicabut`."""

REFERENCES = "references"
"""A citation with no verb and no region: it mentions, it does not act on."""

Resolution = Literal["resolved", "inferred", "ambiguous", "unresolved", "self"]


def _squash(text: str | None) -> str:
    return re.sub(r"[^0-9a-z]+", "", (text or "").lower())


def _words(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).upper()


def _number(text: str) -> str:
    """`005` and `5` are one number; `5A` is not `5`."""
    return re.sub(r"^0+(?=\d)", "", re.sub(r"\s+", "", text).upper())


@dataclass(frozen=True, slots=True)
class DocIdentity:
    """What a document calls itself -- the key other documents' citations resolve to."""

    doc_id: str
    regulation_type: str
    enacting_body: str
    number: str
    year: int
    type_abbreviation: str = ""


def _region(profile: Profile, text: str | None) -> str:
    """The issuing region, squashed; empty for the state itself."""
    squashed = _squash(text)
    nationals = {_squash(n) for n in profile.spec.relations.national_bodies}
    return "" if squashed in nationals else squashed


def identity_of(profile: Profile, chunks: Sequence[Chunk]) -> DocIdentity | None:
    """The document's own identity, from its chunks, or None.

    None when the title block named no type, number and year: such a document can cite
    others but cannot be cited, and the gap is counted, not filled.
    """
    for c in chunks:
        if c.regulation_type and c.regulation_number and c.year:
            return DocIdentity(
                doc_id=c.doc_id,
                regulation_type=_words(c.regulation_type),
                enacting_body=_region(profile, c.enacting_body),
                number=_number(c.regulation_number),
                year=c.year,
                type_abbreviation=_words(profile.abbreviate(c.regulation_type)),
            )
    return None


@dataclass(frozen=True, slots=True)
class Relation:
    """One edge, read from one sentence."""

    relation: str
    target_text: str
    target_type: str
    target_body: str
    target_number: str
    target_year: int
    evidence_chunk_id: str
    quote: str
    partial: bool = False
    mentions: int = 1
    cue: str | None = None

    @property
    def key(self) -> tuple[str, str, str, int, str]:
        return (
            self.target_type,
            self.target_body,
            self.target_number,
            self.target_year,
            self.relation,
        )


@dataclass(slots=True)
class _Cue:
    rule: RelationRule
    start: int
    end: int


class RelationExtractor:
    """Reads the relations of one document from its chunks. Build once per process."""

    def __init__(self, profile: Profile) -> None:
        spec = profile.spec.relations
        self.profile = profile
        self.spec = spec
        alternation = "|".join(re.escape(t) for t in profile._types_sorted)
        self.reference = (
            re.compile(spec.reference.format(types=alternation)) if spec.reference else None
        )
        self.cues = [
            (rule, re.compile(rule.cue)) for rule in spec.rules if rule.cue is not None
        ]
        self.regions = [
            (rule, re.compile(rule.region_start), re.compile(rule.region_end or r"(?!x)x"))
            for rule in spec.rules
            if rule.region_start is not None
        ]
        self.sentence_end = re.compile(spec.sentence_end)
        self.demote = re.compile(spec.demote) if spec.demote else None
        self.demote_subject = (
            re.compile(spec.demote_unless_adjacent) if spec.demote_unless_adjacent else None
        )
        self.partial = re.compile(spec.partial) if spec.partial else None
        self.title_marker = re.compile(spec.title_marker) if spec.title_marker else None
        self.negate = re.compile(spec.negate) if spec.negate else None
        self._own: DocIdentity | None = None
        self.list_item = re.compile(spec.list_item)

    # ------------------------------------------------------------------
    def extract(
        self, chunks: Sequence[Chunk], own: DocIdentity | None = None
    ) -> list[Relation]:
        """Every relation the chunks state, once per (relation, target), in document order."""
        if self.reference is None:
            return []
        self._own = own
        found: dict[tuple[str, str, str, int, str], Relation] = {}
        inherited: RelationRule | None = None
        in_region: dict[str, bool] = {}

        for chunk in sorted(chunks, key=lambda c: c.seq):
            chapter = chunk.chapter or ""
            if any(chapter.startswith(prefix) for prefix in self.spec.skip_chapters):
                inherited = None
                continue
            preamble = (
                chunk.article == self.spec.preamble_article
                and len(chunk.body) <= self.spec.preamble_max_chars
            )
            for paragraph in re.split(r"\n\s*\n", chunk.body):
                # Converters double the spaces inside a citation (`Tingkat  II  Nomor  11`).
                paragraph = re.sub(r"\s+", " ", paragraph).strip()
                if not paragraph:
                    continue
                header_rule: RelationRule | None = None
                carried = inherited if self.list_item.match(paragraph) else None
                offset = 0
                for statement in self._statements(paragraph):
                    start = paragraph.find(statement, offset)
                    offset = start + len(statement) if start >= 0 else offset
                    region_rule = (
                        self._region_at(paragraph, max(start, 0), in_region)
                        if preamble
                        else None
                    )
                    for rel in self._read(statement, chunk, carried, region_rule):
                        if own is not None and self._is_self(rel, own):
                            continue
                        previous = found.get(rel.key)
                        found[rel.key] = (
                            rel if previous is None else _more_mentions(previous, rel)
                        )
                    header_rule = header_rule or self._cue_rule(statement)
                if preamble:
                    self._advance(paragraph, in_region)
                if header_rule is not None and paragraph.rstrip().endswith(":"):
                    inherited = header_rule
                elif not self.list_item.match(paragraph):
                    inherited = None
        return list(found.values())

    # ------------------------------------------------------------------
    def _statements(self, paragraph: str) -> list[str]:
        parts = [p.strip() for p in self.sentence_end.split(paragraph) if p and p.strip()]
        return parts or [paragraph]

    def _cue_rule(self, statement: str) -> RelationRule | None:
        for rule, pattern in self.cues:
            if pattern.search(statement):
                return rule
        return None

    def _region_at(
        self, paragraph: str, start: int, state: dict[str, bool]
    ) -> RelationRule | None:
        """The region rule in force at `start` in this paragraph.

        State is per relation and survives across paragraphs and chunks, because a legal
        basis is a list of paragraphs (`Mengingat : 1. ... 2. ...`) and not one.
        """
        for rule, begin, end in self.regions:
            opened = begin.search(paragraph)
            closed = end.search(paragraph, opened.end() if opened else 0)
            inside = state.get(rule.relation, False)
            if opened and start >= opened.start():
                inside = True
            if closed and start >= closed.start():
                inside = False
            if inside:
                return rule
        return None

    def _advance(self, paragraph: str, state: dict[str, bool]) -> None:
        """Carry the open/closed state past the end of a paragraph."""
        for rule, begin, end in self.regions:
            opened = begin.search(paragraph)
            closed = end.search(paragraph, opened.end() if opened else 0)
            if opened:
                state[rule.relation] = True
            if closed:
                state[rule.relation] = False

    def _read(
        self,
        statement: str,
        chunk: Chunk,
        carried: RelationRule | None,
        region_rule: RelationRule | None,
    ) -> Iterable[Relation]:
        assert self.reference is not None
        cues = [
            _Cue(rule, m.start(), m.end())
            for rule, pattern in self.cues
            for m in pattern.finditer(statement)
            if not self._negated(statement, m.start())
        ]
        cited = [
            m
            for m in self.reference.finditer(statement)
            if (g := m.groupdict()).get("type")
            and g.get("number")
            and (g.get("year") or "").isdigit()
        ]
        for index, m in enumerate(cited):
            groups = m.groupdict()
            number, year, kind = groups["number"], groups["year"], groups["type"]
            nearest = self._nearest(cues, m.start(), m.end())
            adjacent = (
                nearest is not None
                and nearest.end <= m.start()
                and m.start() - nearest.end <= self.spec.adjacent
            )
            relation = REFERENCES
            cue_text: str | None = None
            # The legal basis is a list, whatever verbs the cited titles contain.
            if region_rule is not None:
                relation, adjacent = region_rule.relation, False
            elif nearest is not None:
                relation = nearest.rule.relation
                cue_text = statement[nearest.start : nearest.end]
            elif carried is not None:
                relation = carried.relation
            if relation != REFERENCES and (
                self._demoted(self.demote, statement, m.start())
                or (not adjacent and self._demoted(self.demote_subject, statement, m.start()))
            ):
                relation, cue_text = REFERENCES, None
            if (
                relation != REFERENCES
                and region_rule is None
                and nearest is not None
                and index > 0
                and self._in_title_of(statement, cited[index - 1], m, nearest)
            ):
                relation, cue_text = REFERENCES, None
            partial = bool(
                relation != REFERENCES
                and self.partial is not None
                and self.partial.search(statement[max(0, m.start() - 60) : m.start()])
            )
            yield Relation(
                relation=relation,
                target_text=m.group().strip(),
                target_type=_words(kind),
                target_body=_region(self.profile, groups.get("body")),
                target_number=_number(number),
                target_year=int(year),
                evidence_chunk_id=chunk.id,
                quote=statement[:400],
                partial=partial,
                cue=cue_text,
            )

    def _nearest(self, cues: list[_Cue], start: int, end: int) -> _Cue | None:
        """The cue closest to the citation on a side its rule allows, within its window."""
        best: _Cue | None = None
        best_gap = 1 << 30
        for cue in cues:
            before, after = cue.end <= start, cue.start >= end
            side = cue.rule.side
            if not (before or after):
                continue
            if (side == "before" and not before) or (side == "after" and not after):
                continue
            gap = start - cue.end if before else cue.start - end
            if gap <= (cue.rule.window or self.spec.window) and gap < best_gap:
                best, best_gap = cue, gap
        return best

    def _in_title_of(
        self, statement: str, earlier: re.Match[str], this: re.Match[str], cue: _Cue
    ) -> bool:
        """Is the cue part of the subject of an earlier citation of some OTHER instrument?"""
        if self.title_marker is None:
            return False
        stop = cue.start if cue.end <= this.start() else this.start()
        if stop < earlier.end() or not self.title_marker.search(
            statement[earlier.end() : stop]
        ):
            return False
        g = earlier.groupdict()
        probe = Relation(
            relation=REFERENCES,
            target_text="",
            target_type=_words(g["type"]),
            target_body=_region(self.profile, g.get("body")),
            target_number=_number(g["number"]),
            target_year=int(g["year"]),
            evidence_chunk_id="",
            quote="",
        )
        return not (self._own is not None and self._is_self(probe, self._own))

    def _negated(self, statement: str, start: int) -> bool:
        return self.negate is not None and bool(
            self.negate.search(statement[max(0, start - 40) : start])
        )

    @staticmethod
    def _demoted(pattern: re.Pattern[str] | None, statement: str, start: int) -> bool:
        return pattern is not None and bool(
            pattern.search(statement[max(0, start - 160) : start])
        )

    def _is_self(self, rel: Relation, own: DocIdentity) -> bool:
        return (
            rel.target_number == own.number
            and rel.target_year == own.year
            and _type_compatible(rel.target_type, own)
            and (
                not rel.target_body
                or not own.enacting_body
                or _body_match(rel.target_body, own)
            )
        )


def _more_mentions(previous: Relation, rel: Relation) -> Relation:
    return Relation(
        relation=previous.relation,
        target_text=previous.target_text,
        target_type=previous.target_type,
        target_body=previous.target_body or rel.target_body,
        target_number=previous.target_number,
        target_year=previous.target_year,
        evidence_chunk_id=previous.evidence_chunk_id,
        quote=previous.quote,
        partial=previous.partial and rel.partial,
        mentions=previous.mentions + rel.mentions,
        cue=previous.cue,
    )


# ---------------------------------------------------------------------------
# resolution: which document in the corpus a citation means
# ---------------------------------------------------------------------------
def _type_compatible(cited: str, doc: DocIdentity) -> bool:
    """`PERDA` is a Perda Kab., a Perda Kota or a Perda Prov.; `PERATURAN DAERAH` is any
    of its three full types. A cited type that is a prefix of the document's is compatible."""
    for own in (doc.regulation_type, doc.type_abbreviation):
        if cited == own or own.startswith(cited + " "):
            return True
    return False


def _body_match(cited_body: str, doc: DocIdentity) -> bool:
    if not (cited_body and doc.enacting_body):
        return False
    return doc.enacting_body in cited_body or cited_body in doc.enacting_body


class Index:
    """Documents by (number, year): the cheap half of a citation, checked first.

    The corpus holds some regulations more than once (a scan and its re-typed copy, a draft
    and the final). Copies are one instrument: a citation of it is not ambiguous between
    them, and a revocation of it ends every copy.
    """

    def __init__(self, identities: Iterable[DocIdentity]) -> None:
        self._by_key: dict[tuple[str, int], list[DocIdentity]] = defaultdict(list)
        self._group: dict[tuple[str, str, str, int], list[str]] = defaultdict(list)
        self._of: dict[str, tuple[str, str, str, int]] = {}
        self.size = 0
        for identity in sorted(identities, key=lambda i: i.doc_id):
            key = (
                identity.regulation_type,
                identity.enacting_body,
                identity.number,
                identity.year,
            )
            if not self._group[key]:
                self._by_key[(identity.number, identity.year)].append(identity)
            self._group[key].append(identity.doc_id)
            self._of[identity.doc_id] = key
            self.size += 1

    def candidates(self, number: str, year: int) -> list[DocIdentity]:
        """One document per distinct instrument; the copies are `copies(doc_id)`."""
        return self._by_key.get((number, year), [])

    def copies(self, doc_id: str) -> list[str]:
        """Every document that is the same instrument as this one, itself included."""
        key = self._of.get(doc_id)
        return list(self._group[key]) if key else [doc_id]


def resolve(
    rel: Relation, citing: DocIdentity | None, index: Index
) -> tuple[str | None, Resolution, float]:
    """The cited document's id (or None), how sure that is, and why.

    * `resolved` 1.0   -- a single instrument fits, region and all.
    * `inferred` 0.7  -- the citation names no region; the citing document's own region
      is assumed (`Peraturan Daerah Nomor 5 Tahun 2010` inside a Lahat regulation).
    * `inferred` 0.6  -- the sentence about the cited instrument names exactly one of the
      regions that fit (`... Pelimpahan Kewenangan Kabupaten Empat Lawang`).
    * `ambiguous` 0    -- several instruments fit and nothing separates them.
    * `unresolved` 0   -- none fits, which includes "a different region's".
    """
    cands = [
        c
        for c in index.candidates(rel.target_number, rel.target_year)
        if _type_compatible(rel.target_type, c)
    ]
    if not cands:
        return None, "unresolved", 0.0
    if rel.target_body:
        named = _most_specific(
            rel.target_body, [c for c in cands if _body_match(rel.target_body, c)]
        )
        if len(named) == 1:
            return named[0].doc_id, "resolved", 1.0
        if len(named) > 1:
            return _hinted(rel, named)
        return None, "unresolved", 0.0  # the region the citation names is not in the corpus
    national = [c for c in cands if not c.enacting_body]
    if len(national) == 1:
        return national[0].doc_id, "resolved", 1.0
    if citing is not None and citing.enacting_body:
        local = [c for c in cands if c.enacting_body == citing.enacting_body]
        if len(local) == 1:
            return local[0].doc_id, "inferred", 0.7
        if not local:
            return None, "unresolved", 0.0
    if len(cands) > 1:
        return _hinted(rel, cands)
    return None, "unresolved", 0.0


def _most_specific(cited_body: str, matched: list[DocIdentity]) -> list[DocIdentity]:
    """`Bangka Tengah` contains `Bangka`; the citation means the longer region, not both."""
    exact = [c for c in matched if c.enacting_body == cited_body]
    if exact:
        return exact
    longest = max((len(c.enacting_body) for c in matched), default=0)
    return [c for c in matched if len(c.enacting_body) == longest]


def _hinted(rel: Relation, cands: list[DocIdentity]) -> tuple[str | None, Resolution, float]:
    """Separate several fitting instruments by which region the sentence itself names."""
    sentence = _squash(rel.quote)
    named = [c for c in cands if c.enacting_body and c.enacting_body in sentence]
    if len(named) == 1:
        return named[0].doc_id, "inferred", 0.6
    return None, "ambiguous", 0.0


# ---------------------------------------------------------------------------
# status: derived from edges, never stored on a document
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class DocStatus:
    doc_id: str
    status: str = "none_known"
    """`revoked`, `partially_revoked`, `amended` or `none_known`. Never "in force"."""

    revoked_by: list[str] = field(default_factory=list)
    amended_by: list[str] = field(default_factory=list)


def derive_status(
    edges: Iterable[tuple[str, str, str | None, bool]],
    doc_ids: Iterable[str],
    copies: Callable[[str], Sequence[str]] | None = None,
) -> dict[str, DocStatus]:
    """Status per document from `(source_doc, relation, target_doc, partial)` edges.

    Only resolved edges count: an edge that points at nothing marks nothing. `copies`
    expands a target to every document that is the same instrument, so a revoked
    regulation is revoked in each copy of it and not only the one a citation resolved to.
    """
    out = {d: DocStatus(d) for d in doc_ids}
    expand = copies or (lambda d: (d,))
    full_revokers: set[str] = set()
    part_revokers: set[str] = set()
    for source, relation, resolved, partial in edges:
        if resolved is None:
            continue
        mine = set(expand(source))
        for target in expand(resolved):
            if target not in out or target in mine:
                continue
            if relation in SUPERSEDING:
                out[target].revoked_by.append(source)
                (part_revokers if partial else full_revokers).add(target)
            elif relation == "amends":
                out[target].amended_by.append(source)
    for doc_id, status in out.items():
        if doc_id in full_revokers:
            status.status = "revoked"
        elif doc_id in part_revokers:
            status.status = "partially_revoked"
        elif status.amended_by:
            status.status = "amended"
    return out
