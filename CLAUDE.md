# CLAUDE.md — Ravel

> Context file for AI coding agents working in this repository.
> Read this first. It defines what Ravel is, the rules you must follow, and the hard
> boundaries that keep a corpus reproducible.

---

## 1. What Ravel is

**Ravel** is the offline corpus compiler for [Vera](../Vera). It turns raw source
documents into a versioned **corpus bundle** — parquet shards plus a manifest — that is
loaded into PostgreSQL and served by Vera.

The name is a contranym: *to tangle* and *to untangle*. Ravel unravels documents into
structured threads (headings, clauses, tables, entities) and ravels those threads into
one searchable corpus.

**One-line purpose:** make a corpus **reproducible** (same inputs → same bundle),
**resumable** (a dead GPU box costs minutes, not days), and **comparable** (two chunking
strategies can be scored against each other).

Ravel is **operator infrastructure** — a CLI a human or agent runs on a GPU box. It is
not a server, not an MCP tool, and never on a query path.

---

## 2. The core mental model

Ravel is a **compiler**, not a script.

```
Vera's job:    embed → route → search → fuse → return cited results
Ravel's job:   extract → chunk → enrich → embed → cluster → bundle
```

A compiler has deterministic stages, a typed intermediate representation, caching by
content hash, and a reproducible artifact at the end. Every design decision here follows
from taking that analogy literally. The **canonical document format**
(`docs/CANONICAL_FORMAT.md`) is the IR. Treat it with a compiler author's seriousness.

---

## 3. Architecture in one screen

```
                 sources/ (files on disk; no crawling — see Krawl)
                            │
        ┌───────────────────┴────────────────────┐
PHASE A │  EXTRACT  (GPU-bound, expensive)        │  SmolDocling / OCR / native text
  ONCE  │  → canonical markdown + layout + tables │  cached, keyed by content hash
        └───────────────────┬────────────────────┘
                            │   ← hard artifact boundary; never crossed backwards
        ┌───────────────────┴────────────────────┐
PHASE B │  CHUNK    structure-aware, per corpus   │  cheap, re-run per variant
 MANY   │  ENRICH   NER - entities - KG - signals │
        │  EMBED    pluggable: local or API       │  GPU-bound, expensive, re-runnable
        └───────────────────┬────────────────────┘
                            │
        ┌───────────────────┴────────────────────┐
PHASE C │  CLUSTER  domain anchors + k-means      │  whole-corpus, not per-document
 GLOBAL │  BUNDLE   parquet shards + manifest     │
        └───────────────────┬────────────────────┘
                            │
                      LOAD (COPY → Postgres)  →  Vera serves
```

Three phases with different shapes: **A** is per-document and streams; **B** is per-chunk
and streams; **C** cannot start until every vector exists. Confusing these is the main
way this project goes wrong.

---

## 4. Repository structure

```
Ravel/
├── CLAUDE.md                      ← you are here
├── README.md
├── docs/
│   ├── ARCHITECTURE.md            ← stage graph, artifact boundaries, phase split
│   ├── CANONICAL_FORMAT.md        ← the IR: structured markdown + layout + tables
│   ├── EXTRACTION.md              ← SmolDocling/OCR/native; throughput, concurrency
│   ├── PROFILES.md                ← document profiles: corpus knowledge as data, not code
│   ├── CHUNKING.md                ← chunker registry; provenance capture rules
│   ├── ENRICHMENT.md              ← NER, entities, KG edges, signals, keyword stats
│   ├── EMBEDDING.md               ← pluggable embedders; dimension; omni; consistency
│   ├── BUNDLE.md                  ← output contract: parquet + manifest + schema gen
│   ├── VARIANTS.md                ← the experiment harness
│   ├── EXECUTION.md               ← work queue, checkpoints, resume, portability
│   ├── INTERFACES.md              ← CLI / MCP / HTTP faces, Docker images, HF substrate
│   ├── STACK.md                   ← Python 3.14, libraries, environments, no-orchestrator
│   ├── CLUSTERING.md              ← domain anchors, k-means, assignment, generations
│   ├── LOOPHOLES.md               ← known failure modes + solutions
│   ├── EVAL.md                    ← how a variant is scored and promoted
│   ├── MIGRATION.md               ← absorbing notebooks, pre_embed, ID_Legal
│   ├── ABSORPTION.md              ← measured survey of the existing corpus and code
│   └── OPEN_QUESTIONS.md          ← decisions still on the table
│
├── ravel/                         ← Python package (libraries dictate language)
│   ├── cli.py                     ← ravel extract | build | bundle | load | eval
│   ├── sources/                   ← discovery, content hashing, source manifests
│   ├── spec/                      ← profile loader: YAML → validated, compiled, hashed
│   ├── extract/                   ← native, text/html, ocr, smoldocling → canonical
│   │                                 + structure.py: profile-driven heading assignment
│   ├── canon/                     ← canonical format: parse, validate, serialize
│   ├── chunk/                     ← chunker registry (pasal/ayat, heading, token, code)
│   ├── enrich/                    ← NER, entity linking, KG edges, signals, tf-idf
│   ├── embed/                     ← embedder plugins: local (transformers/vLLM), API
│   ├── cluster/                   ← domain anchors, k-means, assignment
│   ├── bundle/                    ← parquet writer, manifest, schema generation
│   ├── load/                      ← COPY into Postgres; preflight checks
│   ├── runtime/                   ← work queue, checkpoint store, concurrency, progress
│   ├── variants/                  ← variant configs, matrix runs
│   ├── mcp/                       ← MCP server: parser authoring tools (authoring only)
│   └── service/                   ← FastAPI parse-as-a-service endpoint
│
├── registry/                      ← corpus knowledge as data (in git, reviewed)
│   └── profiles/                  ← <id>@<version>.yaml · see docs/PROFILES.md
├── corpora/                       ← per-corpus definitions (id_legal.yaml, ...)
├── eval/                          ← labeled query→answer sets + scoring
├── notebooks/                     ← archived originals of ported notebooks (provenance)
├── docker/                        ← ravel-job and ravel-service images
└── pipeline.yaml
```

