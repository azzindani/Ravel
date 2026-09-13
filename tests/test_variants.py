"""Variants: hashed configurations whose cache keys *are* the reuse policy.

    A cache key contains every parameter that can change the output. A variant silently
    inheriting another's artifacts because a key was incomplete produces a bundle whose
    manifest is a lie — the failure this whole design exists to prevent.
                                                                    — `VARIANTS.md` §4

So most of this file is about keys moving when they should and staying put when they
should, which is the same assertion from both sides.
"""

from __future__ import annotations

import pytest

from variants import (
    STAGES,
    VariantError,
    VariantSpec,
    check_baseline_present,
    matrix,
    reuse_plan,
)


def variant(name: str = "pasal", **stages: dict) -> VariantSpec:
    config = {
        "extract": {"profile": "id_regulation@1.0", "extractor": "native"},
        "chunk": {"chunker": "unit", "max_tokens": 512},
        "enrich": {"enrichers": ["bm25", "factors"]},
        "embed": {"id": "qwen3-0.6b", "template": "body", "dim": 1024},
        "cluster": {"k": 400, "seed": 42},
    }
    config.update(stages)
    return VariantSpec(corpus_id="id_legal", name=name, config=config)


# -- the keys ---------------------------------------------------------------------------


def test_the_reuse_table_falls_out_of_the_key_graph() -> None:
    """! `VARIANTS.md` §4's table, reproduced without anyone maintaining it. Changing the
    embedder must leave chunk and enrich reusable and invalidate only what is downstream."""
    base = variant()
    reembedded = base.with_overrides(embed={"id": "qwen3-8b"})

    shared = base.shares_with(reembedded)

    assert shared == ("extract", "chunk", "enrich")
    assert "embed" not in shared and "cluster" not in shared


def test_a_new_enricher_does_not_invalidate_the_vectors() -> None:
    base = variant()
    enriched = base.with_overrides(enrich={"enrichers": ["bm25", "factors", "ner"]})

    assert enriched.shares_with(base) == ("extract", "chunk", "embed", "cluster")


def test_a_different_chunker_invalidates_everything_below_it() -> None:
    base = variant()
    rechunked = base.with_overrides(chunk={"chunker": "token", "size": 512})

    assert rechunked.shares_with(base) == ("extract",)


def test_a_change_upstream_moves_every_key_downstream() -> None:
    """! The property that makes the chain worth having. A different extractor is a
    different corpus, and nothing built on it may be reused."""
    base = variant()
    reextracted = base.with_overrides(extract={"extractor": "smoldocling"})

    assert reextracted.shares_with(base) == ()


def test_clustering_hangs_off_the_vectors_not_beside_them() -> None:
    """Centroids are means of vectors: swap the vectors and the centroids describe a space
    that no longer exists. `reembed.py` says the same thing from Vera's side."""
    base = variant()
    reembedded = base.with_overrides(embed={"dim": 4096})

    assert base.stage_key("cluster") != reembedded.stage_key("cluster")
    assert base.config["cluster"] == reembedded.config["cluster"], "unchanged, yet re-keyed"


def test_the_name_is_not_part_of_the_hash() -> None:
    """! `VARIANTS.md` §3: two variants that hash the same are the same variant, whatever
    they are called."""
    assert variant("pasal").config_hash == variant("renamed").config_hash


def test_two_corpora_with_identical_configs_are_not_the_same_variant() -> None:
    a = variant()
    b = VariantSpec(corpus_id="other_corpus", name="pasal", config=a.config)

    assert a.config_hash != b.config_hash


def test_the_hash_is_stable_across_key_order() -> None:
    """A config built by merging dicts in a different order is the same config."""
    a = variant()
    reordered = VariantSpec(
        corpus_id="id_legal",
        name="pasal",
        config={stage: a.config[stage] for stage in reversed(STAGES)},
    )

    assert a.config_hash == reordered.config_hash


