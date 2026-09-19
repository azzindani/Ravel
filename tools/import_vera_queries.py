"""Import Vera's hand-labelled seed set into Ravel's eval format.

`ABSORPTION.md` §13 (retracted and corrected): `dev_tools/eval/queries.json` in
github.com/azzindani/Vera holds 50 hand-written Indonesian cases across 12 question
shapes, each labelled by opening the clause and phrasing the question the way a person
would ask it. That is the non-circular seed `eval/README.md` asks for, and it is the only
ground truth either project has.

The source argument is a local path, not a URL: this reads a working copy so that a label
edit and its re-import are one step, rather than a fetch of whatever happens to be on
`main`.

A script rather than a one-off edit, because the conversion makes four lossy decisions
and every one of them should be re-runnable and reviewable:

1. **Chunk ids are dropped.** `EVAL.md` §1: relevance is a document + locator, never a
   chunk id -- ids change with every chunker, which is the entire point of variants.
   `answer_chunks_spike01` names rows in one spike's corpus and cannot survive a
   re-chunk. The ids are preserved in `notes` (they are the provenance of the original
   labelling and cannot be re-derived once dropped), but they are not relevance.
2. **Twelve question shapes collapse to five.** Vera's shapes describe *question intent*;
   Ravel's `QueryType` describes *which retrieval path is exercised*. The original shape
   is preserved in `notes` so the finer distinction is not lost.
3. **`hard_negative` maps to `factual`, not `negative`.** It has a correct answer -- the
   "negative" is a set of documents that must not outrank it. Ravel's schema has nowhere
   to put that, and calling it a negative would assert the corpus cannot answer it, which
   `LabeledQuery.__post_init__` then enforces as a contradiction.
4. **Regulation short forms come from the profile**, never from a map written here
   (`CLAUDE.md` §12: corpus knowledge is data, not code).

Usage:
    python tools/import_vera_queries.py ../Vera/dev_tools/eval/queries.json
        --profile registry/profiles/id_regulation@1.0.yaml
        --out eval/id_legal/queries@v1.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from evaluate.labels import (  # noqa: E402
    LabeledQuery,
    QuerySet,
    QueryType,
    Relevance,
    check_composition,
    check_size,
)

#: Vera's twelve question shapes -> Ravel's five retrieval paths. Every Vera shape is
#: listed explicitly: a mapping with a default silently absorbs a shape added later, and
#: the whole point of the composition check is that the mix stays visible.
SHAPE_TO_TYPE: dict[str, QueryType] = {
    "conceptual": QueryType.CONCEPTUAL,
    "authority": QueryType.FACTUAL,
    "numeric": QueryType.FACTUAL,
    "sanction": QueryType.FACTUAL,
    "exception": QueryType.FACTUAL,
    "obligation": QueryType.FACTUAL,
    "procedural": QueryType.FACTUAL,
    "underspecified": QueryType.FACTUAL,
    "hard_negative": QueryType.FACTUAL,
    "exact_ref": QueryType.EXACT_CITATION,
    "multi_tier": QueryType.CROSS_REFERENCE,
    "out_of_domain": QueryType.NEGATIVE,
}

#: The clause that was actually read and answers the question. `EVAL.md`'s scale: 3 = the
#: answering clause, 1 = related context. Every `answer_articles` entry in the seed set is
#: the former -- the labeller opened it and wrote the question from it.
ANSWERING_CLAUSE = 3


def load_abbreviations(profile: Path) -> dict[str, str]:
    """The short forms Indonesian citations actually use, read from the profile."""
    spec = yaml.safe_load(profile.read_text(encoding="utf-8"))
    for section in spec.values():
        if isinstance(section, dict) and "abbreviations" in section:
            abbrevs = section["abbreviations"]
            if isinstance(abbrevs, dict):
                return {str(k).upper(): str(v) for k, v in abbrevs.items()}
    raise SystemExit(f"{profile} declares no `abbreviations` map; nothing to cite with.")


def identifier(article: dict[str, Any], abbrevs: dict[str, str]) -> str:
    """`UU 30/2007` -- the form a query uses, and the form `identity.identifier` carries."""
    kind = str(article["regulation_type"]).strip().upper()
    short = abbrevs.get(kind)
    if short is None:
        raise SystemExit(
            f"the profile has no abbreviation for {kind!r}. Add it there rather than "
            f"here -- a short form invented in a script is the §12 defect reappearing."
        )
    return f"{short} {article['regulation_number']}/{article['year']}"


def compose_notes(case: dict[str, Any], shape: str) -> str:
    """Everything the target schema has no field for, kept rather than discarded."""
    parts = [f"vera_shape={shape}", f"difficulty={case.get('difficulty', '?')}"]
    if source := case.get("source"):
        parts.append(f"read={source}")
    if note := case.get("note"):
        parts.append(str(note))
    if blocked := case.get("must_not_rank_first"):
        parts.append(
            "must_not_rank_first (Vera spike01 chunk ids; no Ravel field expresses this "
            f"discrimination test yet): {', '.join(blocked)}"
        )
    if spike := case.get("answer_chunks_spike01"):
        parts.append(f"labelled against spike01 chunks {', '.join(spike)} -- not relevance")
    if case.get("expect_empty"):
        parts.append("expect_empty: the correct result is no result")
    return " · ".join(parts)


def convert(case: dict[str, Any], abbrevs: dict[str, str]) -> LabeledQuery:
    shape = str(case["type"])
    if shape not in SHAPE_TO_TYPE:
        raise SystemExit(f"{case['id']}: unmapped question shape {shape!r}")
    kind = SHAPE_TO_TYPE[shape]

    relevant: list[Relevance] = []
    if kind is not QueryType.NEGATIVE:
        for article in case.get("answer_articles", []):
            relevant.append(
                Relevance(
                    doc=identifier(article, abbrevs),
                    locator=str(article.get("article", "")),
                    grade=ANSWERING_CLAUSE,
                )
            )
        regulation = case.get("answer_regulation")
        if regulation:
            # exact_ref names the instrument, not a clause: locator stays empty, which
            # `Relevance` documents as the weaker but legitimate "anywhere in this
            # document" label.
            relevant.append(
                Relevance(doc=identifier(regulation, abbrevs), grade=ANSWERING_CLAUSE)
            )

    return LabeledQuery(
        qid=str(case["id"]),
        query=str(case["query"]),
        type=kind,
        relevant=tuple(relevant),
        notes=compose_notes(case, shape),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Vera's labelled seed set.")
    parser.add_argument("source", type=Path, help="Vera's dev_tools/eval/queries.json")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--version", default="v1-vera-seed")
    args = parser.parse_args()

    payload = json.loads(args.source.read_text(encoding="utf-8"))
    cases = payload["queries"] if isinstance(payload, dict) else payload
    abbrevs = load_abbreviations(args.profile)

    queries = QuerySet(
        items=tuple(convert(case, abbrevs) for case in cases),
        version=args.version,
        notes=(
            f"Imported from {args.source.as_posix()} by tools/import_vera_queries.py. "
            f"Hand-labelled, NOT yet domain-reviewed -- the source README's own warning."
        ),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    written = queries.save(args.out)

    print(f"wrote {written} queries -> {args.out}")
    print(f"composition: {queries.composition()}")
    for label, (ok, detail) in (
        ("size", check_size(queries)),
        ("mix", check_composition(queries)),
    ):
        print(f"  [{'ok' if ok else 'NOT OK'}] {label}: {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
