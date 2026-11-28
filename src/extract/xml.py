"""XML into canonical documents, as a declared dialect says (`registry/dialects/`).

The profile names the dialects it reads (`extract.xml.dialects`); the root element picks
one. Every element name lives in the dialect: which element is a unit, where its heading
is printed, what is furniture, what wraps a note, what sets the page, what is italic. This
file holds only what is the same for every XML: walking the tree, reading a table grid,
normalising text. `SOURCE_US_CFR.md` §3 is the first corpus read this way.

Every structural element becomes a heading block whose `attrs` state what the markup says
-- `unit`, `number`, `name`, `marker` -- so the chunker never reads a number back out of
prose. Body elements become paragraphs, tables canonical tables, and the page an element
sets is carried on every block after it.

! The structure is the markup's, not a guess. Heading levels are the profile's
(`structure.units`), and a unit is closed when its element closes: a section after a
subject group in the same subpart is not inside that group, and a level-only heading stack
would have said it was.

! Strict. A file that is not well-formed XML fails the document; the notebook this
replaces parsed with `recover=True`, which drops what it cannot read and reports success.
Elements skipped are skipped by name and counted; an element the dialect does not name is
still read for its text and named in `extraction.warnings`, so a schema change shows on
the first run instead of as missing text.
"""

from __future__ import annotations

import functools
import hashlib
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from canon import BlockType, CanonicalDoc, Cell, Extraction, Source, Table
from extract.base import BlockBuilder, ExtractionEmpty, ExtractionFailed, Probe, registry
from sources import guess_mime, sha256_file
from spec.xml_dialect import LabelRule, XmlDialect, XmlOptions

if TYPE_CHECKING:
    from spec.models import Profile

#: 1.1: a document assembled from linked files is identified by every file it read
#: (`_assembly_digest`), ✗ by the file that links them alone.
VERSION = "1.1"

_SPACE = re.compile(r"\s+")
#: Where `split_at` cuts a leaf: a character no text holds.
_CUT = "\x00"
#: A part of a composed source (`+{NO.DOC/YEAR}/{NO.DOC/NO.CURRENT}`): no braces inside.
_COMPOSED = re.compile(r"\{([^{}]+)\}")
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_DATE = re.compile(r"([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2}),\s+(\d{4})")


def clean(text: str) -> str:
    """One line of text: NFKC (so the thin space after `§` is a space), whitespace collapsed."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("­", "").replace("​", "").replace("﻿", "")
    return _SPACE.sub(" ", text).strip()


def iso_date(text: str | None) -> str | None:
    """`Revised as of January 1, 2026` -> `2026-01-01`; None when it says no date."""
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text.strip()):
        return text.strip()
    # Formex states `ISO="19931025"`: compact, and a date only if it is one (✗ `99999999`).
    if compact := re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text.strip()):
        year, month, day = (int(g) for g in compact.groups())
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
        return None
    match = _DATE.search(text)
    if not match or match.group(1).lower() not in _MONTHS:
        return None
    month = _MONTHS.index(match.group(1).lower()) + 1
    return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(2)):02d}"


@functools.lru_cache(maxsize=256)
def _re(pattern: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(pattern, flags)


@dataclass(frozen=True)
class _Compiled:
    """A dialect with its lists as sets and the profile's levels, built once per profile."""

    dialect: XmlDialect
    levels: dict[str, int]
    skip: frozenset[str]
    graphics: frozenset[str]
    leaves: frozenset[str]
    inline: frozenset[str]
    quoted: frozenset[str]
    reserved_text: frozenset[str]
    superscript: frozenset[str]
    breaks: frozenset[str]
    drop: frozenset[str]
    split_at: frozenset[str]

    @classmethod
    def build(cls, dialect: XmlDialect, levels: dict[str, int]) -> _Compiled:
        return cls(
            dialect=dialect,
            levels=levels,
            skip=frozenset(dialect.skip),
            graphics=frozenset(dialect.graphics),
            leaves=frozenset(dialect.leaves),
            inline=frozenset(dialect.inline),
            quoted=frozenset(dialect.quoted),
            reserved_text=frozenset(dialect.reserved_text),
            superscript=frozenset(dialect.text.superscript),
            breaks=frozenset(dialect.text.breaks),
            drop=frozenset(dialect.text.drop) | frozenset(dialect.graphics),
            split_at=frozenset(dialect.text.split_at),
        )

    def unit_of(self, element: ET.Element) -> str | None:
        units = self.dialect.units
        if element.tag in units.tags:
            return units.tags[element.tag]
        rule = units.by_attr
        if rule and _re(rule.tag).match(element.tag):
            return rule.unit_for(element)
        return None

    def within(self, unit: str, open_units: list[tuple[str, str]]) -> str:
        """The unit `unit` is read as, given the units open around it (`units.inside`)."""
        rules = self.dialect.units.inside
        for outer, _ in reversed(open_units):
            renamed = rules.get(outer, {}).get(unit)
            if renamed:
                return renamed
        return unit

    def flat_of(self, element: ET.Element) -> str | None:
        """The unit a flat heading opens (`<Heading level="2">`), or None."""
        rule = self.dialect.units.flat
        if rule is None or not _re(rule.tag).match(element.tag):
            return None
        return rule.unit_for(element)

    def is_page(self, element: ET.Element) -> bool:
        return self.dialect.page is not None and element.tag == self.dialect.page.tag


