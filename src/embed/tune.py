"""Measure the batching a machine can take, instead of guessing it.

`spec.hardware.estimate` scales one measured baseline by memory. That is a starting point; a
card's real limit depends on its clocks, its driver and the model, and the failure when a
guess is too bold is not a slow run but a memory cliff (128 items with 60,000 bytes ran at
4.7 vectors/s where 32 items ran at 34). So the tuner climbs a ladder of scales from the
estimate, times each rung on real texts, stops at the first failure or the first clear
regression, and backs off one rung from a cliff. The measurement is injected, so the search
is tested without a GPU.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from runtime.batching import batched
from spec.hardware import HardwareSpec

__all__ = ["Trial", "TuneResult", "scaled", "timed_measure", "tune"]

LADDER = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0)
#: A rung this much slower than the best so far ends the climb: past the knee, bigger only
#: costs memory.
REGRESSION = 0.90


@dataclass(frozen=True, slots=True)
class Trial:
    scale: float
    items: int
    bytes: int
    square: int
    per_second: float | None
    """`None` when the rung failed (out of memory)."""
    error: str = ""


@dataclass
class TuneResult:
    best: HardwareSpec
    trials: list[Trial] = field(default_factory=list)
    note: str = ""


def scaled(base: HardwareSpec, scale: float, *, max_items: int = 256) -> HardwareSpec:
    return base.model_copy(
        update={
            "batch_items": min(max_items, max(1, round(base.batch_items * scale))),
            "batch_bytes": max(1, round(base.batch_bytes * scale)),
            "batch_square": round(base.batch_square * scale),
        }
    )


def is_out_of_memory(exc: BaseException) -> bool:
    return "outofmemory" in type(exc).__name__.lower() or "out of memory" in str(exc).lower()


def tune(
    measure: Callable[[HardwareSpec], float],
    base: HardwareSpec,
    *,
    ladder: Sequence[float] = LADDER,
) -> TuneResult:
    """Climb the ladder; return the best rung that is not one step from a cliff."""
    trials: list[Trial] = []
    best_rate = 0.0
    for scale in ladder:
        candidate = scaled(base, scale)
        sizes = (candidate.batch_items, candidate.batch_bytes, candidate.batch_square)
        try:
            rate = float(measure(candidate))
        except Exception as exc:  # noqa: BLE001 - a memory failure at this size ends the climb
            if not is_out_of_memory(exc):
                raise
            trials.append(Trial(scale, *sizes, None, "out of memory"))
            break
        trials.append(Trial(scale, *sizes, rate))
        if best_rate and rate < REGRESSION * best_rate:
            break
        best_rate = max(best_rate, rate)

    ok = [t for t in trials if t.per_second is not None]
    if not ok:
        raise RuntimeError("every batch size failed, even the smallest: nothing to tune")
    top = max(ok, key=lambda t: t.per_second or 0.0)
    note = ""
    cliff = trials[-1].per_second is None
    if cliff and top is ok[-1] and len(ok) > 1:
        top = ok[-2]
        note = "backed off one rung from a memory cliff"
    chosen = scaled(base, top.scale)
    return TuneResult(
        best=chosen.model_copy(update={"measured": {"vectors_per_second": top.per_second}}),
        trials=trials,
        note=note,
    )


def timed_measure(
    embed: Callable[[list[str]], object],
    texts: Sequence[str],
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> Callable[[HardwareSpec], float]:
    """A measurement over real texts: longest first, in the batches the candidate asks for."""
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]), reverse=True)

    def measure(hw: HardwareSpec) -> float:
        done = 0
        began = clock()
        for batch in batched(
            order,
            text_of=lambda i: texts[i],
            max_bytes=hw.batch_bytes,
            max_items=hw.batch_items,
            max_square=hw.batch_square,
        ):
            embed([texts[i] for i in batch])
            done += len(batch)
        return done / max(clock() - began, 1e-9)

    return measure
