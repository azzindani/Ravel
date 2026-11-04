"""Check a knowledge-base build against what its profile says to expect.

A profile that reads 80% of documents' subjects and a profile that reads 8% both run to
completion; only a number somebody wrote down beforehand tells them apart. `expected` is
that number, and this is the comparison -- kept to counts the driver already has, so
checking costs nothing and cannot be skipped for being slow.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from spec import ExpectedSpec

__all__ = ["Violation", "check"]


@dataclass(frozen=True, slots=True)
class Violation:
    what: str
    detail: str

    def __str__(self) -> str:
        return f"{self.what}: {self.detail}"


def check(
    expected: ExpectedSpec, *, present: Counter[str], chunks: int, oversize: int
) -> list[Violation]:
    """Every floor the build fell below. Empty means the build met its profile.

    `present[field]` is how many chunks carried the field. A build with no chunks fails
    every coverage floor: "no data" is not "all data is fine".
    """
    found: list[Violation] = []
    for name, floor in sorted(expected.coverage.items()):
        share = present[name] / chunks if chunks else 0.0
        if share < floor:
            found.append(
                Violation(
                    f"coverage[{name}]",
                    f"{share:.1%} of {chunks:,} chunks, expected at least {floor:.1%}",
                )
            )
    if expected.max_row_tokens is not None and chunks:
        share = oversize / chunks
        if share > expected.oversize_share:
            found.append(
                Violation(
                    "max_row_tokens",
                    f"{oversize:,} of {chunks:,} chunks ({share:.3%}) exceed "
                    f"{expected.max_row_tokens:,} tokens, expected at most "
                    f"{expected.oversize_share:.3%}",
                )
            )
    return found