@dataclass
class _Walk:
    """State of one document's walk."""

    d: _Compiled
    builder: BlockBuilder = field(default_factory=BlockBuilder)
    tables: list[Table] = field(default_factory=list)
    page: int | None = None
    skipped: Counter[str] = field(default_factory=Counter)
    unknown: Counter[str] = field(default_factory=Counter)
    reserved: Counter[str] = field(default_factory=Counter)
    units: Counter[str] = field(default_factory=Counter)
    graphics: int = 0
    pending_label: str = ""
    #: The units open around the walk, outermost first: (unit, number).
    open_units: list[tuple[str, str]] = field(default_factory=list)
    #: The quotes being read, innermost last (`_body`).
    quotes: list[_Quote] = field(default_factory=list)
    quotes_ended: int = 0
    #: Unit element -> the page in force where it starts, in document order, for `@page`.
    #: ! ✗ `page`: a break printed inside a paragraph never reaches it (`PRTPAGE` in `P`,
    #: 534 of 785 in three Federal Register issues), so it lags the page actually in force.
    page_at: dict[int, int | None] = field(default_factory=dict)
    #: The document's root, which a unit attribute `/path` is read from.
    root: ET.Element | None = None


@dataclass
class _Quote:
    """A quoted container being read, and what the first unit it quotes says about it."""

    #: The units open where the quote begins: the note's own section, subpart, part.
    enclosing: dict[str, str]
    unit: str | None = None
    level: int | None = None
    number: str = ""
    #: The quote opens with a unit that encloses it: the note revises its own part.
    own: bool = False
    ended: bool = False


