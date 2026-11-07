"""What a long embedding run reports while it runs, and where it writes it.

A GPU run is hours long and the unit that is sealed to disk is a window of tens of thousands
of vectors, so "wait for the next shard" is not progress, it is a blind wait. This module is
the other channel: a counter fed once per batch, a rate over the last couple of minutes, an
estimate of what is left, and a small JSON file rewritten atomically so any other process
(`ravel embed --status`, a dashboard, an agent) can read it without touching the run.

The numbers are honest about what they are:

* `done` counts vectors that are finished, including windows a previous run sealed, so the
  percentage means "of the corpus", not "of this run".
* `rate` is measured over a sliding span, not from the start: the first batches are the longest
  texts and a mean from the start would promise a time the run never keeps. `eta_seconds` is
  `None` until there is a rate to divide by, never a guess.
* `sealed` is what survives a crash. `done - sealed` is the work in the current window that a
  kill would lose.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["Progress", "Tracker", "format_progress", "read_progress"]

SPAN_SECONDS = 120.0


@dataclass
class Progress:
    """One reading of the run. Everything a status line needs, nothing it has to compute."""

    corpus: str
    variant: str
    phase: str = "starting"  # starting | embedding | done | failed
    pid: int = 0
    total: int = 0
    done: int = 0  # vectors finished, sealed windows from earlier runs included
    sealed: int = 0  # vectors in windows already on disk and in the ledger
    this_run: int = 0
    windows: int = 0
    windows_total: int = 0
    rate: float = 0.0  # vectors per second over the last SPAN_SECONDS
    mean_rate: float = 0.0  # vectors per second since this run started
    eta_seconds: float | None = None
    elapsed: float = 0.0
    started: float = 0.0
    updated: float = 0.0
    longest_batch_chars: int = 0
    device: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    @property
    def percent(self) -> float:
        return 100.0 * self.done / self.total if self.total else 0.0

    def to_json(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["percent"] = round(self.percent, 3)
        return data


def format_progress(p: Progress) -> str:
    """One line: `  3.2%   54,000/1,716,628   312/s   eta 1h46m   window 3/86`."""
    eta = "eta ?" if p.eta_seconds is None else f"eta {_duration(p.eta_seconds)}"
    gpu = ""
    if p.device.get("memory_gb") is not None:
        gpu = f"   gpu {p.device['memory_gb']:.1f} GB"
    return (
        f"{p.percent:5.1f}%   {p.done:,}/{p.total:,}   {p.rate:,.0f}/s   {eta}"
        f"   window {p.windows}/{p.windows_total}{gpu}"
    )


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{rest % 60:02d}s"
    return f"{rest}s"


def read_progress(path: Path | str) -> dict[str, Any] | None:
    """The last reading, or None when there is no run yet or the file is mid-replace."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    return data if isinstance(data, dict) else None


class Tracker:
    """Fed once per batch, tells the file and the screen how far along the run is.

    The clock is injected so the rate and the estimate are tested against a clock that moves
    exactly as the test says, not against sleeping.
    """

    def __init__(
        self,
        *,
        corpus: str,
        variant: str,
        total: int,
        windows_total: int,
        path: Path | str | None = None,
        every: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        device: Callable[[], dict[str, Any]] | None = None,
        on_update: Callable[[Progress], None] | None = None,
    ) -> None:
        self.path = Path(path) if path else None
        self.every = every
        self._clock = clock
        self._wall = wall
        self._device = device
        self._on_update = on_update
        self._t0 = clock()
        self._last_emit = float("-inf")
        self._samples: deque[tuple[float, int]] = deque([(self._t0, 0)])
        self.state = Progress(
            corpus=corpus,
            variant=variant,
            pid=os.getpid(),
            total=total,
            windows_total=windows_total,
            started=wall(),
            updated=wall(),
        )

    # -- inputs ----------------------------------------------------------------------
    def skip(self, vectors: int) -> None:
        """A window an earlier run sealed: done, and not part of this run's speed."""
        self.state.done += vectors
        self.state.sealed += vectors
        self.state.windows += 1
        self.emit()

    def advance(self, vectors: int, chars: int = 0) -> None:
        """One batch finished."""
        s = self.state
        s.phase = "embedding"
        s.done += vectors
        s.this_run += vectors
        s.longest_batch_chars = max(s.longest_batch_chars, chars)
        now = self._clock()
        self._samples.append((now, s.this_run))
        while len(self._samples) > 2 and now - self._samples[0][0] > SPAN_SECONDS:
            self._samples.popleft()
        self.emit()

    def seal(self, vectors: int) -> None:
        """A window is on disk and in the ledger: this much survives a crash."""
        self.state.sealed += vectors
        self.state.windows += 1
        self.emit(force=True)

    def finish(self, phase: str = "done", note: str = "") -> None:
        self.state.phase = phase
        self.state.note = note
        self.emit(force=True)

    # -- output ----------------------------------------------------------------------
    def snapshot(self) -> Progress:
        s = self.state
        now = self._clock()
        s.elapsed = now - self._t0
        s.updated = self._wall()
        s.mean_rate = s.this_run / s.elapsed if s.elapsed > 0 else 0.0
        first_t, first_n = self._samples[0]
        last_t, last_n = self._samples[-1]
        s.rate = (last_n - first_n) / (last_t - first_t) if last_t > first_t else 0.0
        remaining = max(0, s.total - s.done)
        s.eta_seconds = remaining / s.rate if s.rate > 0 else (0.0 if not remaining else None)
        if self._device is not None:
            try:
                s.device = self._device()
            except Exception:  # a status probe must never stop a run
                s.device = {}
        return s

    def emit(self, force: bool = False) -> None:
        now = self._clock()
        if not force and now - self._last_emit < self.every:
            return
        self._last_emit = now
        snap = self.snapshot()
        if self.path is not None:
            self._write(snap)
        if self._on_update is not None:
            self._on_update(snap)

    def _write(self, snap: Progress) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap.to_json(), indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
