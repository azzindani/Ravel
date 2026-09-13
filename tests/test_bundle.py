"""The manifest and the schema generated from it.

Every constant in here is a measured one: 748,558 chunks, a 20,000-dim sparse arm fitted
over 1,000 documents, 1024-dim vectors, 100% missing `source_url`. They are the
incumbent's numbers, so these tests describe a corpus that actually existed rather than
one that would be convenient to check.
"""

from __future__ import annotations

import json

import pytest

from bundle import (
    BundleManifest,
    ManifestError,
    SparseSpec,
    check_provenance,
    check_vocabulary_covers_corpus,
    render_schema,
)
from embed import InstructionStyle, spec_for

INCUMBENT_CHUNKS = 748_558


def manifest(**over: object) -> BundleManifest:
    base: dict[str, object] = {
        "corpus_id": "id_legal",
        "run_id": "r1",
        "chunk_count": INCUMBENT_CHUNKS,
        "source_manifest_sha256": "a" * 64,
        "dense": spec_for(dim=1024, id="qwen3-0.6b"),
        "chunker": "unit",
        "chunker_version": "1.0",
        "text_search_config": "indonesian",
    }
    return BundleManifest(**(base | over))  # type: ignore[arg-type]


def sparse(**over: object) -> SparseSpec:
    base: dict[str, object] = {
        "scheme": "bm25",
        "dim": 20_000,
        "k1": 1.2,
        "b": 0.75,
        "vocab_sha256": "b" * 64,
        "fit_docs": INCUMBENT_CHUNKS,
    }
    return SparseSpec(**(base | over))  # type: ignore[arg-type]


# -- the manifest refuses what cannot be reproduced --------------------------------


def test_a_truncated_vocabulary_hash_is_rejected() -> None:
    """The vocabulary *is* the recipe; a short hash cannot verify it."""
    with pytest.raises(ManifestError, match="full sha256"):
        sparse(vocab_sha256="abc123")


def test_a_vocabulary_fitted_over_nothing_is_rejected() -> None:
    with pytest.raises(ManifestError, match="cannot project a query"):
        sparse(fit_docs=0)


# -- §7, made checkable -------------------------------------------------------------


def test_a_per_shard_vocabulary_is_caught() -> None:
    """! The incumbent's real defect. `fit_transform` ran per 1,000-row chunk, so the
    corpus holds ~1,000 vocabularies and ~1,000 IDF weightings — each saved correctly
    beside its own shard, none comparable with any other. Fragmentation looks healthy,
    which is why it needs a check rather than an inspection."""
    ok, detail = check_vocabulary_covers_corpus(manifest(sparse=sparse(fit_docs=1_000)))

    assert not ok
    assert "1,000 of 748,558" in detail


def test_a_corpus_wide_vocabulary_passes() -> None:
    ok, detail = check_vocabulary_covers_corpus(manifest(sparse=sparse()))

    assert ok, detail


def test_no_sparse_arm_is_not_a_failure() -> None:
    ok, _ = check_vocabulary_covers_corpus(manifest(sparse=None))

    assert ok


# -- provenance is reported, not enforced ------------------------------------------


def test_missing_provenance_is_reported_with_its_share() -> None:
    """! Not an error. The incumbent has no `source_url` on 100% of rows and is still
    worth loading as a scored baseline — flagged, never dressed up as verifiable."""
    ok, detail = check_provenance(manifest(), missing_source_urls=INCUMBENT_CHUNKS)

    assert not ok
    assert "100.0%" in detail
    assert "provenance-incomplete" in detail


def test_complete_provenance_passes() -> None:
    ok, _ = check_provenance(manifest(), missing_source_urls=0)

    assert ok


# -- the fingerprint is a rebuild decision ------------------------------------------


