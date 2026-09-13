"""Legal references, extracted with character positions — the graph's raw material.

What this replaces
------------------
`06_ID_Legal/core/knowledge_graph/kg_core.py` does this job with five regexes and five
weight tables hardcoded in Python. `ABSORPTION.md` §7 said "port selectively"; measuring
first showed what to leave behind. Its regulation pattern is

    (?:UU|PP|Perpres|Perda|Permen|Peraturan Pemerintah|Undang-Undang)
    \\s*(?:No\\.?\\s*)?\\d+\\s*(?:Tahun\\s*)?\\d{4}

and `No\\.?` matches `No.` but **not `Nomor`**, which is how Indonesian legal text
actually writes it. Measured over eight realistic inline citations it caught three — every
abbreviated `UU No. 40 Tahun 2007` form, and none of the spelled-out
`Undang-Undang Nomor 40 Tahun 2007`, `Peraturan Pemerintah Nomor 24 Tahun 2018` or
`Peraturan Daerah Kabupaten ... Nomor 16 Tahun 2022` ones. It is the §4 defect again, one
layer down: a regex written against a handful of examples, in code, where the corpus it
serves cannot correct it.

So the patterns here come from the **profile**. `identity.citation` and
`identity.cross_ref` are already maintained as data, already tested against real
documents, and already cover the 28 declared types — which means this module gets the
regional instruments that are 60% of the corpus for free, and a future corpus in another
jurisdiction gets its own without touching this file (`CLAUDE.md` §12).

Why positions
-------------
`ABSORPTION.md` §6 found the incumbent's entity payloads already carry character
positions, and that is the one thing worth keeping unchanged: a mention with a position
can be mapped back to a span, highlighted in a citation, and re-verified against the
source text. A mention without one is an assertion.

What this is not
----------------
Not a graph, and not a score. It produces edges; `kg_pagerank` and `kg_degree_centrality`
are whole-corpus properties that cannot be computed per chunk — which is exactly why the
incumbent has those columns and they are NULL on all 748,558 rows. `RelationshipGraph`
implements both correctly and is instantiated only by its own unit test; nothing ever
built the graph from the corpus. That is a Phase C job (`CLAUDE.md` §3), and putting it
here would repeat the mistake in a new codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from spec.models import Profile

__all__ = ["Reference", "ReferenceKind", "extract_references"]


class ReferenceKind(StrEnum):
    REGULATION = "regulation"
    """A citation of another instrument — an edge between documents."""

    INTERNAL = "internal"
    """A `Pasal 5 ayat (2)` pointer — an edge inside this document."""


@dataclass(frozen=True, slots=True)
class Reference:
    """One reference, with the span it was read from."""

    kind: ReferenceKind
    text: str
    start: int
    end: int
    fields: dict[str, str]
    """Named groups the profile's pattern captured — `type`, `number`, `year` for a
    regulation; `pasal`, `ayat`, `huruf` for an internal pointer."""

    identifier: str | None = None
    """Short form, when the reference names a type the profile knows: `UU 40/2007`.

    ! `None` rather than a guess. This is what Vera's exact-match bypass keys on, and an
    identifier that is wrong is worse than one that is absent, because the bypass trusts
    it completely (`docs/PROFILES.md`).
    """

    @property
    def span(self) -> tuple[int, int]:
        return self.start, self.end


def _identifier(profile: Profile, fields: dict[str, str]) -> str | None:
    document_type = fields.get("type")
    number, year = fields.get("number"), fields.get("year")
    if not (document_type and number and year):
        return None
    short = profile.abbreviate(document_type.upper())
    return f"{short} {number}/{year}"


def _dedupe(refs: list[Reference]) -> list[Reference]:
    """Drop references wholly contained in an earlier, longer one.

    ! Regulation and internal patterns overlap: `Pasal 5 UU 40/2007` matches both, and
    counting it twice inflates any degree metric computed downstream. Longest-span wins,
    which prefers the more specific reading.
    """
    kept: list[Reference] = []
    for ref in sorted(refs, key=lambda r: (r.start, -(r.end - r.start))):
        if any(k.start <= ref.start and ref.end <= k.end for k in kept):
            continue
        kept.append(ref)
    return kept


def extract_references(profile: Profile, text: str) -> list[Reference]:
    """Every legal reference in `text`, in document order, with positions.

    Returns `[]` when the profile declares no patterns — a profile that cannot describe
    citations should produce no citations, not guesses from a built-in fallback.
    """
    found: list[Reference] = []

    if profile.citation is not None:
        for m in profile.citation.finditer(text):
            fields = {k: v for k, v in m.groupdict().items() if v}
            found.append(
                Reference(
                    kind=ReferenceKind.REGULATION,
                    text=m.group().strip(),
                    start=m.start(),
                    end=m.end(),
                    fields=fields,
                    identifier=_identifier(profile, fields),
                )
            )

    if profile.cross_ref is not None:
        for m in profile.cross_ref.finditer(text):
            fields = {k: v for k, v in m.groupdict().items() if v}
            found.append(
                Reference(
                    kind=ReferenceKind.INTERNAL,
                    text=m.group().strip(),
                    start=m.start(),
                    end=m.end(),
                    fields=fields,
                )
            )

    return _dedupe(found)


def reference_counts(refs: list[Reference]) -> dict[str, int]:
    """Per-kind counts, for the signals sidecar.

    The incumbent's `kg_cross_ref_count` is this number. It is kept as a plain count and
    not turned into a score here: weighting belongs to whoever fits weights, and
    `SCORING.md` fits them against an eval set rather than choosing them in a constructor
    the way `kg_core.py`'s twelve `kg_weights` are.
    """
    counts = {kind.value: 0 for kind in ReferenceKind}
    for ref in refs:
        counts[ref.kind.value] += 1
    counts["total"] = len(refs)
    counts["distinct_regulations"] = len(
        {r.identifier for r in refs if r.identifier is not None}
    )
    return counts
