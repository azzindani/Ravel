"""An XML dialect: what a publisher's elements mean, declared (`registry/dialects/`).

The `xml` extractor knows how to walk a tree, read a table grid and normalise text. It
knows no element name. Which element is a unit, where its heading is printed, which
elements are print furniture, which wrap a note, which set a page, which mark italics --
all of it is read from a dialect file, so a schema change at the publisher is a YAML diff
reviewed like any other registry change, not a code release (`CLAUDE.md` §12).

A profile names the dialects it reads (`extract.xml.dialects: [gpo_cfr@1.0, ...]`). The
loader replaces each name with the file's contents before the profile is validated, so
`structure_hash` covers every rule here and a corrected dialect re-extracts.

! Each rule says what the markup states. A heading is found where the publisher prints it
or the unit has none; no rule guesses a heading out of body text.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _regex(value: str | None) -> str | None:
    if value is not None:
        re.compile(value)
    return value


class Detect(_Base):
    """Which files are this dialect: the root element's tag, as a regex."""

    root: str

    _root = field_validator("root")(_regex)


class ByAttribute(_Base):
    """Units named by an attribute (`<DIV5 TYPE="PART">`) instead of by their tag.

    `path` reads a descendant's text instead of an attribute: Germany's outline headings are
    `<norm>`s whose depth is the length of `metadaten/gliederungseinheit/gliederungskennzahl`
    (`010` > `010010`). With `match: regex` the `values` keys are patterns, tried in order and
    matched in full (`'^\\d{6}$': heading2`).
    """

    tag: str = Field(description="regex every such element's tag matches, e.g. '^DIV\\d+$'")
    attr: str = ""
    path: str | None = None
    match: Literal["exact", "regex"] = "exact"
    values: dict[str, str] = Field(description="attribute value -> unit name")
    # ! Left out of the dump while empty, so declaring the rule changes no hash of a dialect
    # that does not use it (the dump is what extraction is keyed by).
    unless: list[str] = Field(
        default_factory=list,
        exclude_if=lambda v: not v,
        description="paths: an element where any states text is not this unit. Germany's "
        "`<norm>` that states its number (`enbez` `§ 1`) is a provision, even when it also "
        "names the outline it is in (`gliederungseinheit` `Art 97`)",
    )

    _tag = field_validator("tag")(_regex)

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        if bool(self.attr) == bool(self.path):
            raise ValueError("a unit rule reads either `attr` or `path`, exactly one")
        if self.match == "regex":
            for key in self.values:
                re.compile(key)
        return self

    def unit_for(self, element: ET.Element) -> str | None:
        """The unit this element is, by its attribute or its descendant's text."""
        for path in self.unless:
            found = element.find(path)
            if found is not None and "".join(found.itertext()).strip():
                return None
        if self.path:
            found = element.find(self.path)
            value = "".join(found.itertext()).strip() if found is not None else ""
        else:
            value = element.get(self.attr, "")
        if not value:
            return None
        if self.match == "exact":
            return self.values.get(value)
        return next((u for k, u in self.values.items() if re.fullmatch(k, value)), None)


class Units(_Base):
    tags: dict[str, str] = Field(default_factory=dict, description="element tag -> unit name")
    by_attr: ByAttribute | None = None
    #: Headings printed as siblings of what they head (`<Heading level="2">` before the
    #: sections of a Division): each opens its unit, which runs to the next heading of
    #: the same or a shallower unit, or to the end of the element holding it.
    flat: ByAttribute | None = None
    #: The attribute an element states its own number in (`<DIV8 N="170.105">`). Read
    #: before the number pattern of the unit's heading.
    number_attr: str | None = None
    #: Unit -> attribute -> sources, relative to the unit's element and tried in order:
    #: `path` (an element's text), `path@attr`, `.@attr` (its own attribute), `/path` (from
    #: the document's root: front matter beside the text), `=text` (a
    #: constant, last in a list). `number`, when stated, is the unit's number instead of
    #: the one its heading prints. `marker`, when stated and the heading opens with it, is
    #: the heading's marker and the rest its name (`<enbez>Anhang 1 zu Nummer 3</enbez>`).
    attrs: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    #: Unit -> {unit -> the unit it is read as when inside it}. Japan's supplementary
    #: provisions (`<SupplProvision>`, 附則) hold `<Article>`s numbered from 第一条 again; read
    #: as `article` they would cite as the law's own first article. `{suppl: {article:
    #: suppl_article}}` makes them a unit of their own, with its own heading and citation.
    inside: dict[str, dict[str, str]] = Field(default_factory=dict)

    def names(self) -> set[str]:
        named = set(self.by_attr.values.values()) if self.by_attr else set()
        flat = set(self.flat.values.values()) if self.flat else set()
        renamed = {target for rules in self.inside.values() for target in rules.values()}
        return set(self.tags.values()) | named | flat | renamed


