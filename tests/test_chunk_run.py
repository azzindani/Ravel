"""The chunking driver and the chunk store, end to end on a synthetic corpus.

Markdown sources rather than PDFs, as in `test_extract_run.py`: these assert the
pipeline's behaviour — keys, resume, variant isolation, the parquet round trip — and a
PDF fixture would make them slow without making them stronger.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chunk import Chunking, read_chunks, write_chunks
from chunk.run import STAGE
from extract import Extraction
from runtime import Ledger
from spec import CorpusSpec

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
        }
    )


@pytest.fixture
def workspace(corpus: CorpusSpec, tmp_path: Path) -> Path:
    """A workspace with Phase A already done — chunking never reads source files."""
    ws = tmp_path / "ws"
    Extraction(corpus, workspace=ws).run()
    return ws


# ===========================================================================
def test_a_run_produces_chunk_shards(corpus: CorpusSpec, workspace: Path) -> None:
    report = Chunking(corpus, workspace=workspace).run()

    assert report.chunked == 4
    assert report.chunks > 4
    assert report.failed == 0
    assert report.empty == 0
    assert sorted((workspace / "chunks" / "fixture" / "default").glob("*/part-*.parquet"))


def test_a_second_run_does_nothing(corpus: CorpusSpec, workspace: Path) -> None:
    first = Chunking(corpus, workspace=workspace).run()
    second = Chunking(corpus, workspace=workspace).run()

    assert first.chunks > 0
    assert second.chunks == 0
    assert second.skipped_done == 4
    assert second.shards == 0


def test_variants_do_not_share_rows_or_resume_state(
    corpus: CorpusSpec, workspace: Path
) -> None:
    """! Two chunkings of one corpus are not interchangeable. A variant that resumed
    from another's ledger would skip work it never did, and a bundle mixing their rows
    would be unverifiable (`LOOPHOLES.md` §1)."""
    small = corpus.model_copy(deep=True)
    small.chunk.max_tokens = 64

    a = Chunking(corpus, workspace=workspace, variant="wide").run()
    b = Chunking(small, workspace=workspace, variant="narrow").run()

    assert b.skipped_done == 0
    assert a.chunks and b.chunks

    rows_a = {c.id for c in _rows(workspace, "wide")}
    rows_b = {c.id for c in _rows(workspace, "narrow")}
    assert rows_a.isdisjoint(rows_b)


def test_the_chunker_can_be_overridden_per_run(corpus: CorpusSpec, workspace: Path) -> None:
    run = Chunking(corpus, workspace=workspace, chunker="token", variant="baseline")
    report = run.run()

    assert run.entry.id == "token"
    assert all(c.chunker == "token" for c in _rows(workspace, "baseline"))
    assert report.chunks > 0


def test_the_profile_comes_from_extraction_not_from_routing(
    corpus: CorpusSpec, workspace: Path
) -> None:
    """! Recorded at extraction, read back here. A document structured by one profile
    and chunked by another would have headings saying pasal and a chunker looking for
    something else."""
    Chunking(corpus, workspace=workspace).run()
    assert all(c.profile == "id_regulation@1.0" for c in _rows(workspace, "default"))


def test_provenance_survives_the_round_trip(corpus: CorpusSpec, workspace: Path) -> None:
    Chunking(corpus, workspace=workspace).run()

    rows = _rows(workspace, "default")
    assert rows
    for chunk in rows:
        assert chunk.source_url.startswith("hf://datasets/example/fixture/")
        assert len(chunk.source_sha256) == 64
        assert chunk.body.strip()
        assert chunk.block_ids


def test_identifiers_reach_the_stored_rows(corpus: CorpusSpec, workspace: Path) -> None:
    """`identifier` is what Vera's exact-match bypass keys on. A corpus where it is
    silently null looks healthy by every other measure."""
    report = Chunking(corpus, workspace=workspace).run()

    assert report.identifier_rate > 0.5
    assert any(
        c.identifier and c.identifier.startswith("Perbup ") for c in _rows(workspace, "default")
    )


def test_the_ledger_namespaces_the_stage_by_variant(
    corpus: CorpusSpec, workspace: Path
) -> None:
    Chunking(corpus, workspace=workspace, variant="one").run()

    with Ledger(workspace / "state" / "fixture" / "ledger.db") as ledger:
        assert ledger.counts(f"{STAGE}:one") == {"done": 4}
        assert ledger.counts(f"{STAGE}:two") == {}


# -- the store --------------------------------------------------------------
def test_a_chunk_survives_parquet_unchanged(
    corpus: CorpusSpec, workspace: Path, tmp_path: Path
) -> None:
    original = Chunking(corpus, workspace=workspace).chunks_for(next(_docs(workspace)))
    path = str(tmp_path / "chunks.parquet")
    assert write_chunks(original, path) == len(original)

    assert read_chunks(path) == original


def _rows(workspace: Path, variant: str):  # noqa: ANN202
    from chunk import iter_chunk_shards

    return list(iter_chunk_shards(workspace, "fixture", variant))


def _docs(workspace: Path):  # noqa: ANN202
    from extract import iter_canon

    return iter_canon(workspace, "fixture")


def test_a_reconfigured_variant_writes_a_new_generation(
    corpus: CorpusSpec, workspace: Path
) -> None:
    """! A variant name is not enough to identify an artifact set.

    `ShardWriter` resumes into a fresh shard index rather than overwriting, so
    re-running one variant after a config change appended new rows beside the old ones
    and the reader saw both. A forced re-chunk of the real corpus left two generations
    in one directory — 121,757 rows becoming 243,514 with nothing raised.
    """
    wide = Chunking(corpus, workspace=workspace)
    wide.run()

    narrow_spec = corpus.model_copy(deep=True)
    narrow_spec.chunk.max_tokens = 64
    narrow = Chunking(narrow_spec, workspace=workspace)
    narrow.run()

    assert wide.output_generation != narrow.output_generation
    rows = _rows(workspace, "default")
    assert {c.config_hash for c in rows} == {narrow.config.config_hash}


def test_forcing_a_rerun_does_not_duplicate_rows(corpus: CorpusSpec, workspace: Path) -> None:
    run = Chunking(corpus, workspace=workspace)
    first = run.run()
    run.run(force=True)

    ids = [c.id for c in _rows(workspace, "default")]
    assert len(ids) == len(set(ids)) == first.chunks


def test_a_new_canonical_generation_chunks_into_a_new_directory(
    corpus: CorpusSpec, tmp_path: Path
) -> None:
    """! The generation a run reads must be resolved to a name, not left as "latest".

    Hashing the placeholder made every canonical generation map to one chunk
    directory, so re-extracting and re-chunking appended the new rows beside the old:
    121,757 chunks plus 120,878 in the same folder, silently. A fix that names a moving
    target has not fixed anything.
    """
    workspace = tmp_path / "ws"
    first = Extraction(corpus, workspace=workspace)
    first.run()
    a = Chunking(corpus, workspace=workspace)
    a.run()

    moved = corpus.model_copy(deep=True)
    moved.provenance.base_url = "hf://datasets/example/other/"
    second = Extraction(moved, workspace=workspace)
    second.run()
    b = Chunking(moved, workspace=workspace)
    b.run()

    assert a.generation == first.generation
    assert b.generation == second.generation
    assert a.output_generation != b.output_generation

    # Reading the newest generation must yield exactly one extraction's worth of rows.
    rows = _rows(workspace, "default")
    assert rows
    assert all(c.source_url.startswith("hf://datasets/example/other/") for c in rows)


def test_a_corrected_extraction_is_rechunked(corpus: CorpusSpec, tmp_path: Path) -> None:
    """! A doc_id is the sha256 of the SOURCE, so it is identical across two
    extractions of one file — and a fixed extractor bug produces a completely different
    canonical document under the same doc_id. Without the generation in the key,
    re-chunking a corrected extraction found every key done, wrote nothing, and
    reported success while the old chunks stayed in place."""
    workspace = tmp_path / "ws"
    Extraction(corpus, workspace=workspace).run()
    Chunking(corpus, workspace=workspace).run()

    moved = corpus.model_copy(deep=True)
    moved.provenance.base_url = "hf://datasets/example/other/"
    Extraction(moved, workspace=workspace).run()
    again = Chunking(moved, workspace=workspace).run()

    assert again.skipped_done == 0
    assert again.chunks > 0
