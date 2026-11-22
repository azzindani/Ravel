"""The lexical arm as a stage: one vocabulary for the whole corpus, a sparse vector per chunk,
and a load plan that carries them into the `sparse` column.

The invariants worth failing on: the vocabulary is fitted ONCE over every shard (not per
shard, `ABSORPTION.md` §7), a re-run changes nothing, and a vocabulary without vectors, or
vectors without a vocabulary, never reaches the database.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest

from bundle import BundleManifest, BundleWriter, read_bundle, write_vectors
from chunk.models import Chunk
from chunk.store import write_chunks
from embed import spec_for
from enrich.sparse import Bm25Vectorizer
from enrich.sparse_run import VOCAB_NAME, build_sparse
from load import load_plan

DIM = 8
BODIES = [
    "retribusi pelayanan pasar ditetapkan dengan peraturan daerah",
    "pajak daerah dan retribusi daerah dipungut berdasarkan peraturan",
    "setiap orang wajib menaati ketentuan peraturan perundang-undangan",
    "sanksi pidana dikenakan bagi pelanggar ketentuan ini",
]


def _chunk(i: int, body: str) -> Chunk:
    return Chunk(
        id=f"c{i}",
        doc_id=f"d{i % 2}",
        body=body,
        token_count=len(body.split()),
        source_title="Peraturan Daerah Nomor 1 Tahun 2020",
        source_url="https://example.test/perda-1-2020",
        source_sha256="b" * 64,
        chunker="unit",
        chunker_version="1.0",
        config_hash="h",
    )


@pytest.fixture
def chunk_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "chunks"
    directory.mkdir()
    write_chunks([_chunk(i, BODIES[i]) for i in (0, 1)], str(directory / "part-00000.parquet"))
    write_chunks([_chunk(i, BODIES[i]) for i in (2, 3)], str(directory / "part-00001.parquet"))
    return directory


def test_one_vocabulary_is_fitted_over_every_shard(chunk_dir: Path, tmp_path: Path) -> None:
    report = build_sparse(chunk_dir, tmp_path / "sparse")

    assert report.fitted_now
    assert report.documents == 4  # all four chunks, across both shards
    vectorizer = Bm25Vectorizer.load(tmp_path / "sparse" / VOCAB_NAME)
    assert vectorizer.n_docs == 4
    # A term that occurs only in the second shard is in the vocabulary: the fit was global.
    assert "sanksi" in vectorizer.vocab
    assert "retribusi" in vectorizer.vocab


def test_each_chunk_gets_one_sparse_row_in_its_shard_order(
    chunk_dir: Path, tmp_path: Path
) -> None:
    build_sparse(chunk_dir, tmp_path / "sparse")

    for shard in ("part-00000.parquet", "part-00001.parquet"):
        chunk_ids = pq.read_table(str(chunk_dir / shard), columns=["id"])["id"].to_pylist()
        sparse = pq.read_table(str(tmp_path / "sparse" / shard)).to_pydict()
        assert sparse["chunk_id"] == chunk_ids
        assert all(lit.startswith("{") and "/" in lit for lit in sparse["sparse"])


def test_the_stored_vector_scores_a_query_like_the_vectoriser_does(
    chunk_dir: Path, tmp_path: Path
) -> None:
    """The invariant is the score, not the text: a literal that parses and ranks nonsense
    would pass a format check."""
    build_sparse(chunk_dir, tmp_path / "sparse")
    vectorizer = Bm25Vectorizer.load(tmp_path / "sparse" / VOCAB_NAME)
    rows = pq.read_table(str(tmp_path / "sparse" / "part-00000.parquet")).to_pydict()
    literal = rows["sparse"][0]  # the first chunk: "retribusi pelayanan pasar ..."

    pairs = literal[1 : literal.index("}")].split(",")
    stored = {int(k) - 1: float(v) for k, v in (p.split(":") for p in pairs if p)}
    query = vectorizer.query("retribusi pasar")

    via_literal = sum(w * stored.get(i, 0.0) for i, w in query.items())
    assert via_literal == pytest.approx(
        vectorizer.score("retribusi pasar", BODIES[0]), rel=1e-4
    )
    assert via_literal > 0


def test_a_rerun_changes_nothing_and_skips_finished_shards(
    chunk_dir: Path, tmp_path: Path
) -> None:
    out = tmp_path / "sparse"
    first = build_sparse(chunk_dir, out)
    vocab_bytes = (out / VOCAB_NAME).read_bytes()

    second = build_sparse(chunk_dir, out)

    assert first.shards == 2 and second.shards == 0
    assert second.skipped == 2
    assert not second.fitted_now
    assert (out / VOCAB_NAME).read_bytes() == vocab_bytes
    assert second.vocab_sha256 == first.vocab_sha256
    assert second.rows == 4


def test_an_update_keeps_the_earlier_vocabulary(chunk_dir: Path, tmp_path: Path) -> None:
    # ! A delta refuses two bundles whose vocabularies differ: an update must not refit.
    earlier = build_sparse(chunk_dir, tmp_path / "old")
    later = build_sparse(chunk_dir, tmp_path / "new", vocab=tmp_path / "old" / VOCAB_NAME)
    assert not later.fitted_now
    assert later.vocab_sha256 == earlier.vocab_sha256


def test_another_vocabulary_over_written_shards_is_refused(
    chunk_dir: Path, tmp_path: Path
) -> None:
    build_sparse(chunk_dir, tmp_path / "a")
    build_sparse(chunk_dir, tmp_path / "b", max_features=3)
    with pytest.raises(ValueError, match="another vocabulary"):
        build_sparse(chunk_dir, tmp_path / "b", vocab=tmp_path / "a" / VOCAB_NAME)


def test_a_stage_with_no_chunks_is_refused(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError, match="ravel chunk"):
        build_sparse(tmp_path / "empty", tmp_path / "out")


# -- the load plan --------------------------------------------------------------------------


def _bundle(tmp_path: Path, chunk_dir: Path, *, with_vectors_for_sparse: bool = True) -> Path:
    sparse_dir = tmp_path / "sparse"
    build_sparse(chunk_dir, sparse_dir)
    vectorizer = Bm25Vectorizer.load(sparse_dir / VOCAB_NAME)
    manifest = BundleManifest(
        corpus_id="id_legal",
        run_id="r1",
        chunk_count=4,
        source_manifest_sha256="a" * 64,
        dense=spec_for(dim=DIM, id="q"),
        sparse=vectorizer.to_spec() if with_vectors_for_sparse else None,
        chunker="unit",
        chunker_version="1.0",
        text_search_config="indonesian",
    )
    writer = BundleWriter(root=tmp_path / "bundle", manifest=manifest)
    matrix = np.random.default_rng(0).normal(size=(4, DIM)).astype(np.float32)
    for index, name in enumerate(("part-00000.parquet", "part-00001.parquet")):
        target = writer.shard_path("chunks", index)
        shutil.copyfile(chunk_dir / name, target)
        writer.record(f"chunks/{target.name}", 2)
        ids = pq.read_table(str(target), columns=["id"])["id"].to_pylist()
        rows = write_vectors(
            writer.shard_path("vectors", index),
            ids,
            matrix[index * 2 : index * 2 + 2],
            dim=DIM,
        )
        writer.record(f"vectors/part-{index:05d}.parquet", rows)
        if with_vectors_for_sparse:
            sparse_target = writer.shard_path("sparse", index)
            shutil.copyfile(sparse_dir / name, sparse_target)
            writer.record(f"sparse/{sparse_target.name}", 2)
    if with_vectors_for_sparse:
        shutil.copyfile(sparse_dir / VOCAB_NAME, writer.root / VOCAB_NAME)
        writer.record(VOCAB_NAME, vectorizer.dim)
    writer.write_reference(matrix[0], "Pasal 1")
    writer.write_failed([])
    writer.seal(clustering={})
    return writer.root


def test_the_plan_stages_copies_and_merges_the_sparse_vectors(
    chunk_dir: Path, tmp_path: Path
) -> None:
    root = _bundle(tmp_path, chunk_dir)
    plan = {step.name: step for step in load_plan(read_bundle(root), root)}

    assert "sparsevec(" in plan["stage"].sql
    assert plan["copy sparse"].inputs == (
        "sparse/part-00000.parquet",
        "sparse/part-00001.parquet",
    )
    assert "sparse_stage" in plan["copy sparse"].sql
    assert "LEFT JOIN sparse_stage sp ON sp.chunk_id = c.id" in plan["merge"].sql
    assert "sp.sparse" in plan["merge"].sql
    assert "DROP TABLE sparse_stage" in plan["drop staging"].sql


def test_a_bundle_without_the_lexical_arm_has_no_trace_of_it_in_its_plan(
    chunk_dir: Path, tmp_path: Path
) -> None:
    root = _bundle(tmp_path, chunk_dir, with_vectors_for_sparse=False)
    plan = {step.name: step for step in load_plan(read_bundle(root), root)}

    assert "copy sparse" not in plan
    assert "sparse_stage" not in plan["stage"].sql
    assert "sparse_stage" not in plan["merge"].sql


def test_a_vocabulary_without_vectors_cannot_be_loaded(chunk_dir: Path, tmp_path: Path) -> None:
    root = _bundle(tmp_path, chunk_dir)
    document = read_bundle(root)
    for name in [n for n in document["inventory"]["counts"] if n.startswith("sparse/")]:
        del document["inventory"]["counts"][name]

    with pytest.raises(ValueError, match="sparse"):
        load_plan(document, root)


def test_cjk_runs_become_bigrams_and_other_text_is_tokenised_as_before() -> None:
    from enrich.sparse import WORDS, WORDS_CJK, tokenize

    # ! `\w\w+` read a Japanese clause as one term no query would ever repeat.
    assert tokenize("博物館法第二条", WORDS_CJK) == [
        "博物",
        "物館",
        "館法",
        "法第",
        "第二",
        "二条",
    ]
    assert tokenize("法", WORDS_CJK) == ["法"]
    for text in ("Wajib Pajak a 28/2007", "C-46, s. 718.2 Purpose of Act", "§ 7 Wohnsitz"):
        assert tokenize(text, WORDS_CJK) == tokenize(text, WORDS)


def test_an_artifact_keeps_the_tokeniser_it_was_fitted_with(tmp_path: Path) -> None:
    from enrich.sparse import WORDS, WORDS_CJK, Bm25Vectorizer

    new = Bm25Vectorizer.fit(["博物館法第二条 定義", "公立博物館"])
    assert new.token_pattern == WORDS_CJK and "博物" in new.vocab
    new.save(tmp_path / "v.json")
    assert Bm25Vectorizer.load(tmp_path / "v.json").token_pattern == WORDS_CJK
    old = Bm25Vectorizer({"pajak": 0}, {"pajak": 1.0}, 1.0, 1, 1, token_pattern=WORDS)
    old.save(tmp_path / "o.json")
    assert Bm25Vectorizer.load(tmp_path / "o.json").token_pattern == WORDS
