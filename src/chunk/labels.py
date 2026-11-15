"""Labels a chunk's own text decides, applied the same way at chunking and to a built bundle.

The chunker labels a chunk by the headings above it. When a heading was lost, the label is
wrong and the text is not: an explanation whose `PENJELASAN` heading OCR dropped is filed
under `PENUTUP`, and cited as the article it explains. `ExplanationRule` reads the text.

! One function for both callers, and that is the point of this module. `chunk.unit` applies
it to every chunk it emits; `bundle.relabel` applies it to the chunks of a bundle that was
built before the rule existed. Because the rule reads only `(chapter, article, body)`, which
both have, a relabelled bundle carries exactly the labels a rebuild would -- and the chunk
ids do not move, since a chunk id hashes its locator and its blocks, never its chapter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from spec.models import ExplanationSpec

__all__ = ["ExplanationRule"]


@dataclass(frozen=True)
class ExplanationRule:
    region: str
    article: re.Pattern[str]
    opens: re.Pattern[str]

    @classmethod
    def compile(cls, spec: ExplanationSpec) -> ExplanationRule:
        flags = re.IGNORECASE if spec.ignorecase else 0
        # The body may repeat the article label first (`Pasal 5 Cukup jelas`).
        lead = r"^\s*(?:" + spec.article.lstrip("^") + r"[^\s]*\s*)?"
        return cls(
            region=spec.region,
            article=re.compile(spec.article),
            opens=re.compile(lead + r"(?:" + spec.opens.lstrip("^") + r")", flags),
        )

    def chapter_for(self, chapter: str | None, article: str | None, body: str) -> str | None:
        """The chapter this chunk should carry, or None when its own is right."""
        if not article or not self.article.search(article):
            return None
        if (chapter or "").startswith(self.region):
            return None
        return self.region if self.opens.search(body) else None
