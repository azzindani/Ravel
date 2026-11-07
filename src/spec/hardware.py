"""Hardware profiles: how a corpus is embedded on *this* machine, kept out of the corpus.

A corpus definition says what to build: the model, the text layout, the chunking. Nothing in
it may depend on the card the build happens to run on, because the same corpus is built on a
laptop with 8 GB, on a rented 4090, and on a datacenter GPU, and the vectors must be the same
product wherever they were made. What differs between those machines is only *how fast and in
what pieces*: how many texts go through the model at once, and on which device.

That is this module. A `HardwareSpec` holds the batching and device settings; none of them
enters the embedder's fingerprint or a window's cache key, so vectors made on one machine are
reused on another (`embed/run.py`, `window_key`).

Where a spec comes from, most specific first:

1. **Named**: `--hardware rtx-3070-ti-laptop-8gb`, or `embed.hardware` in the corpus.
2. **Matched**: a profile in `registry/hardware/` whose `match` fits the detected card.
3. **Estimated**: a heuristic that scales a measured baseline by the card's free memory. It is
   a starting point and says so; `ravel hardware tune` replaces it with a measurement.

The heuristic exists because the first real run was tuned by hand on an 8 GB laptop, and the
next machine will not be. Its baseline is that run (items 32, bytes 40,000, items x longest^2
150M on 8.6 GB): batches that fit there, scaled by how much more memory there is beyond the
model's own weights.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from spec.models import Base

__all__ = [
    "HardwareRegistry",
    "HardwareSpec",
    "Match",
    "Probe",
    "estimate",
    "probe_device",
]

#: What the baseline settings were measured on (RTX 3070 Ti Laptop, Qwen3-Embedding-0.6B).
BASELINE_VRAM_GB = 8.59
BASELINE_MODEL_GB = 1.2
BASELINE_ITEMS = 32
BASELINE_BYTES = 40_000
BASELINE_SQUARE = 80_000_000
HEADROOM = 0.85
MAX_ITEMS = 128


class Match(Base):
    """When a profile applies. Every field that is set must hold."""

    gpu_contains: list[str] = Field(default_factory=list)
    """Case-insensitive substrings of the device name; any one matching is enough."""
    vram_gb_min: float | None = None
    vram_gb_max: float | None = None
    device: str | None = None
    """`cuda`, `cpu`, `mps`."""

    def specificity(self) -> int:
        return (
            2 * bool(self.gpu_contains)
            + (self.vram_gb_min is not None)
            + (self.vram_gb_max is not None)
            + (self.device is not None)
        )

    def fits(self, probe: Probe) -> bool:
        if self.device is not None and self.device != probe.device:
            return False
        if self.gpu_contains and not any(
            token.lower() in probe.name.lower() for token in self.gpu_contains
        ):
            return False
        if self.vram_gb_min is not None and probe.vram_gb < self.vram_gb_min:
            return False
        return not (self.vram_gb_max is not None and probe.vram_gb > self.vram_gb_max)


class HardwareSpec(Base):
    id: str
    device: str | None = None
    """Override the embedder's device; `None` lets the backend choose."""
    batch_items: int = Field(default=32, ge=1)
    batch_bytes: int = Field(default=40_000, ge=1)
    batch_square: int = Field(default=0, ge=0)
    memory_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    """Share of GPU memory the process may reserve; 0 = uncapped. See `EmbedSpec`."""
    match: Match = Field(default_factory=Match)
    measured: dict[str, Any] = Field(default_factory=dict)
    """What was observed on the machine this profile describes: device, model, throughput,
    date. Absent for an estimate, which is how a reader tells a measurement from a guess."""
    notes: str = ""


@dataclass(frozen=True, slots=True)
class Probe:
    """What the machine is, as far as batching cares."""

    device: str = "cpu"
    name: str = "cpu"
    vram_gb: float = 0.0


def probe_device() -> Probe:
    """The first CUDA device if torch can see one, else the CPU. Never imports torch eagerly:
    a machine without it (CI, a laptop that only runs the loader) is a CPU machine."""
    try:
        import torch
    except ImportError:
        return Probe()
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        return Probe("cuda", str(props.name), round(props.total_memory / 1e9, 2))
    return Probe()


def estimate(probe: Probe, *, model_gb: float = BASELINE_MODEL_GB) -> HardwareSpec:
    """Scale the baseline by the memory left once the model's weights are loaded.

    Linear in free memory, capped: past a point a bigger batch stops adding throughput (the
    8 GB run was compute-bound at ~11k tokens/s) and only adds the risk of a memory cliff.
    """
    if probe.device != "cuda":
        return HardwareSpec(
            id="estimate-cpu",
            batch_items=8,
            batch_bytes=16_000,
            batch_square=0,
            notes="estimate: no accelerator detected",
        )
    free = probe.vram_gb * HEADROOM - model_gb
    base = BASELINE_VRAM_GB * HEADROOM - BASELINE_MODEL_GB
    scale = max(0.25, free / base)
    return HardwareSpec(
        id=f"estimate-{probe.vram_gb:g}gb",
        batch_items=min(MAX_ITEMS, max(8, round(BASELINE_ITEMS * scale))),
        batch_bytes=round(BASELINE_BYTES * scale),
        batch_square=round(BASELINE_SQUARE * scale),
        memory_fraction=0.9,
        notes=(
            f"estimate for {probe.name} ({probe.vram_gb:g} GB), scaled {scale:.2f}x from an "
            "8.6 GB baseline; run `ravel hardware tune` to measure"
        ),
    )


class HardwareRegistry:
    """The profiles in `registry/hardware/`."""

    def __init__(self, specs: dict[str, HardwareSpec]) -> None:
        self.specs = specs

    @classmethod
    def load(cls, root: Path | str | None = None) -> HardwareRegistry:
        base = Path(root) if root else Path(__file__).resolve().parents[2] / "registry"
        specs: dict[str, HardwareSpec] = {}
        for path in sorted((base / "hardware").glob("*.yaml")):
            spec = HardwareSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            if spec.id in specs:
                raise ValueError(f"hardware profile {spec.id!r} is defined twice")
            specs[spec.id] = spec
        return cls(specs)

    def get(self, ref: str) -> HardwareSpec:
        try:
            return self.specs[ref]
        except KeyError:
            raise KeyError(
                f"no hardware profile {ref!r} · known: {sorted(self.specs) or 'none'}"
            ) from None

    def matching(self, probe: Probe) -> HardwareSpec | None:
        """The most specific profile that fits, ties broken by id so the choice is stable."""
        fitting = [s for s in self.specs.values() if s.match.fits(probe)]
        if not fitting:
            return None
        return max(fitting, key=lambda s: (s.match.specificity(), s.id))

    def resolve(
        self,
        ref: str | None,
        probe: Probe | None = None,
        *,
        model_gb: float = BASELINE_MODEL_GB,
    ) -> tuple[HardwareSpec, str]:
        """`(spec, why)`: named, matched, or estimated, and the reason, for the run log."""
        if ref and ref != "auto":
            return self.get(ref), f"profile {ref} (named)"
        found = probe if probe is not None else probe_device()
        matched = self.matching(found)
        if matched is not None:
            return matched, f"profile {matched.id} (matches {found.name})"
        guess = estimate(found, model_gb=model_gb)
        return guess, f"{guess.id} (no profile matches {found.name}; heuristic)"
