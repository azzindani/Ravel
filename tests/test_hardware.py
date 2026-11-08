"""Hardware profiles: choosing batching for the machine, and measuring instead of guessing."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from embed.factory import spec_of, with_hardware
from embed.tune import scaled, timed_measure, tune
from spec import EmbedSpec, HardwareRegistry, HardwareSpec, Probe, estimate
from spec.hardware import Match

LAPTOP = Probe("cuda", "NVIDIA GeForce RTX 3070 Ti Laptop GPU", 8.59)


def registry() -> HardwareRegistry:
    return HardwareRegistry.load()


def test_the_laptop_it_was_measured_on_matches_its_profile() -> None:
    spec, why = registry().resolve(None, LAPTOP)
    assert spec.id == "rtx-3070-ti-laptop-8gb" and "matches" in why
    assert (spec.batch_items, spec.batch_bytes, spec.batch_square) == (256, 40_000, 80_000_000)


def test_an_unknown_card_gets_a_heuristic_that_says_so() -> None:
    spec, why = registry().resolve(None, Probe("cuda", "NVIDIA GeForce RTX 4090", 24.0))
    assert spec.id.startswith("estimate") and "heuristic" in why
    assert "tune" in spec.notes and not spec.measured


def test_more_memory_means_bigger_batches_up_to_a_cap() -> None:
    small = estimate(Probe("cuda", "x", 8.59))
    big = estimate(Probe("cuda", "x", 24.0))
    huge = estimate(Probe("cuda", "x", 80.0))
    assert small.batch_square < big.batch_square < huge.batch_square
    assert small.batch_items <= big.batch_items <= huge.batch_items <= 128
    assert (small.batch_items, small.batch_bytes) == (32, 40_000)  # the baseline, exactly
    assert small.memory_fraction == 0.9


def test_a_cpu_gets_small_batches() -> None:
    assert estimate(Probe()).batch_items == 8


def test_a_named_profile_wins_over_a_match_and_an_unknown_name_is_refused() -> None:
    reg = HardwareRegistry(
        {
            "a": HardwareSpec(id="a", batch_items=7, match=Match(device="cuda")),
            "b": HardwareSpec(id="b", batch_items=9, match=Match(gpu_contains=["4090"])),
        }
    )
    probe = Probe("cuda", "NVIDIA GeForce RTX 4090", 24.0)
    assert reg.resolve("a", probe)[0].batch_items == 7
    assert reg.resolve(None, probe)[0].id == "b", "the more specific match wins"
    with pytest.raises(KeyError, match="no hardware profile"):
        reg.resolve("nope", probe)


def test_a_duplicate_profile_id_is_refused(tmp_path: Path) -> None:
    (tmp_path / "hardware").mkdir()
    for name in ("one", "two"):
        (tmp_path / "hardware" / f"{name}.yaml").write_text("id: same\n", encoding="utf-8")
    with pytest.raises(ValueError, match="twice"):
        HardwareRegistry.load(tmp_path)


def test_hardware_never_changes_what_a_vector_is() -> None:
    """The whole point: the same corpus on a laptop and on a 4090 is the same product, so the
    fingerprint (and with it every window's cache key) must not move."""
    base = EmbedSpec(
        kind="local", model="m", revision="r", dim=8, pooling="mean", batch_items=1
    )
    laptop = with_hardware(base, registry().get("rtx-3070-ti-laptop-8gb"))
    big = with_hardware(base, estimate(Probe("cuda", "x", 80.0)))
    assert laptop.batch_items != big.batch_items
    fingerprints = {spec_of(c).fingerprint() for c in (base, laptop, big)}
    assert len(fingerprints) == 1


def test_only_a_local_model_is_sized_to_an_accelerator() -> None:
    api = EmbedSpec(
        kind="api", model="m", revision="r", dim=8, base_url="http://x", batch_items=5
    )
    assert with_hardware(api, estimate(Probe("cuda", "x", 24.0))).batch_items == 5


# -- the tuner ---------------------------------------------------------------------------
BASE = HardwareSpec(id="t", batch_items=32, batch_bytes=40_000, batch_square=80_000_000)


class Oom(Exception):
    pass


def curve(points: dict[float, float | None]) -> Callable[[HardwareSpec], float]:
    def measure(hw: HardwareSpec) -> float:
        rate = points[round(hw.batch_items / BASE.batch_items, 2)]
        if rate is None:
            raise Oom("CUDA out of memory")
        return rate

    return measure


def test_the_tuner_keeps_the_knee_and_stops_when_bigger_is_slower() -> None:
    result = tune(curve({0.5: 25, 0.75: 31, 1.0: 34, 1.5: 31, 2.0: 20, 3.0: 5, 4.0: 1}), BASE)
    assert result.best.batch_items == 32
    assert [t.scale for t in result.trials][-1] == 2.0, "it must stop at the regression"


def test_a_memory_cliff_backs_the_choice_off_one_rung() -> None:
    result = tune(curve({0.5: 20, 0.75: 24, 1.0: 30, 1.5: 36, 2.0: None, 3.0: 0, 4.0: 0}), BASE)
    assert result.trials[-1].per_second is None
    assert result.best.batch_items == scaled(BASE, 1.0).batch_items
    assert "backed off" in result.note


def test_a_failure_that_is_not_memory_is_not_swallowed() -> None:
    def broken(hw: HardwareSpec) -> float:
        raise ValueError("a bug")

    with pytest.raises(ValueError, match="a bug"):
        tune(broken, BASE)


def test_if_even_the_smallest_batch_fails_there_is_nothing_to_tune() -> None:
    with pytest.raises(RuntimeError, match="nothing to tune"):
        tune(curve({0.5: None}), BASE, ladder=(0.5,))


def test_the_timed_measure_batches_the_way_the_candidate_asks() -> None:
    seen: list[int] = []
    ticks = iter([0.0, 2.0])
    measure = timed_measure(
        lambda texts: seen.append(len(texts)),
        ["x" * 100] * 10,
        clock=lambda: next(ticks),
    )
    rate = measure(HardwareSpec(id="m", batch_items=4, batch_bytes=10**6))
    assert seen == [4, 4, 2] and rate == 5.0


def test_the_corpus_leaves_batching_to_the_hardware_profile() -> None:
    from spec import CorpusSpec

    embed = CorpusSpec.find("id_reg_markdown").embed
    assert embed is not None
    assert embed.hardware == "auto"
    assert {"batch_items", "batch_bytes", "batch_square"}.isdisjoint(embed.model_fields_set)


def test_the_cli_lists_the_profiles_and_the_choice() -> None:
    from typer.testing import CliRunner

    from cli import app

    result = CliRunner().invoke(app, ["hardware", "list"])
    assert result.exit_code == 0, result.output
    assert "rtx-3070-ti-laptop-8gb" in result.output and "a build here uses" in result.output


# -- serving-backend verification -------------------------------------------------------
def _prefix_spec():  # type: ignore[no-untyped-def]
    from embed import InstructionStyle, spec_for

    return spec_for(
        dim=16,
        id="v",
        instruction_style=InstructionStyle.PREFIX,
        query_instruction="Given a legal question, retrieve passages",
    )


def test_a_backend_that_matches_the_reference_passes() -> None:
    from embed import HashEmbedder, ReferenceHashEmbedder
    from embed.verify import compare_backends

    spec = _prefix_spec()
    result = compare_backends(
        HashEmbedder(spec), ReferenceHashEmbedder(spec), ["Pasal 1", "Pasal 2"], ["siapa?"]
    )
    assert result.passed and result.min_cosine > 0.9999 and result.queries == 1


def test_a_backend_that_drops_the_instruction_is_caught_on_queries() -> None:
    """The silent failure this exists for: well-formed, unit-norm, the right width, wrong."""
    from embed import RawHashEmbedder, ReferenceHashEmbedder
    from embed.verify import compare_backends

    spec = _prefix_spec()
    result = compare_backends(
        RawHashEmbedder(spec), ReferenceHashEmbedder(spec), ["Pasal 1"], ["siapa berwenang?"]
    )
    assert not result.passed and "DISAGREE" in result.describe()


def test_a_backend_is_never_compared_with_itself() -> None:
    from embed import HashEmbedder
    from embed.verify import compare_backends

    same = HashEmbedder(_prefix_spec())
    with pytest.raises(ValueError, match="itself|same"):
        compare_backends(same, same, ["x"])


def test_the_memory_cap_travels_from_the_profile_to_the_embedder_config() -> None:
    base = EmbedSpec(kind="local", model="m", revision="r", dim=8, pooling="mean")
    capped = with_hardware(base, registry().get("rtx-3070-ti-laptop-8gb"))
    assert capped.memory_fraction == 0.85 and base.memory_fraction == 0.0
    assert spec_of(capped).fingerprint() == spec_of(base).fingerprint(), "a cap is not a vector"
