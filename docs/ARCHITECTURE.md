# ARCHITECTURE.md — Ravel

Full system design. For the intermediate representation see `CANONICAL_FORMAT.md`; for
the output contract see `BUNDLE.md`.

---

## 1. The problem Ravel solves

`06_ID_Legal` proved a hybrid legal search engine works. It also proved the way it was
built does not scale as a practice: notebooks in `AI_Workspace`, cells run by hand,
cleanup applied ad hoc. The resulting corpus has three defects that have nothing to do
with its quality:

1. **Not reproducible.** Nobody can regenerate it from sources.
2. **Not resumable.** A long run that dies is a long run repeated.
3. **Not comparable.** There is no way to build a second corpus with different chunking
   and find out which one retrieves better.

Ravel exists to remove those three defects. Retrieval quality is a *consequence* —
you cannot improve what you cannot measure, and you cannot measure what you cannot
rebuild.

---

## 2. The stage graph

```
  corpora/id_legal.yaml          ← corpus definition: sources, chunker, enrichers
          │
  ┌───────▼────────┐
  │ SOURCES        │  discover files, hash content, write source manifest
  └───────┬────────┘  key: sha256(file bytes)
          │
  ┌───────▼────────┐
  │ EXTRACT        │  PHASE A — GPU, expensive, deterministic, CACHED
  │  smoldocling   │  → canonical document (markdown + layout + tables)
  │  native / ocr  │  key: sha256(bytes) + extractor id + extractor version
  └───────┬────────┘
          │ ════════ hard artifact boundary ════════
  ┌───────▼────────┐
  │ CHUNK          │  PHASE B — cheap, per variant
  │                │  → chunks with provenance, heading_path, identifier
  └───────┬────────┘  key: canonical hash + chunker id + chunker config
          │
  ┌───────▼────────┐
  │ ENRICH         │  NER, entity linking, KG edges, factors, keyword stats
  │                │  → signals (disposable), entities, edges
  └───────┬────────┘  key: chunk id + enricher id + enricher version
          │
  ┌───────▼────────┐
  │ EMBED          │  GPU or API; document-side instruction; pooling; L2
  │                │  → vectors (fp16 / halfvec-ready)
  └───────┬────────┘  key: chunk id + model + version + instruction + pooling
          │
  ┌───────▼────────┐
  │ CLUSTER        │  PHASE C — WHOLE CORPUS, cannot stream
  │                │  domain anchors + k-means centroids + assignment
  └───────┬────────┘
          │
  ┌───────▼────────┐
  │ BUNDLE         │  parquet shards + manifest + generated schema.sql
  └───────┬────────┘
          │
  ┌───────▼────────┐
  │ LOAD           │  preflight → COPY → index → verify
  └───────┬────────┘
          │
        Vera
```

Each stage is a pure function of its inputs plus a version number. That is what makes
the cache keys above sound, and the cache keys are what make resume and variants
possible.

---

## 3. The three phases and why they differ

| | Phase A: Extract | Phase B: Chunk/Enrich/Embed | Phase C: Cluster/Bundle |
|---|---|---|---|
| Unit of work | one document | one chunk | the entire corpus |
| Shape | streaming | streaming | batch, blocking |
| Cost | very high (GPU VLM) | high (GPU embed), low (chunk) | moderate |
| Re-run frequency | ~never | every variant | every variant |
| Parallelism | document-level, GPU-batched | chunk-level, GPU-batched | data-parallel k-means |
| Failure cost | days | hours | minutes |

**The boundary between A and B is the most important line in the project.** Extraction
is where the GPU-days go. Chunking is where the experiments happen. If chunking can
trigger re-extraction, every experiment costs a full re-run and you stop experimenting —
which is precisely how ID_Legal ended up frozen.

Consequence: **the canonical format must be rich enough that no chunker ever needs the
original file.** If a chunker wants font size, bounding boxes or reading order, those
belong in the canonical document from the start. See `CANONICAL_FORMAT.md` §5.