# -- what a variant refuses ---------------------------------------------------------------


def test_an_unresolved_value_is_refused() -> None:
    """! The way a parameter escapes a cache key is by not being in it. A `None` means
    "whatever the implementation does today", so two runs that behave differently would
    share a key."""
    with pytest.raises(VariantError, match="unresolved value at embed.dim"):
        variant(embed={"id": "q", "template": "body", "dim": None})


def test_an_unresolved_value_is_found_however_deep_it_is() -> None:
    with pytest.raises(VariantError, match=r"cluster.anchors\[1\].threshold"):
        variant(cluster={"k": 400, "seed": 1, "anchors": [{"id": "a"}, {"threshold": None}]})


def test_a_missing_stage_is_refused_rather_than_defaulted() -> None:
    """! Omitting a stage and stating it empty are different configurations that would
    otherwise hash alike."""
    with pytest.raises(VariantError, match="declares no \\['enrich'\\]"):
        VariantSpec(
            corpus_id="id_legal",
            name="x",
            config={s: {} for s in STAGES if s != "enrich"},
        )


def test_a_stage_nothing_reads_is_refused() -> None:
    """A parameter filed under an unknown stage never reaches a cache key."""
    with pytest.raises(VariantError, match="unknown stages"):
        VariantSpec(
            corpus_id="id_legal",
            name="x",
            config={**{s: {} for s in STAGES}, "postprocess": {"trim": True}},
        )


# -- the matrix ---------------------------------------------------------------------------


def test_a_matrix_expands_to_the_product() -> None:
    built = matrix(
        variant(),
        {"chunk": {"chunker": ["unit", "token"]}, "embed": {"template": ["body", "path+body"]}},
    )

    assert len(built) == 4
    assert len({v.config_hash for v in built}) == 4


def test_a_matrix_is_ordered_so_shared_work_is_adjacent() -> None:
    """! Sorting by the key sequence puts variants sharing an upstream stage next to each
    other, so a sweep walks through one generation of chunks instead of four. Same total
    work; the difference is what the disk has to hold at once."""
    built = matrix(
        variant(),
        {"chunk": {"chunker": ["unit", "token"]}, "embed": {"template": ["body", "path+body"]}},
    )
    chunk_keys = [v.stage_key("chunk") for v in built]

    runs = sum(1 for a, b in zip(chunk_keys, chunk_keys[1:], strict=False) if a != b) + 1
    assert runs == 2, "each chunking should be contiguous, not revisited"


def test_the_reuse_plan_says_what_a_sweep_actually_costs() -> None:
    """Sixteen variants over two chunkings cost two chunkings, and the number should be
    visible before the sweep starts rather than inferred from the disk afterwards."""
    built = matrix(
        variant(),
        {
            "embed": {"template": ["body", "path+body"], "dim": [1024, 4096]},
        },
    )

    plan = reuse_plan(built)

    assert plan["variants"] == 4
    assert plan["distinct"]["extract"] == 1
    assert plan["distinct"]["chunk"] == 1
    assert plan["distinct"]["embed"] == 4
    assert plan["total_stage_runs"] < plan["naive_stage_runs"]


def test_an_empty_matrix_is_the_base_variant() -> None:
    base = variant()

    assert matrix(base, {}) == (base,)


def test_an_unknown_stage_in_a_matrix_is_refused() -> None:
    with pytest.raises(VariantError, match="unknown stage"):
        matrix(variant(), {"rerank": {"model": ["a", "b"]}})


def test_a_sweep_without_the_baseline_arm_is_reported() -> None:
    """! `VARIANTS.md` §5. Four new ideas measured only against each other tell you which
    of the four is best and nothing about whether any beats what is in production."""
    built = matrix(variant("newidea"), {"embed": {"dim": [1024, 4096]}})

    ok, detail = check_baseline_present(built, "token512")

    assert not ok
    assert "in production" in detail
    assert check_baseline_present([*built, variant("token512")], "token512")[0]
