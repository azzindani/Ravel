"""US Code of Federal Regulations XML: the annual CFR and the eCFR (`SOURCE_US_CFR.md` §3).

Two dialects of one hierarchy, told apart by the root element:

    CFRDOC   TITLE SUBTITLE CHAPTER SUBCHAP PART SUBPART SUBJGRP SECTION APPENDIX
    ECFR     DIV1..DIV9 with TYPE="TITLE" ... TYPE="APPENDIX"

Both become one canonical shape. Every structural element is a heading block whose
`attrs` state what the markup says -- `unit`, `number`, `name`, `marker` -- so the
chunker never has to read a number back out of prose. Body elements become paragraphs,
tables become canonical tables, and the page set by `PRTPAGE` is carried on every block
after it, so a chunk of the official edition cites its printed page.

! The structure is the markup's, not a guess. That is why this extractor sets heading
levels itself and closes a unit when its element closes: a section that follows a
subject group in the same subpart is not inside that group, and a level-only heading
stack would have said it was.

! Strict. A file that is not well-formed XML fails the document; the notebook this
replaces parsed with `recover=True`, which drops what it cannot read and reports success.
Elements skipped are skipped by name and counted; an element this file does not know is
still read for its text and named in `extraction.warnings`, so a schema change shows on
the first run instead of as missing text.
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from canon import BlockType, CanonicalDoc, Cell, Extraction, Source, Table
from extract.base import BlockBuilder, ExtractionFailed, Probe, registry
from sources import guess_mime, sha256_file

VERSION = "1.0"

#: The hierarchy, outermost first, with the level each unit's heading is given. A profile
#: for this extractor must declare the same names at the same levels (`structure.from_markup`).
LEVELS: dict[str, int] = {
    "title": 1,
    "subtitle": 2,
    "chapter": 3,
    "subchapter": 4,
    "part": 5,
    "subpart": 6,
    "subjgrp": 7,
    "section": 8,
    "appendix": 8,
}

CFR_UNITS = {
    "TITLE": "title",
    "SUBTITLE": "subtitle",
    "CHAPTER": "chapter",
    "SUBCHAP": "subchapter",
    "PART": "part",
    "SUBPART": "subpart",
    "SUBJGRP": "subjgrp",
    "SECTION": "section",
    "APPENDIX": "appendix",
}
ECFR_TYPES = {
    "TITLE": "title",
    "SUBTITLE": "subtitle",
    "CHAPTER": "chapter",
    "SUBCHAP": "subchapter",
    "PART": "part",
    "SUBPART": "subpart",
    "SUBJGRP": "subjgrp",
    "SECTION": "section",
    "APPENDIX": "appendix",
}

#: Navigation and print furniture: repeated elsewhere, or about the page and not the law.
SKIP = frozenset(
    {
        "TOC", "CONTENTS", "LRH", "RRH", "EAR", "SECHD", "PTHD", "PGHD", "CHAPTI",
        "TOCHD", "FMTR", "BMTR", "AMDDATE", "VOLUME", "CFRTOC", "CFRTITLE", "STARS",
        "img", "br",
    }
)  # fmt: skip
#: Pictures. Nothing to read; counted, so the gap is visible.
GRAPHICS = frozenset({"GPH", "GID", "MATH", "MID"})
#: Blocks of text with no structure inside.
LEAVES = frozenset(
    {
        "P", "FP", "FP-1", "FP-2", "FP-DASH", "FL-2", "PSPACE", "CITA", "TNOTE", "TDESC",
        "HD", "HD1", "HD2", "HD3", "HED", "HEAD", "SIG", "FRDOC", "DATE", "NAME",
        "POSITION",
    }
)  # fmt: skip
#: Wrappers that group blocks, and the kind each gives what it holds.
CONTAINERS = {
    "EXTRACT": "extract",
    "NOTE": "note",
    "EDNOTE": "editorial_note",
    "AUTH": "authority",
    "SOURCE": "source",
    "SECAUTH": "authority",
    "EXAMPLE": "example",
    "EFFDNOT": "effective_date_note",
    "APPRO": "approval",
    "FTNT": "footnote",
    "DIV": "",
    "CROSSREF": "cross_reference",
}
TABLES = frozenset({"GPOTABLE", "TABLE"})
#: Inline markup: read as part of the text around it.
INLINE = frozenset(
    {"E", "I", "B", "SU", "FR", "AC", "FTREF", "XREF", "LI", "SUB", "SUP", "a", "span", "em"}
)

_DASH = re.compile(r"\s*[—–]\s*")
_SPACE = re.compile(r"\s+")
_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}
_DATE = re.compile(r"([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2}),\s+(\d{4})")
_RESERVED = re.compile(r"\[\s*reserved\s*\]", re.IGNORECASE)
_CONTINUED = re.compile(r"\s*\(continued\)\s*$", re.IGNORECASE)


def clean(text: str) -> str:
    """One line of text: NFKC (so the thin space after `§` is a space), whitespace collapsed."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("­", "").replace("​", "").replace("﻿", "")
    return _SPACE.sub(" ", text).strip()


