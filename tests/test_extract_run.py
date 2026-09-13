"""The extraction driver end to end, on a synthetic corpus.

Markdown rather than PDFs, deliberately: these assert the *pipeline's* behaviour —
keys, resume, provenance, the deferred/failed distinction — and a PDF fixture would
make them slow without making them stronger.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from extract import Extraction
from extract.run import STAGE, stage_key
from runtime import Ledger
from sources import scan
from spec import CorpusSpec, default_registry

DOC = """# PERATURAN BUPATI BOYOLALI NOMOR {n} TAHUN 2011

Menimbang bahwa ketentuan berikut berlaku.

## BAB II

Pasal 1

(1) Wajib Pajak wajib menyelenggarakan pembukuan sesuai ketentuan yang berlaku.

Pasal 2

(2) Ketentuan lebih lanjut diatur dengan Peraturan Bupati.
"""


@pytest.fixture
def corpus(tmp_path: Path) -> CorpusSpec:
    sources = tmp_path / "src"
    sources.mkdir()
    for n in range(1, 6):
        (sources / f"perbup_{n}.md").write_text(DOC.format(n=n), encoding="utf-8")
    return CorpusSpec.model_validate(
        {
            "id": "fixture",
            "title": "Fixture corpus",
            "sources": {"root": str(sources), "include": ["**/*.md"]},
            "provenance": {"base_url": "hf://datasets/example/fixture/"},
            "extract": {
                "profile": "id_regulation",
                "extractors": ["text"],
                "shard_target_mb": 1,
            },
        }
    )


def run(corpus: CorpusSpec, workspace: Path, **kw: object):  # noqa: ANN201
    return Extraction(corpus, workspace=workspace).run(**kw)  # type: ignore[arg-type]


# ===========================================================================
def test_a_run_produces_shards_and_a_ledger(corpus: CorpusSpec, tmp_path: Path) -> None:
    report = run(corpus, tmp_path / "ws")

    assert report.extracted == 5
    assert report.failed == 0
    assert report.coverage == 1.0
    assert sorted((tmp_path / "ws" / "canon" / "fixture").glob("*/part-*.parquet"))


def test_a_second_run_does_nothing(corpus: CorpusSpec, tmp_path: Path) -> None:
    """! The whole claim of the execution model. Killing a run and rerunning it must
    process only what is missing (`EXECUTION.md` §1)."""
    workspace = tmp_path / "ws"
    first = run(corpus, workspace)
    second = run(corpus, workspace)

    assert first.extracted == 5
    assert second.extracted == 0
    assert second.skipped_done == 5
    assert second.shards == 0


def test_force_reprocesses_everything(corpus: CorpusSpec, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    run(corpus, workspace)
    forced = run(corpus, workspace, force=True)
    assert forced.extracted == 5


def test_a_new_document_is_picked_up_without_redoing_the_rest(
    corpus: CorpusSpec, tmp_path: Path
) -> None:
    workspace = tmp_path / "ws"
    run(corpus, workspace)
    (Path(corpus.sources.root) / "perbup_9.md").write_text(DOC.format(n=9), encoding="utf-8")

    second = run(corpus, workspace)
    assert second.extracted == 1
    assert second.skipped_done == 5


def test_provenance_is_carried_from_the_corpus_definition(
    corpus: CorpusSpec, tmp_path: Path
) -> None:
    """! The defect this whole field exists to prevent: all 748,558 rows of the
    incumbent corpus have no source_url, and a citation cannot be back-filled."""
    from extract import iter_canon

    run(corpus, tmp_path / "ws")
    docs = list(iter_canon(tmp_path / "ws", "fixture"))

    assert docs
    for doc in docs:
        assert doc.source.url is not None
        assert doc.source.url.startswith("hf://datasets/example/fixture/")
        assert doc.source.url.endswith(".md")


def test_the_profile_is_applied_during_the_run(corpus: CorpusSpec, tmp_path: Path) -> None:
    from canon import BlockType
    from extract import iter_canon

    run(corpus, tmp_path / "ws")
    doc = next(iter_canon(tmp_path / "ws", "fixture"))

    units = {b.attrs.get("unit") for b in doc.blocks if b.type is BlockType.HEADING}
    assert {"bab", "pasal"} <= units
    assert doc.extraction.params["profile"] == "id_regulation@1.0"


# -- keys -------------------------------------------------------------------
def test_the_key_changes_when_the_profile_changes(corpus: CorpusSpec) -> None:
    """A cache key that omits a parameter lets a stale artifact be reused while the
    manifest claims otherwise — `LOOPHOLES.md` §1."""
    registry = default_registry()
    (source,) = scan(Path(corpus.sources.root), ["**/perbup_1.md"])[0]

    args = {"extractor": "text", "version": "1.0", "corpus_hash": corpus.extraction_hash}
    with_id = stage_key(source, profile=registry.get("id_regulation"), **args)
    with_generic = stage_key(source, profile=registry.get("generic"), **args)

    assert with_id != with_generic


def test_the_key_changes_when_the_corpus_config_changes(corpus: CorpusSpec) -> None:
    """base_url becomes doc.source.url, so it is part of the document's content."""
    moved = corpus.model_copy(deep=True)
    moved.provenance.base_url = "hf://datasets/example/other/"
    assert _key(corpus) != _key(moved)


