"""Vectors of unchanged chunks, taken from an earlier snapshot instead of embedded again.

    ravel embed <corpus> -w work/<corpus>/<new snapshot> --reuse work/<corpus>/<old snapshot>

A regulation corpus changes a little at a time. Canada is republished every two weeks, the
CFR title by title once a year, and LEGI daily. Each new snapshot is built in its own workspace
(`SOURCE_US_CFR.md` §2), and without this its embedding costs as much as the first one,
because hours of GPU are spent reproducing vectors that already exist.

* **Matched by what the model saw, ✗ by chunk id.** The key is the sha256 of `embed_text`,
  the exact string the embedder is sent. A chunk id hashes its locator and blocks and not its
  heading path, so a renamed parent heading keeps the id but changes the text, and reusing by
  id would keep the stale vector.
* **Only from the same vector space.** The earlier run's `embed.json` must name the same
  embedder fingerprint, or nothing is reused and the run stops (`CLAUDE.md` §7.7). Because
  `embed.json` is written only when a run completes, a half-finished run cannot be reused.
* **Across profile versions.** A newer profile may change the `embed_text` template. The
  earlier texts are then rebuilt with the profile that made them: the one `embed.json`
  names, or, for a run that predates the field, the version of the same profile whose
  template hash it records. A chunk whose text the new template leaves as it was keeps its
  vector; one it changes is embedded again. A template no registry version reproduces is
  refused, since the texts the vectors were made from can no longer be named.
* **Reproduction is checked, not assumed.** Before the first reused vector is written, a
  sample of reused texts is embedded again and must match its stored vector at cosine
  >= `MIN_COSINE`. This is the rule `bundle relabel` follows (`DATABASE.md` §4c).
* **The output does not change.** A reused vector is the vector that would have been
  computed, so window keys, shards and the bundle are the same as for a full embed, and
  `bundle delta` sees no difference.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from chunk.derive import embed_text
from chunk.store import iter_chunks
from spec import Profile
from spec.loader import Registry, default_registry

__all__ = ["MIN_COSINE", "ReuseError", "VectorReuse", "text_digest"]

#: A reused vector must be reproduced at least this closely (`bundle relabel`'s bar).
MIN_COSINE = 0.999
#: Reused texts embedded again before the first reused vector is written.
SAMPLE = 16
#: Earlier vector shards held in memory at once. New and old corpora share an order, so the
#: rows a window reuses come from one or two shards.
CACHED_SHARDS = 4


class ReuseError(ValueError):
    """An earlier run whose vectors cannot stand in for this run's."""


def text_digest(text: str) -> bytes:
    return hashlib.sha256(text.encode("utf-8")).digest()[:16]


