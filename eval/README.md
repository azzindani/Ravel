# eval/ — the labeled sets and the promotion record

Data, not code. The scorer lives in `src/evaluate/` (named `evaluate` rather than `eval`
so it does not shadow a builtin).

```
eval/
├── README.md
├── <corpus>/
│   └── queries@<version>.jsonl     frozen labeled sets
└── promotions.jsonl                which variant won, when, and by how much
```

## What a labeled query looks like

```jsonc
{
  "qid": "q0042",
  "query": "Berapa tarif PPh untuk penghasilan di atas 500 juta?",
  "type": "factual",              // factual | conceptual | exact_citation | cross_reference | negative
  "relevant": [
    { "doc": "UU 36/2008", "locator": "Pasal 17 ayat (1)", "grade": 3 }
  ],
  "notes": "…"
}
```

**Relevance is a document plus a locator, never a chunk id.** Chunk ids change with every
chunker — that is the entire point of variants — so a set keyed on them can compare a
corpus only with itself. A chunk counts as a hit when its provenance *covers* the labeled
locator, which is what lets a pasal-chunked corpus and an ayat-chunked corpus be scored
against the same labels (`EVAL.md` §1, `evaluate/metrics.py`).

`ravel eval check <file>` validates a set: size, composition drift, and the contradictions
the loader refuses outright (a negative with an answer; a positive with none).

## Discipline

- **Freeze before sweeping.** The version is part of every report, and two numbers from
  different set versions refuse to be compared — a set that moves with the corpus cannot
  compare anything.
- **Hold out ~20%.** `QuerySet.split()` is stratified and seeded. Sweeping against the
  whole set overfits the only ground truth there is.
- **Report the interval, not the mean.** A 1.5-point Recall@50 difference on 200 queries
  is usually noise. `EvalReport.beats()` refuses a win whose confidence intervals overlap.

## What not to generate

Two traps, both measured in `ABSORPTION.md` §13 and §18.4:

- A set built from templates filled with the same metadata the index is keyed on
  (`"Apa bunyi lengkap {article} dalam {reg_name} Nomor {reg_number} Tahun {year}?"`)
  scores 100% on exact-citation **by construction**. It looks like 750,000 pairs and
  measures a string format. A question containing its own answer's address is not a query.
- Unreviewed synthetic labels measure the generator, not the corpus.

The non-circular seed is real questions asked by real people and answered by lawyers who
cite the regulation. Collecting them is `Krawl`'s job, not Ravel's (`CLAUDE.md` §7.9).

## promotions.jsonl

Append-only. Variant, config hash, bundle version, eval run, set version, metrics,
interval, margin, build cost, decision, rationale — one line per decision, never rewritten.

In six months "why is the corpus chunked this way?" must have a data answer rather than an
archaeology project. That single property is the difference between Ravel and the corpus
it replaces.
