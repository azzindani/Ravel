# CLAUDE.md — Ravel

> Context file for AI coding agents working in this repository. It holds the rules; the
> detail lives in `docs/`, and **[`docs/README.md`](docs/README.md) is the index**. A doc is
> named the same everywhere, so `PROFILES.md` §7 means `docs/pipeline/PROFILES.md` §7.

---

## 1. What Ravel is

**Ravel** is the offline corpus compiler for [Vera](https://github.com/azzindani/Vera). It
turns a publisher's documents into a versioned **corpus bundle** (parquet shards plus a
manifest) that is loaded into PostgreSQL and served by Vera, and into a public dataset.

**One-line purpose:** make a corpus **reproducible** (same inputs → same bundle),
**resumable** (a dead GPU box costs minutes, not days), and **comparable** (two chunking
strategies can be scored against each other).

Ravel is **operator infrastructure**: a CLI a human or agent runs on a GPU box. It is not a
server, not an MCP tool, and never on a query path.

## 2. The mental model

Ravel is a **compiler**, not a script.

```
Vera's job:    embed → route → search → fuse → return cited results
Ravel's job:   fetch → extract → chunk → signals → sparse → embed → bundle → load / publish
```

Deterministic stages, a typed intermediate representation (`CANONICAL_FORMAT.md`), caching
by content hash, a reproducible artifact at the end. Phase A (extract) is per document and
run once; phase B (chunk, enrich, embed) is per chunk and re-run per variant; phase C
(bundle) cannot start until every vector exists. Confusing them is the main way this
project goes wrong. The diagram is in `ARCHITECTURE.md`; the layers in `PIPELINE.md`.

## 3. Where things are