def test_repackaging_knobs_do_not_invalidate_extracted_documents(
    corpus: CorpusSpec,
) -> None:
    """! The other half of `LOOPHOLES.md` §1, and the easier half to get wrong.

    A key must not omit a parameter that changes the output — but hashing the whole
    corpus spec overshoots: shard sizing and failure tolerance decide how work is
    packaged, never what a document says. Discarding 931 extracted documents because
    a memory cap was lowered teaches people to distrust the cache.
    """
    repackaged = corpus.model_copy(deep=True)
    repackaged.extract.shard_target_mb = 512
    repackaged.extract.shard_max_blocks = 1000
    repackaged.extract.failure_tolerance = 0.5

    assert repackaged.config_hash != corpus.config_hash
    assert _key(repackaged) == _key(corpus)


def _key(corpus: CorpusSpec) -> str:
    (source,) = scan(Path(corpus.sources.root), ["**/perbup_1.md"])[0]
    return stage_key(
        source,
        extractor="text",
        version="1.0",
        profile=default_registry().get(corpus.extract.profile),
        corpus_hash=corpus.extraction_hash,
    )


# -- failure handling -------------------------------------------------------
def test_an_empty_document_is_quarantined_not_counted_as_extracted(
    corpus: CorpusSpec, tmp_path: Path
) -> None:
    (Path(corpus.sources.root) / "blank.md").write_text("   \n\n  \n", encoding="utf-8")
    report = run(corpus, tmp_path / "ws")

    assert report.extracted == 5
    assert report.failed == 1
    assert report.coverage < 1.0


def test_failures_are_recorded_so_a_rerun_does_not_retry_forever(
    corpus: CorpusSpec, tmp_path: Path
) -> None:
    workspace = tmp_path / "ws"
    (Path(corpus.sources.root) / "blank.md").write_text("   \n", encoding="utf-8")
    run(corpus, workspace)

    with Ledger(workspace / "state" / "fixture" / "ledger.db") as ledger:
        counts = ledger.counts(STAGE)
    assert counts["done"] == 5
    assert counts.get("quarantined") == 1


def test_tolerance_is_a_number_the_corpus_sets(corpus: CorpusSpec, tmp_path: Path) -> None:
    for n in range(3):
        (Path(corpus.sources.root) / f"blank_{n}.md").write_text("  \n", encoding="utf-8")
    report = run(corpus, tmp_path / "ws")

    assert report.failed == 3
    assert not report.within(0.02)
    assert report.within(0.5)


def test_identical_bytes_enter_the_corpus_once(corpus: CorpusSpec, tmp_path: Path) -> None:
    """! A document is its bytes. Two paths holding the same bytes are one document,
    and counting it twice is how 931 documents became 1,862 in a real run — silently,
    with nothing overwritten and nothing raised."""
    root = Path(corpus.sources.root)
    (root / "copy_of_1.md").write_text((root / "perbup_1.md").read_text(encoding="utf-8"),
                                       encoding="utf-8")
    report = run(corpus, tmp_path / "ws")

    assert report.considered == 6
    assert report.extracted == 5
    assert report.duplicates == 1
    assert report.coverage == 1.0


def test_a_new_generation_does_not_read_the_old_one(
    corpus: CorpusSpec, tmp_path: Path
) -> None:
    """! `ShardWriter` resumes into a fresh shard index rather than overwriting, which
    is right for resume and wrong when the keys change. Bumping an extractor version
    appended new shards beside the stale ones and the next stage read every document
    twice. A generation directory makes a superseded extraction inert, not invisible."""
    from extract import iter_canon

    workspace = tmp_path / "ws"
    first = Extraction(corpus, workspace=workspace)
    first.run()

    moved = corpus.model_copy(deep=True)
    moved.provenance.base_url = "hf://datasets/example/other/"
    second = Extraction(moved, workspace=workspace)
    second.run()

    assert first.generation != second.generation
    assert len(list(iter_canon(workspace, "fixture", second.generation))) == 5
    assert all(
        d.source.url.startswith("hf://datasets/example/other/")
        for d in iter_canon(workspace, "fixture", second.generation)
    )