Python 3.14 throughout, managed by `uv`: the whole dependency surface (SmolDocling,
transformers, vLLM, PyMuPDF, pyarrow, faiss) is Python. Vera is Rust because a stateless
query engine benefits from it; Ravel would gain nothing and lose the ecosystem. Full
stack and version policy in `docs/STACK.md`.

---

## 5. Architecture principles (do not violate)

1. **Extract once, chunk many.** Extraction output is cached by source content hash. A
   chunking experiment must never trigger re-extraction. (`docs/ARCHITECTURE.md` §3)
2. **The canonical format is the only interface between phases.** Chunkers, enrichers and
   table builders read canonical documents — never the original PDF, never the
   extractor's raw output. (`docs/CANONICAL_FORMAT.md`)
3. **Provenance is captured at chunk creation and immutable.** It is the product. Never
   synthesize, never backfill, never repair. (`docs/CHUNKING.md` §4)
4. **Enrichment is disposable.** Signals, entities and graph edges live in separate
   tables that can be dropped and rebuilt without touching `chunks`.
   (`docs/ENRICHMENT.md` §2)
5. **Every stage is idempotent and keyed by content hash.** Re-running never
   double-writes. This is what makes resume safe. (`docs/EXECUTION.md` §3)
6. **The manifest is part of the corpus.** Model, version, instruction strings, pooling,
   normalization, chunker version, source hashes. Without it the bundle is unverifiable
   and Vera's startup canary has nothing to check. (`docs/BUNDLE.md` §3)
7. **Schema is generated from the manifest, not hardcoded.** Embedding dimension is a
   variable because the embedder is a plugin. (`docs/EMBEDDING.md` §2)
8. **Sources are read-only inputs.** Ravel never modifies, renames or deletes a source
   file.
9. **Every path is a URI.** `file://`, `s3://`, `hf://` are interchangeable through
   fsspec. This is what makes local, hybrid and cloud-to-cloud the same code.
   (`docs/INTERFACES.md` §5)
10. **vLLM and faiss-gpu are accelerators, never dependencies.** Windows has neither; a
    stage that cannot run without them kills local development. (`docs/STACK.md` §1)
11. **MCP is authoring-time only.** It writes configs; the CLI consumes them. A build
    must never depend on an MCP server being alive. (`docs/INTERFACES.md` §2)
12. **Corpus knowledge is data, never code.** Patterns, vocabularies, document types
    and structure markers live in `registry/profiles/*.yaml`. A regex specific to one
    corpus inside a `.py` file is the ID_Legal defect reappearing.
    (`docs/PROFILES.md`, `docs/ABSORPTION.md` §4)
13. **An extractor reports what is on the page; a structurer decides what it means.**
    Heading assignment is a separate, profile-driven pass over canonical blocks — never
    logic inside an extractor. (`docs/EXTRACTION.md` §4b)
14. **Chunkers group; `Builder` builds.** A chunker chooses which blocks belong together
    and what to call the result. It never constructs a `Chunk` — one place reads
    provenance off the canonical document, so "never emit a chunk without full
    provenance" is structural rather than remembered. (`docs/CHUNKING.md` §3b)