| path | holds |
|---|---|
| `src/` | the Python package: `cli.py`, `sources/` (fetch, scan), `spec/` (profile, dialect, corpus loaders), `extract/`, `canon/`, `chunk/`, `enrich/`, `embed/`, `bundle/`, `load/`, `db/`, `runtime/`, `variants/`, `evaluate/`, `cluster/` (optional) |
| `registry/` | corpus knowledge as data: `profiles/`, `dialects/`, `lexicons/`, `hardware/`, and `published.yaml` (frozen files) |
| `corpora/` | one YAML per corpus: where its publisher is, its profile, its embedder |
| `datasets/` | the card of every published dataset (`<NAME>.md` is `Azzindani/<NAME>`'s README) |
| `docs/` | `design/`, `pipeline/`, `sources/`, `eval/`, `project/`: see the index |
| `docker/` | the job image and the pinned vLLM server (`compose.vllm.yml`) |
| `tools/`, `eval/`, `tests/` | offline helpers, labelled sets, the suite |
| `sources/`, `work/` | downloaded sources and workspaces: on disk, never in git |

Python 3.14, managed by `uv` (`STACK.md`). Lint, format and `mypy --strict` are enforced in
CI. What is built and what is not: `PROGRESS.md`.

## 4. Architecture principles (do not violate)

1. **Extract once, chunk many.** Extraction output is cached by source content hash; a
   chunking experiment never triggers re-extraction. (`ARCHITECTURE.md` §3)
2. **The canonical format is the only interface between phases.** Chunkers, enrichers and
   table builders read canonical documents, never the source or an extractor's raw output.
   (`CANONICAL_FORMAT.md`)
3. **Provenance is captured at chunk creation and immutable.** It is the product. Never
   synthesize, never backfill, never repair. (`CHUNKING.md` §4)
4. **Enrichment is disposable.** Signals, entities and graph edges live in sidecars that
   can be dropped and rebuilt without touching `chunks`. (`ENRICHMENT.md` §2)
5. **Every stage is idempotent and keyed by content hash.** Re-running never
   double-writes; this is what makes resume safe. (`EXECUTION.md` §3)
6. **The manifest is part of the corpus.** Model, version, instructions, pooling,
   normalization, chunker version, source hashes. Without it Vera's startup canary has
   nothing to check. (`BUNDLE.md` §3)
7. **Schema is generated from the manifest, not hardcoded.** The embedding dimension is a
   variable because the embedder is a plugin. (`EMBEDDING.md` §2)
8. **Sources are read-only inputs.** Ravel never modifies, renames or deletes a source file.
9. **Every path is a URI.** `file://`, `s3://`, `hf://` through fsspec. (`INTERFACES.md` §5)
10. **vLLM and faiss-gpu are accelerators, never dependencies.** Windows has neither.
    (`STACK.md` §1)
11. **MCP is authoring-time only.** A build never depends on an MCP server being alive.
    (`INTERFACES.md` §2)
12. **Corpus knowledge is data, never code.** Patterns, vocabularies, document types and
    structure markers live in `registry/profiles/*.yaml`; what a publisher's XML elements
    mean lives in `registry/dialects/*.yaml`. A regex or element name specific to one
    corpus inside a `.py` file is the ID_Legal defect reappearing. A new source follows
    `ADDING_A_SOURCE.md`, step by step. (`PROFILES.md`, `ABSORPTION.md` §4)
13. **An extractor reports what is on the page; a structurer decides what it means.**
    Heading assignment is a profile-driven pass over canonical blocks. (`EXTRACTION.md` §4b)
14. **Chunkers group; `Builder` builds.** A chunker never constructs a `Chunk`: one place
    reads provenance off the canonical document. (`CHUNKING.md` §3b)
15. **A validator must assert the invariant, not replay the construction.** A check that
    re-implements the code it checks certifies that code's bugs. (`LOOPHOLES.md`)
16. **Build the knowledge base before the index.** Every layer writes to one place only:
    `body` takes deletions, derived text takes repairs, sidecars take anything.
    (`PIPELINE.md`)
17. **Clustering belongs to Vera.** A bundle ships with `cluster_id` empty and no
    `clusters.parquet`; Vera assigns it at deploy. (`CLUSTERING.md`)
18. **A published registry file is frozen.** A change is a new version beside it, and
    `ravel update` carries the corpus across. (`PROFILES.md` §7, `registry/published.yaml`)
19. **Everything a rebuild needs is in git.** A source is rebuilt by `ravel build`, a
    dataset republished by `ravel publish` from its card in `datasets/`. A step that lives
    only in a shell history or a scratch file is not done. (`DATABASE.md` §4a)

## 5. The lifecycle of a corpus

| step | command | doc |
|---|---|---|
| add a source | corpus YAML + profile + dialect, by the process | `ADDING_A_SOURCE.md` |
| build from nothing | `ravel build <corpus> -w <ws> --out <bundle> --base-url <vLLM>` | `DATABASE.md` §4a |
| one stage | `ravel sources fetch` · `extract` · `chunk` · `signals` · `sparse` · `embed` · `bundle build` | `PIPELINE.md` |
| check the embedding server | `ravel verify-backend <corpus> --base-url ...` | `EMBED_VERIFICATION.md` |
| serve it | `ravel db prepare <bundle> --dsn ...` | `DATABASE.md` |
| a newer snapshot or profile | `ravel update <corpus> --from <old ws> --from-bundle <old bundle> ...` | `DATABASE.md` §4b |
| new text labels only | `ravel bundle relabel` + `ravel db relabel` | `DATABASE.md` §4c |
| publish a dataset | `ravel publish <corpus> -w <ws> --repo Azzindani/<NAME>` | `datasets/README.md` |
| inspect | `ravel status`, `ravel profiles try`, `ravel bundle show` | |

The embedding server is the docker vLLM container (`docker/compose.vllm.yml`), checked with
`verify-backend` before anything is embedded through it.

## 6. What you must NEVER do

1. **Never re-extract to satisfy a downstream change.** Extend the format and re-extract
   deliberately, as a versioned event.
2. **Never write enrichment output into the `chunks` table.** It is immutable.
3. **Never mix vectors from different models, versions, instructions or pooling in one
   column.** A different space is a separate column with its own query-side embedder.
4. **Never emit a chunk without full provenance.** No source_url, no page/section → the
   chunk does not exist.
5. **Never checkpoint to local disk only** when the run can happen on Kaggle/Colab/Vast.
   (`EXECUTION.md` §4)
6. **Never load a bundle whose manifest fails the consistency preflight** (cosine
   round-trip against the query-side embedder).
7. **Never let a variant silently reuse another variant's cache.** Cache keys include the
   full config hash.
8. **Never hardcode the embedding dimension, chunk size, cluster count or batch size.**
9. **Never scrape.** Sources are files. The one exception is a corpus's declared `fetch:`
   block: a publisher's bulk listing, downloaded to the publisher's own paths, never
   overwritten. (`SOURCE_US_CFR.md` §2)
10. **Never mutate a published bundle, dataset or registry file.** A change produces a new
    version.
11. **Never edit `registry/` or `corpora/` text to tidy it.** A profile's text is part of the
    config hash every stage is cached by, and a published one is frozen.

---

*Companion project: [azzindani/Vera](https://github.com/azzindani/Vera) (the serving
engine). Where this file and
[Vera's `CLAUDE.md`](https://github.com/azzindani/Vera/blob/main/CLAUDE.md) disagree about
the corpus schema, **Ravel takes precedence** — Ravel writes, Vera reads.*
