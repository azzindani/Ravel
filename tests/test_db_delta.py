"""Building a delta from two bundles, and the plan that applies it. No database needed: the
equivalence of an applied delta with a rebuild is checked against real Postgres in
`tools/ci_bundle_check.py`."""

from __future__ import annotations

import sys
from pathlib import Path

import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from synthetic_corpus import DocSpec, build_corpus  # noqa: E402

from bundle.writer import read_bundle  # noqa: E402
from db import DeltaError, build_delta  # noqa: E402
from db.delta import recipe_differences  # noqa: E402
from load import DeltaSpec, load_plan  # noqa: E402
from load.preflight import check_checksums  # noqa: E402

OLD = {"a": DocSpec(2), "b": DocSpec(2), "c": DocSpec(1)}


def _scope(delta: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for row in pq.read_table(delta / "scope.parquet").to_pylist():
        out.setdefault(row["kind"], set()).add(row["doc_id"])
    return out


def test_a_delta_is_the_difference_of_the_document_sets(tmp_path: Path) -> None:
    old = build_corpus(tmp_path, OLD, name="old")
    new = build_corpus(
        tmp_path, {"a": DocSpec(2), "c": DocSpec(1), "d": DocSpec(3)}, name="new"
    )
    report = build_delta(old, new, tmp_path / "delta")

    assert (report.added_docs, report.removed_docs, report.unchanged_docs) == (1, 1, 2)
    assert (report.added_chunks, report.removed_chunks) == (3, 2)
    assert _scope(report.out)["removed"] == {"b"}
    chunks = pq.read_table(report.out / "chunks" / "part-00000.parquet").to_pylist()
    assert {r["doc_id"] for r in chunks} == {"d"}
    vectors = pq.read_table(report.out / "vectors" / "part-00000.parquet").to_pylist()
    assert {r["chunk_id"] for r in vectors} == {"d-0", "d-1", "d-2"}


def test_the_delta_is_a_checksummed_bundle_that_names_its_parent(tmp_path: Path) -> None:
    old = build_corpus(tmp_path, OLD, name="old")
    new = build_corpus(tmp_path, {**OLD, "d": DocSpec(1)}, name="new")
    report = build_delta(old, new, tmp_path / "delta")
    document = read_bundle(report.out)

    assert check_checksums(report.out, document).status.value == "ok"
    assert document["delta"]["parent_manifest_sha256"] == read_bundle(old)["manifest_sha256"]
    assert document["delta"]["child_manifest_sha256"] == read_bundle(new)["manifest_sha256"]
    assert document["delta"]["child_chunk_count"] == 6
    assert document["chunk_count"] == 1  # what the delta itself carries


def test_a_derived_row_of_an_unchanged_document_is_replaced_when_it_changes(
    tmp_path: Path,
) -> None:
    """d revokes a: a is unchanged, but its status is now `revoked` and d's relation arrives."""
    old = build_corpus(tmp_path, OLD, name="old")
    new = build_corpus(tmp_path, {**OLD, "d": DocSpec(1, revokes="a")}, name="new")
    report = build_delta(old, new, tmp_path / "delta")
    scope = _scope(report.out)

    assert "a" in scope["status"] and "d" in scope["status"]
    assert "b" not in scope["status"] or "b" in scope["removed"]
    assert scope["relations"] == {"d"}
    statuses = {
        r["doc_id"]: r for r in pq.read_table(report.out / "doc_status.parquet").to_pylist()
    }
    assert statuses["a"]["status"] == "revoked" and statuses["a"]["revoked_by"] == ["d"]


def test_a_document_that_leaves_takes_its_derived_rows_with_it(tmp_path: Path) -> None:
    old = build_corpus(tmp_path, {**OLD, "d": DocSpec(1, revokes="a")}, name="old")
    new = build_corpus(tmp_path, OLD, name="new")
    scope = _scope(build_delta(old, new, tmp_path / "delta").out)
    assert scope["removed"] == {"d"}
    assert scope["relations"] == {"d"} and "a" in scope["status"]


def test_bundles_of_different_recipes_are_refused(tmp_path: Path) -> None:
    old = build_corpus(tmp_path, OLD, name="old")
    new = build_corpus(tmp_path, OLD | {"d": DocSpec(1)}, name="new", model="another-embedder")
    assert recipe_differences(read_bundle(old), read_bundle(new))
    with pytest.raises(DeltaError, match="not built by the same recipe"):
        build_delta(old, new, tmp_path / "delta")


def test_the_same_version_twice_is_not_a_delta(tmp_path: Path) -> None:
    old = build_corpus(tmp_path, OLD, name="old")
    with pytest.raises(DeltaError, match="same version"):
        build_delta(old, old, tmp_path / "delta")


def test_the_plan_checks_the_parent_first_and_removes_before_it_merges(tmp_path: Path) -> None:
    old = build_corpus(tmp_path, OLD, name="old")
    new = build_corpus(
        tmp_path, {"a": DocSpec(2), "c": DocSpec(1), "d": DocSpec(1)}, name="new"
    )
    report = build_delta(old, new, tmp_path / "delta")
    document = read_bundle(report.out)
    block = document["delta"]
    plan = load_plan(
        document,
        report.out,
        delta=DeltaSpec(
            parent_manifest_sha256=block["parent_manifest_sha256"],
            child_manifest_sha256=block["child_manifest_sha256"],
            child_source_manifest_sha256=block["child_source_manifest_sha256"],
            child_chunk_count=block["child_chunk_count"],
        ),
    )
    names = [s.name for s in plan]

    assert names[0] == "check parent"
    assert not {"schema", "stamp corpus_meta", "stamp canary", "indexes", "clusters"} & set(
        names
    )
    assert names.index("remove") < names.index("merge")
    assert names.index("copy scope") < names.index("remove")
    assert names.index("advance version") > names.index("verify counts")
    remove = next(s for s in plan if s.name == "remove").sql
    assert "DELETE FROM chunks" in remove and "DELETE FROM doc_status" in remove
    advance = next(s for s in plan if s.name == "advance version").sql
    assert block["child_manifest_sha256"] in advance and "corpus_versions" in advance
    verify = next(s for s in plan if s.name == "verify counts").sql
    assert f"<> {block['child_chunk_count']}" in verify


def test_a_removal_only_delta_has_nothing_to_insert(tmp_path: Path) -> None:
    old = build_corpus(tmp_path, OLD, name="old")
    new = build_corpus(tmp_path, {"a": DocSpec(2), "c": DocSpec(1)}, name="new")
    report = build_delta(old, new, tmp_path / "delta")
    assert (report.added_docs, report.added_chunks, report.removed_docs) == (0, 0, 1)
    document = read_bundle(report.out)
    assert not [
        n for n in document["inventory"]["counts"] if n.startswith(("chunks/", "vectors/"))
    ]