15. **A validator must assert the invariant, not replay the construction.** A check that
    re-implements the code it checks certifies that code's bugs — 931 documents passed a
    heading_path rule that shared the builder's defect. (`docs/LOOPHOLES.md`)

---

## 6. Command surface

Full detail in the per-stage docs.

| Command | Role |
|---|---|
| `ravel profiles list\|show\|try\|route` | inspect and test document profiles |
| `ravel sources scan <corpus>` | discover files, hash, write the source manifest |
| `ravel extract <corpus>` | Phase A: documents → canonical, cached, resumable |
| `ravel chunk <corpus> [--chunker c] [--variant v]` | Phase B: canonical → chunks with provenance |
| `ravel build <corpus> --variant <v>` | Phase B: chunk → enrich → embed |
| `ravel cluster <corpus> --variant <v>` | Phase C: domain anchors + k-means + assign |
| `ravel bundle <corpus> --variant <v>` | write parquet shards + manifest |
| `ravel load <bundle> --dsn ...` | preflight, `COPY` into Postgres, verify |
| `ravel eval <bundle> [<bundle> ...]` | score variants against the labeled set |
| `ravel status <corpus>` | progress, checkpoint state, what would resume |
| `ravel mcp` | MCP server for parser authoring (stdio) — never in a build path |
| `ravel serve` | HTTP parse-as-a-service endpoint |

Three faces over one core (`docs/INTERFACES.md`): the CLI builds corpora and must work
with nothing else running; MCP authors parser configs into the registry; HTTP exposes
parsing as a stateless service.

---

## 7. What you must NEVER do

1. **Never re-extract to satisfy a downstream change.** If a chunker needs something the
   canonical format lacks, extend the format and re-extract *deliberately*, as a
   versioned event — not implicitly.
2. **Never write enrichment output into the `chunks` table.** It is immutable.
3. **Never mix vectors from different models, versions, instructions or pooling in one
   column.** Omni/multimodal vectors are a different space — a separate column with its
   own query-side embedder, never blended.
4. **Never emit a chunk without full provenance.** No source_url, no page/section → the
   chunk does not exist.
5. **Never checkpoint to local disk only** when the run can happen on Kaggle/Colab/Vast.
   Ephemeral disk is not a checkpoint. (`docs/EXECUTION.md` §4)
6. **Never load a bundle whose manifest fails the consistency preflight** (cosine
   round-trip against the query-side embedder).
7. **Never let a variant silently reuse another variant's cache.** Cache keys include the
   full config hash.
8. **Never hardcode the embedding dimension, chunk size, cluster count or batch size.**
   Read from corpus/variant config.
9. **Never scrape.** Sources are files. Collection belongs to `Krawl`.
10. **Never mutate a published bundle.** A change produces a new bundle version.

---

## 8. Progress tracker

- [x] Canonical format spec + validator + round-trip test
- [x] Source scanner: discovery, content hashing, source manifest
- [x] Execution runtime: ledger, sharding, resume, bounded memory
- [ ] Extractor: SmolDocling (structure → markdown), batched on GPU
- [x] Extractor: native PDF text + HTML/markdown/plain, profile-driven structuring
- [ ] Extractor fallbacks: OCR, SmolDocling, table extraction
- [x] Chunker registry + profile-driven `unit` chunker with provenance
- [x] Text-layer quality gate: clean/damaged/absent routing — `src/extract/quality.py`
- [x] URI substrate (`file://`, `hf://`, `s3://`) — `src/uris.py`
- [~] Embedder plugin interface + manifest + preflight — `src/embed/`; local (transformers/vLLM) and API backends still to write
- [~] Bundle writer: manifest + generated schema — `src/bundle/`; parquet shard writer still to build
- [ ] Clustering: domain anchors, k-means, assignment, generation stamping
- [ ] Loader: preflight, `COPY`, post-load verification
- [~] Enrichment: BM25 sparse arm + ingest-time ranking factors — `src/enrich/`; NER, entities and KG edges still to build
- [ ] Variant harness: config hashing, matrix runs, cache reuse
- [ ] Eval: labeled ID regulation set; score and promote a variant
- [ ] MCP server: parser authoring tools (inspect, preview, diff, save)
- [ ] HTTP service + two Docker images (`ravel-job`, `ravel-service`)
- [ ] Remote URI backends: R2 working state, HF datasets for published bundles
- [ ] Rebuild `06_ID_Legal` end-to-end and beat it on the eval set

---

*Companion project: `../Vera` (the serving engine). Where this file and Vera's
`CLAUDE.md` disagree about the corpus schema, **Ravel takes precedence** — Ravel writes,
Vera reads.*
