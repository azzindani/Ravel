# Ravel

[![CI](https://github.com/azzindani/Ravel/actions/workflows/ci.yml/badge.svg)](https://github.com/azzindani/Ravel/actions/workflows/ci.yml)

**Unravel documents into threads; ravel the threads into a corpus.** The offline
compiler that turns raw source documents into a versioned, verifiable corpus bundle
that [Vera](https://github.com/azzindani/Vera) serves.

`ravel` is a contranym — it means both *to tangle* and *to untangle*. Both halves are
the job: pull tangled documents apart into clean structured threads (headings, clauses,
tables, entities), then weave those threads into one searchable fabric.

---

## Why it exists

`06_ID_Legal` was built piece by piece — notebooks, hand-run cells,
ad-hoc cleanup. It produced a corpus that works, and that **cannot be rebuilt,
re-embedded, or compared against an alternative**. Every chunking idea since has been
untestable, because there is no way to build variant B and score it against variant A.

Ravel is the pipeline that makes a corpus **reproducible, resumable, and comparable**.

## What it does

```
sources → extract → canonical markdown → chunk → enrich → embed → cluster → bundle → load
                    └── cached, built once ──┘   └──── re-run per variant ────┘
```

Given a directory of documents, Ravel produces a **corpus bundle**: parquet shards plus
a manifest pinning the exact model, instruction strings, pooling, chunker version, and
source hashes that produced it. Loading that bundle into Postgres is a separate, dumb
step. The bundle is the deliverable — reproducible, shippable, re-loadable without
re-embedding.

## What it is not

- **Not a crawler.** Sources arrive as files. Collection stays in `Krawl`.
- **Not a search engine.** Ravel writes; Vera reads. Ravel never serves a query.
- **Not a notebook.** Every step is resumable, idempotent, and keyed by content hash.

## Three faces, one core

```
CLI  — bulk builds on a rented GPU (primary; works with nothing else running)
MCP  — an agent loop for authoring parsers: inspect → propose → preview → save
HTTP — parse-as-a-service in Docker, stateless
```

All three share one registry and one canonical format. The MCP server is authoring-time
only — a build must never depend on it. See [docs/INTERFACES.md](docs/INTERFACES.md).

## Design principles

1. **Extract once, chunk many.** Extraction is expensive and deterministic; chunking is
   cheap and experimental. Never re-extract to re-chunk.
2. **Provenance is immutable; enrichment is disposable.** They never share a table.
3. **The embedder is a plugin.** Local or API, text or omni — the corpus schema is
   generated from the manifest, not hardcoded.
4. **Resume is not a feature, it is the execution model.** Kaggle dies at 9 hours; a
   Vast box gets reclaimed. Both are normal.
5. **A variant is a first-class object.** If you cannot score two corpora against each
   other, you are back to ID_Legal.

## Design docs

| Doc | Contents |
|---|---|
| [CLAUDE.md](CLAUDE.md) | anchor context for AI coding agents; principles; never-do list |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | stage graph, artifact boundaries, phase split |
| [docs/CANONICAL_FORMAT.md](docs/CANONICAL_FORMAT.md) | the intermediate document format — the core contract |
| [docs/EXTRACTION.md](docs/EXTRACTION.md) | SmolDocling, OCR, tables; throughput and concurrency |
| [docs/PROFILES.md](docs/PROFILES.md) | document profiles: the corpus knowledge that must not live in code |
| [docs/CHUNKING.md](docs/CHUNKING.md) | structure-aware chunker registry; provenance capture |
| [docs/ENRICHMENT.md](docs/ENRICHMENT.md) | NER, entities, knowledge graph, signals, keyword stats |
| [docs/EMBEDDING.md](docs/EMBEDDING.md) | pluggable embedders, dimension, omni, consistency |
| [docs/BUNDLE.md](docs/BUNDLE.md) | the output contract: parquet + manifest + schema generation |
| [docs/VARIANTS.md](docs/VARIANTS.md) | the experiment harness; what is held fixed |
| [docs/EXECUTION.md](docs/EXECUTION.md) | work queue, checkpoints, resume, portability |
| [docs/INTERFACES.md](docs/INTERFACES.md) | CLI / MCP / HTTP faces, Docker images, HF substrate |
| [docs/STACK.md](docs/STACK.md) | Python 3.14, libraries, environments, no-orchestrator |
| [docs/CLUSTERING.md](docs/CLUSTERING.md) | domain anchors, k-means, cluster assignment |
| [docs/LOOPHOLES.md](docs/LOOPHOLES.md) | known failure modes + solutions |
| [docs/EVAL.md](docs/EVAL.md) | how a variant is scored and promoted |
| [docs/MIGRATION.md](docs/MIGRATION.md) | absorbing the notebooks, `pre_embed`, and ID_Legal |
| [docs/ABSORPTION.md](docs/ABSORPTION.md) | survey of what exists today, measured: corpus, defects, verdicts |
| [docs/OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md) | decisions still on the table |

## Relationship to Vera

```
Ravel (offline, GPU, transient)        Vera (online, VPS, always-on)
────────────────────────────────       ────────────────────────────────
documents → bundle → Postgres    ─────► query → route → search → cite
    owns the schema                        reads the schema
```

Ravel owns the schema because Ravel is the only thing that writes. Readers do not
define formats. See [docs/BUNDLE.md](docs/BUNDLE.md) §2.

The seam is the **`corpus_meta` row**. `src/bundle/schema.py` generates the table;
`src/load/plan.py` stamps the manifest into it *before* the first chunk, because
`chunks.corpus_id REFERENCES corpus_meta(id)`. Vera reads that row at startup and builds
its embedder from it — so a manifest field Ravel gets wrong does not fail here, it fails
Vera's startup canary. The manifest is deliberately **stricter** than `corpus_meta`
(`padding_side` and separate document/query instruction strings have no column there), so
the bundle, not the loaded database, is the authoritative record of how a corpus was made.

Shared between the two repositories:

| Thing | Here | In Vera | Rule |
|---|---|---|---|
| Labelled eval queries | [`eval/id_legal/queries@v1.jsonl`](eval/id_legal) | [`dev_tools/eval/queries.json`](https://github.com/azzindani/Vera/blob/main/dev_tools/eval/queries.json) — 50 cases, the origin | Imported by [`tools/import_vera_queries.py`](tools/import_vera_queries.py); chunk ids are dropped, because relevance is a document + locator ([docs/EVAL.md](docs/EVAL.md) §1) |
| Byte-aware batching | [`src/runtime/batching.py`](src/runtime/batching.py) | [`dev_tools/pre_embed/batching.py`](https://github.com/azzindani/Vera/blob/main/dev_tools/pre_embed/batching.py) — frozen | Ported per [docs/ABSORPTION.md](docs/ABSORPTION.md) §7. Ravel owns it; Vera's copy is held only until `pre_embed`'s remaining scripts follow it |
| Corpus schema | [`src/bundle/schema.py`](src/bundle/schema.py) — generated | [`migrations/*.sql`](https://github.com/azzindani/Vera/tree/main/migrations) — applied by hand | Generated from the manifest here, never hand-written there |

Vera: **[github.com/azzindani/Vera](https://github.com/azzindani/Vera)**

Family: Folio · Pipeline · Sift · Vera · **Ravel**.
