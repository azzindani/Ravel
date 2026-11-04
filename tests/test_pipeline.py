"""The profile's `pipeline:` section: defaults, derived text, and the expected-output contract.

`docs/PIPELINE.md`. The profile says what the layers after extraction do for a kind of
document; these tests pin that the corpus can still override it, that derived text never
reaches `body`, and that a build below its profile's floors is reported rather than trusted.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from canon import Block, BlockType, CanonicalDoc
from chunk import (
    Builder,
    Chunk,
    ChunkConfig,
    Chunking,
    check,
    embed_text,
    get,
    read_chunks,
    search_text,
    write_chunks,
)
from extract import Extraction
from spec import (
    CHUNK_FIELDS,
    CorpusSpec,
    ExpectedSpec,
    Profile,
    ProfileSpec,
    Registry,
    default_registry,
)
from spec.corpus import ChunkSpec

MD = "id_regulation_md@1.1"


@pytest.fixture(scope="module")
def md() -> Profile:
    return default_registry().get(MD)


def with_pipeline(profile: Profile, **pipeline: object) -> Profile:
    raw = profile.spec.model_dump(mode="json")
    raw["pipeline"] = {**raw["pipeline"], **pipeline}
    return Profile.compile(ProfileSpec.model_validate(raw))


def chunk(**fields: object) -> Chunk:
    base: dict[str, object] = {
        "id": "x",
        "doc_id": "d",
        "body": "Peraturan ini mulai berlaku pada tanggal diundangkan.",
        "token_count": 9,
        "source_title": "t",
        "source_url": "hf://x/t.md",
        "source_sha256": "0" * 64,
        "chunker": "unit",
        "chunker_version": "1.1",
        "config_hash": "c",
    }
    return Chunk.model_validate({**base, **fields})


# ---------------------------------------------------------------------------
# precedence: engine default < profile < corpus
# ---------------------------------------------------------------------------
def test_the_profile_supplies_what_the_corpus_does_not_state(md: Profile) -> None:
    config = ChunkSpec().to_config(md)
    assert (config.max_tokens, config.min_tokens) == (4096, 8)
    assert config.params == {"subunits": False}


def test_a_field_the_corpus_states_beats_the_profile(md: Profile) -> None:
    spec = ChunkSpec.model_validate({"max_tokens": 1024})
    config = spec.to_config(md)
    assert config.max_tokens == 1024
    assert config.min_tokens == 8  # still the profile's


def test_a_corpus_default_is_not_a_statement(md: Profile) -> None:
    # `ChunkSpec().max_tokens` is 512, but nobody wrote it, so it must not beat the profile.
    assert ChunkSpec().max_tokens == 512
    assert ChunkSpec().to_config(md).max_tokens == 4096


def test_params_merge_with_the_corpus_winning(md: Profile) -> None:
    spec = ChunkSpec.model_validate({"params": {"subunits": True, "split_on": ["pasal"]}})
    assert spec.to_config(md).params == {"subunits": True, "split_on": ["pasal"]}


def test_without_a_profile_the_corpus_alone_decides() -> None:
    assert ChunkSpec().to_config(None) == ChunkConfig()


def test_the_chunker_follows_the_same_precedence(md: Profile) -> None:
    assert ChunkSpec().resolved_chunker(md) == "unit"
    assert ChunkSpec.model_validate({"chunker": "heading"}).resolved_chunker(md) == "heading"
    assert ChunkSpec().resolved_chunker(None) == "heading"


# ---------------------------------------------------------------------------
# derived text: the body is never touched
# ---------------------------------------------------------------------------
def test_the_embedder_is_shown_the_context_then_the_body(md: Profile) -> None:
    c = chunk(
        regulation_type="PERATURAN BUPATI",
        regulation_number="26",
        year=2011,
        about="PENJABARAN ANGGARAN",
        chapter="BAB I - KETENTUAN UMUM",
        article="Pasal 5",
    )
    text = embed_text(c, md)
    assert text == (
        "PERATURAN BUPATI 26/2011 › PENJABARAN ANGGARAN › BAB I - KETENTUAN UMUM › Pasal 5"
        f"\n\n{c.body}"
    )
    assert c.body == "Peraturan ini mulai berlaku pada tanggal diundangkan."


def test_a_part_with_no_information_is_dropped_not_left_as_a_hole(md: Profile) -> None:
    c = chunk(chapter="N/A", article="Pasal 5")  # no type, no subject, and N/A is a label
    assert embed_text(c, md) == f"Pasal 5\n\n{c.body}"


def test_with_nothing_to_say_the_body_is_returned_unchanged(md: Profile) -> None:
    c = chunk()
    assert embed_text(c, md) == c.body
    assert embed_text(c, None) == c.body


def test_a_template_may_not_name_a_field_a_chunk_lacks() -> None:
    with pytest.raises(ValidationError, match="embed_text names 'nonsense'"):
        with_pipeline(
            default_registry().get(MD),
            derive={"embed_text": {"context": ["{nonsense}"]}},
        )


def test_search_text_repairs_damage_in_the_matched_copy_only(md: Profile) -> None:
    c = chunk(body="Pasal5 menetapkan denda Rp1O0.000 bagi pelanggar.")
    assert search_text(c, md) == "Pasal 5 menetapkan denda Rp100.000 bagi pelanggar."
    assert c.body.startswith("Pasal5")  # what is cited is what the source printed


def test_every_name_a_profile_may_use_is_a_real_chunk_field() -> None:
    assert set(Chunk.model_fields) >= CHUNK_FIELDS


# ---------------------------------------------------------------------------
# the expected-output contract
# ---------------------------------------------------------------------------
def test_a_build_that_meets_its_floors_has_no_violations() -> None:
    expected = ExpectedSpec(coverage={"about": 0.8}, max_row_tokens=100, oversize_share=0.01)
    assert check(expected, present=Counter(about=90), chunks=100, oversize=1) == []


def test_a_coverage_floor_is_reported_with_its_numbers() -> None:
    expected = ExpectedSpec(coverage={"about": 0.8})
    (violation,) = check(expected, present=Counter(about=50), chunks=100, oversize=0)
    assert violation.what == "coverage[about]"
    assert "50.0%" in violation.detail and "80.0%" in violation.detail


def test_a_build_with_no_chunks_fails_every_floor() -> None:
    expected = ExpectedSpec(coverage={"about": 0.1, "article": 0.1})
    assert len(check(expected, present=Counter(), chunks=0, oversize=0)) == 2


def test_oversize_is_a_share_so_one_run_on_block_does_not_fail_a_corpus() -> None:
    expected = ExpectedSpec(max_row_tokens=100, oversize_share=0.001)
    assert check(expected, present=Counter(), chunks=10_000, oversize=3) == []
    assert check(expected, present=Counter(), chunks=10_000, oversize=30)


@pytest.mark.parametrize("floor", [-0.1, 1.5])
def test_a_floor_outside_zero_to_one_is_refused(floor: float) -> None:
    with pytest.raises(ValidationError, match="within 0..1"):
        ExpectedSpec(coverage={"about": floor})


def test_a_floor_on_an_unknown_field_is_refused() -> None:
    with pytest.raises(ValidationError, match="expected.coverage names 'bogus'"):
        ExpectedSpec(coverage={"bogus": 0.5})


# ---------------------------------------------------------------------------
# cache keys
# ---------------------------------------------------------------------------
def test_changing_a_label_does_not_invalidate_extraction(md: Profile) -> None:
    raw = md.spec.model_dump(mode="json")
    for unit in raw["structure"]["units"]:
        unit["role"] = None
    raw["structure"]["opening"] = "Something else"
    relabelled = ProfileSpec.model_validate(raw)
    assert relabelled.structure_hash == md.spec.structure_hash
    assert relabelled.config_hash != md.spec.config_hash  # chunking still re-keys


def test_changing_a_pattern_does_invalidate_extraction(md: Profile) -> None:
    raw = md.spec.model_dump(mode="json")
    raw["structure"]["units"][0]["pattern"] = "^(?:NEVER)$"
    assert ProfileSpec.model_validate(raw).structure_hash != md.spec.structure_hash


def test_the_pipeline_section_is_in_the_chunking_key_but_not_the_extraction_key(
    md: Profile,
) -> None:
    changed = with_pipeline(md, chunk={"max_tokens": 2048})
    assert changed.config_hash != md.config_hash
    assert changed.structure_hash == md.structure_hash


# ---------------------------------------------------------------------------
# routing and the driver
# ---------------------------------------------------------------------------
def test_routing_considers_only_the_newest_version_of_each_id() -> None:
    registry = default_registry()
    old, new = registry.get("id_regulation_md@1.0"), registry.get(MD)
    sample = "PERATURAN BUPATI X NOMOR 1 TAHUN 2010\nDENGAN RAHMAT TUHAN YANG MAHA ESA"
    for order in ([old, new], [new, old]):
        routed = Registry(order).route(mime="text/markdown", sample=sample)
        assert routed is not None and routed.ref == MD


DOC = """PERATURAN BUPATI BOYOLALI NOMOR {n} TAHUN 2011 TENTANG PAJAK DAERAH