class XmlExtractor:
    """XML into canonical documents by a declared dialect. Standard library only."""

    id = "xml"
    version = VERSION

    def supports(self, path: Path, probe: Probe) -> bool:
        return path.suffix.lower() == ".xml" or probe.mime in ("text/xml", "application/xml")

    def extract(
        self, path: Path, *, title: str, url: str | None = None, profile: Profile | None = None
    ) -> CanonicalDoc:
        if profile is None:
            raise ExtractionFailed(f"{path.name}: xml reads by a profile's dialect, none given")
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            raise ExtractionFailed(f"{path.name}: not well-formed XML: {exc}") from exc

        compiled = _dialect_for(profile, root.tag)
        if compiled is None:
            raise ExtractionFailed(
                f"{path.name}: no dialect of {profile.ref} reads a <{root.tag}> document"
            )
        dialect = compiled.dialect
        members: list[Path] = []
        assembled, lost = (
            _assemble(root, path, dialect, members) if dialect.links else (None, 0)
        )
        state = _Walk(compiled, root=root)
        self._document(root, state)

        if not any(b.type is not BlockType.HEADING for b in state.builder.blocks):
            # ! Empty by its own statement only when every link it kept was found: a text whose
            # articles are all out of force, at any depth (a section still in force can hold
            # only articles that are not). One with a kept link whose file is missing lost
            # files, and that is a failure.
            if (
                assembled is not None
                and dialect.links
                and dialect.links.empty_when_nothing_kept
                and lost == 0
            ):
                raise ExtractionEmpty(f"{path.name}: nothing in force ({dialect.ref} dialect)")
            holders = [e for p in dialect.document.empty_when_bare for e in root.iterfind(p)]
            pictures = set(dialect.graphics) | set(dialect.tables.elements)
            if holders and all(
                not "".join(e.itertext()).strip()
                and not any(d.tag in pictures for d in e.iter())
                for e in holders
            ):
                raise ExtractionEmpty(f"{path.name}: states no text ({dialect.ref} dialect)")
            raise ExtractionFailed(f"{path.name}: produced no text ({dialect.ref} dialect)")

        warnings = [
            f"unknown element {tag} x{n}: read for its text" for tag, n in state.unknown.items()
        ]
        if state.graphics:
            warnings.append(f"{state.graphics} graphics not extracted (no text)")
        if state.quotes_ended:
            warnings.append(
                f"{state.quotes_ended} quoted containers ended early: a unit that cannot be "
                "part of the quote was read as structure"
            )
        digest = sha256_file(path)
        size = path.stat().st_size
        if members:
            assert dialect.links is not None
            digest, size = _assembly_digest(path, digest, members, dialect.links.base)
        return CanonicalDoc(
            doc_id=f"sha256:{digest}",
            source=Source(
                path=path.as_posix(),
                url=url,
                title=title,
                mime=guess_mime(path),
                bytes=size,
                sha256=digest,
            ),
            extraction=Extraction(
                extractor=self.id,
                extractor_version=self.version,
                params={
                    "dialect": dialect.ref,
                    "units": dict(state.units),
                    "reserved": dict(state.reserved),
                    "skipped": dict(state.skipped),
                    "tables": len(state.tables),
                    "quotes_ended": state.quotes_ended,
                    **({"links": assembled} if assembled is not None else {}),
                },
                extracted_at=datetime.now(UTC),
                warnings=warnings,
            ),
            blocks=state.builder.blocks,
            tables=state.tables,
        )

    # -- the document ------------------------------------------------------------------
    def _document(self, root: ET.Element, state: _Walk) -> None:
        d = state.d
        rule = d.dialect.document
        if d.dialect.page is not None and any(
            "@page" in sources
            for attrs in d.dialect.units.attrs.values()
            for sources in attrs.values()
        ):
            state.page_at = _pages_in_force(root, d)
        date = next((v for s in rule.date if (v := iso_date(_source(root, s, d)))), None)
        dated = {"date": date, "year": date[:4]} if date else {}
        for path in rule.drop:
            parent_path, _, tag = path.rpartition("/")
            for parent in root.iterfind(parent_path or "."):
                for child in parent.findall(tag):
                    parent.remove(child)
                    state.skipped[f"dropped: {path}"] += 1
        for path in rule.latest:
            parent_path, _, tag = path.rpartition("/")
            for parent in root.iterfind(parent_path or "."):
                for child in parent.findall(tag)[:-1]:
                    parent.remove(child)
                    state.skipped[f"superseded: {path}"] += 1

        if rule.top is None:
            top = [root] if d.unit_of(root) else [c for c in root if d.unit_of(c)]
            for child in root if d.unit_of(root) is None else ():
                if d.unit_of(child) is None and child.tag not in rule.ignore:
                    state.skipped[f"outside units: {child.tag}"] += 1
            for element in top:
                self._unit(element, state, dated=dated)
            return

        top_element = root.find(rule.top)
        if top_element is None:
            raise ExtractionFailed(f"{root.tag} without a {rule.top} element")
        trailing: list[ET.Element] = []
        after = False
        for child in root:
            if child is top_element:
                after = True
            elif after and rule.trailing and (d.unit_of(child) or d.is_page(child)):
                trailing.append(child)
            elif child.tag not in rule.ignore:
                state.skipped[f"outside {rule.top}: {child.tag}"] += 1
        unit = d.unit_of(top_element) or ""
        heading, consumed = self._heading(top_element, unit, state)
        if not heading and rule.compose:
            parts = (clean(_source(root, s, d) or "") for s in rule.compose)
            heading = "—".join(p for p in parts if p)
        extra: dict[str, str] = {}
        if rule.number:
            number = _label(heading, d.dialect.label_rule(unit), "")[2]
            if not number:
                text = _source(root, rule.number, d) or ""
                number = _label(text, d.dialect.label_rule(unit), "")[2]
            extra["number"] = number
        self._unit(
            top_element,
            state,
            dated=dated,
            found=(heading, consumed),
            extra=extra,
            trailing=trailing,
        )

    # -- units ---------------------------------------------------------------------------
    def _unit(
        self,
        element: ET.Element,
        state: _Walk,
        *,
        dated: dict[str, str] | None = None,
        found: tuple[str, set[int]] | None = None,
        extra: dict[str, str] | None = None,
        trailing: list[ET.Element] | None = None,
    ) -> None:
        d = state.d
        unit = d.unit_of(element)
        if unit is None:
            self._body(element, state, kind="")
            return
        unit = d.within(unit, state.open_units)
        heading, consumed = found or self._heading(element, unit, state)
        state.units[unit] += 1
        if self._is_reserved(element, unit, heading, d):
            state.reserved[unit] += 1
            return
        level = d.levels[unit]
        children = list(element)
        first = min(consumed) if consumed else 0
        for index, child in enumerate(children[:first]):
            # A page break printed before the heading is the heading's page.
            if d.is_page(child):
                self._child(child, state, dated)
                consumed.add(index)
        page = state.page_at.get(id(element))
        attrs = self._attrs(unit, heading, element, d, page=page, root=state.root) | (
            extra or {}
        )
        state.open_units.append((unit, attrs["number"]))
        if heading:
            self._flush_label(state)
            if dated and unit == d.dialect.document.dated_unit:
                attrs |= dated
            state.builder.add(
                BlockType.HEADING, heading, level=level, page=state.page, attrs=attrs
            )
        rest = [c for i, c in enumerate(children) if i not in consumed]
        flats: list[int] = []
        for child in [*rest, *(trailing or ())]:
            if not self._flat(child, state, flats):
                self._child(child, state, dated)
        self._close_flats(state, flats, 0)
        state.open_units.pop()
        if heading:
            state.builder.close(level)

    def _child(self, child: ET.Element, state: _Walk, dated: dict[str, str] | None) -> None:
        d = state.d
        if d.is_page(child):
            assert d.dialect.page is not None
            page = child.get(d.dialect.page.attr, "")
            state.page = int(page) if page.isdigit() and int(page) > 0 else state.page
        elif d.unit_of(child):
            self._unit(child, state, dated=dated)
        else:
            self._body(child, state, kind="")

    def _heading(self, element: ET.Element, unit: str, state: _Walk) -> tuple[str, set[int]]:
        """The unit's heading text, and the indexes of the children that held it."""
        rule = state.d.dialect.heading(unit)
        children = list(element)
        paths = [rule.path] if isinstance(rule.path, str) else rule.path or []
        for path in paths:
            found = element.find(path)
            text = self._text(found, state.d) if found is not None else ""
            if text:
                if rule.take and found is not None:
                    _take(element, path, found)
                return text, set()
        if rule.join:
            used: list[int] = []
            parts: list[str] = []
            for slot in rule.join:
                # ! A path (`metadaten/enbez`) is read in place: it is not a child, and the
                # element holding it is skipped furniture.
                inner = next(
                    (f for tag in slot if "/" in tag and (f := element.find(tag)) is not None),
                    None,
                )
                if inner is not None:
                    parts.append(self._text(inner, state.d))
                    if rule.take:
                        _take(element, next(t for t in slot if element.find(t) is inner), inner)
                    continue
                index = next(
                    (i for tag in slot for i, c in enumerate(children) if c.tag == tag), None
                )
                if index is not None and index not in used:
                    used.append(index)
                    parts.append(self._text(children[index], state.d))
            if any(parts):
                return clean(" ".join(p for p in parts if p)), set(used)
        for i, child in enumerate(children):
            if child.tag in rule.first:
                return self._text(child, state.d), {i}
            if rule.anywhere or child.tag in rule.skip:
                continue
            break
        return "", set()

    def _attrs(
        self,
        unit: str,
        heading: str,
        element: ET.Element,
        d: _Compiled,
        *,
        page: int | None = None,
        root: ET.Element | None = None,
    ) -> dict[str, str]:
        """What the markup states about a unit: its number, its name, its marker.

        A source `/path` is read from the document's root: front matter printed beside the
        text and not in any unit (Spain's BOE states a law's rank and number in
        `data/metadatos`, beside `data/texto`).

        The source `@page` is the page the unit opens on: its own first page break when that
        comes before any of its text, else the page in force where it starts (`page`). A
        Federal Register notice that opens mid-page prints no break of its own, and the first
        break inside it is the *next* page.
        """
        units = d.dialect.units

        def value_of(source: str) -> str | None:
            if source == "@page":
                return _opening_page(element, d, page)
            if source.startswith("/"):
                return _source(root, source[1:], d) if root is not None else None
            return _source(element, source, d)

        stated = {
            key: value
            for key, sources in units.attrs.get(unit, {}).items()
            if (value := next((v for s in sources if (v := value_of(s))), None))
        }
        own = stated.pop("number", None)
        if own is None and units.number_attr:
            own = element.get(units.number_attr)
        stated_marker = stated.pop("marker", None)
        marker, name, number = _label(heading, d.dialect.label_rule(unit), own, stated_marker)
        return {
            **{k: clean(v) for k, v in stated.items()},
            "unit": unit,
            "number": clean(number),
            "name": name,
            "marker": marker,
        }

    # -- flat headings -------------------------------------------------------------------
    def _flat(self, child: ET.Element, state: _Walk, flats: list[int]) -> bool:
        """Open the unit a flat heading starts, closing those it ends. False: not one."""
        d = state.d
        unit = d.flat_of(child)
        if unit is None:
            return False
        level = d.levels[unit]
        self._close_flats(state, flats, level)
        heading, consumed = self._heading(child, unit, state)
        state.units[unit] += 1
        if heading:
            attrs = self._attrs(unit, heading, child, d, root=state.root)
            self._flush_label(state)
            state.builder.add(
                BlockType.HEADING, heading, level=level, page=state.page, attrs=attrs
            )
            state.open_units.append((unit, attrs["number"]))
            flats.append(level)
        for index, inner in enumerate(child):
            if index not in consumed:
                self._body(inner, state, kind="")
        return True

    def _close_flats(self, state: _Walk, flats: list[int], level: int) -> None:
        """Close the flat units open at `level` or deeper (0: all of them)."""
        while flats and flats[-1] >= level:
            state.builder.close(flats.pop())
            state.open_units.pop()

    def _is_reserved(self, element: ET.Element, unit: str, heading: str, d: _Compiled) -> bool:
        """A unit that holds nothing but its own `[Reserved]`."""
        rule = d.dialect.reserved
        if unit in rule.never:
            return False
        if any(c.tag not in rule.ignore for c in element):
            return False
        if _re(rule.pattern, re.IGNORECASE).search(heading):
            return True
        return rule.tag is not None and element.find(rule.tag) is not None

    # -- body ----------------------------------------------------------------------------
    def _body(
        self, element: ET.Element, state: _Walk, *, kind: str, quoted: bool = False
    ) -> None:
        """Body text: a leaf is one paragraph, a container is read child by child.

        `quoted`: inside a quoted container, where a unit is text and not structure.
        """
        d = state.d
        tag = element.tag
        if d.is_page(element):
            self._child(element, state, None)
            return
        if tag in d.skip:
            state.skipped[tag] += 1
            return
        if tag in d.graphics:
            state.graphics += 1
            return
        if tag in d.dialect.tables.elements:
            self._table(element, state, kind=kind)
            return
        if tag in d.reserved_text:
            state.reserved["text"] += 1
            return
        unit = d.unit_of(element)
        quote = state.quotes[-1] if quoted and state.quotes else None
        if unit and quote is not None and not quote.ended and d.dialect.quotes_run_on:
            self._check_quote(quote, unit, element, state)
        if unit and (quote is None or quote.ended):
            self._unit(element, state)
            return
        if unit:
            heading, consumed = self._heading(element, unit, state)
            self._paragraph(heading, state, kind=kind, tag=tag)
            for index, child in enumerate(element):
                if index not in consumed:
                    self._body(child, state, kind=kind, quoted=True)
            return

        if (flat := d.flat_of(element)) is not None:
            # Only a quote reaches here: a flat heading in quoted text is its text.
            heading, consumed = self._heading(element, flat, state)
            self._paragraph(heading, state, kind=kind, tag=tag)
            for index, child in enumerate(element):
                if index not in consumed:
                    self._body(child, state, kind=kind, quoted=quoted)
            return

        containers = d.dialect.containers
        if tag in d.dialect.items and not quoted:
            for child in element:
                self._paragraph(
                    self._text(child, d), state, kind=containers.get(tag) or kind, tag=child.tag
                )
            return
        block_children = [c for c in element if c.tag not in d.inline]
        if not (tag in d.leaves or tag in containers or tag in d.inline):
            state.unknown[tag] += 1
        if tag in d.leaves or not block_children:
            # ! A container holding its text directly (`<SECAUTH>16 U.S.C. 1531</SECAUTH>`)
            # is still that container: its kind is kept, not the enclosing one.
            own = kind if quoted else (containers.get(tag) or kind)
            emphasis = self._emphasis(element, d)
            for piece in self._pieces(element, d):
                spans = [e for e in emphasis if e in piece]
                self._paragraph(piece, state, kind=own, tag=tag, emphasis=spans)
            return
        opens = tag in d.quoted and not quoted
        # Inside a quote everything is the quote's: an `AUTH` quoted in revised text is not
        # the part's authority.
        inner = kind if quoted else (containers.get(tag, "") or kind)
        quoted = quoted or opens
        if opens:
            state.quotes.append(_Quote(enclosing=dict(state.open_units)))
        if element.text and element.text.strip():
            self._paragraph(clean(element.text), state, kind=inner, tag=tag)
        flats: list[int] = []
        for child in element:
            if child.tag in d.inline:
                self._paragraph(self._text(child, d), state, kind=inner, tag=child.tag)
            elif quoted or not self._flat(child, state, flats):
                self._body(child, state, kind=inner, quoted=quoted)
            if child.tail and child.tail.strip():
                self._paragraph(clean(child.tail), state, kind=inner, tag=tag)
        self._close_flats(state, flats, 0)
        if opens:
            state.quotes.pop()

    def _check_quote(self, quote: _Quote, unit: str, element: ET.Element, state: _Walk) -> None:
        """End a quote at a unit that cannot be part of it.

        ! GPO sometimes leaves a `REVTXT` open to the end of the volume. In 40 CFR vol. 33
        (2024) one quoting subpart B of part 702 runs on over 2,539 sections in 13 parts;
        in 34 CFR vol. 3 (2025) one quoting part 656 holds parts 657 to 679. Read as
        quoted, all of them vanished into one note. A quote opens with the unit it revises,
        so two things end it: a unit shallower than that one (a PART after a quoted
        SUBPART), and, when the quote revises a unit that encloses its note, another unit
        of that type with another number (PART 657 in the quote of part 656). From there
        the markup is read as structure, and the count is reported.
        """
        d = state.d
        level = d.levels[unit]
        heading, _ = self._heading(element, unit, state)
        number = self._attrs(unit, heading, element, d)["number"]
        if quote.level is None:
            quote.unit, quote.level, quote.number = unit, level, number
            quote.own = bool(number) and quote.enclosing.get(unit) == number
            return
        shallower = level < quote.level
        another = quote.own and unit == quote.unit and number != quote.number
        if shallower or another:
            quote.ended = True
            state.quotes_ended += 1

    def _paragraph(
        self, text: str, state: _Walk, *, kind: str, tag: str, emphasis: list[str] | None = None
    ) -> None:
        if not text:
            return
        label = state.d.dialect.label
        # `Authority:` and `Source:` are labels of the paragraph that follows them.
        if tag in label.tags and text.endswith(label.suffix) and len(text) <= label.max_chars:
            state.pending_label = f"{state.pending_label} {text}".strip()
            return
        if state.pending_label:
            text, state.pending_label = f"{state.pending_label} {text}", ""
        attrs: dict[str, object] = {"tag": tag}
        if kind:
            attrs["kind"] = kind
        if emphasis:
            attrs["emphasis"] = emphasis
        # ! A footnote is a paragraph with a kind, not a FOOTNOTE block: chunk bodies read
        # body types only, and a CFR footnote often carries the condition a rule applies on.
        state.builder.add(BlockType.PARAGRAPH, text, page=state.page, attrs=attrs)

    def _flush_label(self, state: _Walk) -> None:
        """A label with nothing after it before the next heading is a paragraph of its own."""
        if state.pending_label:
            label, state.pending_label = state.pending_label, ""
            tag = state.d.dialect.label.tags[0] if state.d.dialect.label.tags else ""
            state.builder.add(BlockType.PARAGRAPH, label, page=state.page, attrs={"tag": tag})

    def _pieces(self, element: ET.Element, d: _Compiled) -> list[str]:
        """The element's text, cut where a `split_at` element stands."""
        if not d.split_at or not any(n.tag in d.split_at for n in element.iter()):
            return [self._text(element, d)]
        pieces = (clean(t) for t in self._text(element, d, raw=True).split(_CUT))
        return [p for p in pieces if p]

    def _text(self, element: ET.Element, d: _Compiled, *, raw: bool = False) -> str:
        """The element's text with inline markup read in place; superscripts as `^n`."""
        parts: list[str] = []

        def walk(node: ET.Element) -> None:
            if raw and node is not element and node.tag in d.split_at:
                parts.append(_CUT)
            if node.tag in d.superscript:
                inner = "".join(node.itertext()).strip()
                if inner:
                    parts.append(f"^{inner}")
            elif node.tag not in d.drop:
                if node.text:
                    parts.append(node.text)
                for child in node:
                    walk(child)
                    if child.tag in d.breaks:
                        parts.append(" ")
            if node is not element and node.tail:
                parts.append(node.tail)

        walk(element)
        return "".join(parts) if raw else clean("".join(parts))

    def _emphasis(self, element: ET.Element, d: _Compiled) -> list[str]:
        """The italic spans of a paragraph, in order: where the CFR marks a defined term."""
        rule = d.dialect.emphasis
        spans: list[str] = []
        for node in element.iter():
            if node is element or node.tag not in rule.elements:
                continue
            codes = rule.elements[node.tag]
            if codes is not None and node.get(rule.attr) not in codes:
                continue
            if text := clean("".join(node.itertext())):
                spans.append(text)
        return spans

    # -- tables --------------------------------------------------------------------------
    def _table(self, element: ET.Element, state: _Walk, *, kind: str) -> None:
        d = state.d
        layout = d.dialect.tables.elements[element.tag]
        reader = {
            "gpo": _gpo_table,
            "html": _html_table,
            "cals": _cals_table,
            "formex": _formex_table,
        }[layout]
        rows, header_rows, caption = reader(element, lambda e: self._text(e, d))
        if not rows:
            return
        n_cols = max(len(r) for r in rows)
        cells = [
            Cell(r=r, c=c, text=text)
            for r, row in enumerate(rows)
            for c, text in enumerate(row)
            if text
        ]
        table_id = f"t{len(state.tables):04d}"
        state.tables.append(
            Table(
                id=table_id,
                page=state.page,
                caption=caption or None,
                n_rows=len(rows),
                n_cols=n_cols,
                header_rows=header_rows,
                cells=cells,
            )
        )
        attrs = {"tag": element.tag, **({"kind": kind} if kind else {})}
        # The caption lives on the table; a table reference carries no text of its own.
        state.builder.add(BlockType.TABLE_REF, "", page=state.page, ref=table_id, attrs=attrs)
        for note_tag in d.dialect.tables.notes:
            for note in element.iter(note_tag):
                self._paragraph(
                    self._text(note, d), state, kind=d.dialect.tables.note_kind, tag=note_tag
                )