class HeadingRule(_Base):
    """Where a unit's heading is printed. Tried in order: `path`, `join`, `first`.

    - `path`: a descendant (`TOC/TOCHD/HD`), or several tried in order; read in place and
      not consumed, since the element holding it is furniture skipped anyway.
    - `join`: slots of direct children, each the first present of its tags, joined by a
      space in slot order (`[[SECTNO], [SUBJECT, RESERVED]]`; `[[Label], [MarginalNote]]`
      reads `2 Purpose of Act` where the note is printed first). Consumed. A slot entry with
      a `/` is a descendant path, read in place and not consumed
      (`[[metadaten/enbez], [metadaten/titel]]`). When no slot is present, `first` is
      tried.
    - `first`: the first direct child past the `skip` tags, if its tag is one of these;
      any other child ends the search. With `anywhere`, the first child with the tag,
      wherever it sits. Consumed.
    - `take`: a descendant a `path` or a `join` slot reads is removed once read, so it is
      not read again as body. Spain's BOE prints an article's heading as a paragraph of
      its text (`<version><p class="articulo">Artículo 1.</p><p>...`), ✗ in furniture.
    """

    path: str | list[str] | None = None
    join: list[list[str]] = Field(default_factory=list)
    first: list[str] = Field(default_factory=list)
    skip: list[str] = Field(default_factory=list)
    anywhere: bool = False
    # ! Left out of the dump while false: no other dialect's hash moves.
    take: bool = Field(default=False, exclude_if=lambda v: not v)


class LabelRule(_Base):
    """How a heading splits into the `marker`, `name` and `number` its block states.

    `pattern` must name groups `marker` and `name`; a heading it does not match is all
    marker. `number` is searched in the marker for a group `number`. `name_strip` is
    removed from the name (`(CONTINUED)` on a chapter that runs over volumes).
    """

    pattern: str = r"^(?P<marker>.*?)\s*[—–]\s*(?P<name>.*)$"
    number: str | None = None
    name_strip: str | None = None

    _patterns = field_validator("pattern", "number", "name_strip")(_regex)

    @model_validator(mode="after")
    def _groups(self) -> Self:
        if {"marker", "name"} - set(re.compile(self.pattern).groupindex):
            raise ValueError(f"label pattern needs groups `marker` and `name`: {self.pattern}")
        if self.number and "number" not in re.compile(self.number).groupindex:
            raise ValueError(f"label number pattern needs a group `number`: {self.number}")
        return self


class Reserved(_Base):
    """A unit holding nothing but its own `[Reserved]` is counted, not emitted."""

    pattern: str = r"\[\s*reserved\s*\]"
    tag: str | None = None
    #: Children that do not count as content when deciding a unit is empty.
    ignore: list[str] = Field(default_factory=list)
    #: Units never treated as reserved, however empty.
    never: list[str] = Field(default_factory=list)

    _pattern = field_validator("pattern")(_regex)


