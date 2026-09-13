"""BM25 sparse vectoriser — the lexical arm of hybrid search.

Ported near-verbatim from Vera's `dev_tools/pre_embed/sparse.py`, which was hand-rolled
rather than taken from sklearn for one reason, stated in its own docstring:

    the vocabulary and weights ARE the recipe. The corpus we inherited had tfidf vectors
    whose vocabulary was never saved, which made them unusable for query scoring —
    20,000 opaque integers with no term map.

`ABSORPTION.md` §7 first recorded that as "the vocabulary was lost" and the fourth pass
corrected it to something worse: it was **fragmented**. The vocabulary *was* saved — once
per 1,000-row shard, by a `fit_transform` that ran per chunk. A thousand vocabularies and
a thousand IDF weightings, each internally consistent, none comparable with any other, and
no symptom from inside any one of them.

What this port adds
-------------------
`fit()` records how many documents it saw, and `to_spec()` derives the manifest entry from
the fitted object rather than from a caller's claim. A `SparseSpec` is therefore not
something you assert — it is something the vectoriser hands you, and
`check_vocabulary_covers_corpus` compares it against the corpus size. The fragmented case
becomes a preflight failure with the numbers in it instead of a property you would have to
already suspect to go looking for.

Scoring is INNER PRODUCT, not cosine. Document vectors carry the BM25 weights; the query
vector carries presence per matched term, so the dot product *is* the BM25 score. Cosine
would re-normalise the document-length term BM25 has already handled deliberately.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

__all__ = ["K1", "B", "TOKEN_RE", "Bm25Vectorizer", "TokeniserDrift", "tokenize"]

# ! Part of the recipe. Documents and queries must tokenise identically; if this pattern
# changes, every vector in every corpus built with it is invalidated. `load()` refuses an
# artifact whose pattern differs rather than silently producing vectors that do not match.
TOKEN_RE = re.compile(r"(?u)\b\w\w+\b")

K1 = 1.5
B = 0.75


class TokeniserDrift(ValueError):
    """A saved vectoriser was fitted with a different tokeniser than this code uses."""


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


class Bm25Vectorizer:
    """Fit on a corpus; transform documents and queries into sparse vectors."""

    def __init__(
        self,
        vocab: dict[str, int],
        idf: dict[str, float],
        avgdl: float,
        n_docs: int,
        dim: int,
        k1: float = K1,
        b: float = B,
    ) -> None:
        self.vocab = vocab
        self.idf = idf
        self.avgdl = avgdl
        self.n_docs = n_docs
        self.dim = dim
        self.k1 = k1
        self.b = b

    # -- fitting ------------------------------------------------------------

    @classmethod
    def fit(
        cls,
        docs: Iterable[str],
        *,
        max_features: int = 20_000,
        k1: float = K1,
        b: float = B,
    ) -> Bm25Vectorizer:
        """Build vocabulary and IDF from an iterable of texts.

        ! Fit on the FULL corpus, even when only part of it will be embedded now. Fitting
        is text-only CPU work; doing it once over everything means later batches need no
        re-fit and no back-fill, because the vocabulary and IDF are already final. Fitting
        per batch instead is the defect in `ABSORPTION.md` §7 — and it does not announce
        itself, because each batch's vectors are perfectly consistent with each other.
        """
        df: Counter[str] = Counter()
        n_docs = 0
        total_len = 0

        for text in docs:
            toks = tokenize(text)
            if not toks:
                continue
            n_docs += 1
            total_len += len(toks)
            df.update(set(toks))

        # Top terms by document frequency, then sorted alphabetically so index assignment
        # is deterministic across runs — a vocabulary whose indices shift between runs
        # produces vectors that cannot be compared with the previous build's.
        keep = sorted(t for t, _ in df.most_common(max_features))
        vocab = {t: i for i, t in enumerate(keep)}
        idf = {t: math.log(1.0 + (n_docs - df[t] + 0.5) / (df[t] + 0.5)) for t in keep}
        avgdl = total_len / n_docs if n_docs else 0.0
        return cls(vocab, idf, avgdl, n_docs, len(keep), k1, b)

    # -- transforming -------------------------------------------------------

    def document(self, text: str) -> dict[int, float]:
        """BM25-weighted document vector, as {index: weight}."""
        toks = tokenize(text)
        if not toks:
            return {}
        tf = Counter(t for t in toks if t in self.vocab)
        dl = len(toks)
        norm = self.k1 * (1 - self.b + self.b * dl / self.avgdl) if self.avgdl else self.k1

        out: dict[int, float] = {}
        for term, f in tf.items():
            w = self.idf[term] * (f * (self.k1 + 1)) / (f + norm)
            if w > 0:
                out[self.vocab[term]] = w
        return out

    def query(self, text: str) -> dict[int, float]:
        """Query vector — presence-weighted, so the dot product IS the BM25 score."""
        toks = {t for t in tokenize(text) if t in self.vocab}
        return {self.vocab[t]: 1.0 for t in toks}

    def score(self, query: str, document: str) -> float:
        """The BM25 score, by the same arithmetic the database will do."""
        q, d = self.query(query), self.document(document)
        return sum(w * d.get(i, 0.0) for i, w in q.items())

    # -- pgvector interop ---------------------------------------------------

    def to_sparsevec(self, vec: dict[int, float]) -> str:
        """pgvector `sparsevec` literal — `{idx:val,...}/dim`, indices **1-based**."""
        if not vec:
            return "{}/" + str(self.dim)
        body = ",".join(f"{i + 1}:{v:.6g}" for i, v in sorted(vec.items()))
        return "{" + body + "}/" + str(self.dim)

    # -- persistence and the manifest ---------------------------------------

    def payload(self) -> dict[str, Any]:
        return {
            "scheme": "bm25",
            "k1": self.k1,
            "b": self.b,
            "dim": self.dim,
            "n_docs": self.n_docs,
            "avgdl": self.avgdl,
            "token_pattern": TOKEN_RE.pattern,
            "lowercase": True,
            "vocab": self.vocab,
            "idf": self.idf,
        }

    def sha256(self) -> str:
        """Digest of the artifact this vectoriser writes — its identity in the manifest."""
        return hashlib.sha256(self._blob().encode("utf-8")).hexdigest()

    def _blob(self) -> str:
        return json.dumps(self.payload(), ensure_ascii=False, sort_keys=True)

    def save(self, path: str | Path) -> str:
        """Write the vectoriser and return its sha256."""
        blob = self._blob()
        Path(path).write_text(blob, encoding="utf-8")
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_spec(self) -> Any:
        """The manifest entry for this arm, derived rather than asserted.

        ! This is the point of the port. `fit_docs` comes from the object that did the
        fitting, so a manifest cannot claim corpus-wide coverage for a vocabulary fitted
        on one shard. `bundle.check_vocabulary_covers_corpus` then compares it against the
        corpus size, and §7's defect is a preflight failure carrying its own numbers
        instead of something you must already suspect to find.
        """
        from bundle.manifest import SparseSpec

        return SparseSpec(
            scheme="bm25",
            dim=self.dim,
            k1=self.k1,
            b=self.b,
            vocab_sha256=self.sha256(),
            fit_docs=self.n_docs,
        )

    @classmethod
    def load(cls, path: str | Path) -> Bm25Vectorizer:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if d["token_pattern"] != TOKEN_RE.pattern:
            raise TokeniserDrift(
                f"tokeniser drift: artifact used {d['token_pattern']!r}, this code uses "
                f"{TOKEN_RE.pattern!r} — vectors would not match"
            )
        return cls(d["vocab"], d["idf"], d["avgdl"], d["n_docs"], d["dim"], d["k1"], d["b"])