def _assemble(
    root: ET.Element, path: Path, dialect: XmlDialect, members: list[Path]
) -> tuple[dict[str, int], int]:
    """Put every followed link's file inside its link element, in place, depth first.
    Each file read is appended to `members`, in the order it was read. Returns the counts
    and how many links that passed a `keep` filter had no file: content lost, ✗ a page the
    dump never holds (LEGI's `JORFTEXT` title page).

    ! A link whose file is missing keeps its own text and is counted, never guessed at: an
    article the dump does not hold is a gap that shows, not an empty article.
    """
    assert dialect.links is not None
    links = dialect.links
    base = (path.parent / links.base).resolve()
    rules = {rule.element: rule for rule in links.rules}
    counts = {"followed": 0, "dropped": 0, "missing": 0, "kept": 0}
    lost = 0
    pending = [root]
    while pending:
        node = pending.pop()
        for child in list(node):
            rule = rules.get(child.tag)
            if rule is None or (rule.parents and node.tag not in rule.parents):
                pending.append(child)
                continue
            if any(child.get(a, "") not in values for a, values in rule.keep.items()):
                node.remove(child)
                counts["dropped"] += 1
                continue
            if rule.keep:
                counts["kept"] += 1
            value = child.get(rule.attr, "")
            match = _re(rule.pattern).fullmatch(value) if rule.pattern else None
            if rule.pattern and match is None:
                counts["missing"] += 1
                lost += bool(rule.keep)
                continue
            groups = match.groupdict() if match else {}
            target = (base / rule.file.format(value=value, **groups).lstrip("/")).resolve()
            if not target.is_relative_to(base) or not target.is_file():
                counts["missing"] += 1
                lost += bool(rule.keep)
                continue
            if counts["followed"] >= links.max_files:
                limit = f"{links.max_files:,}"
                raise ExtractionFailed(f"{path.name}: more than {limit} linked files")
            try:
                linked = ET.parse(target).getroot()
            except ET.ParseError as exc:
                raise ExtractionFailed(f"{target.name}: not well-formed XML: {exc}") from exc
            child.text = None
            child.append(linked)
            members.append(target)
            counts["followed"] += 1
            pending.append(linked)
    return counts, lost


