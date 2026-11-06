"""Is this document worth indexing? (`QUALITY.md`)

`extract/quality.py` asks the question of a PDF's text layer, before extraction. This asks it
of the finished chunks, because a Markdown corpus arrives already extracted: the OCR was done
years ago by somebody else, and what is left to judge is the words.

A bad scan is not a document with a few typos. `Peroluron Doeroh Kobupolen Kediri` reads
fine to a person and matches nothing a person would type. Counting words nobody ever uses
separates it from a clean document by an order of magnitude (median 0.6% unknown words;
the worst 1% of documents sit above 30%), where bare-number lines, short words and symbol
share all overlap with legitimate tables and annexes.

The unit judged is the **chunk**, not the file. Damage is local: a stamp, a page, an annex
of figures. Judging the whole file by its worst page would throw away 250-page regulations
for one bad scan; judging it by its average would index a document that is half noise.

! Nothing here changes a chunk. The verdict is a sidecar (`CLAUDE.md` §5.4); `indexable`
tells the index half which documents to skip, and the chunks stay in the knowledge base.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from spec import Profile
from spec.loader import registry_root

__all__ = ["DocQuality", "Lexicon", "QualityGate", "Verdict"]

Verdict = Literal["clean", "degraded", "unusable", "absent"]

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_REPLACEMENT = "�"


@dataclass(frozen=True, slots=True)
class Lexicon:
    """Words a clean document uses. Data in the registry, not a list in the code."""

    words: frozenset[str]
    sha256: str

    @classmethod
    def load(cls, path: Path) -> Lexicon:
        # ! Line endings are normalised before hashing: git on Windows may check the file out
        # with CRLF, and the same list must not hash differently on two machines.
        raw = path.read_bytes().replace(b"
", b"
")
        words = frozenset(
            line.strip().lower()
            for line in raw.decode("utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        )
        return cls(words, hashlib.sha256(raw).hexdigest())

    @classmethod
    def empty(cls) -> Lexicon:
        return cls(frozenset(), "")

    def __len__(self) -> int:
        return len(self.words)


@dataclass(frozen=True, slots=True)
class DocQuality:
    """A verdict and the evidence for it, one row per document."""

    doc_id: str
    verdict: Verdict
    indexable: bool
    chunks: int
    damaged_chunks: int
    words: int
    """Judged words: letters only, at least `min_word_chars` long."""
    unknown_share: float
    damaged_share: float
    """Share of judged words that sit in damaged chunks."""
    replacement_per_1000: float
    reasons: tuple[str, ...]


class QualityGate:
    """Scores a document's chunks. Pure: the same chunks and profile give the same verdict."""

    def __init__(self, profile: Profile, registry: Path | str | None = None) -> None:
        self.spec = profile.spec.quality
        self.lexicon = Lexicon.empty()
        if self.spec.lexicon:
            path = registry_root(registry) / self.spec.lexicon
            if not path.is_file():
                raise FileNotFoundError(
                    f"{profile.ref}: quality.lexicon {self.spec.lexicon!r} not found at {path}"
                )
            self.lexicon = Lexicon.load(path)

    @property
    def hash(self) -> str:
        """What the verdicts depend on: the thresholds and the exact word list."""
        digest = hashlib.sha256()
        digest.update(self.spec.model_dump_json().encode("utf-8"))
        digest.update(self.lexicon.sha256.encode("ascii"))
        return digest.hexdigest()[:16]

    # ---- a chunk ------------------------------------------------------------------
    def _count(self, text: str) -> tuple[int, int]:
        """(judged words, of which unknown).

        ! Table rows are not judged. A table of districts, villages and names is made of words
        no word list holds, and a 7,800-character annex of fertiliser quotas per sub-district
        read as 63% noise. Damage in a table still shows in the prose around it.
        """
        minimum = self.spec.min_word_chars
        acronym = self.spec.acronym_max_chars
        judged = unknown = 0
        words = self.lexicon.words
        if "|" in text:
            text = "\n".join(
                line for line in text.splitlines() if not line.lstrip().startswith("|")
            )
        for match in _WORD.finditer(text):
            word = match.group()
            if len(word) < minimum or (len(word) <= acronym and word.isupper()):
                continue
            judged += 1
            if words and word.lower() not in words:
                unknown += 1
        return judged, unknown

    def chunk_damaged(self, text: str) -> bool:
        """Whether one chunk body is noise. Also what the index half can ask per chunk."""
        judged, unknown = self._count(text)
        return self._damaged(text, judged, unknown)

    def _damaged(self, text: str, judged: int, unknown: int) -> bool:
        replacement = 1000 * text.count(_REPLACEMENT) / max(len(text), 1)
        if replacement >= self.spec.chunk_replacement > 0:
            return True
        if judged < self.spec.min_chunk_words:
            return False
        return unknown / judged >= self.spec.chunk_unknown

    # ---- a document ---------------------------------------------------------------
    def score(self, doc_id: str, bodies: Iterable[str]) -> DocQuality:
        s = self.spec
        chunks = damaged_chunks = words = unknown_words = damaged_words = chars = repl = 0
        for body in bodies:
            chunks += 1
            judged, unknown = self._count(body)
            words += judged
            unknown_words += unknown
            chars += len(body)
            repl += body.count(_REPLACEMENT)
            if self._damaged(body, judged, unknown):
                damaged_chunks += 1
                damaged_words += judged

        unknown_share = unknown_words / words if words else 0.0
        damaged_share = damaged_words / words if words else 0.0
        replacement = 1000 * repl / chars if chars else 0.0

        reasons: list[str] = []
        verdict: Verdict
        if words < s.absent_max_words:
            verdict = "absent"
            reasons.append(f"{words} words in {chunks} chunk(s): no text layer worth reading")
        elif damaged_share >= s.unusable_share:
            verdict = "unusable"
            reasons.append(
                f"{damaged_share:.0%} of words sit in damaged chunks "
                f"({damaged_chunks} of {chunks})"
            )
        else:
            if damaged_chunks:
                reasons.append(f"{damaged_chunks} of {chunks} chunks damaged")
            if unknown_share >= s.degraded_unknown:
                reasons.append(f"{unknown_share:.0%} unknown words")
            if replacement >= s.degraded_replacement:
                reasons.append(f"{replacement:.1f} replacement characters per 1000")
            verdict = "degraded" if reasons else "clean"
        return DocQuality(
            doc_id=doc_id,
            verdict=verdict,
            indexable=verdict not in s.not_indexable,
            chunks=chunks,
            damaged_chunks=damaged_chunks,
            words=words,
            unknown_share=unknown_share,
            damaged_share=damaged_share,
            replacement_per_1000=replacement,
            reasons=tuple(reasons),
        )
