# OPEN_QUESTIONS.md — Ravel

Decisions not yet made. Each has a leaning and what would settle it. Resolved items move
into the relevant doc and are struck here with the date and the answer.

---

## 1. First embedding model: 0.6B or straight to 8B?

`Vera/EMBEDDING.md` pins Qwen3-Embedding-8B at 4096. `Vera/.test/` holds the 0.6B.

Building ID_Legal at **0.6B/1024** first proves the whole pipeline end-to-end for a
fraction of the GPU cost, and the pipeline is model-agnostic so nothing is thrown away.
The risk is tuning chunking against a weaker model and finding the conclusions do not
transfer to 8B.

**Partly settled by hardware (2026-09-12).** The dev box is an RTX 3070 Ti Laptop with
**8GB VRAM**. Qwen3-Embedding-8B needs ~16GB at fp16, so it *cannot* run locally at all;
0.6B (~1.2GB) and SmolDocling-256M both fit comfortably. So the split is forced: local
development is 0.6B/1024, production bundles are 8B/4096 on rented GPU (`STACK.md` §5).

**The local half is settled outright (2026-09-13).** Qwen3-Embedding-0.6B is on disk at
1.2 GB and its `hidden_size` is 1024 — which is also the incumbent corpus's dimension, so
the baseline in `ABSORPTION.md` §1 was built with this exact model and is reproducible
here without renting anything. Qwen3-Reranker-0.6B sits beside it. See `ABSORPTION.md`
§10 for the full inventory, and §11 for the reason the incumbent's vectors should not be
trusted to be what their dimension suggests: the run that made them never applied its
instruction.

**Still open:** whether chunker sweeps run at 0.6B (cheap, local, fast iteration) or must
run at 8B to be trustworthy.

**Leaning:** sweep at 0.6B, then verify on the sample that 0.6B and 8B rank the variants
in the same order. If they disagree, all sweeps move to rented GPU.

**Settles it:** run both on the sample once the eval set exists.

---

## 2. Checkpoint store

Must survive Kaggle/Colab session death (`EXECUTION.md` §4).

| Option | For | Against |
|---|---|---|
| Cloudflare R2 | no egress fees, S3 API, cheap at TB scale | another account/credential |
| Hugging Face datasets | free, versioned, already in the workflow (`10_Dataset_HF`) | not built for millions of small files; rate limits |
| Backblaze B2 | cheap, S3-compatible | egress cost |
| Local + manual sync | zero setup | not a checkpoint; defeats the purpose |

**Leaning:** R2 for working state (queue, shards, canon), HF datasets for **published
bundles** (versioning and sharing are exactly what it is good at).

**Settles it:** measure upload throughput for ~30K canonical JSON files from Kaggle.

---

## 3. Page rasters for the omni path

Does the canonical document retain page images, or a deterministic recipe to regenerate
them? (`CANONICAL_FORMAT.md` §7)

Storing rasters for 30K documents is large. A recipe (page, DPI, colorspace, renderer
version) is tiny but makes canonical documents non-self-sufficient — regeneration needs
the source file to still exist and the renderer to be reproducible.

**Leaning:** recipe, with sources treated as permanent and hash-verified.

**Settles it:** whether an omni embedder actually enters the plan. Until then, record the
recipe fields — they cost nothing.

---

## 4. ~~SmolDocling quality on Indonesian legal pages~~ — REFRAMED 2026-09-12

Measured on 56 real regulation PDFs (`tools/structure_eval.py`). The answer was not the
one the question assumed.

**Typography does not encode structure in this corpus.** Of 221 structural marker lines
sampled: **2.7% bold**, set at **1.0x body size**, **58.4% centered**. `Pasal 9` is
typeset exactly like body text. Typography-based heading detection therefore scores
**15.4% recall at 4.0% precision**, and yields a clean pasal sequence in **9%** of
documents.

Pattern-and-layout detection (`extract/structure.py`) yields a clean sequence in **43%**
— nearly 5x better, on a metric neither arm optimizes for.