def text_of(element: ET.Element) -> str:
    """The element's text with inline markup read in place; `SU` as `^n`."""
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        if node.tag == "SU":
            inner = "".join(node.itertext()).strip()
            if inner:
                parts.append(f"^{inner}")
        elif node.tag in GRAPHICS or node.tag in ("img",):
            pass
        else:
            if node.text:
                parts.append(node.text)
            for child in node:
                walk(child)
                if child.tag in ("br", "LI"):
                    parts.append(" ")
        if node is not element and node.tail:
            parts.append(node.tail)

    walk(element)
    return clean("".join(parts))


def iso_date(text: str | None) -> str | None:
    """`Revised as of January 1, 2026` -> `2026-01-01`; None when it says no date."""
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text.strip()):
        return text.strip()
    match = _DATE.search(text)
    if not match or match.group(1).lower() not in _MONTHS:
        return None
    month = _MONTHS[match.group(1).lower()]
    return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(2)):02d}"


def split_heading(text: str) -> tuple[str, str]:
    """`PART 170—REPORTING ...` -> (`PART 170`, `REPORTING ...`)."""
    parts = _DASH.split(text, maxsplit=1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else (text.strip(), "")


@dataclass
class _Walk:
    """State of one document's walk."""

    builder: BlockBuilder = field(default_factory=BlockBuilder)
    tables: list[Table] = field(default_factory=list)
    page: int | None = None
    skipped: Counter[str] = field(default_factory=Counter)
    unknown: Counter[str] = field(default_factory=Counter)
    reserved: Counter[str] = field(default_factory=Counter)
    units: Counter[str] = field(default_factory=Counter)
    graphics: int = 0
    pending_label: str = ""


class CfrXmlExtractor:
    """CFR and eCFR XML into canonical documents. Standard library only."""

    id = "cfr_xml"
    version = VERSION

    def supports(self, path: Path, probe: Probe) -> bool:
        if path.suffix.lower() != ".xml":
            return False
        try:
            with open(path, "rb") as handle:
                head = handle.read(4096)
        except OSError:
            return False
        return b"<CFRDOC" in head or b"<ECFR" in head or b'TYPE="PART"' in head

    def extract(self, path: Path, *, title: str, url: str | None = None) -> CanonicalDoc:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            raise ExtractionFailed(f"{path.name}: not well-formed XML: {exc}") from exc

        state = _Walk()
        dialect = "ecfr" if root.tag == "ECFR" or root.tag.startswith("DIV") else "cfr"
        if dialect == "cfr":
            self._cfr_document(root, state)
        else:
            self._ecfr_document(root, state)

        if not any(b.type is not BlockType.HEADING for b in state.builder.blocks):
            raise ExtractionFailed(f"{path.name}: produced no text ({dialect} dialect)")

        warnings = [
            f"unknown element {tag} x{n}: read for its text" for tag, n in state.unknown.items()
        ]
        if state.graphics:
            warnings.append(f"{state.graphics} graphics not extracted (no text)")
        digest = sha256_file(path)
        return CanonicalDoc(
            doc_id=f"sha256:{digest}",
            source=Source(
                path=path.as_posix(),
                url=url,
                title=title,
                mime=guess_mime(path),
                bytes=path.stat().st_size,
                sha256=digest,
            ),
            extraction=Extraction(
                extractor=self.id,
                extractor_version=self.version,
                params={
                    "dialect": dialect,
                    "units": dict(state.units),
                    "reserved": dict(state.reserved),
                    "skipped": dict(state.skipped),
                    "tables": len(state.tables),
                },
                extracted_at=datetime.now(UTC),
                warnings=warnings,
            ),
            blocks=state.builder.blocks,
            tables=state.tables,
        )

    # -- the CFR (govinfo annual edition) -----------------------------------------
    def _cfr_document(self, root: ET.Element, state: _Walk) -> None:
        front = root.find("FMTR")
        revised = iso_date(_first_text(front, ".//REVISED")) if front is not None else None
        number = _first_text(front, ".//TITLENUM") if front is not None else None
        title = root.find("TITLE")
        if title is None:
            raise ExtractionFailed("CFRDOC without a TITLE element")
        # ! Title 3 prints its chapter after an empty TITLE, as the root's sibling: read as
        # the title's own. Its presidential documents (`PROC`, `EXECORD`) sit there too, and
        # are counted, not read (`SOURCE_US_CFR.md` §3).
        trailing: list[ET.Element] = []
        after = False
        for child in root:
            if child is title:
                after = True
            elif after and (child.tag in CFR_UNITS or child.tag == "PRTPAGE"):
                trailing.append(child)
            elif child.tag not in ("FMTR", "BMTR", "AMDDATE"):
                state.skipped[f"outside TITLE: {child.tag}"] += 1
        # Title 3 prints no title heading: composed from the title page's own two lines,
        # `Title 3` and `The President`, never from anything the file does not say.
        subject = _first_text(front, "TITLEPG/SUBJECT") if front is not None else None
        heading = _first_text(title, "CFRTITLE/TITLEHD/HD") or (
            "—".join(p for p in (clean(number or ""), clean(subject or "")) if p)
        )
        marker, name = split_heading(heading)
        attrs = {
            "number": _last_token(marker) or _last_token(number or ""),
            "name": name,
            "marker": marker,
        }
        if revised:
            attrs |= {"date": revised, "year": revised[:4]}
        self._cfr_unit(title, "title", state, heading=heading, extra=attrs, trailing=trailing)

    def _cfr_unit(
        self,
        element: ET.Element,
        unit: str,
        state: _Walk,
        *,
        heading: str | None = None,
        extra: dict[str, str] | None = None,
        trailing: list[ET.Element] | None = None,
    ) -> None:
        consumed: set[int] = set()
        if heading is None:
            heading, consumed = self._cfr_heading(element, unit)
        state.units[unit] += 1
        if _is_reserved(element, unit, heading):
            state.reserved[unit] += 1
            return
        level = LEVELS[unit]
        children = list(element)
        first = min(consumed) if consumed else 0
        for index, child in enumerate(children[:first]):
            # A page break printed before the heading is the heading's page.
            if child.tag == "PRTPAGE":
                self._cfr_child(child, state)
                consumed.add(index)
        if heading:
            self._flush_label(state)
            attrs = self._attrs(unit, heading, element) | (extra or {})
            state.builder.add(
                BlockType.HEADING, heading, level=level, page=state.page, attrs=attrs
            )
        for index, child in enumerate(children):
            if index in consumed:
                continue
            self._cfr_child(child, state)
        for child in trailing or ():
            self._cfr_child(child, state)
        if heading:
            state.builder.close(level)

    def _cfr_heading(self, element: ET.Element, unit: str) -> tuple[str, set[int]]:
        """The unit's heading text, and the indexes of the children that held it."""
        children = list(element)
        if unit == "section":
            number = next((i for i, c in enumerate(children) if c.tag == "SECTNO"), None)
            subject = next((i for i, c in enumerate(children) if c.tag == "SUBJECT"), None)
            reserved = next((i for i, c in enumerate(children) if c.tag == "RESERVED"), None)
            used = {i for i in (number, subject) if i is not None}
            parts = [text_of(children[i]) for i in sorted(used)]
            if subject is None and reserved is not None:
                parts.append(text_of(children[reserved]))
                used.add(reserved)
            return clean(" ".join(p for p in parts if p)), used
        if unit == "chapter":
            # The chapter's heading is printed in its table of contents.
            found = _first_text(element, "TOC/TOCHD/HD")
            if found:
                return found, set()
        for i, child in enumerate(children):
            if child.tag == "HD":
                return text_of(child), {i}
            if child.tag in ("PRTPAGE", "EAR", "LRH", "RRH"):
                continue
            if (
                unit in ("part", "subpart", "appendix", "subchapter")
                and child.tag == "RESERVED"
            ):
                return text_of(child), {i}
            break
        return "", set()

    def _cfr_child(self, child: ET.Element, state: _Walk) -> None:
        tag = child.tag
        if tag == "PRTPAGE":
            page = child.get("P", "")
            state.page = int(page) if page.isdigit() and int(page) > 0 else state.page
            return
        if tag in CFR_UNITS:
            self._cfr_unit(child, CFR_UNITS[tag], state)
            return
        self._body(child, state, kind="")

    # -- the eCFR ------------------------------------------------------------------
    def _ecfr_document(self, root: ET.Element, state: _Walk) -> None:
        volume = root.find("VOLUME")
        date = iso_date(volume.get("AMDDATE")) if volume is not None else None
        date = date or iso_date(_first_text(root, "AMDDATE"))
        top = (
            [root]
            if root.tag.startswith("DIV")
            else [c for c in root if c.tag.startswith("DIV")]
        )
        for child in root if not root.tag.startswith("DIV") else []:
            if not child.tag.startswith("DIV") and child.tag not in ("AMDDATE", "VOLUME"):
                state.skipped[f"outside DIV: {child.tag}"] += 1
        extra = {"date": date, "year": date[:4]} if date else {}
        for div in top:
            self._ecfr_div(div, state, extra=extra)

    def _ecfr_div(self, element: ET.Element, state: _Walk, extra: dict[str, str]) -> None:
        unit = ECFR_TYPES.get(element.get("TYPE", ""))
        if unit is None:
            self._body(element, state, kind="")
            return
        head = element.find("HEAD")
        heading = text_of(head) if head is not None else ""
        state.units[unit] += 1
        if _is_reserved(element, unit, heading):
            state.reserved[unit] += 1
            return
        level = LEVELS[unit]
        if heading:
            self._flush_label(state)
            attrs = self._attrs(unit, heading, element) | (extra if unit == "title" else {})
            state.builder.add(
                BlockType.HEADING, heading, level=level, page=state.page, attrs=attrs
            )
        for child in element:
            if child is head:
                continue
            if child.tag.startswith("DIV") and child.get("TYPE") in ECFR_TYPES:
                self._ecfr_div(child, state, extra)
            else:
                self._body(child, state, kind="")
        if heading:
            state.builder.close(level)

    # -- shared ----------------------------------------------------------------------
    def _attrs(self, unit: str, heading: str, element: ET.Element) -> dict[str, str]:
        """What the markup states about a unit: its number, its name, its marker."""
        if unit == "section":
            match = re.match(r"^(§§?\s*[\w.\-–—]+)\s*(.*)$", heading)
            marker, name = (match.group(1), match.group(2)) if match else (heading, "")
            number = element.get("N") or re.sub(r"^§§?\s*", "", marker)
            return {
                "unit": unit,
                "number": clean(number),
                "name": name.strip(),
                "marker": marker,
            }
        marker, name = split_heading(heading)
        # A chapter that runs over volumes repeats its heading as `... (CONTINUED)`. The
        # heading keeps it, as printed; the name is the agency's.
        name = _CONTINUED.sub("", name)
        number = element.get("N") or _unit_number(unit, marker)
        return {"unit": unit, "number": clean(number or ""), "name": name, "marker": marker}

    def _body(self, element: ET.Element, state: _Walk, *, kind: str) -> None:
        """Body text: a leaf is one paragraph, a container is read child by child."""
        tag = element.tag
        if tag == "PRTPAGE":
            self._cfr_child(element, state)
            return
        if tag in SKIP:
            state.skipped[tag] += 1
            return
        if tag in GRAPHICS:
            state.graphics += 1
            return
        if tag in TABLES:
            self._table(element, state, kind=kind)
            return
        if tag == "RESERVED":
            state.reserved["text"] += 1
            return
        if tag.startswith("DIV") and element.get("TYPE") in ECFR_TYPES:
            self._ecfr_div(element, state, {})
            return
        if tag in CFR_UNITS:
            self._cfr_unit(element, CFR_UNITS[tag], state)
            return

        block_children = [c for c in element if c.tag not in INLINE]
        known = tag in LEAVES or tag in CONTAINERS or tag in INLINE
        if not known:
            state.unknown[tag] += 1
        if tag in LEAVES or not block_children:
            self._paragraph(text_of(element), state, kind=kind, tag=tag)
            return
        inner = CONTAINERS.get(tag, "") or kind
        if element.text and element.text.strip():
            self._paragraph(clean(element.text), state, kind=inner, tag=tag)
        for child in element:
            if child.tag in INLINE:
                self._paragraph(text_of(child), state, kind=inner, tag=child.tag)
            else:
                self._body(child, state, kind=inner)
            if child.tail and child.tail.strip():
                self._paragraph(clean(child.tail), state, kind=inner, tag=tag)

    def _paragraph(self, text: str, state: _Walk, *, kind: str, tag: str) -> None:
        if not text:
            return
        # `Authority:` and `Source:` are labels of the paragraph that follows them.
        if tag in ("HD", "HED") and text.endswith(":") and len(text) <= 40:
            state.pending_label = f"{state.pending_label} {text}".strip()
            return
        if state.pending_label:
            text, state.pending_label = f"{state.pending_label} {text}", ""
        attrs: dict[str, str] = {"tag": tag}
        if kind:
            attrs["kind"] = kind
        # ! A footnote is a paragraph with a kind, not a FOOTNOTE block: chunk bodies read
        # body types only, and a CFR footnote often carries the condition a rule applies on.
        state.builder.add(BlockType.PARAGRAPH, text, page=state.page, attrs=attrs)

    def _flush_label(self, state: _Walk) -> None:
        """A label with nothing after it before the next heading is a paragraph of its own."""
        if state.pending_label:
            label, state.pending_label = state.pending_label, ""
            state.builder.add(BlockType.PARAGRAPH, label, page=state.page, attrs={"tag": "HD"})

    def _table(self, element: ET.Element, state: _Walk, *, kind: str) -> None:
        rows, header_rows, caption = (
            _gpo_table(element) if element.tag == "GPOTABLE" else _html_table(element)
        )
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
        for note in element.iter("TNOTE"):
            self._paragraph(text_of(note), state, kind="table_note", tag="TNOTE")


def _gpo_table(element: ET.Element) -> tuple[list[list[str]], int, str]:
    caption = " ".join(text_of(t) for t in element.findall("TTITLE")).strip()
    columns = int(element.get("COLS", "0") or 0)
    rows: list[list[str]] = []
    head = element.find("BOXHD")
    header_rows = 0
    if head is not None:
        heads = [text_of(c) for c in head.findall("CHED")]
        top = [text_of(c) for c in head.findall("CHED") if c.get("H", "1") == "1"]
        header = top if len(top) == columns or not columns else heads[:columns]
        if any(header):
            rows.append(header)
            header_rows = 1
    for entry in element.findall("ROW"):
        rows.append([text_of(e) for e in entry.findall("ENT")])
    return rows, header_rows, caption


def _html_table(element: ET.Element) -> tuple[list[list[str]], int, str]:
    caption_el = element.find("CAPTION")
    caption = text_of(caption_el) if caption_el is not None else ""
    grid: dict[tuple[int, int], str] = {}
    header_rows = 0
    for r, tr in enumerate(element.iter("TR")):
        c = 0
        for cell in tr:
            if cell.tag not in ("TD", "TH"):
                continue
            while (r, c) in grid:
                c += 1
            span_r = int(cell.get("rowspan", cell.get("ROWSPAN", "1")) or 1)
            span_c = int(cell.get("colspan", cell.get("COLSPAN", "1")) or 1)
            text = text_of(cell)
            for dr in range(span_r):
                for dc in range(span_c):
                    grid[(r + dr, c + dc)] = text if (dr, dc) == (0, 0) else ""
            c += span_c
        if (
            all(cell.tag == "TH" for cell in tr if cell.tag in ("TD", "TH"))
            and r == header_rows
        ):
            header_rows += 1
    if not grid:
        return [], 0, caption
    n_rows = max(r for r, _ in grid) + 1
    n_cols = max(c for _, c in grid) + 1
    rows = [[grid.get((r, c), "") for c in range(n_cols)] for r in range(n_rows)]
    return rows, header_rows, caption


def _first_text(element: ET.Element | None, path: str) -> str | None:
    if element is None:
        return None
    found = element.find(path)
    text = text_of(found) if found is not None else ""
    return text or None


def _last_token(text: str) -> str:
    tokens = text.split()
    return tokens[-1] if tokens else ""


def _unit_number(unit: str, marker: str) -> str:
    """`PART 170` -> `170`; `Appendix A to Part 170` -> `A`; `Title 2` -> `2`."""
    tokens = marker.split()
    if unit == "appendix" and len(tokens) >= 2:
        return tokens[1]
    return tokens[-1] if len(tokens) >= 2 else ""


def _is_reserved(element: ET.Element, unit: str, heading: str) -> bool:
    """A unit that holds nothing but its own `[Reserved]`."""
    if unit in ("title", "subtitle", "chapter"):
        return False
    content = [
        c
        for c in element
        if c.tag not in ("PRTPAGE", "EAR", "HD", "HEAD", "SECTNO", "SUBJECT", "RESERVED")
    ]
    if content:
        return False
    return bool(_RESERVED.search(heading)) or element.find("RESERVED") is not None


registry.register(CfrXmlExtractor())
