"""Chunk sizes are counted in the embedder's own tokens (`STANDARD.md` §2).

Hermetic: the tokenizer is a stand-in counter, so no model is downloaded. What is asserted
is the wiring: the corpus's embedder names the tokenizer, it is in the config and so in the
chunk ids, every chunk's `token_count` is its count, and a tokenizer that cannot be loaded
stops the run instead of counting another way.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import chunk.run
import chunk.tokens
from chunk import ChunkConfig, Chunking, iter_chunk_shards
from chunk.tokens import ESTIMATE, counter_for, tokenizer_ref
from extract import Extraction
from spec import CorpusSpec

DOC = """PERATURAN BUPATI BOYOLALI NOMOR {n} TAHUN 2011 TENTANG PAJAK DAERAH

## BAB II

Pasal 1

(1) Wajib Pajak wajib menyelenggarakan pembukuan sesuai ketentuan yang berlaku.

(2) Pembukuan tersebut disimpan selama sepuluh tahun di tempat kedudukannya.
"""

REF = "Example/Model@0123abcd"


def corpus(tmp_path: Path, embed: dict[str, Any] | None) -> CorpusSpec:
    sources = tmp_path / "src"
    sources.mkdir(exist_ok=True)
    for n in range(1, 3):
        (sources / f"perbup_{n}.md").write_text(DOC.format(n=n), encoding="utf-8")
    raw: dict[str, Any] = {
        "id": "fixture",
        "title": "Fixture corpus",
        "sources": {"root": str(sources), "include": ["**/*.md"]},
        "provenance": {"base_url": "hf://datasets/example/fixture/"},
        "extract": {"profile": "id_regulation", "extractors": ["text"]},
        "chunk": {"chunker": "unit", "max_tokens": 256, "min_tokens": 8},
    }
    if embed is not None:
        raw["embed"] = embed
    return CorpusSpec.model_validate(raw)


def local(**extra: Any) -> dict[str, Any]:
    return {
        "kind": "local",
        "id": "example",
        "model": "Example/Model",
        "revision": "0123abcd",
        "dim": 8,
        **extra,
    }


def test_the_embedder_names_the_tokenizer() -> None:
    assert tokenizer_ref(None) == ESTIMATE
    assert tokenizer_ref(CorpusSpec.find("ca_laws").embed) == (
        "Qwen/Qwen3-Embedding-0.6B@97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    )


def test_the_tokenizer_is_in_the_hash_and_the_estimate_keeps_the_old_one() -> None:
    """! A config that counts by estimate hashes as it did before the field existed, so no
    chunk id moves unless the count does."""
    base = ChunkConfig(max_tokens=4096)
    assert base.tokenizer == ESTIMATE and base.config_hash == "745e2063fd707fd9"
    counted = ChunkConfig(max_tokens=4096, tokenizer=REF)
    assert counted.config_hash != base.config_hash


def test_every_chunk_is_counted_by_the_embedders_tokenizer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def fake(ref: str) -> Any:
        seen.append(ref)
        return len  # one token per character: unmistakably not the estimate

    monkeypatch.setattr(chunk.run, "counter_for", fake)
    spec = corpus(tmp_path, local())
    ws = tmp_path / "ws"
    Extraction(spec, workspace=ws).run()
    run = Chunking(spec, workspace=ws)
    assert run.config.tokenizer == REF
    run.run()
    chunks = list(iter_chunk_shards(ws, "fixture"))
    assert chunks and set(seen) == {REF}
    assert all(c.token_count == len(c.body) for c in chunks)


def test_a_tokenizer_that_cannot_load_stops_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    import tokenizers

    def broken(*_: Any, **__: Any) -> Any:
        raise OSError("offline and not cached")

    counter_for.cache_clear()
    monkeypatch.setattr(tokenizers.Tokenizer, "from_pretrained", broken)
    with pytest.raises(RuntimeError, match="chunking stops"):
        chunk.tokens.counter_for(REF)
    counter_for.cache_clear()
