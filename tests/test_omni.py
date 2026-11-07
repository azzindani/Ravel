"""The second vector space: a bundle can carry it, the schema and plan know about it, and a
text-only bundle is exactly what it was before it existed (`EMBEDDING.md` §4)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np
import pytest

from bundle import (
    BundleManifest,
    BundleWriter,
    ManifestError,
    read_bundle,
    render_indexes,
    render_schema,
    write_vectors,
)
from chunk.models import Chunk
from chunk.store import write_chunks
from embed import HashOmniEmbedder, ModalItem, OmniEmbedder, spec_for
from embed.spec import Modality
from load import load_plan

DIM, OMNI_DIM = 16, 24


def omni_spec(**over: object):  # type: ignore[no-untyped-def]
    base = spec_for(dim=OMNI_DIM, id="omni-test")
    return dataclasses.replace(base, modality=Modality.OMNI, model="ravel/omni", **over)


def manifest(omni: object = None) -> BundleManifest:
    return BundleManifest(
        corpus_id="c",
        run_id="r",
        chunk_count=3,
        source_manifest_sha256="a" * 64,
        dense=spec_for(dim=DIM, id="text-test"),
        omni=omni,  # type: ignore[arg-type]
    )


def build(root: Path, *, with_omni: bool) -> dict:  # type: ignore[type-arg]
    """A sealed bundle; with the omni space only two of its three chunks have an image."""
    writer = BundleWriter(root=root, manifest=manifest(omni_spec() if with_omni else None))
    chunks = [
        Chunk(
            id=f"c{i}",
            doc_id="d0",
            body=f"Pasal {i + 1} memuat ketentuan.",
            token_count=4,
            source_title="t",
            source_url="https://example/t",
            source_sha256="b" * 64,
            chunker="unit",
            chunker_version="1",
            config_hash="h",
        )
        for i in range(3)
    ]
    writer.record(
        "chunks/part-00000.parquet", write_chunks(chunks, str(writer.shard_path("chunks", 0)))
    )
    rng = np.random.default_rng(0)
    writer.record(
        "vectors/part-00000.parquet",
        write_vectors(
            writer.shard_path("vectors", 0),
            ["c0", "c1", "c2"],
            rng.normal(size=(3, DIM)),
            dim=DIM,
        ),
    )
    if with_omni:
        writer.record(
            "vectors_omni/part-00000.parquet",
            write_vectors(
                writer.shard_path("vectors_omni", 0),
                ["c0", "c2"],
                rng.normal(size=(2, OMNI_DIM)),
                dim=OMNI_DIM,
            ),
        )
    writer.write_reference(rng.normal(size=DIM).astype(np.float32), "x")
    writer.write_failed([])
    writer.seal()
    return read_bundle(writer.root)


# -- the manifest -------------------------------------------------------------------------
def test_a_text_bundle_is_exactly_what_it_was_before_the_second_space_existed() -> None:
    plain = manifest()
    assert "omni" not in plain.to_dict()
    assert "omni" not in plain.to_json()


def test_the_omni_block_is_in_the_manifest_and_changes_its_fingerprint() -> None:
    with_omni = manifest(omni_spec())
    assert with_omni.to_dict()["omni"]["dim"] == OMNI_DIM
    assert with_omni.fingerprint() != manifest().fingerprint()


def test_the_second_space_must_declare_itself_omni() -> None:
    with pytest.raises(ManifestError, match="modality 'omni'"):
        manifest(spec_for(dim=OMNI_DIM, id="another-text-model"))


def test_the_same_space_twice_is_not_a_second_space() -> None:
    """An omni model as the dense column and again as the omni one: two copies of one space
    that could drift apart, in two columns."""
    with pytest.raises(ManifestError, match="same space"):
        BundleManifest(
            corpus_id="c",
            run_id="r",
            chunk_count=1,
            source_manifest_sha256="a" * 64,
            dense=omni_spec(),
            omni=omni_spec(),
        )


# -- the schema -----------------------------------------------------------------------------
def test_the_schema_has_a_second_column_at_its_own_width_only_when_declared() -> None:
    with_omni = render_schema(manifest(omni_spec()))
    assert f"embedding_omni      halfvec({OMNI_DIM})" in with_omni
    assert f"halfvec({DIM})" in with_omni, "the text column is untouched"
    plain = render_schema(manifest())
    assert "embedding_omni" not in plain and "omni" not in plain.lower().replace("omnibus", "")
    assert "ADD COLUMN IF NOT EXISTS omni JSONB" in with_omni
    assert "ADD COLUMN IF NOT EXISTS embedding_omni halfvec(24)" in with_omni


def test_the_omni_index_exists_only_with_the_omni_space() -> None:
    assert "chunks_omni_idx" in render_indexes(manifest(omni_spec()))
    assert "chunks_omni_idx" not in render_indexes(manifest())


# -- the load plan --------------------------------------------------------------------------
def test_the_plan_loads_the_second_space_with_a_left_join_and_checks_its_ids(
    tmp_path: Path,
) -> None:
    document = build(tmp_path / "b", with_omni=True)
    plan = {step.name: step for step in load_plan(document, tmp_path / "b")}
    assert "copy vectors_omni" in plan and "verify omni" in plan
    assert (
        f"halfvec({OMNI_DIM})" in plan["stage"].sql
        and "vectors_omni_stage" in plan["stage"].sql
    )
    merge = plan["merge"].sql
    assert "LEFT JOIN vectors_omni_stage" in merge and '"embedding_omni"' in merge
    assert "JOIN vectors_stage v" in merge
    assert "DROP TABLE vectors_omni_stage" in plan["drop staging"].sql
    assert "omni" in plan["stamp corpus_meta"].sql


def test_a_text_bundle_has_no_trace_of_the_second_space_in_its_plan(tmp_path: Path) -> None:
    document = build(tmp_path / "t", with_omni=False)
    steps = load_plan(document, tmp_path / "t")
    assert not any("omni" in step.name for step in steps)
    stamp = next(s for s in steps if s.name == "stamp corpus_meta").sql
    assert "omni" not in stamp, "a text bundle must load into a corpus_meta that predates it"
    assert "LEFT JOIN" not in next(s for s in steps if s.name == "merge").sql


def test_a_declared_space_without_vectors_cannot_be_planned(tmp_path: Path) -> None:
    document = build(tmp_path / "b", with_omni=True)
    del document["inventory"]["counts"]["vectors_omni/part-00000.parquet"]
    with pytest.raises(ValueError, match="no vectors_omni shards"):
        load_plan(document, tmp_path / "b")


# -- the interface ------------------------------------------------------------------------
def test_an_item_needs_something_to_embed() -> None:
    with pytest.raises(ValueError, match="text, an image, or both"):
        ModalItem()
    assert ModalItem(text="caption").text == "caption"
    assert ModalItem(image=b"\x89PNG").image == b"\x89PNG"


def test_the_test_omni_embedder_distinguishes_images_and_has_its_own_width() -> None:
    embedder = HashOmniEmbedder(omni_spec())
    assert isinstance(embedder, OmniEmbedder)
    a, b = embedder.embed_items([ModalItem(image=b"page-1"), ModalItem(image=b"page-2")])
    again = embedder.embed_items([ModalItem(image=b"page-1")])[0]
    assert len(a) == OMNI_DIM and a != b and a == again
