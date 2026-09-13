"""The manifest and the preflight — `ABSORPTION.md` §11 as executable checks.

The finding these exist for: an instruction-aware model was given no instruction for
748,558 documents, because the helper that built it was defined and never called and the
seam that should have applied it returned its argument unchanged. Nothing failed. These
tests are the thing that would have failed.
"""

from __future__ import annotations

import pytest

from embed import (
    EmbedderSpec,
    HashEmbedder,
    InstructionStyle,
    PaddingSide,
    Pooling,
    PreflightError,
    RawHashEmbedder,
    check_instruction_is_applied,
    check_spec_pair,
    cosine,
    preflight,
    render_document,
    render_query,
    spec_for,
)

SAMPLES = [
    "Pasal 1 Dalam Peraturan Bupati ini yang dimaksud dengan Daerah adalah Kabupaten.",
    "Setiap orang berhak atas pengakuan, jaminan, perlindungan dan kepastian hukum.",
]


# -- the manifest refuses what cannot be reproduced --------------------------------


def test_model_version_may_not_float() -> None:
    """! A floating alias can update under a corpus that took GPU-days to build."""
    with pytest.raises(ValueError, match="exact revision"):
        spec_for(model_version="latest")


def test_an_instruction_that_is_never_declared_is_rejected() -> None:
    """The §11 defect has a shape, and the manifest refuses to hold it: a supplied
    instruction with `instruction_style='none'` is an instruction that exists and is
    never applied."""
    with pytest.raises(ValueError, match="never applied"):
        spec_for(doc_instruction="Represent the document")


def test_a_declared_style_with_no_instruction_is_rejected() -> None:
    with pytest.raises(ValueError, match="Declare 'none'"):
        spec_for(instruction_style=InstructionStyle.PREFIX)


# -- the fingerprint moves when a vector would ------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model", "other/model"),
        ("model_version", "2.0.0"),
        ("dim", 16),
        ("pooling", Pooling.MEAN),
        ("padding_side", PaddingSide.RIGHT),
        ("normalize", False),
        ("dtype", "bfloat16"),
        ("provider", "openrouter"),
    ],
)
def test_every_field_that_changes_a_vector_changes_the_fingerprint(
    field: str, value: object
) -> None:
    """! `padding_side` is in this list deliberately. Under last-token pooling it decides
    which token is read; get it wrong and every short text in a batch pools a PAD
    position — a unit-norm vector that means nothing. The two local Qwen3 encoders
    disagree on it, so it is not hypothetical."""
    base = spec_for()
    changed = spec_for(**{field: value})

    assert base.fingerprint() != changed.fingerprint()
    assert not base.same_space(changed)


def test_id_is_not_part_of_the_fingerprint() -> None:
    """Renaming an embedder does not move its vectors, so it must not invalidate a cache."""
    assert spec_for(id="a").fingerprint() == spec_for(id="b").fingerprint()


# -- rendering: one place, and it matches the model's own template ------------------


def test_prefix_style_matches_qwen3_template_including_the_space() -> None:
    """! The lab notebook in §11 wrote `Query:{query}` with no space. A different string
    is a different vector, and the corpus and query sides would have disagreed."""
    spec = spec_for(
        instruction_style=InstructionStyle.PREFIX,
        doc_instruction="Represent the document",
        query_instruction="Given a query, retrieve relevant passages",
    )

    assert render_query(spec, "apa itu pasal") == (
        "Instruct: Given a query, retrieve relevant passages\nQuery: apa itu pasal"
    )


def test_chat_system_style_normalises_trailing_punctuation() -> None:
    """Qwen3-VL appends `.` when the instruction lacks it, so two spellings of the same
    instruction must round-trip to one rendering."""
    bare = spec_for(
        instruction_style=InstructionStyle.CHAT_SYSTEM,
        doc_instruction="Represent the document",
        query_instruction="Represent the query",
    )
    dotted = spec_for(
        instruction_style=InstructionStyle.CHAT_SYSTEM,
        doc_instruction="Represent the document.",
        query_instruction="Represent the query.",
    )

    assert render_document(bare, "x") == render_document(dotted, "x")
    assert render_document(bare, "x")[0] == {
        "role": "system",
        "content": "Represent the document.",
    }


def test_none_style_passes_text_through_unchanged() -> None:
    assert render_document(spec_for(), "Pasal 1") == "Pasal 1"


