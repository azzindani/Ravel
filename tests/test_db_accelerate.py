"""What `db prepare` builds beyond the load, against a stand-in connection.

No database needed: the point is which statements run, in which order, and what is
reported when the server refuses one. The SQL itself was run on the 1.7M corpus as Vera's
migration 0007 (`Vera/docs/project/ROADMAP.md` Stage 3).
"""

from __future__ import annotations

import pytest

# ! Skipped, ✗ failed, where the `db` extra is absent: the samples-routing job installs
# only `pymupdf` and collects every test module before selecting by marker.
psycopg = pytest.importorskip("psycopg")

from db.accelerate import (  # noqa: E402 · after the skip, on purpose
    BM25_PLAIN,
    BM25_WITH_REGION,
    REGION_FIELD,
    REGIONS,
    accelerate,
    refresh_regions,
)


class FakeConn:
    """Records every statement; refuses the ones named; answers the region-field probe."""

    def __init__(self, refuse: tuple[str, ...] = (), region_field: bool = True) -> None:
        self.statements: list[str] = []
        self.refuse = refuse
        self.region_field = region_field

    def execute(self, sql: str, params: tuple = ()):
        if any(r in sql for r in self.refuse):
            raise psycopg.errors.FeatureNotSupported(f"refused: {sql[:30]}\ndetail")
        self.statements.append(sql)
        row = (self.region_field,) if sql == REGION_FIELD else None
        return type("Cur", (), {"fetchone": lambda _self: row})()


def test_the_bm25_index_carries_the_region_field_and_the_list_follows() -> None:
    conn = FakeConn()
    report = accelerate("", conn=conn)
    assert BM25_WITH_REGION in conn.statements and BM25_PLAIN not in conn.statements
    assert REGIONS in conn.statements
    assert conn.statements.index(BM25_WITH_REGION) < conn.statements.index(REGIONS)
    assert report.applied == [
        "provenance guard",
        "text index (rum)",
        "bm25 index (pg_search)",
        "region scope",
    ]
    assert report.skipped == {}


def test_an_index_built_before_the_field_is_replaced_not_kept() -> None:
    # `IF NOT EXISTS` alone would keep a field-less index and leave the arm off.
    assert "DROP INDEX chunks_bm25" in BM25_WITH_REGION
    assert "NOT LIKE '%enacting_body%'" in BM25_WITH_REGION
    assert "(enacting_body::pdb.literal)" in BM25_WITH_REGION


def test_a_pg_search_without_the_literal_field_still_gets_the_plain_index() -> None:
    conn = FakeConn(refuse=("pdb.literal",), region_field=False)
    report = accelerate("", conn=conn)
    assert BM25_PLAIN in conn.statements
    assert "bm25 index (pg_search), fallback 1" in report.applied
    assert report.skipped["bm25 index (pg_search), preferred"].startswith("refused")
    # ! No list without the field: Vera reads the two as one capability.
    assert REGIONS not in conn.statements
    assert report.skipped["region scope"] == "chunks_bm25 has no enacting_body field"


def test_no_pg_search_at_all_is_reported_and_serving_continues() -> None:
    conn = FakeConn(refuse=("pg_search",), region_field=False)
    report = accelerate("", conn=conn)
    assert report.skipped["bm25 index (pg_search)"].startswith("refused")
    assert "bm25 index (pg_search)" not in report.applied
    assert "region scope" in report.skipped


@pytest.mark.parametrize("field", [True, False])
def test_refreshing_writes_the_list_only_where_the_index_can_serve_it(field: bool) -> None:
    conn = FakeConn(region_field=field)
    assert refresh_regions(conn) is field
    assert (REGIONS in conn.statements) is field


def test_the_list_leaves_out_artefacts_and_generic_words() -> None:
    assert "length(enacting_body) >= 4" in REGIONS
    for word in ("KABUPATEN", "KOTA", "REPUBLIK INDONESIA"):
        assert word in REGIONS
    assert "UNDANG|PEMERINTAH|PRESIDEN" in REGIONS
