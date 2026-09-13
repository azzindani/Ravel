"""The extractor plumbing: heading paths by construction, and the registry."""

from __future__ import annotations

import pytest

from canon import BlockType
from extract.base import BlockBuilder, Probe, Registry


def test_heading_path_is_materialized_on_every_block() -> None:
    """! The invariant that makes a chunker a linear scan instead of a tree walk
    (CANONICAL_FORMAT.md §3). Every extractor builds through BlockBuilder so this
    holds by construction, not by each extractor remembering to."""
    b = BlockBuilder()
    b.add(BlockType.HEADING, "UU 28/2007", level=1)
    b.add(BlockType.PARAGRAPH, "Menimbang bahwa...")
    b.add(BlockType.HEADING, "Bab II", level=2)
    b.add(BlockType.HEADING, "Pasal 9", level=3)
    para = b.add(BlockType.PARAGRAPH, "(1) Wajib Pajak...")

    assert para.heading_path == ["UU 28/2007", "Bab II", "Pasal 9"]
    assert b.blocks[1].heading_path == ["UU 28/2007"]


def test_closing_a_section_pops_its_children() -> None:
    b = BlockBuilder()
    b.add(BlockType.HEADING, "UU 28/2007", level=1)
    b.add(BlockType.HEADING, "Bab II", level=2)
    b.add(BlockType.HEADING, "Pasal 9", level=3)
    b.add(BlockType.HEADING, "Bab III", level=2)
    para = b.add(BlockType.PARAGRAPH, "text")

    assert para.heading_path == ["UU 28/2007", "Bab III"]


def test_reading_order_is_assigned_densely() -> None:
    b = BlockBuilder()
    for i in range(5):
        b.add(BlockType.PARAGRAPH, f"line {i}")
    assert [x.reading_order for x in b.blocks] == [0, 1, 2, 3, 4]
    assert [x.id for x in b.blocks] == [f"b{i:05d}" for i in range(5)]


def test_heading_without_a_level_defaults_to_one() -> None:
    b = BlockBuilder()
    assert b.add(BlockType.HEADING, "Judul").level == 1


# -- registry ---------------------------------------------------------------
class Fake:
    def __init__(self, id: str, version: str) -> None:
        self.id, self.version = id, version

    def supports(self, path: object, probe: Probe) -> bool:
        return True

    def extract(self, path: object, *, title: str, url: str | None = None) -> object:
        raise NotImplementedError


def test_registry_addresses_by_id_and_version() -> None:
    r = Registry()
    r.register(Fake("native", "1.0"))
    assert r.get("native@1.0").version == "1.0"
    assert r.get("native").version == "1.0"
    assert r.ids() == ["native"]


def test_registry_refuses_to_overwrite_a_version() -> None:
    """! A registry entry is immutable once used in a published bundle; a change is
    a new version (INTERFACES.md §6). Silently replacing one would make
    `config_hash` a lie."""
    r = Registry()
    r.register(Fake("native", "1.0"))
    with pytest.raises(ValueError, match="already registered"):
        r.register(Fake("native", "1.0"))


def test_registry_reports_what_it_knows_when_asked_for_something_else() -> None:
    r = Registry()
    r.register(Fake("native", "1.0"))
    with pytest.raises(KeyError, match="native"):
        r.get("smoldocling")


def test_weight_detected_headings_do_not_land_at_the_deepest_level() -> None:
    """! A bold, body-size heading used to get level 9 — the deepest the format
    allows — which inverts the hierarchy: every later heading appears to nest
    under it."""
    from extract.native import _heading_level

    sizes = [18.0, 14.0]  # two size-derived heading levels
    assert _heading_level(18.0, 11.0, sizes) == 1
    assert _heading_level(14.0, 11.0, sizes) == 2
    assert _heading_level(11.0, 11.0, sizes) == 3  # bold at body size, not 9


def test_same_level_headings_displace_rather_than_nest() -> None:
    """! The defect that certified 931 documents with a wrong heading_path.

    A profile assigns fixed levels with gaps on purpose — `id_regulation` numbers bab
    3 and pasal 6 — so a document opening at level 3 left the stack one deep, and the
    stack was indexed by depth. Real regulations came out reading
    `Menimbang › Mengingat › BAB I`: three siblings recorded as ancestors.
    """
    from canon import BlockType
    from extract.base import BlockBuilder

    builder = BlockBuilder()
    builder.add(BlockType.HEADING, "Menimbang", level=3)
    builder.add(BlockType.HEADING, "Mengingat", level=3)
    builder.add(BlockType.HEADING, "BAB I", level=3)
    builder.add(BlockType.HEADING, "Pasal 1", level=6)
    body = builder.add(BlockType.PARAGRAPH, "Ketentuan umum.")

    assert body.heading_path == ["BAB I", "Pasal 1"]


def test_a_heading_path_is_strictly_increasing_in_level() -> None:
    """The invariant, stated directly: an ancestor is always shallower."""
    from canon import BlockType
    from extract.base import BlockBuilder

    builder = BlockBuilder()
    headings = [(2, "Buku I"), (3, "Bab I"), (6, "Pasal 1"), (3, "Bab II"), (6, "Pasal 2")]
    for level, text in headings:
        builder.add(BlockType.HEADING, text, level=level)
    last = builder.add(BlockType.PARAGRAPH, "x")

    assert last.heading_path == ["Buku I", "Bab II", "Pasal 2"]
