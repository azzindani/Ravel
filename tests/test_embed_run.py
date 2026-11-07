"""The embedding stage, end to end on a synthetic corpus with the deterministic embedder.

No GPU, no download, no network. What these assert is the stage's behaviour: that vectors
line up with their chunks, that a rerun does nothing, that a different embedder cannot
reuse another's vectors, and that a window which cannot be right is refused before it is
written. Whether a real model is *good* is the preflight's and the eval's question.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import pytest

from bundle.writer import iter_vectors
from chunk import Chunking, iter_chunk_shards
from chunk.derive import embed_text
from embed import (
    Embedding,
    HashEmbedder,
    build_embedder,
    check_vectors,
    render_document,
    spec_for,
)
from embed.local import last_token_index
from embed.run import window_key
from embed.spec import InstructionStyle, PaddingSide
from extract import Extraction
from runtime import Ledger
from spec import CorpusSpec, default_registry
from spec.corpus import EmbedSpec

DOC = """PERATURAN BUPATI BOYOLALI NOMOR {n} TAHUN 2011 TENTANG PAJAK DAERAH

## Menimbang

bahwa untuk melaksanakan ketentuan tersebut perlu menetapkan peraturan.

## BAB II

Pasal 1

(1) Wajib Pajak wajib menyelenggarakan pembukuan sesuai ketentuan yang berlaku.

(2) Pembukuan tersebut disimpan selama sepuluh tahun di tempat kedudukannya.

Pasal 2