# -- the check that would have caught the incumbent --------------------------------


def test_declared_instruction_that_is_applied_passes() -> None:
    spec = spec_for(
        instruction_style=InstructionStyle.PREFIX,
        doc_instruction="Represent the document",
        query_instruction="Retrieve relevant passages",
    )

    ok, detail = check_instruction_is_applied(spec)

    assert ok, detail


def test_an_embedder_that_drops_the_instruction_fails_preflight() -> None:
    """! The whole point. `RawHashEmbedder` is the incumbent's `prepare_texts` returning
    `contents` unchanged: a correct-looking embedder that declares an instruction and
    never applies it. Cosine still computes, vectors are still unit-norm, retrieval would
    still return neighbours. The preflight is what says no."""
    spec = spec_for(
        instruction_style=InstructionStyle.PREFIX,
        doc_instruction="Represent the document",
        query_instruction="Retrieve relevant passages",
    )
    honest, dropped = HashEmbedder(spec), RawHashEmbedder(spec)

    report = preflight(honest, dropped, SAMPLES)

    assert not report.passed
    assert "cosine" in report.describe()
    with pytest.raises(PreflightError):
        report.raise_for_status()


def test_no_instruction_declared_is_not_a_failure() -> None:
    """Embedding without an instruction is legitimate — it just has to be *declared*
    rather than defaulted into by omission."""
    spec = spec_for()

    report = preflight(HashEmbedder(spec), HashEmbedder(spec), SAMPLES)

    assert report.passed, report.describe()


# -- space agreement ----------------------------------------------------------------


def test_matching_embedders_round_trip() -> None:
    spec = spec_for(dim=32)

    report = preflight(HashEmbedder(spec), HashEmbedder(spec), SAMPLES)

    assert report.passed, report.describe()


def test_mismatched_padding_side_is_reported_by_name() -> None:
    """The failure names the field, because "vectors differ" sends someone looking at
    the model and the answer is a tokenizer flag."""
    ok, detail = check_spec_pair(spec_for(), spec_for(padding_side=PaddingSide.RIGHT))

    assert not ok
    assert "padding_side" in detail


def test_round_trip_is_skipped_not_faked_when_specs_disagree() -> None:
    """! Comparing vectors from two different spaces produces a number. Reporting that
    number as a cosine result would be worse than reporting nothing."""
    build = HashEmbedder(spec_for(dim=8))
    query = HashEmbedder(spec_for(dim=8, model="other/model"))

    report = preflight(build, query, SAMPLES)
    cosine_check = next(c for c in report.checks if c[0] == "cosine round trip")

    assert not report.passed
    assert "skipped" in cosine_check[2]


def test_preflight_over_no_samples_fails() -> None:
    """A preflight that checked nothing must not report success."""
    spec = spec_for()

    report = preflight(HashEmbedder(spec), HashEmbedder(spec), [])

    assert not report.passed


# -- the test double is honest ------------------------------------------------------


def test_hash_embedder_is_deterministic_across_calls() -> None:
    """Seeded from SHA-256, not `hash()`, so a fingerprint recorded today still matches
    tomorrow and on another platform."""
    spec = spec_for(dim=16)

    assert HashEmbedder(spec).embed_documents(SAMPLES) == HashEmbedder(spec).embed_documents(
        SAMPLES
    )


def test_hash_embedder_respects_normalize() -> None:
    unit = HashEmbedder(spec_for(dim=16)).embed_documents(["x"])[0]

    assert cosine(unit, unit) == pytest.approx(1.0)
    assert sum(v * v for v in unit) == pytest.approx(1.0)


def test_dimension_is_honoured() -> None:
    assert len(HashEmbedder(spec_for(dim=64)).embed_documents(["x"])[0]) == 64


def test_spec_travels_into_the_manifest() -> None:
    """`BUNDLE.md` §3 — the manifest is part of the corpus, not a log line."""
    spec = spec_for(
        instruction_style=InstructionStyle.PREFIX,
        doc_instruction="Represent the document",
        query_instruction="Retrieve relevant passages",
    )
    m = EmbedderSpec.manifest(spec)

    for required in ("fingerprint", "pooling", "padding_side", "normalize",
                     "instruction_style", "doc_instruction", "query_instruction",
                     "model_version", "dim"):
        assert required in m, f"{required} missing from the manifest"