class Document(_Base):
    """The file's own front matter: the outermost unit and the edition's date.

    `top` is the root's child read as the outermost unit, or a path to it (`data/texto`:
    Spain's BOE prints its metadata beside the text); without it, the root itself if
    it is a unit, else every unit among its children. With `trailing`, units and pages
    printed after `top` as the root's children are read inside it (title 3 prints its
    chapter after an empty TITLE). Children in `ignore` are read nowhere and not counted;
    any other is counted as skipped.

    Sources are `path` (an element's text) or `path@attr`, relative to the root, tried in
    order. `compose` builds the outermost heading from two lines of front matter, joined by
    an em dash, when the file prints none -- never from anything the file does not say.
    `number` is where the outermost unit's number is read when its heading states none.
    The date goes on the heading of `dated_unit` as `date` and `year`.
    """

    detect: Detect
    top: str | None = None
    trailing: bool = False
    ignore: list[str] = Field(default_factory=list)
    compose: list[str] = Field(default_factory=list)
    number: str | None = None
    date: list[str] = Field(default_factory=list)
    dated_unit: str | None = None
    #: Paths, relative to the root, of the elements that hold a document's text. When
    #: every one of them is bare (no text anywhere inside, no picture, no table), the file
    #: states it holds none (Germany's `<textdaten/>` and `<text format="decorated"/>`: a
    #: notice whose text is the law it names): counted as empty, ✗ as a failure. A file with
    #: text there that still reads as nothing fails.
    empty_when_bare: list[str] = Field(default_factory=list)
    #: Paths, relative to the root, of elements removed once the date is read and before
    #: anything else is: front matter whose tag is also text elsewhere. Formex prints a
    #: descriptor's `DATE` beside the act it links, and the same `DATE` inline in the act's
    #: sentences (`of <DATE>20 May 1996</DATE>`); a tag rule would lose one or read the other.
    #: Paths may cross into linked files (`FMX/DOC.MAIN.PUB/REF.PHYS/ACT/TITLE`), since links
    #: are followed first. ! Left out of the dump while empty: no other dialect's hash moves.
    drop: list[str] = Field(default_factory=list, exclude_if=lambda v: not v)
    #: Paths, relative to the root, of an element repeated once per edition of the same
    #: text, oldest first: only the last is read, the earlier ones removed with `drop`.
    #: Spain's BOE keeps every wording an article has had (`bloque/version`), and only the
    #: newest is in force. ! Left out of the dump while empty.
    latest: list[str] = Field(default_factory=list, exclude_if=lambda v: not v)


class Text(_Base):
    """Inline reading: superscripts as `^n`, break elements as a space, dropped elements.

    `split_at`: elements that end a paragraph inside a leaf (`<br/>` in LEGI's
    `<CONTENU>I. - ...<br/><br/>1° ...</CONTENU>`): each stretch is its own paragraph, so a
    long article splits on its own lines instead of as one block.
    """

    superscript: list[str] = Field(default_factory=list)
    breaks: list[str] = Field(default_factory=list)
    drop: list[str] = Field(default_factory=list)
    split_at: list[str] = Field(default_factory=list)


class Link(_Base):
    """An element that stands for another file of the same document (`<LIEN_ART id=...>`).

    The file is read and its root put inside the link element, so the document is walked
    as one tree. `keep`: attribute -> the values a link must have to be followed (`etat:
    [VIGUEUR]`, the version in force); any other link is dropped with what it names.
    `file` is formatted with the attribute's value (`{value}`) and the named groups of
    `pattern`, and read relative to `Links.base`. The link's own text (a title repeated from
    the file) is dropped once the file is in place. `parents`: followed only where its parent
    is one of these (an article's own list of its other versions is not its content).
    """

    element: str
    attr: str
    file: str
    pattern: str | None = None
    keep: dict[str, list[str]] = Field(default_factory=dict)
    parents: list[str] = Field(default_factory=list)

    _pattern = field_validator("pattern")(_regex)


class Links(_Base):
    """A document stored as many files: `base` is the document's folder, relative to the
    folder of the file the corpus lists (`../..` from `texte/struct/<id>.xml`)."""

    base: str = "."
    rules: list[Link] = Field(min_length=1)
    #: Files read at most, so a link cycle or a runaway tree ends the document, loudly.
    max_files: int = Field(default=100_000, ge=1)
    #: A document with no text, every link it kept found, states it holds nothing (LEGI: no
    #: article in force, even under a section that is): counted as empty, ✗ as a failure.
    #: A kept link whose file is missing still fails it.
    empty_when_nothing_kept: bool = False


class Emphasis(_Base):
    """Italic elements, and the type codes that make them italic (`E T="03"`). An element
    listed with no codes is always italic (`I`)."""

    elements: dict[str, list[str] | None] = Field(default_factory=dict)
    attr: str = "T"


class Label(_Base):
    """A short heading ending in a colon labels the paragraph that follows it
    (`Authority:` then `5 U.S.C. 301`): the two are read as one paragraph."""

    tags: list[str] = Field(default_factory=list)
    suffix: str = ":"
    max_chars: int = 40


class Tables(_Base):
    """Table elements and the grid layout each is read with. Layouts are code: they are
    the format's mechanics (`gpo`: BOXHD/CHED/ROW/ENT, `html`: TR/TD/TH with spans,
    `cals`: OASIS tgroup/colspec/thead/tbody/row/entry with namest/nameend/morerows,
    `formex`: the Publications Office's TBL/CORPUS/ROW/CELL with COL/COLSPAN/ROWSPAN)."""

    elements: dict[str, Literal["gpo", "html", "cals", "formex"]] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    note_kind: str = "table_note"


class Page(_Base):
    """The element that sets the printed page, and its attribute."""

    tag: str
    attr: str


