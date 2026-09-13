"""Structure detection: a separate, recorded pass over canonical blocks.

Why this is its own stage
-------------------------
The native extractor infers headings from typography. Measured on 56 real Indonesian
regulations that recovers **15.4% of structural headings at 4.0% precision**, because
the corpus does not typeset structure:

    structural marker lines: 221
      bold      2.7%          <- not weight
      centered  58.4%         <- layout is the signal
      size      1.0x body     <- not size

`Pasal 9` is set in the body font. Structure there comes from **what the line says**,
not how it is styled — and that is corpus knowledge, not PDF knowledge.

So an extractor reports **what is on the page** (text, position, size, weight) and a
structurer decides **what it means**, using a profile loaded from the registry. A
different corpus swaps the profile; no code changes, and no re-extraction. That is the
Phase A/B boundary applied one level down.

! This reads canonical blocks and writes canonical blocks. It never reopens the
source, so the boundary holds (LOOPHOLES.md §8).
"""

from __future__ import annotations

from dataclasses import dataclass

from canon import Block, BlockType, CanonicalDoc
from extract.base import BlockBuilder
from spec import Profile

#: Block types that are furniture, never structure — a repeated page header reading
#: "BAB II" is still a page header.
NEVER_STRUCTURAL = (
    BlockType.PAGE_HEADER,
    BlockType.PAGE_FOOTER,
    BlockType.TABLE_REF,
    BlockType.FIGURE_REF,
)

#: A line whose horizontal centre sits within this share of the page width of the page
#: centre counts as centered. Loose enough to survive ragged extraction.
CENTRE_TOLERANCE = 0.08


@dataclass(frozen=True, slots=True)
class Structurer:
    """Assigns headings to canonical blocks according to a profile."""

    profile: Profile

    @property
    def ref(self) -> str:
        return self.profile.ref

    @property
    def trust_typography(self) -> bool:
        """Whether the extractor's typographic heading guesses are kept.

        ! False for any profile with real structure patterns, and that is the point.
        Where typography scores 4% precision its guesses are noise; keeping them
        buries the real hierarchy under ~7,000 false headings per 50 documents. A
        profile that has no patterns of its own (`generic`) sets this True, because
        then typography is the only signal there is.
        """
        return bool(self.profile.option("trust_typography", False))

    def apply(self, doc: CanonicalDoc) -> CanonicalDoc:
        builder = BlockBuilder()
        promoted = demoted = 0

        for block in doc.ordered():
            unit = self._unit_for(block)

            if unit is None:
                drop_heading = block.type is BlockType.HEADING and not self.trust_typography
                demoted += drop_heading
                builder.add(
                    BlockType.PARAGRAPH if drop_heading else block.type,
                    block.text,
                    level=None if drop_heading else block.level,
                    page=block.page,
                    bbox=block.bbox,
                    confidence=block.confidence,
                    ref=block.ref,
                    attrs=block.attrs,
                )
                continue

            promoted += 1
            builder.add(
                BlockType.HEADING,
                block.text,
                level=unit.level,
                page=block.page,
                bbox=block.bbox,
                confidence=block.confidence,
                attrs={**block.attrs, "unit": unit.name},
            )

        params = dict(doc.extraction.params) | {
            "profile": self.profile.ref,
            "profile_hash": self.profile.config_hash,
            "headings_promoted": promoted,
            "headings_demoted": demoted,
        }
        return doc.model_copy(
            update={
                "blocks": builder.blocks,
                "extraction": doc.extraction.model_copy(update={"params": params}),
            }
        )

    def _unit_for(self, block: Block):  # noqa: ANN202 — spec.models.Unit | None
        if block.type in NEVER_STRUCTURAL:
            return None
        return self.profile.structural(block.text)


def is_centered(bbox: tuple[float, float, float, float] | None, page_width: float) -> bool:
    if not bbox or page_width <= 0:
        return False
    middle = (bbox[0] + bbox[2]) / 2
    return abs(middle - page_width / 2) < page_width * CENTRE_TOLERANCE