def _assembly_digest(path: Path, own: str, members: list[Path], base: str) -> tuple[str, int]:
    """The identity of a document stored as many files: the linking file's digest, then
    each file read, by its path inside the document's folder and its own digest.

    ! The linking file alone is not the document. LEGI amends an article in the article's
    own file; a text whose `struct` file did not change would keep its id, and an update
    would see nothing to replace. Every file read is in the digest, so any change to any of
    them is a new document, and `bundle delta` replaces it. The path is in it too: the same
    bytes linked from another folder are another text.
    """
    folder = (path.parent / base).resolve()
    digest = hashlib.sha256(f"assembly:1 {own}\n".encode())
    size = path.stat().st_size
    for member in members:
        name = member.relative_to(folder).as_posix()
        digest.update(f"{name} {sha256_file(member)}\n".encode())
        size += member.stat().st_size
    return digest.hexdigest(), size


def _dialect_for(profile: Profile, root_tag: str) -> _Compiled | None:
    for compiled in _compiled_dialects(profile):
        if _re(compiled.dialect.document.detect.root).match(root_tag):
            return compiled
    return None


#: Compiled dialects by the profile's structure hash: a profile is unhashable, and the
#: hash changes exactly when a rule the walk reads does.
_COMPILED: dict[str, tuple[_Compiled, ...]] = {}


