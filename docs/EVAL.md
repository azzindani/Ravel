# EVAL.md — Ravel

How a variant is scored and promoted. Without this doc the rest of Ravel is a very
organized way to produce corpora nobody can compare.

> Vera has its own `EVAL.md` for tuning the *query* side (`clusters_probed`, RRF weights,
> thresholds). Ravel's eval tunes the *corpus* side (chunking, template, model, k). They
> share the labeled set and must not be tuned simultaneously — change one thing at a time
> or neither result means anything.

---

## 1. The labeled set

The scarce resource. Build it once, carefully, and defend it.

Each item:

```jsonc
{
  "qid": "q0042",
  "query": "Berapa tarif PPh untuk penghasilan di atas 500 juta?",
  "relevant": [
    { "doc": "UU 36/2008", "locator": "Pasal 17 ayat (1)", "grade": 3 }
  ],
  "type": "factual" | "conceptual" | "exact_citation" | "cross_reference" | "negative",
  "notes": "…"
}
```

**Relevance is recorded as a document + locator, never as a chunk id.** Chunk ids change
with every chunker — that is the whole point of variants. A chunk counts as a hit when
its provenance covers the labeled locator. This is what makes two differently-chunked
corpora comparable at all, and getting it wrong makes the eval set worthless.

Composition targets:
- ~40% factual lookups (the common case)
- ~20% exact citation ("apa isi Pasal 9 UU 28/2007") — tests the identifier path
- ~20% conceptual/paraphrase — tests the embedder
- ~10% cross-reference — tests chunking context
- ~10% **negative** queries with no answer in the corpus — tests the honest-empty path

Negatives are the ones people skip and the ones that catch a system confidently
returning something irrelevant.

Aim for 150–300 items to start. Below ~100 the noise exceeds the effect sizes you care
about, and you will promote a variant on a coin flip.

---

## 2. Metrics

| Metric | Answers |
|---|---|
| Recall@k (k = 5, 20, 50) | is the right clause in the candidate set at all |
| MRR / nDCG@10 | is it near the top |
| Exact-citation recall | is a named regulation *ever* missed — target 100% |
| Empty-rate on negatives | does it correctly return nothing |
| Chunk count / corpus size | cost of the gain |
| Build cost (GPU-hours) | cost of the build |

**Recall@50 is the corpus-side metric.** Ranking within the candidate set is Vera's job;
if a chunk is not in the candidate set, no ranking change can save it. Ravel is judged on
whether the right text is retrievable at all.

Exact-citation recall below 100% is a bug, not a tuning result.

---

## 3. Isolating the corpus from the engine

To score chunking independently of Vera's routing, Ravel evaluates in two modes:

1. **Oracle mode** — brute-force search over the whole variant's vectors, no routing.
   Measures the ceiling the chunking + embedding achieve. This is the corpus-quality
   number.
2. **Routed mode** — through Vera against a loaded bundle. Measures what a user actually
   gets, including routing loss.

The gap between them is the routing loss, and it is diagnostic: a big gap means
clustering or `clusters_probed` needs work, not chunking. Reporting one number without
the other has repeatedly led people to re-chunk when their real problem was routing.

---

## 4. Comparison discipline

- **Same labeled set, same metrics, same seed.** A variant comparison changes exactly one
  dimension.
- **Report noise.** Bootstrap a confidence interval over the query set. A 1.5-point
  Recall@50 difference on 200 queries is usually noise, and treating it as signal is how
  you end up with a corpus tuned to randomness.
- **Always include the baseline arm** (`token512`) so results are anchored across months.
- **Sample first.** Score variants on a stratified sample before committing GPU-days;
  only finalists run full-size.
- **Hold out.** Keep ~20% of the labeled set unused during sweeps and check the winner
  against it once. Sweeping against the whole set overfits it — and the set is the only
  ground truth you have.

---

## 5. The promotion record

Promotion is recorded, not remembered: variant, bundle version, eval run id, metrics,
confidence interval, margin over incumbent, build cost, decision and rationale. Kept in
`eval/promotions.jsonl`.

In six months the question "why is the corpus chunked this way?" must have a data answer,
not an archaeology project. That single property is the difference between Ravel and
ID_Legal.

---

## 6. Bootstrapping the first set

ID_Legal is the first corpus and has no labeled set yet. Path:

1. Mine real questions from ID_Legal's existing conversation logs if any exist.
2. Generate candidates with an LLM from known regulations, then **review every one by
   hand**. Unreviewed synthetic labels measure the generator, not the corpus.

   > **Do not expect a head start from the QAI notebooks.** They look like the seed of
   > this set and are not (`ABSORPTION.md` §13): all three run `flan-t5-large` over 4–20
   > rows of an **English medical** dataset with the prompt
   > `"Generate a question related to: {context}"`, and their `data/` output directory is
   > empty.
   >
   > **And do not use the one that *is* Indonesian and legal.**
   > `id-reg-qa-generation-v*` generates at real scale — 64 shards, 10 pairs per row —
   > from 10 question templates filled with the same metadata the index is keyed on,
   > answered with the chunk verbatim (`ABSORPTION.md` §18.4):
   >
   > ```
   > "Apa bunyi lengkap {article} dalam {reg_name} Nomor {reg_number} Tahun {year}?"
   > ```
   >
   > Exact-citation retrieval scores **100% on this by construction**. It would look like
   > a 750,000-pair set and would measure a string format. A question that contains its
   > own answer's address is not a query.
   >
   > **The non-circular seed is `hukumonline.com/klinik`** — real questions from real
   > people, answered by lawyers who cite the regulation, which is exactly the
   > exact-citation arm §4 needs. Scrapers for it exist and no data survived; collection
   > is `Krawl`'s job (`CLAUDE.md` §7.9).
   >
   > This step starts from zero, and it is the long pole in `EVAL.md` — budget for it.
3. Write the exact-citation and negative arms by hand — they are cheap and they are the
   two arms that catch real failures.
4. Freeze v1 of the set before the first variant sweep. A set that changes with the
   corpus cannot compare anything.
