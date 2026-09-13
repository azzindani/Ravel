"""Is this text layer worth using? (`EXTRACTION.md` §2, `ABSORPTION.md` §16)

Coverage and quality are different questions, and only the first one is free.

`Probe.text_ratio` counts pages that carry extractable text. Measured over 300 source
PDFs, that number says 93.7% and hides a three-way split:

    clean text layer    80.3%   native extraction is correct and costs milliseconds
    damaged text layer  13.3%   fully present, extracts without error, and is wrong
    no text layer        6.3%   genuinely needs OCR or a VLM

The middle class is twice the size of the bottom one and is the dangerous one. Those PDFs
usually carry somebody else's bad OCR from years ago, baked in as a real text layer, so a
coverage gate accepts them and the damage travels all the way to a chunk. `ABSORPTION.md`
§3 is what that looks like after 748,558 rows.

The signatures below are not chosen on taste. They are the ones that actually separated
the classes in that measurement — space loss 8.7%, control characters 5.3%, U+FFFD 0% in
that sample but present in the extraction outputs of §15. Mean word length was tried and
discarded: it fired on nothing the other three had not already caught.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

__all__ = ["TextLayer", "TextQuality", "Thresholds", "score_text"]

# Control characters that are never legitimate document text. \t \n \r are excluded:
# they are layout, not damage.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_REPLACEMENT = "�"
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


class TextLayer(StrEnum):
    """The routing decision. Three ways, not two — see the module docstring."""

    CLEAN = "clean"
    """Extract natively. Milliseconds, no GPU."""

    DAMAGED = "damaged"
    """Text is present and untrustworthy. Re-read the page; do not believe the bytes."""

    ABSENT = "absent"
    """Nothing to read. OCR or a VLM is the only option."""


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Tunable, because `CLAUDE.md` §8 forbids burying corpus constants in code.

    The defaults are the values the §16 measurement was taken with, so a run that leaves
    them alone reproduces that measurement rather than approximating it.
    """

    min_chars_per_page: int = 200
    """Below this a page is a scan with stray marks, not a text layer."""

    max_unbroken_run: int = 24
    """Longest run of letters, with no space, still accepted. 25+ is damage.

    ! 24 rather than a rounder number, for two reasons that agree. Indonesian's longest
    words in actual legal use top out below it — `mempertanggungjawabkan` is 22,
    `pertanggungjawaban` 18 — while `PENGESAHANPERJANJIANANTARAPEMERINTAHREPUBLIKIN` is
    45. And it is the boundary `ABSORPTION.md` §16 was measured with, so the defaults
    here reproduce that measurement exactly (80.3 / 13.3 / 6.3) rather than approximating
    it. Moving it to 25 reclassifies three of those 300 documents."""

    allow_control_chars: int = 0
    allow_replacement_chars: int = 0


@dataclass(frozen=True, slots=True)
class TextQuality:
    """A verdict plus the evidence for it.

    The evidence is the point. `ABSORPTION.md` §18.3's lesson is that an extraction
    setting nobody recorded is a setting nobody can review, so the reason a document was
    routed to OCR travels with the document instead of living in the router's head.
    """

    layer: TextLayer
    reasons: tuple[str, ...]
    chars: int
    chars_per_page: float
    control_chars: int
    replacement_chars: int
    longest_run: int

    @property
    def usable(self) -> bool:
        """True only for `CLEAN`. `DAMAGED` is not a degraded yes."""
        return self.layer is TextLayer.CLEAN

    def describe(self) -> str:
        if not self.reasons:
            return f"{self.layer}: {self.chars_per_page:.0f} chars/page"
        return f"{self.layer}: " + "; ".join(self.reasons)


def longest_unbroken_run(text: str) -> int:
    """Longest run of letters uninterrupted by a space, in characters.

    ! Counted over letters only, so numbers, punctuation and identifiers do not trip it.
    A regulation is full of `Nomor 21/PMK.03/2022`, which is not a word and is not
    damage.
    """
    return max((len(m.group()) for m in _WORD.finditer(text)), default=0)


def score_text(
    text: str,
    *,
    pages: int | None = None,
    thresholds: Thresholds | None = None,
) -> TextQuality:
    """Classify an extracted text layer as clean, damaged or absent.

    `pages` scales the emptiness test; without it the text is judged as a single page,
    which is the right default for HTML and plain text.
    """
    t = thresholds or Thresholds()
    chars = len(text.strip())
    per_page = chars / max(pages or 1, 1)

    control = len(_CONTROL.findall(text))
    replacement = text.count(_REPLACEMENT)
    run = longest_unbroken_run(text)

    if per_page < t.min_chars_per_page:
        return TextQuality(
            layer=TextLayer.ABSENT,
            reasons=(f"{per_page:.0f} chars/page < {t.min_chars_per_page}",),
            chars=chars,
            chars_per_page=per_page,
            control_chars=control,
            replacement_chars=replacement,
            longest_run=run,
        )

    reasons: list[str] = []
    if control > t.allow_control_chars:
        reasons.append(f"{control} control character(s)")
    if replacement > t.allow_replacement_chars:
        reasons.append(f"{replacement} U+FFFD replacement character(s)")
    if run > t.max_unbroken_run:
        reasons.append(f"{run}-letter run with no space (space loss)")

    return TextQuality(
        layer=TextLayer.DAMAGED if reasons else TextLayer.CLEAN,
        reasons=tuple(reasons),
        chars=chars,
        chars_per_page=per_page,
        control_chars=control,
        replacement_chars=replacement,
        longest_run=run,
    )


def normalise_damage(text: str) -> str:
    """Strip what is unambiguously damage, for the cases worth salvaging.

    ! Deliberately tiny, and deliberately not a repair. It removes control characters and
    normalises Unicode; it does **not** try to reinsert spaces or guess at `Nenteri`.
    `CLAUDE.md` §7.1 draws the line: a downstream stage may not paper over an extraction
    defect, because the result is a plausible document that no longer matches its source.
    Re-extraction is the fix; this only keeps damage from breaking a parser on its way to
    being re-extracted.
    """
    return _CONTROL.sub("", unicodedata.normalize("NFC", text))