def _compiled_dialects(profile: Profile) -> tuple[_Compiled, ...]:
    key = profile.structure_hash
    if key not in _COMPILED:
        options = XmlOptions.model_validate(profile.extract_options("xml"))
        levels = {u.name: u.level for u in profile.spec.structure.units}
        _COMPILED[key] = tuple(_Compiled.build(d, levels) for d in options.dialects)
    return _COMPILED[key]


def _pages_in_force(root: ET.Element, d: _Compiled) -> dict[int, int | None]:
    """Every unit element -> the page in force where it starts: the last page break before
    it in document order."""
    assert d.dialect.page is not None
    attr = d.dialect.page.attr
    pages: dict[int, int | None] = {}
    current: int | None = None
    for node in root.iter():
        if d.is_page(node):
            found = node.get(attr, "")
            current = int(found) if found.isdigit() and int(found) > 0 else current
        elif d.unit_of(node):
            pages[id(node)] = current
    return pages


def _opening_page(element: ET.Element, d: _Compiled, in_force: int | None) -> str | None:
    """The page `element` opens on: a page break before any of its text, else `in_force`."""
    for node in element.iter():
        if node is not element and d.is_page(node):
            assert d.dialect.page is not None
            found = node.get(d.dialect.page.attr, "")
            return found if found.isdigit() and int(found) > 0 else None
        if node.text and node.text.strip():
            break
    return str(in_force) if in_force else None