class XmlDialect(_Base):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(pattern=r"^\d+\.\d+$")
    title: str
    description: str = ""
    document: Document
    units: Units
    #: Heading rules by unit; `default` for any unit not listed.
    headings: dict[str, HeadingRule]
    #: Label rules by unit; `default` for any unit not listed.
    labels: dict[str, LabelRule] = Field(default_factory=lambda: {"default": LabelRule()})
    reserved: Reserved = Field(default_factory=Reserved)
    page: Page | None = None
    #: Navigation and print furniture: repeated elsewhere, or about the page, not the law.
    skip: list[str] = Field(default_factory=list)
    #: Pictures: nothing to read; counted so the gap shows.
    graphics: list[str] = Field(default_factory=list)
    #: Blocks of text with no structure inside: one paragraph each.
    leaves: list[str] = Field(default_factory=list)
    #: Wrappers, and the `kind` each gives the paragraphs it holds ("" for none).
    containers: dict[str, str] = Field(default_factory=dict)
    #: Inline markup, read as part of the text around it.
    inline: list[str] = Field(default_factory=list)
    #: Containers whose content is quoted text, such as an amendment's revised section
    #: (`REVTXT`). A unit inside is read as body -- its heading a paragraph -- and never as
    #: structure: it would otherwise open a second section of the same number inside a note.
    quoted: list[str] = Field(default_factory=list)
    #: The publisher may leave a quote open (GPO's `REVTXT` runs to the end of a volume): a
    #: quote then ends at a unit that cannot be part of it. A publisher whose quotes always
    #: close says false, and a quoted amendment holding a whole schedule stays quoted.
    quotes_run_on: bool = True
    #: A document made of many files, assembled by following its links.
    links: Links | None = None
    #: Containers whose children are items, each read as its own paragraph even when they
    #: are inline markup: `<EnablingAuthority>` lists one `<XRefExternal>` per Act, printed
    #: with nothing between them.
    items: list[str] = Field(default_factory=list)
    #: An element that stands for `[Reserved]` text in a body: counted, not read.
    reserved_text: list[str] = Field(default_factory=list)
    text: Text = Field(default_factory=Text)
    emphasis: Emphasis = Field(default_factory=Emphasis)
    label: Label = Field(default_factory=Label)
    tables: Tables = Field(default_factory=Tables)

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if "default" not in self.headings:
            raise ValueError(f"{self.ref}: headings needs a `default` rule")
        units = self.units.names()
        for section in ("headings", "labels"):
            stray = set(getattr(self, section)) - units - {"default"}
            if stray:
                raise ValueError(f"{self.ref}: {section} for unknown units {sorted(stray)}")
        if stray := set(self.units.attrs) - units:
            raise ValueError(f"{self.ref}: units.attrs for unknown units {sorted(stray)}")
        for name in (*self.reserved.never, self.document.dated_unit):
            if name is not None and name not in units:
                raise ValueError(f"{self.ref}: names unknown unit {name!r}")
        if self.document.top and self.document.top.rpartition("/")[2] not in self.units.tags:
            raise ValueError(f"{self.ref}: document.top {self.document.top} is not a unit tag")
        if stray := set(self.items) - set(self.containers):
            raise ValueError(f"{self.ref}: item lists must be containers: {sorted(stray)}")
        if stray := set(self.quoted) - set(self.containers):
            raise ValueError(f"{self.ref}: quoted elements must be containers: {sorted(stray)}")
        roles = {
            "skip": set(self.skip),
            "graphics": set(self.graphics),
            "leaves": set(self.leaves),
            "containers": set(self.containers),
            "inline": set(self.inline),
            "tables": set(self.tables.elements),
            "units": set(self.units.tags),
        }
        names = list(roles)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                both = roles[a] & roles[b]
                if both:
                    raise ValueError(f"{self.ref}: {sorted(both)} declared as both {a} and {b}")
        return self

    def heading(self, unit: str) -> HeadingRule:
        return self.headings.get(unit) or self.headings["default"]

    def label_rule(self, unit: str) -> LabelRule:
        return self.labels.get(unit) or self.labels.get("default") or LabelRule()


class XmlOptions(_Base):
    """`extract.xml` in a profile, after the loader has resolved each dialect by name."""

    dialects: list[XmlDialect] = Field(min_length=1)

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        refs = [d.ref for d in self.dialects]
        if len(set(refs)) != len(refs):
            raise ValueError(f"extract.xml lists a dialect twice: {refs}")
        return self
