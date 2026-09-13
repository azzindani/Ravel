"""What a chunk is.

A chunk is the unit Vera retrieves and a human verifies. Everything else in this
project exists to produce these rows correctly.

Two properties are load-bearing and are enforced here rather than by convention:

**`body` is verbatim.** It is the extracted text, joined — never summarized, never
rewritten, never augmented with a heading to help the embedder. Derived text is
enrichment and lives in signals (`ENRICHMENT.md` §2), because `body` is what a reader
checks against the original. A chunk whose body says something the source does not is
worse than no chunk.

**Provenance is captured at creation and immutable.** Vera enforces this in the
database with a trigger; Ravel must never be the reason it fires (`CHUNKING.md` §4).
There is no repair path on purpose — a wrong citation is fixed by re-chunking from the
canonical document, which produces new ids, so nobody's recorded citation silently
starts pointing at different text.
"""

from __future__ import annotations

import hashlib
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Joined into `heading_path` and `locator_section`. A visible separator that does not
#: occur in legal or technical prose, so the parts stay recoverable by splitting.
PATH_SEP = " › "


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Chunk(Base):
    """One retrievable unit of a corpus (`CHUNKING.md` §5)."""

    id: str
    doc_id: str

    body: str = Field(min_length=1)
    """! Verbatim. See the module docstring."""

    token_count: int = Field(ge=0)
    part: tuple[int, int] = (1, 1)
    """`(n, m)` — piece n of m, when one structural unit had to be split by size."""

    block_ids: list[str] = Field(default_factory=list)
    """Traceability back into the canonical document, to the exact bounding boxes.

    This is what turns "this retrieval result is wrong" into a five-minute
    investigation instead of a re-run.
    """

    # -- provenance (immutable after creation) ------------------------------
    source_title: str
    source_url: str
    """! Not nullable, and never synthesized.

    All 748,558 rows of the incumbent corpus have no source_url because the pipeline
    that built it never carried one, and a citation cannot be back-filled
    (`ABSORPTION.md` §3). A corpus definition supplies `provenance.base_url` or its
    documents do not enter the corpus.
    """

    source_sha256: str
    """Lets a source that changes upstream be detected rather than silently diverge."""

    locator_page: int | None = Field(default=None, ge=1)
    locator_section: str | None = None
    heading_path: str | None = None
    identifier: str | None = None
    """The canonical citation, when the profile can read one.

    Feeds Vera's exact-match keyword bypass, which is how a query naming a specific
    regulation never loses it to semantic drift.
    """

    # -- lineage ------------------------------------------------------------
    chunker: str
    chunker_version: str
    config_hash: str
    profile: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> Self:
        n, total = self.part
        if n < 1 or total < 1 or n > total:
            raise ValueError(f"part {self.part} is not a valid piece n of m")
        if not self.source_url.strip():
            raise ValueError("source_url is required and must not be blank")
        if self.body != self.body.strip():
            raise ValueError("body carries leading or trailing whitespace")
        return self


def chunk_id(
    *,
    doc_id: str,
    chunker: str,
    version: str,
    config_hash: str,
    locator: str,
    body: str,
    anchor: str = "",
) -> str:
    """The deterministic chunk id of `CHUNKING.md` §6.

    Every consequence is intended:

    - Re-running produces identical ids, so writes are idempotent and resume is safe.
    - Changing the chunker or its config changes every id, so two variants of the
      same corpus cannot collide in one table.
    - Changing the body changes the id, so an edited chunk is a *new* chunk. The old
      one cannot be quietly rewritten underneath a citation someone already recorded.

    `anchor` is the document's own coordinate for the chunk — its first block id.

    ! Without it the formula assumes a document never repeats short content at the
    same locator, and real ones do constantly: `KETENTUAN PENUTUP` under `BAB III`,
    a tariff row under `Pasal 16 huruf a`, `PENUTUP` under `BAB VI`. Measured on the
    ID_Legal corpus, **710 of 121,757 chunks collided** — distinct text at distinct
    positions hashing to one id. Vera keys on this, so the second row of each pair
    would have been rejected or would have overwritten the first, and 710 chunks
    would have left the corpus without a count ever looking wrong.

    Parts are separated by a NUL, which cannot occur in any of the fields, so
    ``("ab", "c")`` and ``("a", "bc")`` cannot hash alike.
    """
    material = "\0".join([doc_id, chunker, version, config_hash, locator, anchor, body])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
