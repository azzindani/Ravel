"""`ravel embed --reuse`: a new snapshot takes the vectors of unchanged text from an earlier
one, in the same vector space only, and only once the embedder reproduces them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bundle.writer import iter_vectors, write_vectors
from chunk import Chunking, iter_chunk_shards
from embed import Embedding, build_embedder
from embed.reuse import ReuseError
from extract import Extraction
from spec import CorpusSpec
from spec.corpus import EmbedSpec

DOC = """PERATURAN BUPATI BOYOLALI NOMOR {n} TAHUN 2011 TENTANG PAJAK DAERAH

## BAB II

Pasal 1

(1) Wajib Pajak wajib menyelenggarakan pembukuan sesuai ketentuan yang berlaku {edit}.

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


def snapshot(tmp_path: Path, name: str, edited: set[int]) -> tuple[CorpusSpec, Path]:
    """A corpus of four regulations, `edited` ones changed, built in its own workspace."""
    sources = tmp_path / name / "src"
    sources.mkdir(parents=True)
    for n in range(1, 5):
        text = DOC.format(n=n, edit="dan diubah" if n in edited else "")
        (sources / f"perbup_{n}.md").write_text(text, encoding="utf-8")
    corpus = CorpusSpec.model_validate(
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
    ws = tmp_path / name / "ws"
    Extraction(corpus, workspace=ws).run()
    Chunking(corpus, workspace=ws).run()
    return corpus, ws


def stage(corpus: CorpusSpec, ws: Path, reuse: tuple[Path, ...] = (), **cfg: Any) -> Embedding:
    spec = EmbedSpec.model_validate(embed_config(**cfg))
    return Embedding(
        corpus, workspace=ws, embedder=build_embedder(spec), config=spec, reuse=reuse
    )


def vectors_of(run: Embedding) -> dict[str, np.ndarray[Any, Any]]:
    return {
        cid: np.asarray(v, dtype=np.float32)
        for shard in sorted(run.vector_dir.glob("part-*.parquet"))
        for cid, v in iter_vectors(shard)
    }


def counting(run: Embedding) -> list[int]:
    """Texts the model is asked for, counted."""
    sent: list[int] = []
    inner = run.embedder

    class Counting:
        spec = inner.spec

        def embed_documents(self, texts: list[str]) -> Any:
            sent.append(len(texts))
            return inner.embed_documents(texts)

    run.embedder = Counting()  # type: ignore[assignment]
    return sent


def test_only_changed_text_is_embedded_and_the_result_is_a_full_embed(
    tmp_path: Path,
) -> None:
    corpus, old = snapshot(tmp_path, "old", edited=set())
    stage(corpus, old).run()
    corpus, new = snapshot(tmp_path, "new", edited={2})

    run = stage(corpus, new, reuse=(old,))
    sent = counting(run)
    report = run.run()

    total = len(list(iter_chunk_shards(new, "fixture")))
    assert 0 < report.reused < total
    # The model saw only the changed chunks, plus the reproduction sample.
    changed = total - report.reused
    assert sum(sent) <= changed + min(16, report.reused)
    assert report.reuse_cosine >= 0.999

    _, full_ws = snapshot(tmp_path, "full", edited={2})
    full = stage(corpus, full_ws)
    full.run()
    mine, theirs = vectors_of(run), vectors_of(full)
    assert set(mine) == set(theirs)
    for cid, v in theirs.items():
        assert np.allclose(mine[cid], v, atol=1e-2), cid


def test_another_vector_space_is_refused(tmp_path: Path) -> None:
    corpus, old = snapshot(tmp_path, "old", edited=set())
    stage(corpus, old, padding_side="right").run()
    corpus, new = snapshot(tmp_path, "new", edited={2})
    with pytest.raises(ReuseError, match="another vector space"):
        stage(corpus, new, reuse=(old,))


def test_an_unfinished_run_is_refused(tmp_path: Path) -> None:
    corpus, old = snapshot(tmp_path, "old", edited=set())
    stage(corpus, old).run(limit=5)
    corpus, new = snapshot(tmp_path, "new", edited={2})
    with pytest.raises(ReuseError, match="did not finish"):
        stage(corpus, new, reuse=(old,))


def test_vectors_the_embedder_does_not_reproduce_are_refused(tmp_path: Path) -> None:
    corpus, old = snapshot(tmp_path, "old", edited=set())
    first = stage(corpus, old)
    first.run()
    rng = np.random.default_rng(0)
    for shard in first.vector_dir.glob("part-*.parquet"):
        ids = [cid for cid, _ in iter_vectors(shard)]
        noise = rng.normal(size=(len(ids), DIM)).astype(np.float32)
        noise /= np.linalg.norm(noise, axis=1, keepdims=True)
        write_vectors(shard, ids, noise, dim=DIM)
    corpus, new = snapshot(tmp_path, "new", edited={2})
    with pytest.raises(ReuseError, match="reproduces"):
        stage(corpus, new, reuse=(old,)).run()