class VectorReuse:
    """Index of earlier vectors by the digest of the text they embed."""

    def __init__(
        self,
        sources: Sequence[tuple[Path, Path]],
        *,
        profile: Profile | None,
        fingerprint: str,
        template_hash: str,
        dim: int,
        registry: Registry | None = None,
        routed: bool = False,
    ) -> None:
        self.dim = dim
        self.registry = registry
        self.paths: list[Path] = []
        self.index: dict[bytes, tuple[int, int]] = {}
        self.verified = False
        self._cache: OrderedDict[int, np.ndarray[Any, Any]] = OrderedDict()
        for chunk_dir, vector_dir in sources:
            meta_path = vector_dir / "embed.json"
            if not vector_dir.is_dir():
                raise ReuseError(
                    f"{vector_dir}: no vectors from this embedder ({fingerprint}) and "
                    "template there · another vector space, or never embedded (CLAUDE.md §7.7)"
                )
            if not meta_path.is_file():
                raise ReuseError(
                    f"{vector_dir}: no embed.json · that run did not finish, so its vectors "
                    "are not a complete record of anything"
                )
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            theirs = meta["embedder"]["fingerprint"]
            if theirs != fingerprint:
                raise ReuseError(
                    f"{vector_dir}: embedded by {theirs}, this run by {fingerprint} · "
                    "another vector space (CLAUDE.md §7.7)"
                )
            earlier = profile
            if meta["template_hash"] != template_hash:
                if routed:
                    # ! A routed run's texts are each chunk's own profile's: one changed
                    # template among them cannot be told from the hash, so none is rebuilt.
                    raise ReuseError(
                        f"{vector_dir}: embed_text templates {meta['template_hash']}, this "
                        f"run's {template_hash} · a routed corpus reuses only a run made "
                        "with the same registry templates"
                    )
                earlier = self._made_with(vector_dir, meta, profile)
            from embed.run import profile_named

            reg = self.registry or default_registry()
            digest_of: dict[str, bytes] = {}
            for shard in sorted(chunk_dir.glob("part-*.parquet")):
                for chunk in iter_chunks(str(shard)):
                    shown = profile_named(reg, chunk.profile) if routed else earlier
                    digest_of[chunk.id] = text_digest(embed_text(chunk, shown))
            for path in sorted(vector_dir.glob("part-*.parquet")):
                ids = pq.read_table(str(path), columns=["chunk_id"]).column(0).to_pylist()
                slot = len(self.paths)
                self.paths.append(path)
                for row, chunk_id in enumerate(ids):
                    digest = digest_of.get(chunk_id)
                    if digest is not None:
                        self.index.setdefault(digest, (slot, row))

    def _made_with(
        self, vector_dir: Path, meta: dict[str, Any], profile: Profile | None
    ) -> Profile | None:
        """The profile whose `embed_text` template made an earlier run's texts."""
        from embed.run import template_hash_of

        registry = self.registry or default_registry()
        wanted = meta["template_hash"]
        named = meta.get("profile")
        candidates = (
            [p for p in registry if p.ref == named]
            if named
            else [p for p in registry if profile is not None and p.id == profile.id]
        )
        found = next((p for p in candidates if template_hash_of(p) == wanted), None)
        if found is None:
            raise ReuseError(
                f"{vector_dir}: embed_text template {wanted}, made by "
                f"{named or 'a version of ' + (profile.id if profile else 'no profile')} "
                "that this registry does not hold · the texts the vectors were made from "
                "cannot be rebuilt"
            )
        return found

    def __len__(self) -> int:
        return len(self.index)

    def _shard(self, slot: int) -> np.ndarray[Any, Any]:
        if slot in self._cache:
            self._cache.move_to_end(slot)
            return self._cache[slot]
        column = pq.read_table(str(self.paths[slot]), columns=["embedding"]).column(0)
        matrix = np.asarray(column.combine_chunks().flatten(), dtype=np.float32).reshape(
            -1, self.dim
        )
        self._cache[slot] = matrix
        if len(self._cache) > CACHED_SHARDS:
            self._cache.popitem(last=False)
        return matrix

    def find(self, texts: Sequence[str]) -> tuple[list[int], np.ndarray[Any, Any]]:
        """`(positions, vectors)`: which of `texts` an earlier run embedded, and its vectors."""
        hits = [(i, self.index.get(text_digest(t))) for i, t in enumerate(texts)]
        found = [(i, at) for i, at in hits if at is not None]
        vectors = np.empty((len(found), self.dim), dtype=np.float32)
        for k, (_, (slot, row)) in enumerate(found):
            vectors[k] = self._shard(slot)[row]
        return [i for i, _ in found], vectors


def check_reproduced(stored: np.ndarray[Any, Any], fresh: np.ndarray[Any, Any]) -> float:
    """The lowest cosine between stored vectors and the same texts embedded now."""
    a = stored / np.linalg.norm(stored, axis=1, keepdims=True)
    b = fresh / np.linalg.norm(fresh, axis=1, keepdims=True)
    return float((a * b).sum(axis=1).min())
