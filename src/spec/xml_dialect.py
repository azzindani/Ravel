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
    """Units named by an attribute (`<DIV5 TYPE="PART">`) instead of by their tag."""

    tag: str = Field(description="regex every such element's tag matches, e.g. '^DIV\\d+$'")
    attr: str
    values: dict[str, str] = Field(description="attribute value -> unit name")

    _tag = field_validator("tag")(_regex)


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
    #: `path` (an element's text), `path@attr`, `.@attr` (its own attribute), `=text` (a
    #: constant, last in a list). `number`, when stated, is the unit's number instead of
    #: the one its heading prints.
    attrs: dict[str, dict[str, list[str]]] = Field(default_factory=dict)

    def names(self) -> set[str]:
        named = set(self.by_attr.values.values()) if self.by_attr else set()
        flat = set(self.flat.values.values()) if self.flat else set()
        return set(self.tags.values()) | named | flat


class HeadingRule(_Base):
    """Where a unit's heading is printed. Tried in order: `path`, `join`, `first`.

    - `path`: a descendant (`TOC/TOCHD/HD`), or several tried in order; read in place and
      not consumed, since the element holding it is furniture skipped anyway.
    - `join`: slots of direct children, each the first present of its tags, joined by a
      space in slot order (`[[SECTNO], [SUBJECT, RESERVED]]`; `[[Label], [MarginalNote]]`
      reads `2 Purpose of Act` where the note is printed first). Consumed. When no slot
      is present, `first` is tried.
    - `first`: the first direct child past the `skip` tags, if its tag is one of these;
      any other child ends the search. With `anywhere`, the first child with the tag,
      wherever it sits. Consumed.
    """

    path: str | list[str] | None = None
    join: list[list[str]] = Field(default_factory=list)
    first: list[str] = Field(default_factory=list)
    skip: list[str] = Field(default_factory=list)
    anywhere: bool = False


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

    `top` is the root's child read as the outermost unit; without it, the root itself if
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


class Text(_Base):
    """Inline reading: superscripts as `^n`, break elements as a space, dropped elements."""

    superscript: list[str] = Field(default_factory=list)
    breaks: list[str] = Field(default_factory=list)
    drop: list[str] = Field(default_factory=list)


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
    `cals`: OASIS tgroup/colspec/thead/tbody/row/entry with namest/nameend/morerows)."""

    elements: dict[str, Literal["gpo", "html", "cals"]] = Field(default_factory=dict)
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
        if self.document.top and self.document.top not in self.units.tags:
            raise ValueError(f"{self.ref}: document.top {self.document.top} is not a unit tag")
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
