"""Checks that run before GPU-days are spent, and before a bundle loads.

`CLAUDE.md` §7.6: never load a bundle whose manifest fails the consistency preflight.

A vector-space mismatch produces **no error** — retrieval still returns its k nearest
neighbours, just worse ones. Everything here exists to convert that silence into a
failure, at the only two moments where failing is cheap: before a long build starts, and
before a bundle is loaded into a database.

The instruction check is the one that matters most, and it is deliberately not a test of
the embedder's *output*. `ABSORPTION.md` §11 was a declared instruction that was never
applied — so the check asserts the invariant directly: **if the manifest declares an
instruction, the rendered input must differ from the raw text.** A `prepare_texts` that
returns `contents` unchanged fails it, which is exactly what nobody noticed for 748,558
documents.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from embed.spec import EmbedderSpec, InstructionStyle, render_document, render_query

__all__ = [
    "PreflightError",
    "PreflightReport",
    "check_independent_backends",
    "check_instruction_is_applied",
    "check_spec_pair",
    "cosine",
    "round_trip",
]

DEFAULT_MIN_COSINE = 0.999


class PreflightError(RuntimeError):
    """A check that must stop a build or a load."""


@dataclass(frozen=True, slots=True)
class PreflightReport:
    passed: bool
    checks: tuple[tuple[str, bool, str], ...]

    def raise_for_status(self) -> None:
        if self.passed:
            return
        failed = "\n".join(
            f"  - {name}: {detail}" for name, ok, detail in self.checks if not ok
        )
        raise PreflightError(f"embedder preflight failed:\n{failed}")

    def describe(self) -> str:
        return "\n".join(
            f"{'ok  ' if ok else 'FAIL'} {name}: {detail}" for name, ok, detail in self.checks
        )


class _Embeds(Protocol):
    spec: EmbedderSpec
    """! Part of the protocol, because `preflight` reads it. An embedder that
    cannot say what space it produces cannot be checked against one, and the
    whole point of this module is that the recipe travels with the vectors."""

    def embed_documents(self, batch: list[str]) -> Any: ...
    def embed_queries(self, batch: list[str]) -> Any: ...


# -- individual checks ---------------------------------------------------------


def check_instruction_is_applied(
    spec: EmbedderSpec, probe: str = "Pasal 1"
) -> tuple[bool, str]:
    """The §11 check: a declared instruction must actually change the model input.

    ! This asserts the invariant rather than replaying the construction (`CLAUDE.md`
    §15). It does not ask whether the renderer was written correctly; it asks whether the
    text the model will see differs from the text it was handed. A pass-through cannot
    satisfy that, however well-written the helper beside it is.
    """
    if spec.instruction_style is InstructionStyle.NONE:
        return True, "no instruction declared (none), nothing to apply"

    doc = render_document(spec, probe)
    query = render_query(spec, probe)
    if doc == probe or query == probe:
        which = "document" if doc == probe else "query"
        return False, (
            f"instruction_style is {spec.instruction_style!r} but the rendered {which} "
            f"input is identical to the raw text — the instruction is declared and not "
            f"applied (ABSORPTION.md §11)"
        )
    return True, f"instruction applied to both sides ({spec.instruction_style})"


def check_independent_backends(build: object, query: object) -> tuple[bool, str]:
    """The two sides of a round trip must be different implementations.

    ! This is the check whose absence cost Vera's dense arm, and it is the reason the
    cosine floor alone is not enough. The ingest pipeline's round-trip gate compared the
    serving backend **against itself**, scored 0.999992, and certified a vector space
    that ranked the right answer at median 32 — later measured at median 857 and 2.6%
    Recall@5 against a reference median of 1, after which the arm carried weight 0.0.

    A gate that can only pass is worse than no gate: it buys confidence. Identical inputs
    through identical code produce cosine 1.0 no matter how wrong that code is, so what
    the round trip has to compare is the **serving path against the model's reference
    implementation** — two ways of computing what the manifest declares.
    """
    if build is query:
        return False, (
            "build and query are the same object — a round trip against itself scores "
            "1.0 and certifies nothing (see the docstring)"
        )
    if type(build) is type(query):
        return False, (
            f"both sides are {type(build).__name__} — the round trip would compare a "
            f"backend against itself. Compare the serving path against the model's "
            f"reference implementation, or pass allow_same_backend=True and say why."
        )
    return True, f"{type(build).__name__} vs {type(query).__name__}"


def check_spec_pair(build: EmbedderSpec, query: EmbedderSpec) -> tuple[bool, str]:
    """Build-side and query-side must occupy the same space."""
    if build.same_space(query):
        return True, f"fingerprints match ({build.fingerprint()})"

    differing = [
        k
        for k in (
            "model",
            "model_version",
            "dim",
            "pooling",
            "padding_side",
            "normalize",
            "instruction_style",
            "doc_instruction",
            "query_instruction",
            "modality",
            "dtype",
            "provider",
            "provider_pin",
        )
        if getattr(build, k) != getattr(query, k)
    ]
    return False, f"specs differ in {', '.join(differing)} — vectors are not comparable"


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def round_trip(
    build: _Embeds,
    query: _Embeds,
    samples: Sequence[str],
    *,
    min_cosine: float = DEFAULT_MIN_COSINE,
) -> tuple[bool, str]:
    """Embed the same text both sides; require cosine >= `min_cosine`.

    The sample and its reference vector belong in the bundle so Vera's startup canary has
    something to check against (`BUNDLE.md` §3).
    """
    if not samples:
        return False, "no samples supplied; a preflight over nothing proves nothing"

    left = build.embed_documents(list(samples))
    right = query.embed_documents(list(samples))
    worst, worst_at = 1.0, 0
    for i, (a, b) in enumerate(zip(left, right, strict=True)):
        c = cosine(list(a), list(b))
        if c < worst:
            worst, worst_at = c, i
    ok = worst >= min_cosine
    return ok, (
        f"worst cosine {worst:.6f} over {len(samples)} samples "
        f"(sample {worst_at}, floor {min_cosine})"
    )


# -- the gate ------------------------------------------------------------------


def preflight(
    build: _Embeds,
    query: _Embeds,
    samples: Sequence[str],
    *,
    min_cosine: float = DEFAULT_MIN_COSINE,
    allow_same_backend: bool = False,
) -> PreflightReport:
    """Run every check. Call before a long build and before `ravel load`.

    `allow_same_backend` disables the independence check. It exists because there are
    honest uses — verifying determinism, or a smoke test — and it defaults to False
    because the dishonest use is what shipped a broken corpus.
    """
    checks: list[tuple[str, bool, str]] = []

    ok, detail = check_instruction_is_applied(build.spec)
    checks.append(("instruction is applied (build side)", ok, detail))

    if allow_same_backend:
        checks.append(
            (
                "backends are independent",
                True,
                "skipped by allow_same_backend — this run does not certify the vector space",
            )
        )
    else:
        ok, detail = check_independent_backends(build, query)
        checks.append(("backends are independent", ok, detail))

    ok, detail = check_spec_pair(build.spec, query.spec)
    checks.append(("build and query specs agree", ok, detail))

    if all(c[1] for c in checks):
        ok, detail = round_trip(build, query, samples, min_cosine=min_cosine)
        checks.append(("cosine round trip", ok, detail))
    else:
        checks.append(
            (
                "cosine round trip",
                False,
                "skipped: the specs already disagree, so the vectors cannot be compared",
            )
        )

    return PreflightReport(passed=all(c[1] for c in checks), checks=tuple(checks))