BAB I

KETENTUAN UMUM

Pasal 1

Wajib Pajak wajib menyelenggarakan pembukuan sesuai ketentuan yang berlaku bagi daerah.

Pasal 2

Ketentuan lebih lanjut mengenai tata cara diatur dengan Peraturan Bupati tersendiri nanti.
"""


@pytest.fixture
def corpus(tmp_path: Path) -> CorpusSpec:
    sources = tmp_path / "src"
    sources.mkdir()
    for n in range(1, 4):
        (sources / f"perbup_{n}.md").write_text(DOC.format(n=n), encoding="utf-8")
    return CorpusSpec.model_validate(
        {
            "id": "fixture",
            "title": "Fixture corpus",
            "sources": {"root": str(sources), "include": ["**/*.md"]},
            "provenance": {"base_url": "hf://datasets/example/fixture/"},
            "extract": {"profile": "id_regulation_md", "extractors": ["text"]},
        }
    )


@pytest.fixture
def workspace(corpus: CorpusSpec, tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    Extraction(corpus, workspace=ws).run()
    return ws


def test_the_driver_takes_its_chunker_and_budget_from_the_pinned_profile(
    corpus: CorpusSpec, workspace: Path
) -> None:
    run = Chunking(corpus, workspace=workspace)
    assert run.entry.id == "unit"
    assert run.config.max_tokens == 4096
    assert run.config.params == {"subunits": False}


def test_the_corpus_can_still_override_the_profile(corpus: CorpusSpec, workspace: Path) -> None:
    stated = corpus.model_copy(
        update={"chunk": ChunkSpec.model_validate({"chunker": "heading", "max_tokens": 256})}
    )
    run = Chunking(stated, workspace=workspace)
    assert run.entry.id == "heading"
    assert run.config.max_tokens == 256


def test_a_build_below_the_profiles_floors_says_so(corpus: CorpusSpec, workspace: Path) -> None:
    report = Chunking(corpus, workspace=workspace).run()
    assert report.chunks > 0
    # These documents carry a title and no signing block, so no date can be read.
    assert "coverage[effective_date]" in {v.what for v in report.violations}
    # What the fixture does carry is not reported as missing.
    assert "coverage[regulation_type]" not in {v.what for v in report.violations}


def test_a_chapter_title_that_became_a_label_is_not_also_a_chunk(
    corpus: CorpusSpec, workspace: Path
) -> None:
    Chunking(corpus, workspace=workspace).run()
    shards = sorted((workspace / "chunks" / "fixture" / "default").glob("*/part-*.parquet"))
    chunks = [c for shard in shards for c in read_chunks(str(shard))]
    assert chunks
    assert all(c.body != "KETENTUAN UMUM" for c in chunks)
    assert {c.chapter for c in chunks if c.article == "Pasal 1"} == {"BAB I - KETENTUAN UMUM"}


# ---------------------------------------------------------------------------
# seq
# ---------------------------------------------------------------------------
def _blocks() -> list[Block]:
    texts = ["Satu dua tiga empat lima enam tujuh delapan sembilan sepuluh."] * 3
    return [
        Block(id=f"b{i}", type=BlockType.PARAGRAPH, text=f"{t} {i}", page=1, reading_order=i)
        for i, t in enumerate(texts)
    ]


def test_seq_orders_a_documents_chunks_from_zero(doc: CanonicalDoc, tmp_path: Path) -> None:
    built = doc.model_copy(deep=True)
    built.source.url = "hf://x/y.md"
    built.blocks = _blocks()
    built.tables = []
    entry = get("token")
    builder = Builder(
        doc=built, chunker=entry.id, version=entry.version, config=ChunkConfig(max_tokens=32)
    )
    chunks = list(entry.fn(built, builder))
    assert [c.seq for c in chunks] == list(range(len(chunks)))

    path = str(tmp_path / "c.parquet")
    write_chunks(chunks, path)
    assert [c.seq for c in read_chunks(path)] == list(range(len(chunks)))