**Phase C cannot start early.** Domain anchors and k-means centroids are functions of
the whole vector set. This is why "just add one more document later" is not trivial and
why `CLUSTERING.md` has a separate incremental-assignment path.

---

## 4. Artifact layout

```
<workspace>/
├── sources/<corpus>/                  read-only inputs
│   └── manifest.jsonl                 path, sha256, mime, size, discovered_at
│
├── canon/<corpus>/                    PHASE A output — shared by ALL variants
│   ├── part-*.parquet                 one ROW per canonical document (not one file)
│   └── index.parquet                  doc_id → shard, for random access
│
├── work/<corpus>/<variant>/           PHASE B intermediates
│   ├── chunks/*.parquet
│   ├── signals/*.parquet
│   └── vectors/*.parquet
│
├── bundles/<corpus>/<variant>/<version>/    PHASE C output — the deliverable
│   ├── manifest.json
│   ├── schema.sql
│   ├── domains.parquet
│   ├── clusters.parquet
│   ├── chunks/part-*.parquet
│   ├── signals/part-*.parquet
│   ├── entities.parquet
│   └── edges.parquet
│
└── state/<corpus>/                    checkpoints, work queue, progress
```

`canon/` sitting outside the variant directory is the "extract once, chunk many" rule
made physical. Two variants share one `canon/` tree and cannot corrupt each other's
work because `work/` is keyed by variant.

Canonical documents are **sharded parquet, one row per document** — never one file per
document. Object stores and HF datasets both handle millions of small files badly, and
the whole tree has to be uploadable and re-pullable on a fresh machine
(`INTERFACES.md` §5). Every path above is a URI, not a local path: `file://`, `s3://`
and `hf://` are interchangeable, which is what makes hybrid and cloud-to-cloud builds a
configuration choice rather than a mode (`STACK.md` §3).

---

## 5. What crosses the boundary to Vera

Ravel writes exactly four kinds of thing:

| Artifact | Mutability | Vera reads it |
|---|---|---|
| `chunks` (body + provenance + tsv) | immutable after load | yes, today |
| `domains` / `clusters` (routing) | replaced by generation swap | yes, today |
| `chunk_signals` (enrichment) | droppable, rebuildable | not yet |
| `entities` / `edges` (graph) | droppable, rebuildable | not yet |

The last two ship in the bundle from day one even though Vera ignores them. Rationale in
`LOOPHOLES.md` §7: re-running enrichment over a 100M-row corpus because the bundle format
could not carry it is the expensive mistake, and the format is the cheap part to get
right early.

---

## 6. Where Ravel does not go

- **Collection.** No crawling, no downloading, no site-specific scrapers. Sources arrive
  as files. `Krawl` owns that.
- **Serving.** No query path, no API, no MCP surface. Vera owns that.
- **Generation.** Ravel may call an LLM as an *enricher* (summaries, QA pairs, entity
  extraction), and that output is a disposable signal — never a substitute for the
  source text, never part of provenance.

---

## 7. End-to-end trace (ID_Legal, first build)

```
1. ravel sources scan id_legal          → 30K PDFs hashed, manifest written
2. ravel extract id_legal               → SmolDocling on GPU, batched, checkpointed
                                          resumes cleanly across Kaggle sessions
3. ravel build id_legal --variant pasal → chunk on legal unit, enrich, embed
4. ravel cluster id_legal --variant pasal → 1 domain anchor, ~10K centroids, assign
5. ravel bundle id_legal --variant pasal → parquet + manifest + schema.sql
6. ravel load bundles/.../v1 --dsn ...  → preflight, COPY, GIN index, verify counts
7. ravel eval bundles/.../v1            → score against the labeled query set
8. repeat 3–7 with --variant token512   → compare; promote the winner
```

Step 8 is the entire point. Steps 1–2 happen once.
