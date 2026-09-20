"""Ranking factors computed at ingest, where they are free.

Vera's `SCORING.md` fuses six factor families:

    final = relevance * (1 + sum(w_i * factor_i))

    relevance     dense + sparse + text arms      does this text address the query?
    exactness     identifier match                did the query name this regulation?
    authority     regulation_type                 how binding is this instrument?
    temporal      year                            is this current, or superseded?
    structural    chapter, article                operative clause or annex?
    topical       about                           is the instrument about what was asked?
    completeness  length(body), legal-term density  whole provision or fragment?

Only the first two need the query. **The rest are properties of the chunk**, and that
makes them Ravel's job: computed once at ingest instead of re-derived per query from
string comparisons the engine would have to do a million times.

Two rules inherited from `SCORING.md`, and both are load-bearing
----------------------------------------------------------------
**Factors are a bounded multiplicative prior, never a replacement for relevance.**
Authority without relevance ranks the most prestigious document in the corpus first for
every query — and it *looks* correct, which is what makes it dangerous. Everything here
returns a number in `[0, 1]` intended for the `(1 + sum(w*f))` term, never a score.

**The vocabulary is the profile's, never this module's.** `authority` already read
`identity.authority` from the registry; `structural` and `completeness` did not, and
carried Indonesian patterns and a 26-word Indonesian term list as module constants.
`generic@1.0.yaml` states that everything `id_regulation` does, it does "with
different data and no code changes" — which was false for two of the five factors.
They now read `profile.scoring` (`ScoringSpec`), and a profile that declares nothing
gets `None` rather than a number computed from another corpus's words.

**A factor that cannot be computed is `None`, never `0.0`.** "Unknown" and "lowest" are
different claims, and collapsing them silently demotes every document the profile does
not recognise. `SCORING.md` earned this the hard way: `enacting_body` is populated on all
367,069 live rows and is unusable — `PERATURAN BUPATI` with `enacting_body='MA'` on 10,395
rows, `PERATURAN DAERAH KABUPATEN` with `'RI'` on 7,770 — so a weighted term over it would
be, in that document's words, *noise wearing a coefficient*. Ravel's answer is to emit
`None` rather than a confident zero.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from spec.models import Profile

__all__ = ["Factors", "compute_factors"]

# ! The only pattern left at module level, and it is not corpus knowledge: it says
# "a word is two or more word characters", which is true of every language this
# runs on. Everything that WAS here -- the annex and operative patterns, and 26
# Indonesian legal terms -- moved to `ScoringSpec`, because `generic@1.0.yaml`
# promises that a different corpus needs different data and no code changes, and
# with those constants in Python that promise was false.
_WORD = re.compile(r"(?u)\b\w\w+\b")


def _compiled(patterns: list[str]) -> list[re.Pattern[str]]:
    """Anchored at the start, case-insensitive · a label is a prefix, not a mention.

    ! `match`, not `search`. "Pasal 9 dihapus" is an amending clause that *contains*
    an operative marker without being one, which is the same distinction the profile
    makes with `marker_only`.
    """
    return [re.compile(rf"^\s*(?:{p})", re.IGNORECASE) for p in patterns]


@dataclass(frozen=True, slots=True)
class Factors:
    """Query-independent ranking factors. `None` means not computable, never zero."""

    authority: float | None = None
    temporal: float | None = None
    structural: float | None = None
    topical: float | None = None
    completeness: float | None = None

    legal_term_density: float | None = None
    """The readable input to `completeness`, kept separately so a weight can be refitted
    without re-ingesting the corpus."""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _authority(profile: Profile, document_type: str | None) -> float | None:
    """`rank / authority_scale`, the scale declared by the profile.

    ! A scale, not a maximum. Indonesian regulation tops out at 9 against 10; a
    corpus whose ladder runs 1-5 declares 5 and its top instrument also reaches 1.0.
    Normalising against the highest rank *present* would make the same instrument
    score differently in a corpus that merely lacks a constitution.
    """
    rank = profile.authority_of(document_type)
    return None if rank is None else rank / profile.scoring.authority_scale


def _temporal(year: int | None, *, now: int | None = None, half_life: int = 25) -> float | None:
    """Recency, decaying with age.

    ! Recency is not validity, and this factor must never be read as "still in force".
    A 1945 constitutional provision outranks a 2023 circular on authority and should;
    this only says which of two equally relevant, equally authoritative clauses is more
    likely to be current. Repeal is a graph property Ravel does not compute, and a low
    temporal score on a repealed law is a coincidence, not a finding.
    """
    if year is None:
        return None
    current = now if now is not None else datetime.now(UTC).year
    if not (1800 <= year <= current + 1):
        return None  # out of range: a parse artefact, not an old document
    age = max(current - year, 0)
    return round(half_life / (half_life + age), 6)


def _structural(profile: Profile, chapter: str | None, article: str | None) -> float | None:
    """Is this an operative clause or an annex?

    Vera returns `LAMPIRAN / LAMPIRAN` hits above `Pasal` hits today, which is the
    concrete defect this factor exists to correct. The *question* generalises to any
    corpus with a body and appendices; the labels never do, so they come from the
    profile.

    ! A profile declaring neither list gets `None` for every chunk -- "this profile
    does not classify its sections" -- rather than the labelled fallback. Returning
    0.6 there would be a claim the profile never made.
    """
    sc = profile.scoring
    if not (sc.annex or sc.operative):
        return None
    annex = _compiled(sc.annex)
    operative = _compiled(sc.operative)
    for value in (article, chapter):
        if not value:
            continue
        if any(p.match(value) for p in annex):
            return sc.annex_score
        if any(p.match(value) for p in operative):
            return sc.operative_score
    return None if not (chapter or article) else sc.labelled_score


def _topical(about: str | None) -> float | None:
    """Whether the instrument declares what it is about at all.

    ! Deliberately weak. The query-dependent half — whether `about` matches *this* query
    — belongs to the engine; all Ravel can say at ingest is whether the field is usable.
    The incumbent's `about` column is damaged on a measurable share of rows
    (`ABSORPTION.md` §3: space loss, OCR garble), so "present" is not "usable".
    """
    if about is None:
        return None
    text = about.strip()
    if not text:
        return 0.0
    words = _WORD.findall(text.lower())
    if len(words) < 2:
        return 0.2
    return 1.0


def _completeness(
    profile: Profile, body: str, *, min_chars: int = 40, full_at: int = 400
) -> tuple[float, float]:
    """Whole provision or fragment, plus the legal-term density behind it.

    `min_chars` matches the profile's `cleanup.min_indexable_chars`: below it a chunk is
    not indexable at all (50,883 rows of the incumbent, 6.80%).
    """
    text = body.strip()
    words = _WORD.findall(text.lower())
    # ! A profile declaring no vocabulary gets density 0.0, and `completeness` is
    # then length alone. That is the honest degradation -- and it is exactly what
    # every generic corpus was silently getting while the vocabulary was 26
    # Indonesian words in this file.
    terms = {t.lower() for t in profile.scoring.terms}
    density = sum(1 for w in words if w in terms) / len(words) if words and terms else 0.0
    # ! Below the indexable floor, completeness is zero outright — density cannot lift
    # it. Capping density was not enough: "wajib dilarang" is 14 characters and 100%
    # legal terms, and scored 0.3 on density alone until a test asserted otherwise. A
    # chunk too short to index is not a partially complete provision, it is a fragment,
    # and letting vocabulary argue otherwise is how `Cukup jelas.`-shaped rows climb.
    if len(text) < min_chars:
        return 0.0, round(density, 6)

    length_score = min((len(text) - min_chars) / (full_at - min_chars), 1.0)
    return round(0.7 * length_score + 0.3 * min(density * 4, 1.0), 6), round(density, 6)


def compute_factors(
    *,
    profile: Profile,
    body: str,
    document_type: str | None = None,
    year: int | None = None,
    chapter: str | None = None,
    article: str | None = None,
    about: str | None = None,
    now: int | None = None,
) -> Factors:
    """Every query-independent factor for one chunk.

    `now` is injectable so a corpus built today and re-scored next year produces the same
    numbers from the same inputs — a factor that drifts with the wall clock cannot be part
    of a reproducible bundle.
    """
    completeness, density = _completeness(profile, body)
    return Factors(
        authority=_authority(profile, document_type),
        temporal=_temporal(year, now=now),
        structural=_structural(profile, chapter, article),
        topical=_topical(about),
        completeness=completeness,
        legal_term_density=density,
    )