def test_rebuilding_the_same_corpus_gives_the_same_fingerprint() -> None:
    """! `run_id` and `created_at` are excluded on purpose. If they were not, every
    rebuild would look like a different corpus and the fingerprint could not answer the
    only question it is for: does this need rebuilding?"""
    assert manifest(run_id="a").fingerprint() == manifest(run_id="b").fingerprint()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("chunk_count", 1),
        ("chunker", "heading"),
        ("chunker_version", "2.0"),
        ("text_search_config", "simple"),
        ("source_manifest_sha256", "c" * 64),
        ("dense", spec_for(dim=2048, id="qwen3-vl")),
        ("sparse", sparse(dim=10_000)),
    ],
)
def test_anything_that_changes_the_data_changes_the_fingerprint(
    field: str, value: object
) -> None:
    assert manifest().fingerprint() != manifest(**{field: value}).fingerprint()


def test_manifest_serialises_with_its_own_hash() -> None:
    payload = json.loads(manifest(sparse=sparse()).to_json())

    assert payload["manifest_sha256"] == manifest(sparse=sparse()).fingerprint()
    assert payload["dense"]["padding_side"] == "left"
    assert payload["sparse"]["vocab_sha256"] == "b" * 64


# -- the schema is generated, and carries what the manifest says --------------------


def test_dimension_comes_from_the_manifest_not_a_constant() -> None:
    """`CLAUDE.md` §7.8 — never hardcode the embedding dimension."""
    assert "halfvec(1024)" in render_schema(manifest())
    assert "halfvec(4096)" in render_schema(manifest(dense=spec_for(dim=4096, id="big")))


def test_sparse_column_appears_only_when_declared() -> None:
    assert "sparsevec(20000)" in render_schema(manifest(sparse=sparse()))
    assert "sparsevec" not in render_schema(manifest(sparse=None))


def test_text_search_config_is_carried_not_assumed() -> None:
    """! `indonesian`, not `simple`. Indonesian is heavily affixed — `dikenakan` and
    `dikenai` are one word inflected, and `simple` indexes them as unrelated terms."""
    assert "to_tsvector('indonesian', body)" in render_schema(manifest())
    assert "to_tsvector('simple', body)" in render_schema(
        manifest(text_search_config="simple")
    )


def test_padding_side_reaches_the_schema() -> None:
    """The column Vera's `corpus_meta` does not have, and whose absence is §11."""
    ddl = render_schema(manifest())

    assert "dense_padding_side" in ddl
    assert "left pad" in ddl


def test_instruction_is_three_columns_not_one() -> None:
    """A single nullable `dense_instruction` records "never applied" as NULL —
    indistinguishable from a model that legitimately takes none."""
    ddl = render_schema(
        manifest(
            dense=spec_for(
                dim=1024,
                id="q",
                instruction_style=InstructionStyle.PREFIX,
                doc_instruction="Represent the document",
                query_instruction="Retrieve relevant passages",
            )
        )
    )

    for column in (
        "dense_instruction_style",
        "dense_doc_instruction",
        "dense_query_instruction",
    ):
        assert column in ddl


def test_provenance_incomplete_bundles_get_a_nullable_column_and_a_reason() -> None:
    complete = render_schema(manifest(), provenance_complete=True)
    incomplete = render_schema(manifest(), provenance_complete=False)

    assert "source_url          TEXT NOT NULL," in complete
    assert "source_url          TEXT," in incomplete
    assert "inventing one" in incomplete, "a nullable column must say why it is nullable"


def test_extraction_path_is_recorded_on_every_chunk() -> None:
    """! §18.3: an extraction setting nobody recorded is a setting nobody can review.
    With these columns, "how much of this corpus came from OCR" is a query rather than an
    archaeology project."""
    ddl = render_schema(manifest())

    assert "extractor" in ddl
    assert "text_layer" in ddl


def test_the_manifest_hash_is_embedded_in_the_ddl() -> None:
    """So a schema found on disk can be matched back to the bundle that generated it —
    the drift that got Vera's `0001_init.sql` deleted."""
    m = manifest()

    assert m.fingerprint() in render_schema(m)