Ketentuan lebih lanjut mengenai tata cara diatur dengan Peraturan Bupati tersendiri.
"""

DIM = 16


def embed_config(**overrides: Any) -> dict[str, Any]:
    return {
        "kind": "hash",
        "id": "hash-test",
        "model": "ravel/hash-embedder",
        "revision": "1.0.0",
        "dim": DIM,
        "shard_rows": 5,
        "batch_items": 3,
        **overrides,
    }


@pytest.fixture
def corpus(tmp_path: Path) -> CorpusSpec:
    sources = tmp_path / "src"
    sources.mkdir()
    for n in range(1, 5):
        (sources / f"perbup_{n}.md").write_text(DOC.format(n=n), encoding="utf-8")
    return CorpusSpec.model_validate(
        {
            "id": "fixture",
            "title": "Fixture corpus",
            "sources": {"root": str(sources), "include": ["**/*.md"]},
            "provenance": {"base_url": "hf://datasets/example/fixture/"},
            "extract": {"profile": "id_regulation", "extractors": ["text"]},
            "chunk": {"chunker": "unit", "max_tokens": 256, "min_tokens": 8},
            "embed": embed_config(),
        }
    )


@pytest.fixture
def workspace(corpus: CorpusSpec, tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    Extraction(corpus, workspace=ws).run()
    Chunking(corpus, workspace=ws).run()
    return ws


def stage(corpus: CorpusSpec, workspace: Path, **config: Any) -> Embedding:
    cfg = EmbedSpec.model_validate(embed_config(**config))
    return Embedding(corpus, workspace=workspace, embedder=build_embedder(cfg), config=cfg)


def vectors_of(run: Embedding) -> dict[str, np.ndarray[Any, Any]]:
    out: dict[str, np.ndarray[Any, Any]] = {}
    for shard in sorted(run.vector_dir.glob("part-*.parquet")):
        for chunk_id, vector in iter_vectors(shard):
            assert chunk_id not in out, f"{chunk_id} appears in two shards"
            out[chunk_id] = np.asarray(vector, dtype=np.float32)
    return out


# ===========================================================================
def test_every_chunk_gets_exactly_one_vector_of_the_declared_width(
    corpus: CorpusSpec, workspace: Path
) -> None:
    run = stage(corpus, workspace)
    report = run.run()

    chunks = list(iter_chunk_shards(workspace, "fixture"))
    vectors = vectors_of(run)

    assert report.vectors == len(chunks) > 5
    assert set(vectors) == {c.id for c in chunks}
    assert len(vectors) == len(chunks), "a chunk embedded twice is a chunk in the corpus twice"
    assert all(v.shape == (DIM,) for v in vectors.values())


def test_a_vector_belongs_to_the_chunk_it_is_filed_under(
    corpus: CorpusSpec, workspace: Path
) -> None:
    """! The stage sorts by length and batches, then puts results back. A vector attached
    to the wrong chunk is a silent mis-join, so recompute each one independently."""
    run = stage(corpus, workspace, batch_items=2)
    run.run()
    spec = run.embedder.spec
    reference = HashEmbedder(spec)
    vectors = vectors_of(run)

    for chunk in iter_chunk_shards(workspace, "fixture"):
        expected = np.asarray(reference.embed_documents([embed_text(chunk, run.profile)])[0])
        assert np.allclose(vectors[chunk.id], expected, atol=1e-2), chunk.id


def test_what_is_embedded_is_the_template_and_the_body_is_not_touched(
    corpus: CorpusSpec, workspace: Path
) -> None:
    run = stage(corpus, workspace)
    chunk = next(iter_chunk_shards(workspace, "fixture"))
    shown = embed_text(chunk, run.profile)

    assert chunk.body in shown
    assert chunk.body == next(iter_chunk_shards(workspace, "fixture")).body


def test_a_second_run_does_nothing(corpus: CorpusSpec, workspace: Path) -> None:
    first = stage(corpus, workspace).run()
    second = stage(corpus, workspace).run()

    assert first.vectors > 0
    assert second.vectors == 0
    assert second.skipped_done == first.windows
    assert second.shards == 0


def test_a_killed_run_resumes_from_the_last_sealed_shard(
    corpus: CorpusSpec, workspace: Path
) -> None:
    run = stage(corpus, workspace)
    partial = run.run(limit=5)
    assert 0 < partial.vectors < len(list(iter_chunk_shards(workspace, "fixture")))

    rest = stage(corpus, workspace).run()
    assert rest.skipped_done == partial.shards
    total = len(list(iter_chunk_shards(workspace, "fixture")))
    assert partial.vectors + rest.vectors == total
    assert len(vectors_of(run)) == total


def test_force_recomputes_without_duplicating(corpus: CorpusSpec, workspace: Path) -> None:
    run = stage(corpus, workspace)
    run.run()
    again = stage(corpus, workspace).run(force=True)

    assert again.vectors > 0
    assert len(vectors_of(run)) == len(list(iter_chunk_shards(workspace, "fixture")))


def test_a_different_embedder_cannot_reuse_anothers_vectors(
    corpus: CorpusSpec, workspace: Path
) -> None:
    """! `CLAUDE.md` §7.3 and §7.7. Padding side alone changes a vector, so it changes the
    directory and every key."""
    a = stage(corpus, workspace)
    b = stage(corpus, workspace, padding_side="right")
    a.run()
    second = b.run()

    assert a.vector_dir != b.vector_dir
    assert second.skipped_done == 0 and second.vectors > 0


def test_the_template_is_part_of_the_key(corpus: CorpusSpec, workspace: Path) -> None:
    run = stage(corpus, workspace)
    key = window_key(
        shard="part-00000.parquet",
        start=0,
        end=5,
        fingerprint=run.embedder.spec.fingerprint(),
        template=run.template_hash,
        chunk_generation=run.chunk_dir.name,
    )
    other = window_key(
        shard="part-00000.parquet",
        start=0,
        end=5,
        fingerprint=run.embedder.spec.fingerprint(),
        template="another",
        chunk_generation=run.chunk_dir.name,
    )
    assert key != other


def test_the_ledger_is_written_after_the_shard_exists(
    corpus: CorpusSpec, workspace: Path
) -> None:
    run = stage(corpus, workspace)
    run.run()
    with Ledger(run.ledger_path) as ledger:
        done = ledger.done_keys(run._stage())
    shards = sorted(run.vector_dir.glob("part-*.parquet"))
    assert len(done) == len(shards) > 0
    assert all(pq.read_table(p).num_rows > 0 for p in shards)


def test_a_manifest_is_written_beside_the_vectors(corpus: CorpusSpec, workspace: Path) -> None:
    run = stage(corpus, workspace)
    run.run()
    manifest = (run.vector_dir / "embed.json").read_text(encoding="utf-8")
    assert run.embedder.spec.fingerprint() in manifest
    assert run.template_hash in manifest


def test_a_width_the_corpus_did_not_declare_is_refused_before_any_work(
    corpus: CorpusSpec, workspace: Path
) -> None:
    cfg = EmbedSpec.model_validate(embed_config(dim=DIM))
    wrong = HashEmbedder(spec_for(dim=DIM * 2, id="hash-test"))
    with pytest.raises(ValueError, match="dimensions"):
        Embedding(corpus, workspace=workspace, embedder=wrong, config=cfg)


def test_a_stage_without_chunks_says_to_chunk_first(corpus: CorpusSpec, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="ravel chunk"):
        stage(corpus, tmp_path / "empty")


def test_a_corpus_without_an_embed_section_is_an_error(
    corpus: CorpusSpec, workspace: Path
) -> None:
    bare = corpus.model_copy(update={"embed": None})
    with pytest.raises(ValueError, match="embed"):
        Embedding(bare, workspace=workspace, embedder=HashEmbedder(spec_for(dim=DIM)))


# -- the refusals ---------------------------------------------------------------
def test_check_vectors_refuses_nan_the_wrong_width_and_the_wrong_norm() -> None:
    good = np.eye(3, DIM, dtype=np.float32)
    check_vectors(good, dim=DIM, rows=3, normalized=True)

    with pytest.raises(ValueError, match="expected 3 vectors"):
        check_vectors(good, dim=DIM + 1, rows=3, normalized=True)
    bad = good.copy()
    bad[1, 0] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        check_vectors(bad, dim=DIM, rows=3, normalized=True)
    with pytest.raises(ValueError, match="unit length"):
        check_vectors(good * 2, dim=DIM, rows=3, normalized=True)
    check_vectors(good * 2, dim=DIM, rows=3, normalized=False)  # the manifest said so


class Broken(HashEmbedder):
    """Returns NaN for one text: a model that overflows on one pathological chunk."""

    def embed_documents(self, batch: list[str]) -> Any:
        out = np.asarray(super().embed_documents(batch), dtype=np.float32)
        out[0, 0] = np.nan
        return out


def test_a_window_that_cannot_be_right_is_never_written(
    corpus: CorpusSpec, workspace: Path
) -> None:
    cfg = EmbedSpec.model_validate(embed_config())
    run = Embedding(
        corpus,
        workspace=workspace,
        embedder=Broken(spec_for(dim=DIM, id="hash-test")),
        config=cfg,
    )
    with pytest.raises(ValueError, match="NaN"):
        run.run()
    assert not list(run.vector_dir.glob("part-*.parquet"))
    with Ledger(run.ledger_path) as ledger:
        assert not ledger.done_keys(run._stage())


# -- instruction rendering, the bug this stage's model depends on ----------------------------
def test_a_document_is_embedded_bare_when_only_the_query_carries_an_instruction() -> None:
    """! Qwen3 text: the instruction is on the query, never on the document. A document
    rendered as an empty `Instruct:` template sits in a space no query is asked from."""
    spec = spec_for(
        instruction_style=InstructionStyle.PREFIX,
        doc_instruction="",
        query_instruction="Find law",
    )
    assert render_document(spec, "isi pasal") == "isi pasal"


# -- last-token pooling, which padding side decides -------------------------------------------
def test_the_last_real_token_depends_on_the_padding_side() -> None:
    mask = np.array([[0, 0, 1, 1, 1], [1, 1, 1, 1, 1], [0, 1, 1, 1, 1]])
    assert list(last_token_index(mask, PaddingSide.LEFT)) == [4, 4, 4]

    right = np.array([[1, 1, 1, 0, 0], [1, 1, 1, 1, 1], [1, 1, 0, 0, 0]])
    assert list(last_token_index(right, PaddingSide.RIGHT)) == [2, 4, 1]
    # ! Reading -1 on a right-padded batch pools a PAD position for every short text.
    assert list(last_token_index(right, PaddingSide.LEFT)) != [2, 4, 1]


def test_the_registry_profile_the_corpus_pins_is_the_one_the_template_comes_from(
    corpus: CorpusSpec, workspace: Path
) -> None:
    run = stage(corpus, workspace)
    assert run.profile is default_registry().get("id_regulation")


def test_progress_is_reported_from_the_first_batch_to_the_end(
    corpus: CorpusSpec, workspace: Path
) -> None:
    """A run you cannot watch is a run you cannot trust: the file says how far it is."""
    from embed import read_progress

    seen: list[Any] = []
    run = stage(corpus, workspace, batch_items=2, shard_rows=4)
    report = run.run(on_status=seen.append, status_every=0.0)

    data = read_progress(run.progress_path)
    assert data is not None
    assert data["phase"] == "done"
    assert data["total"] == data["done"] == data["sealed"] == report.vectors
    assert data["percent"] == 100.0
    assert data["windows"] == data["windows_total"] == report.windows
    assert len(seen) > report.windows, "progress must move between sealed windows too"


def test_a_resumed_run_counts_the_sealed_windows_as_done(
    corpus: CorpusSpec, workspace: Path
) -> None:
    from embed import read_progress

    first = stage(corpus, workspace, shard_rows=4)
    first.run()
    again = stage(corpus, workspace, shard_rows=4)
    again.run()
    data = read_progress(again.progress_path)
    assert data is not None
    assert (
        data["percent"] == 100.0 and data["this_run"] == 0 and data["sealed"] == data["total"]
    )


# ===========================================================================
# The bundle assembled from what the stages wrote.
def _sidecar_rows(doc_ids: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "doc_id": d,
            "verdict": "clean",
            "indexable": True,
            "chunks": 1,
            "damaged_chunks": 0,
            "words": 100,
            "unknown_share": 0.0,
            "damaged_share": 0.0,
            "replacement_per_1000": 0.0,
            "reasons": [],
        }
        for d in doc_ids
    ]


def test_a_finished_run_becomes_a_sealed_bundle_that_passes_preflight(
    corpus: CorpusSpec, workspace: Path, tmp_path: Path
) -> None:
    from bundle.build import build_bundle
    from bundle.writer import read_bundle, write_doc_quality
    from load import preflight

    run = stage(corpus, workspace)
    run.run()
    chunks = list(iter_chunk_shards(workspace, "fixture"))
    docs = sorted({c.doc_id for c in chunks})
    # a duplicated document, and one the corpus does not hold: both must be dropped
    quality = tmp_path / "q.parquet"
    write_doc_quality(quality, _sidecar_rows(docs + [docs[0], "sha256:not-in-corpus"]))

    text = "Pasal 1 wajib pajak"
    canary = np.asarray(run.embedder.embed_queries([text])[0], dtype=np.float32)
    report = build_bundle(
        corpus,
        workspace=workspace,
        root=tmp_path / "bundle",
        quality=quality,
        reference=(canary, text),
    )
    assert report.chunks == report.vectors == len(chunks)
    assert report.sidecars["doc_quality"] == len(docs)
    assert report.dropped["doc_quality"] == 2

    document = read_bundle(report.root)
    assert document["chunk_count"] == len(chunks)
    assert document["dense"]["dim"] == DIM
    result = preflight(report.root, target_dim=DIM, free_bytes=10**12)
    assert not result.failed, [c.detail for c in result.failed]


def test_a_half_embedded_corpus_is_not_bundled(
    corpus: CorpusSpec, workspace: Path, tmp_path: Path
) -> None:
    from bundle.build import BundleBuildError, build_bundle

    stage(corpus, workspace).run(limit=5)  # one window of several
    with pytest.raises(BundleBuildError, match="have no vector"):
        build_bundle(corpus, workspace=workspace, root=tmp_path / "bundle")
    assert not (tmp_path / "bundle" / "manifest.json").exists(), "nothing may be sealed"


def test_a_bundle_ships_the_vectors_the_stage_wrote_byte_for_byte(
    corpus: CorpusSpec, workspace: Path, tmp_path: Path
) -> None:
    from bundle.build import build_bundle

    run = stage(corpus, workspace)
    run.run()
    report = build_bundle(corpus, workspace=workspace, root=tmp_path / "b")
    written = {c: v for c, v in vectors_of(run).items()}
    shipped: dict[str, np.ndarray[Any, Any]] = {}
    for shard in sorted((report.root / "vectors").glob("part-*.parquet")):
        for chunk_id, vector in iter_vectors(shard):
            shipped[chunk_id] = np.asarray(vector, dtype=np.float32)
    assert set(shipped) == set(written)
    assert all(np.array_equal(shipped[c], written[c]) for c in written)
