"""A variant is a hashed configuration, and its cache keys are its reuse policy.

The problem this solves
-----------------------
`VARIANTS.md` §1: ID_Legal's real defect was not its chunking — it was that nobody could
find out whether its chunking was good. Without a second corpus to compare against, every
retrieval complaint is unfalsifiable and every proposed fix is an opinion.

The one rule that carries everything
------------------------------------
`VARIANTS.md` §4: *"a cache key contains every parameter that can change the output. A
variant silently inheriting another's artifacts because a key was incomplete produces a
bundle whose manifest is a lie."*

Two structural answers, because a rule this important cannot be a convention:

**1. Keys are chained along the stage graph, not computed per stage.** `chunk`'s key
includes `extract`'s; `embed`'s includes `chunk`'s; `cluster`'s includes `embed`'s. So a
change anywhere upstream changes every key downstream of it, and the reuse table in §4 is
not a table anyone maintains — it falls out of the graph:

    pasal and token512          share extract               (same sources, same extractor)
    same chunker, new embedder  share extract, chunk, enrich
    same chunker, new enricher  share extract, chunk, embed
    different chunker           share extract only

**2. `None` is refused in a resolved config.** A key is only as complete as the dict it
hashes, and the way a parameter escapes the dict is by not being in it — sitting in code
as a default, or as a `None` that means "whatever the implementation does today". Hashing
that produces a stable key for two runs that behave differently, which is precisely the
lie §4 describes. So a resolved config may not contain `None`: the value is either stated
or the config is not resolved.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import product
from typing import Any

__all__ = [
    "STAGES",
    "VariantError",
    "VariantSpec",
    "check_baseline_present",
    "matrix",
    "reuse_plan",
]

#: The stage graph, as `stage -> the stage whose key it extends`.
#:
#: ! This mapping *is* the reuse policy of `VARIANTS.md` §4. Enrichment and embedding both
#: hang off `chunk` and neither hangs off the other, which is what makes "re-embed without
#: re-enriching" and "re-enrich without re-embedding" both free. Clustering hangs off
#: `embed` because centroids are means of vectors: swap the vectors and the centroids
#: describe a space that no longer exists.
_PARENT: dict[str, str | None] = {
    "extract": None,
    "chunk": "extract",
    "enrich": "chunk",
    "embed": "chunk",
    "cluster": "embed",
}

STAGES: tuple[str, ...] = tuple(_PARENT)


class VariantError(ValueError):
    """A variant configuration that cannot be hashed honestly."""


def _digest(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _find_none(value: Any, path: str = "") -> str | None:
    if value is None:
        return path or "<root>"
    if isinstance(value, Mapping):
        for key, item in value.items():
            found = _find_none(item, f"{path}.{key}" if path else str(key))
            if found:
                return found
    elif isinstance(value, list | tuple):
        for i, item in enumerate(value):
            found = _find_none(item, f"{path}[{i}]")
            if found:
                return found
    return None


@dataclass(frozen=True, slots=True)
class VariantSpec:
    """One named, fully resolved configuration."""

    corpus_id: str
    name: str
    config: dict[str, dict[str, Any]]
    """Stage name to resolved parameters. Every stage in `STAGES` must be present, even
    when empty — an absent stage is indistinguishable from one whose parameters were
    forgotten, and the two hash differently for no reason a reader could recover."""

    def __post_init__(self) -> None:
        unknown = set(self.config) - set(STAGES)
        if unknown:
            raise VariantError(
                f"unknown stages {sorted(unknown)}; the stage graph is {list(STAGES)}. "
                f"A parameter filed under a stage nothing reads is a parameter that does "
                f"not reach a cache key."
            )
        missing = [s for s in STAGES if s not in self.config]
        if missing:
            raise VariantError(
                f"variant {self.name!r} declares no {missing} section. State it as an "
                f"empty mapping if the stage takes no parameters — omitting it and "
                f"defaulting it are different configurations that would hash alike."
            )
        dangling = _find_none(self.config)
        if dangling:
            raise VariantError(
                f"variant {self.name!r} has an unresolved value at {dangling}. A `None` "
                f"means 'whatever the implementation does today', so two runs that behave "
                f"differently would share a key (VARIANTS.md §4)."
            )

    # -- keys ---------------------------------------------------------------------

    def stage_key(self, stage: str) -> str:
        """This stage's cache key, including every upstream stage's configuration."""
        if stage not in _PARENT:
            raise VariantError(f"unknown stage {stage!r}")
        parent = _PARENT[stage]
        upstream = self.stage_key(parent) if parent else self.corpus_id
        return _digest([upstream, stage, self.config.get(stage, {})])

    def keys(self) -> dict[str, str]:
        return {stage: self.stage_key(stage) for stage in STAGES}

    @property
    def config_hash(self) -> str:
        """Names the work directory and goes into the manifest.

        The terminal key of the graph, so it moves when anything moves. Two variants that
        hash the same are the same variant, whatever they are called (`VARIANTS.md` §3) —
        which is why the name is not in the hash.
        """
        return self.stage_key("cluster")

    def shares_with(self, other: VariantSpec) -> tuple[str, ...]:
        """Which stages this variant can reuse from `other`'s artifacts.

        ! Reuse is *derived*, never declared. The alternative — a human deciding that two
        variants may share their chunks — is exactly how a bundle ends up claiming a
        recipe that did not produce it.
        """
        mine, theirs = self.keys(), other.keys()
        return tuple(stage for stage in STAGES if mine[stage] == theirs[stage])

    def manifest(self) -> dict[str, Any]:
        return {
            "corpus": self.corpus_id,
            "variant": self.name,
            "config_hash": self.config_hash,
            "keys": self.keys(),
            "config": self.config,
        }

    def with_overrides(self, **stages: Mapping[str, Any]) -> VariantSpec:
        """A sibling variant with some stages replaced. Merges per stage, not wholesale."""
        merged = {stage: dict(params) for stage, params in self.config.items()}
        for stage, params in stages.items():
            if stage not in STAGES:
                raise VariantError(f"unknown stage {stage!r}")
            merged[stage] = {**merged.get(stage, {}), **dict(params)}
        name = "+".join(
            [self.name, *(f"{s}={_digest(p)[:6]}" for s, p in sorted(stages.items()))]
        )
        return VariantSpec(corpus_id=self.corpus_id, name=name, config=merged)


def matrix(
    base: VariantSpec,
    dimensions: Mapping[str, Mapping[str, Sequence[Any]]],
    *,
    name_with: str = "-",
) -> tuple[VariantSpec, ...]:
    """Expand a sweep, ordered so shared work runs once.

    `dimensions` is `{stage: {parameter: [values]}}`, so
    `{"chunk": {"chunker": ["unit", "token"]}, "embed": {"template": ["body", "path+body"]}}`
    expands to four variants.

    ! Ordered by cache key, not by name. Sorting by the key sequence puts variants that
    share an upstream stage next to each other, so a sweep that varies the embedder walks
    through the chunk artifacts once instead of evicting and rebuilding them. It is the
    same total work either way; the difference is whether the disk holds one generation of
    chunks or four.
    """
    axes: list[tuple[str, str, Sequence[Any]]] = [
        (stage, parameter, values)
        for stage, parameters in dimensions.items()
        for parameter, values in parameters.items()
    ]
    if not axes:
        return (base,)
    for stage, _, _ in axes:
        if stage not in STAGES:
            raise VariantError(f"unknown stage {stage!r} in the matrix")

    built: list[VariantSpec] = []
    for combination in product(*(values for _, _, values in axes)):
        overrides: dict[str, dict[str, Any]] = {}
        labels: list[str] = []
        for (stage, parameter, _), value in zip(axes, combination, strict=True):
            overrides.setdefault(stage, {})[parameter] = value
            labels.append(f"{parameter}={value}")
        merged = {s: dict(p) for s, p in base.config.items()}
        for stage, params in overrides.items():
            merged[stage] = {**merged.get(stage, {}), **params}
        built.append(
            VariantSpec(
                corpus_id=base.corpus_id,
                name=name_with.join([base.name, *labels]),
                config=merged,
            )
        )

    return tuple(sorted(built, key=lambda v: tuple(v.keys()[s] for s in STAGES)))


def reuse_plan(variants: Sequence[VariantSpec]) -> dict[str, Any]:
    """What a sweep would actually recompute, before it runs.

    Reports how many distinct artifacts each stage needs across the whole matrix. It is
    the number that makes a sweep's cost legible: sixteen variants that all share one
    extraction and two chunkings cost two chunkings, not sixteen.
    """
    distinct = {stage: {v.stage_key(stage) for v in variants} for stage in STAGES}
    return {
        "variants": len(variants),
        "distinct": {stage: len(keys) for stage, keys in distinct.items()},
        "total_stage_runs": sum(len(keys) for keys in distinct.values()),
        "naive_stage_runs": len(variants) * len(STAGES),
    }


def check_baseline_present(variants: Iterable[VariantSpec], baseline: str) -> tuple[bool, str]:
    """Is the anchor arm in this sweep?

    ! `VARIANTS.md` §5: *"Always keep a baseline arm in the matrix. Without it, results
    drift with every other change and you lose the ability to say whether things got
    better."* A sweep of four new ideas measured only against each other tells you which
    of the four is best and nothing about whether any of them beats what you have.
    """
    names = [v.name for v in variants]
    if any(baseline == name or name.startswith(f"{baseline}-") for name in names):
        return True, f"baseline {baseline!r} is in the sweep"
    return False, (
        f"baseline {baseline!r} is not in this sweep ({names}). Results would be "
        f"comparable to each other and to nothing else — including to the corpus that is "
        f"in production."
    )