def _source(root: ET.Element, source: str, d: _Compiled) -> str | None:
    """`path` -> the element's text; `path@attr` -> the attribute; `=text` -> the text
    itself, the last resort of a list (`['.@regulation-type', '=Act']`). `path~pattern`
    takes the text only where `pattern` is found in it: Germany's outline label `Art 97`,
    never the bare `-` a law prints for an outline with no label; a `(?P<value>...)` group
    in the pattern takes that group only."""
    if source.startswith("="):
        return source[1:] or None
    if source.startswith("+"):
        # `+{a}/{b}/{c}`: the sources inside braces, each read as above, put into the text
        # around them; any of them missing and the whole is missing, ✗ a partial number.
        # Formex states `93/626/EEC` as its parts: `NO.DOC/YEAR`, `NO.CURRENT`, `COM`.
        values: list[str] = []

        def part(match: re.Match[str]) -> str:
            value = _source(root, match.group(1), d) or ""
            values.append(value)
            return value

        composed = _COMPOSED.sub(part, source[1:])
        return composed if values and all(values) else None
    source, _, pattern = source.partition("~")
    # `NO.DOC[@FORMAT="YN"]/YEAR` names an attribute in a predicate, ✗ the one to read.
    path, at, attr = source.rpartition("@")
    if not at or "]" in attr:
        path, attr = source, ""
    found = root.find(path)
    if found is None:
        return None
    value = found.get(attr) if attr else XmlExtractor()._text(found, d)
    if value and pattern:
        match = re.search(pattern, value)
        if not match:
            return None
        # A `value` group takes the part the publisher prints inside other text: the
        # Federal Register's `[FR Doc. 2024-04579 Filed 2-29-24; 11:15 am]` -> 2024-04579.
        if "value" in match.re.groupindex:
            value = match["value"]
    return value or None


def _label(
    heading: str, rule: LabelRule, own_number: str | None, own_marker: str | None = None
) -> tuple[str, str, str]:
    """(marker, name, number) of a heading, as the dialect's label rule reads it.

    `own_marker`: the marker the markup states apart from the heading (`<enbez>§ 1</enbez>`
    before `<titel>`): when the heading opens with it, the rest is the name, and `pattern`
    is not tried. ! No pattern can tell `Anhang 1 zu Nummer 3` from a name that follows it.
    """
    stated = clean(own_marker or "")
    if stated and heading.startswith(stated):
        marker, name = stated, heading[len(stated) :].strip()
    else:
        match = _re(rule.pattern, re.DOTALL).match(heading)
        marker, name = (
            ((match["marker"] or "").strip(), (match["name"] or "").strip())
            if match
            else (heading.strip(), "")
        )
    if rule.name_strip:
        name = _re(rule.name_strip, re.IGNORECASE).sub("", name)
    number = own_number or ""
    if not number and rule.number:
        found = _re(rule.number).search(marker)
        number = found["number"] if found else ""
    return marker, name, number


def _gpo_table(element: ET.Element, text: _TextOf) -> tuple[list[list[str]], int, str]:
    caption = " ".join(text(t) for t in element.findall("TTITLE")).strip()
    columns = int(element.get("COLS", "0") or 0)
    rows: list[list[str]] = []
    head = element.find("BOXHD")
    header_rows = 0
    if head is not None:
        heads = [text(c) for c in head.findall("CHED")]
        top = [text(c) for c in head.findall("CHED") if c.get("H", "1") == "1"]
        header = top if len(top) == columns or not columns else heads[:columns]
        if any(header):
            rows.append(header)
            header_rows = 1
    for entry in element.findall("ROW"):
        rows.append([text(e) for e in entry.findall("ENT")])
    return rows, header_rows, caption


