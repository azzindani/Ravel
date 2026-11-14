"""The domain gate's floor, measured on the prepared database and recorded in `corpus_meta`.

    ravel db calibrate <corpus> --dsn ... --queries <labelled set>

Vera refuses a query whose nearest cluster centroid is less similar than a floor. That
floor is a property of the corpus's vector space AND of its clusters, so it belongs
beside them: measured on the clusters Vera will route by, written into
`corpus_meta.gate`, read by the engine at startup. A floor kept in the engine's
configuration was fitted once, on another corpus (0.39), and on this one sat below the
median of the OFF-topic questions (Vera `docs/EVAL.md` §4c-ter).

The rule is deliberately simple, and recorded with its inputs:

    floor = (lowest in-domain similarity seen) - margin, rounded down to 0.01

* **No real question in the set is refused**, by construction. The margin is for the
  real questions the set does not hold (on 193 cases a floor fitted on half refused one
  of the other half).
* **The lexical floor is 0.** Its signal does not separate the two populations on this
  corpus (AUC 0.52) and caused every false refusal; it is still reported by the engine.
* **What it buys is reported, never assumed:** off-topic refused at that floor, and the
  AUC of the signal, so a corpus whose space does not separate the two is visible.

The queries are the eval set's format: `{"query", "type"}` where `type ==
"out_of_domain"` marks a question the corpus cannot answer, and `exact_ref` (a named
regulation, which the engine's gate exempts) is left out.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["CalibrationError", "GateCalibration", "calibrate_gate", "load_cases"]

#: Fewer than this and the "lowest in-domain similarity" is one unlucky question.
MIN_IN_DOMAIN = 30
#: Fewer than this and "off-topic refused" says nothing.
MIN_OUT_OF_DOMAIN = 10


class CalibrationError(RuntimeError):
    """The floor could not be measured. Nothing was written."""


@dataclass(frozen=True)
class GateCalibration:
    centroid_floor: float
    lexical_floor: float
    margin: float
    in_domain: int
    out_of_domain: int
    lowest_in_domain: float
    off_topic_refused: int
    auc: float
    clusters: int
    queries_sha256: str
    calibrated_at: str


def load_cases(path: str) -> list[dict[str, Any]]:
    """The eval set's cases, from a JSON document (`{"queries": [...]}` or a list) or JSONL."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        cases = data["queries"] if isinstance(data, dict) else data
    except json.JSONDecodeError:
        cases = [json.loads(line) for line in text.splitlines() if line.strip()]
    return [c for c in cases if c.get("query")]


def _auc(pos: Sequence[float], neg: Sequence[float]) -> float:
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def calibrate_gate(
    conn: Any,
    corpus_id: str,
    cases: Sequence[dict[str, Any]],
    embed_queries: Callable[[list[str]], Any],
    *,
    margin: float = 0.02,
    write: bool = True,
) -> GateCalibration:
    """Measure the floor on this database's clusters; record it unless `write` is false."""
    real = [c["query"] for c in cases if c.get("type") not in ("out_of_domain", "exact_ref")]
    off = [c["query"] for c in cases if c.get("type") == "out_of_domain"]
    if len(real) < MIN_IN_DOMAIN or len(off) < MIN_OUT_OF_DOMAIN:
        raise CalibrationError(
            f"{len(real)} in-domain and {len(off)} off-topic questions: at least "
            f"{MIN_IN_DOMAIN} and {MIN_OUT_OF_DOMAIN} are needed for a floor worth recording"
        )
    rows = conn.execute(
        "SELECT centroid::text FROM clusters WHERE corpus_id = %s ORDER BY id", (corpus_id,)
    ).fetchall()
    if not rows:
        raise CalibrationError(f"{corpus_id} has no clusters: prepare the database first")
    centroids = np.array(
        [np.array(json.loads(r[0]), dtype=np.float32) for r in rows], dtype=np.float32
    )
    centroids /= np.linalg.norm(centroids, axis=1, keepdims=True)

    vectors = np.asarray(embed_queries(real + off), dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    # Cosine to the nearest centroid, exactly what `engine::select_clusters` ranks by.
    nearest = (vectors @ centroids.T).max(axis=1)
    sims_real, sims_off = nearest[: len(real)].tolist(), nearest[len(real) :].tolist()

    lowest = min(sims_real)
    floor = math.floor((lowest - margin) * 100) / 100
    digest = hashlib.sha256(
        "\n".join(sorted(real) + ["--"] + sorted(off)).encode("utf-8")
    ).hexdigest()
    result = GateCalibration(
        centroid_floor=floor,
        lexical_floor=0.0,
        margin=margin,
        in_domain=len(real),
        out_of_domain=len(off),
        lowest_in_domain=round(lowest, 4),
        off_topic_refused=sum(s < floor for s in sims_off),
        auc=round(_auc(sims_real, sims_off), 4),
        clusters=len(rows),
        queries_sha256=digest,
        calibrated_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    if write:
        conn.execute("ALTER TABLE corpus_meta ADD COLUMN IF NOT EXISTS gate JSONB")
        conn.execute(
            "UPDATE corpus_meta SET gate = %s::jsonb WHERE id = %s",
            (json.dumps(asdict(result)), corpus_id),
        )
    return result
