"""The progress a long embedding run reports: the rate, the estimate, and the file."""

from __future__ import annotations

import json
from pathlib import Path

from embed.progress import Progress, Tracker, format_progress, read_progress


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def tracker(clock: Clock, path: Path | None = None, total: int = 1000, **kw: object) -> Tracker:
    return Tracker(
        corpus="c",
        variant="default",
        total=total,
        windows_total=10,
        path=path,
        every=5.0,
        clock=clock,
        wall=clock,
        **kw,  # type: ignore[arg-type]
    )


def test_rate_and_eta_follow_the_clock() -> None:
    clock = Clock()
    t = tracker(clock)
    for _ in range(10):
        clock.t += 1.0
        t.advance(10)
    snap = t.snapshot()
    assert snap.done == 100
    assert round(snap.rate) == 10
    assert snap.eta_seconds is not None and round(snap.eta_seconds) == 90
    assert round(snap.percent, 1) == 10.0


def test_no_estimate_before_there_is_a_rate() -> None:
    snap = tracker(Clock()).snapshot()
    assert snap.eta_seconds is None
    assert snap.rate == 0.0


def test_rate_is_recent_not_the_mean_since_start() -> None:
    """The first batches are the longest texts; a mean from the start would promise a time
    the run never keeps."""
    clock = Clock()
    t = tracker(clock)
    for _ in range(10):  # a slow start: 1 vector/s
        clock.t += 1.0
        t.advance(1)
    clock.t += 300.0  # then a long quiet gap pushes the slow start out of the span
    for _ in range(10):  # then 50 vectors/s
        clock.t += 1.0
        t.advance(50)
    snap = t.snapshot()
    assert snap.rate > 40
    assert snap.mean_rate < snap.rate


def test_skipped_windows_count_as_done_but_not_as_speed() -> None:
    clock = Clock()
    t = tracker(clock)
    t.skip(400)
    clock.t += 10.0
    t.advance(100)
    snap = t.snapshot()
    assert snap.done == 500 and snap.sealed == 400 and snap.this_run == 100
    assert snap.percent == 50.0
    assert round(snap.rate) == 10  # 100 vectors in 10 s, not 500


def test_sealed_is_what_survives_a_crash() -> None:
    clock = Clock()
    t = tracker(clock)
    clock.t += 1.0
    t.advance(30)
    assert t.snapshot().sealed == 0 and t.snapshot().done == 30
    t.seal(30)
    assert t.snapshot().sealed == 30


def test_the_file_is_rewritten_at_most_every_interval(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / "state" / "embed-progress-default.json"
    seen: list[Progress] = []
    t = tracker(clock, path, on_update=seen.append)
    t.emit(force=True)
    for _ in range(4):
        clock.t += 1.0  # four batches inside one 5 s interval
        t.advance(10)
    assert len(seen) == 1
    clock.t += 2.0
    t.advance(10)
    assert len(seen) == 2
    data = read_progress(path)
    assert data is not None and data["done"] == 50 and data["phase"] == "embedding"
    assert not path.with_suffix(".tmp").exists()


def test_a_failure_is_written_with_its_reason(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    t = tracker(Clock(), path)
    t.finish("failed", "OutOfMemoryError: CUDA out of memory")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["phase"] == "failed" and "OutOfMemory" in data["note"]


def test_a_missing_or_torn_file_reads_as_none(tmp_path: Path) -> None:
    assert read_progress(tmp_path / "nope.json") is None
    torn = tmp_path / "torn.json"
    torn.write_text('{"done": 1', encoding="utf-8")
    assert read_progress(torn) is None


def test_the_status_line_says_everything_in_one_row() -> None:
    clock = Clock()
    t = tracker(clock, device=lambda: {"memory_gb": 3.2})
    clock.t += 10.0
    t.advance(100)
    line = format_progress(t.snapshot())
    assert "10.0%" in line and "100/1,000" in line and "10/s" in line
    assert "eta 1m30s" in line and "window 0/10" in line and "gpu 3.2 GB" in line


def test_time_is_attributed_to_the_length_of_what_was_embedded() -> None:
    clock = Clock()
    t = tracker(clock)
    t.advance(10, chars=30_000, seconds=3.0)  # 3000 chars each: the head of a window
    t.advance(30, chars=9_000, seconds=1.0)  # 300 chars each: the body
    b = t.snapshot().buckets
    assert round(b["2-4k"]["per_second"], 1) == 3.3
    assert round(b["<1k"]["per_second"], 1) == 30.0