def _cals_table(element: ET.Element, text: _TextOf) -> tuple[list[list[str]], int, str]:
    """An OASIS (CALS) table: `tgroup` columns named by `colspec`, spans by `namest` /
    `nameend` and `morerows`. Every `tgroup` is read into one grid, header rows first."""
    title = element.find("title")
    caption = text(title) if title is not None else ""
    grid: dict[tuple[int, int], str] = {}
    header_rows = 0
    r = 0
    for group in element.iter("tgroup"):
        names = {
            spec.get("colname", ""): int(spec.get("colnum") or index + 1) - 1
            for index, spec in enumerate(group.findall("colspec"))
        }
        for part in ("thead", "tbody", "tfoot"):
            for section in group.findall(part):
                for row in section.findall("row"):
                    c = 0
                    for cell in row.findall("entry"):
                        start = names.get(cell.get("namest") or cell.get("colname") or "")
                        c = start if start is not None and start >= c else c
                        while (r, c) in grid:
                            c += 1
                        end = names.get(cell.get("nameend") or "", c)
                        span_c = max(1, end - c + 1)
                        span_r = 1 + int(cell.get("morerows") or 0)
                        value = text(cell)
                        for dr in range(span_r):
                            for dc in range(span_c):
                                grid[(r + dr, c + dc)] = value if (dr, dc) == (0, 0) else ""
                        c += span_c
                    if part == "thead" and r == header_rows:
                        header_rows += 1
                    r += 1
    if not grid:
        return [], 0, caption
    n_rows = max(r for r, _ in grid) + 1
    n_cols = max(c for _, c in grid) + 1
    rows = [[grid.get((r, c), "") for c in range(n_cols)] for r in range(n_rows)]
    return rows, header_rows, caption


def _html_table(element: ET.Element, text: _TextOf) -> tuple[list[list[str]], int, str]:
    # ! Tag names in either case: GPO's eCFR prints `TR`, LEGI's HTML prints `tr`.
    caption_el = next((e for e in element if e.tag.upper() == "CAPTION"), None)
    caption = text(caption_el) if caption_el is not None else ""
    grid: dict[tuple[int, int], str] = {}
    header_rows = 0
    rows_of = [e for e in element.iter() if e.tag.upper() == "TR"]
    for r, tr in enumerate(rows_of):
        c = 0
        for cell in tr:
            if cell.tag.upper() not in ("TD", "TH"):
                continue
            while (r, c) in grid:
                c += 1
            span_r = int(cell.get("rowspan", cell.get("ROWSPAN", "1")) or 1)
            span_c = int(cell.get("colspan", cell.get("COLSPAN", "1")) or 1)
            value = text(cell)
            for dr in range(span_r):
                for dc in range(span_c):
                    grid[(r + dr, c + dc)] = value if (dr, dc) == (0, 0) else ""
            c += span_c
        if (
            all(cell.tag.upper() == "TH" for cell in tr if cell.tag.upper() in ("TD", "TH"))
            and r == header_rows
        ):
            header_rows += 1
    if not grid:
        return [], 0, caption
    n_rows = max(r for r, _ in grid) + 1
    n_cols = max(c for _, c in grid) + 1
    rows = [[grid.get((r, c), "") for c in range(n_cols)] for r in range(n_rows)]
    return rows, header_rows, caption


def _formex_table(element: ET.Element, text: _TextOf) -> tuple[list[list[str]], int, str]:
    """A Formex table (`TBL`): `CORPUS` holds `ROW`s, or `BLK`s of them under a `TI.BLK`
    title read as a row of its own. A `CELL` states its column (`COL`, from 1) and its spans
    (`COLSPAN`, `ROWSPAN`); a `ROW TYPE="HEADER"` opening the table is a header row. ! A table
    nested in a cell is that cell's text, never rows of this one."""
    title = element.find("TITLE")
    caption = text(title) if title is not None else ""
    grid: dict[tuple[int, int], str] = {}
    header_rows = 0
    r = 0

    def rows_of(holder: ET.Element) -> list[ET.Element]:
        out: list[ET.Element] = []
        for child in holder:
            if child.tag in ("ROW", "TI.BLK"):
                out.append(child)
            elif child.tag == "BLK":
                out.extend(rows_of(child))
        return out

    for corpus in element.findall("CORPUS"):
        for row in rows_of(corpus):
            if row.tag == "TI.BLK":
                while (r, 0) in grid:
                    r += 1
                grid[(r, 0)] = text(row)
                r += 1
                continue
            c = 0
            for cell in row.findall("CELL"):
                stated = cell.get("COL", "")
                start = int(stated) - 1 if stated.isdigit() and int(stated) > 0 else c
                c = start if start >= c else c
                while (r, c) in grid:
                    c += 1
                span_c = max(1, int(cell.get("COLSPAN") or 1))
                span_r = max(1, int(cell.get("ROWSPAN") or 1))
                value = text(cell)
                for dr in range(span_r):
                    for dc in range(span_c):
                        grid[(r + dr, c + dc)] = value if (dr, dc) == (0, 0) else ""
                c += span_c
            if row.get("TYPE") == "HEADER" and r == header_rows:
                header_rows += 1
            r += 1
    if not grid:
        return [], 0, caption
    n_rows = max(r for r, _ in grid) + 1
    n_cols = max(c for _, c in grid) + 1
    rows = [[grid.get((r, c), "") for c in range(n_cols)] for r in range(n_rows)]
    return rows, header_rows, caption


if TYPE_CHECKING:
    from collections.abc import Callable

    _TextOf = Callable[[ET.Element], str]


registry.register(XmlExtractor())


def _take(element: ET.Element, path: str, found: ET.Element) -> None:
    """Remove `found`, which `path` reached from `element`, so the body does not read it."""
    parent_path = path.rpartition("/")[0]
    parent = element.find(parent_path) if parent_path else element
    if parent is not None and found in list(parent):
        parent.remove(found)
