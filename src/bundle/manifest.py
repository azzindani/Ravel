"""The bundle manifest — the recipe, recorded with the data it produced.

`CLAUDE.md` §6: the manifest is part of the corpus. Without it a bundle is unverifiable
and Vera's startup canary has nothing to check.

This is modelled directly on the `corpus_meta` table in Vera's `schema.sql`, which exists
for a reason worth quoting:

    ! This table is the whole lesson of the August/November drift. Vectors whose recipe
    is not written down cannot be verified, extended, or trusted. The engine validates a
    query embedding against THIS ROW, not against a constant compiled into the binary.

Ravel's manifest is deliberately **stricter than `corpus_meta`** in two places, because
the survey measured what each omission costs:

- `padding_side` (`ABSORPTION.md` §11). `corpus_meta` has no column for it. Under
  last-token pooling it decides which token is read, and the two local Qwen3 encoders
  disagree, so a corpus built with one and queried with the other is silently wrong.
- `instruction_style` with separate document and query strings. `corpus_meta` has a
  single `dense_instruction TEXT`, which cannot express "prefix" versus "system turn in a
  chat template", nor that the two sides differ. The incumbent's instruction was never
  applied at all, and one nullable text column would have recorded that as NULL —
  indistinguishable from a model that legitimately takes none.

And one field is borrowed straight back from `corpus_meta`, because it is a good idea
this project did not have: `sparse_fit_docs`. It makes §7's real defect **visible in the
manifest** rather than discoverable by reading the embedding script.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from embed.spec import EmbedderSpec

__all__ = ["BundleManifest", "SparseSpec", "ManifestError"]


class ManifestError(ValueError):
    """A manifest that cannot describe a reproducible corpus."""


@dataclass(frozen=True, slots=True)
class SparseSpec:
    """BM25 vocabulary and weights — the half of the recipe that is usually lost.

    The incumbent's tf-idf vectors were 20,000-dimensional and unusable, not because the
    maths was wrong but because the vocabulary did not travel with them. `ABSORPTION.md`
    §7 first recorded that as "the vocabulary was lost" and the fourth pass corrected it
    to something worse: it was **fragmented**. `fit_transform` ran per 1,000-row chunk, so
    the corpus holds roughly a thousand vocabularies and a thousand IDF weightings, each
    saved correctly beside its own shard, none of them comparable with any other.

    Fragmentation looks healthy. That is why `fit_docs` is required here and why
    `check_vocabulary_covers_corpus` exists: a vocabulary fitted on 1,000 of 748,558
    documents is a defect the manifest can state out loud.
    """

    scheme: str
    dim: int
    k1: float
    b: float
    vocab_sha256: str
    fit_docs: int
    """How many documents the vocabulary and IDF were fitted over. Compared against the
    corpus size by `check_vocabulary_covers_corpus`."""

    def __post_init__(self) -> None:
        if self.dim <= 0:
            raise ManifestError(f"sparse dim must be positive, got {self.dim}")
        if self.fit_docs <= 0:
            raise ManifestError(
                "sparse fit_docs must be positive — a vocabulary fitted over no documents "
                "cannot project a query into the space"
            )
        if len(self.vocab_sha256) != 64:
            raise ManifestError(
                f"vocab_sha256 must be a full sha256 digest, got "
                f"{len(self.vocab_sha256)} chars. "
                "The vocabulary is the recipe; a truncated hash cannot verify it."
            )


@dataclass(frozen=True, slots=True)
class BundleManifest:
    """Everything needed to rebuild, verify or load a corpus bundle."""

    corpus_id: str
    run_id: str
    chunk_count: int
    source_manifest_sha256: str
    dense: EmbedderSpec
    sparse: SparseSpec | None = None
    chunker: str = ""
    chunker_version: str = ""
    profile_ref: str = ""
    text_search_config: str = "simple"
    """! `indonesian`, not `simple`, for this corpus. Indonesian is heavily affixed:
    `dikenakan` and `dikenai` are one word inflected, and `simple` indexes them as two
    unrelated terms (`ABSORPTION.md` §8.3). Defaulted to `simple` because it is the
    language-neutral choice, and set per corpus."""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    notes: str = ""

    def __post_init__(self) -> None:
        if self.chunk_count < 0:
            raise ManifestError(f"chunk_count cannot be negative, got {self.chunk_count}")

    # -- identity ------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["dense"] = self.dense.manifest()
        return out

    def fingerprint(self) -> str:
        """Hash of the recipe, excluding what does not change the data.

        `run_id`, `created_at` and `notes` are excluded deliberately: rebuilding the same
        corpus twice must produce the same fingerprint, or the manifest cannot be used to
        decide whether a rebuild is needed.
        """
        payload = {
            k: v
            for k, v in self.to_dict().items()
            if k not in {"run_id", "created_at", "notes"}
        }
        canonical = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(
            {"manifest_sha256": self.fingerprint(), **self.to_dict()},
            indent=indent,
            default=str,
            ensure_ascii=False,
        )


# -- checks that belong to the manifest rather than the loader -------------------


def check_vocabulary_covers_corpus(
    manifest: BundleManifest, *, min_ratio: float = 0.5
) -> tuple[bool, str]:
    """Was the sparse vocabulary fitted over the corpus, or over one shard of it?

    ! This is §7's defect made checkable. A vocabulary fitted on 1,000 rows of a 748,558
    row corpus is not a smaller vocabulary — it is a *different space per shard*, and
    nothing downstream can tell, because each shard's vectors are internally consistent
    and its vocabulary was saved correctly beside it.
    """
    if manifest.sparse is None:
        return True, "no sparse arm declared"

    fit, total = manifest.sparse.fit_docs, manifest.chunk_count
    if total == 0:
        return True, "empty corpus"
    ratio = fit / total
    if ratio < min_ratio:
        return False, (
            f"sparse vocabulary fitted over {fit:,} of {total:,} chunks ({ratio:.1%}). "
            f"A vocabulary fitted per shard gives every shard its own IDF and its own "
            f"space; the vectors are not comparable across them (ABSORPTION.md §7)."
        )
    return True, f"vocabulary fitted over {fit:,} of {total:,} chunks ({ratio:.0%})"


def check_provenance(manifest: BundleManifest, missing_source_urls: int) -> tuple[bool, str]:
    """How much of this bundle can actually be cited.

    ! Not an error, and deliberately so. The incumbent corpus has no `source_url` on
    100% of its rows and is still worth loading as a scored baseline — but it must be
    loaded **flagged**, not dressed up as verifiable (`ABSORPTION.md` §3, §19).
    """
    if manifest.chunk_count == 0:
        return True, "empty corpus"
    share = missing_source_urls / manifest.chunk_count
    if share == 0:
        return True, "every chunk carries a source_url"
    return False, (
        f"{missing_source_urls:,} of {manifest.chunk_count:,} chunks ({share:.1%}) have no "
        f"source_url — this bundle is provenance-incomplete and must be labelled as such"
    )