**And 92% of documents have a full text layer**; 8% have none. So the GPU/VLM path is
for roughly one document in twelve, not for the corpus.

**The question is now narrower:** what does SmolDocling add *beyond* patterns — on the
8% with no text layer, on tables, and on documents whose text layer is scrambled? That
is far cheaper to answer than "is SmolDocling good enough to build on".

**Still open:** the 57% of documents without a clean pasal run. Gaps concentrate — the
worst 5 documents hold two-thirds of them — so this is a handful of document shapes
(attachments, contents pages restating pasal numbers), not a systematic failure. Read
those five before writing more rules.

---

## 5. Document factors: denormalized or joined?

Doc type, status, dates, hierarchy level (`ENRICHMENT.md` §3). Copy onto every chunk
(fast filtering on Vera's latency-sensitive path, larger rows, update means rewrite) or
keep a `documents` table Vera joins (normalized, cheap updates, an extra join per query)?

**Leaning:** denormalize the small fixed set (type, status, hierarchy, dates) onto chunks;
keep everything else in `documents`. Status changes over time, which argues for the join —
but a regulation being revoked is rare enough to handle as a rebuild.

**Settles it:** measure the join cost on Vera's 2 vCPU profile.

---

## 6. Overlap and heading prepending

Both are variant dimensions rather than decisions, but defaults matter because most
corpora will never be swept.

**Leaning:** no overlap for structure-chunked corpora (clean unit boundaries), small
overlap for token-chunked ones; `path+body` as the default template.

**Settles it:** the first ID_Legal sweep.

---

## 7. ~~Corpus size for ID_Legal~~ — ANSWERED 2026-09-12

**748,558 chunks across 16,381 distinct regulations** (`ABSORPTION.md` §1), mean body
1,234 chars, ~900MB of text. Not the 100M rows Vera's docs plan for.

Consequences: **k ≈ 75**, not 10,000 (`CLUSTERING.md` §3). Sample-fit k-means is
unnecessary — full k-means fits in memory easily. A full 0.6B re-embed of 748K chunks is
hours on one GPU, not GPU-days, which makes variant sweeps far cheaper than budgeted.

Still open: the *source* document count behind those 16,381 regulations, which drives
extraction cost. `ravel sources scan id_legal` against `Azzindani/ID_REG` answers it.

---

## 8. Does Ravel generate Vera's migrations, or only its own schema.sql?

`BUNDLE.md` §2 says Ravel owns the schema. The mechanism is undecided: a generator that
writes into `Vera/migrations/`, or a shared schema definition file both read.

**Leaning:** Ravel generates; Vera's migrations directory carries a "generated — do not
edit" header. Hand-written extras (the provenance trigger in `0002`) stay hand-written
and are appended, since a table generator cannot express a trigger.

**Settles it:** the first schema change after both projects are live.

---

## 9. Does `score_sample` belong in the MCP surface?

It closes the loop for an authoring agent — propose a chunker, preview it, and find out
whether retrieval actually improved — which is far more useful than structural feedback
alone. It is also the tool most likely to be abused into becoming the build path, which
`INTERFACES.md` §2 forbids.

**Leaning:** include it, hard-capped at sample size, with the cap not raisable from the
tool. An agent that wants a full build asks a human to run the CLI.

**Settles it:** whether the authoring loop feels blind without it in practice.

---

## 10. Shared queue across multiple rented boxes

The derived, content-addressed queue (`STACK.md` §4) gives resume for free but does not
let two rented boxes cooperate on one build without racing — both would lease the same
work.

**Leaning:** accept it. Partition work by shard range per box (trivially correct, no
coordination) and only move to a shared Postgres queue if partitioning proves too coarse.

**Settles it:** the first build big enough to want two boxes at once.

---

## 11. HTTP endpoint auth

Strictly internal, or does the parse-as-a-service endpoint (`INTERFACES.md` §3) ever face
anything untrusted? Untrusted input means a document parser is now an attack surface —
PDF parsers are a classic one.

**Leaning:** internal only, no auth, not exposed publicly. Revisit only if there is a real
external consumer, and sandbox extraction if so.
