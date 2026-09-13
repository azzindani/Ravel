# INTERFACES.md — Ravel

One core, three faces: CLI, MCP server, HTTP endpoint. Plus the deployment shapes and the
Hugging Face substrate that makes cloud-to-cloud builds work.

---

## 1. One core, three faces

```
                     ravel/ core
        registry · canonical format · stages · runtime
                          │
        ┌─────────────────┼─────────────────┐
       CLI               MCP               HTTP
   bulk builds     parser authoring    parse-as-a-service
   rented GPU        agent loop        Docker endpoint
```

The faces are thin. Every one of them calls the same registry and produces the same
canonical format — which is only possible because the format is a real contract
(`CANONICAL_FORMAT.md`) rather than an implementation detail.

**The CLI is primary.** It is what builds corpora, and it must work with nothing else
running.

---

## 2. MCP: the parser authoring loop

Writing a chunker or cleanup rule for a new document family is slow, fiddly,
judgment-heavy work with fast feedback — which is exactly what an agent is good at. The
MCP server exposes that loop.

Vera's read-only analog of LOCATE→INSPECT→PATCH→VERIFY is ROUTE→SEARCH→READ→VERIFY.
Ravel's authoring analog is **INSPECT → PROPOSE → PREVIEW → SAVE**.

| Tool | Role | Returns |
|---|---|---|
| `list_parsers` | introspection | registered extractors, cleanups, chunkers with configs |
| `inspect_document` | see what the extractor saw | structural outline, block types, warnings, page count |
| `preview_chunks` | run a chunker config on one document | chunks with provenance, size distribution, boundaries |
| `diff_configs` | compare two chunker configs | chunk-count delta, boundary differences, examples |
| `validate_canon` | check an extraction | schema errors, empty-ratio, structural violations |
| `score_sample` | mini-eval on a labeled subset | Recall@k against a small labeled set |
| `save_parser` | write a config to the registry | the registry entry and its config hash |

Read-mostly, one write. Same return contract as Vera's tools: `success` first, plus
`token_estimate`, `progress`, and on failure `error` + `hint`.

### The hard boundary

> **MCP is authoring-time only. A bulk build must never depend on an MCP server being
> alive.**

MCP writes configs into the registry; the CLI consumes them. A three-day GPU build that
depends on a stdio protocol staying up is not a pipeline. `score_sample` is the tool most
likely to erode this — it is capped at sample size by design, and the cap is not
configurable upward from the tool.

---

## 3. HTTP: parse-as-a-service

A stateless endpoint that takes a document and returns a canonical document, optionally
chunked by a named registry parser.

```
POST /extract      file → canonical document
POST /chunk        canonical document + parser id → chunks
POST /parse        file + parser id → chunks         (the two combined)
GET  /parsers      registry listing
GET  /healthz      model loaded, device, version
```

Stateless, no queue, no checkpoints, no clustering. Bounded request size, bounded
concurrency, model loaded once at startup. This is the face for "other systems need
parsing" — not for building corpora.

---

## 4. Two images, not one

The endpoint and the batch runner are the same code wearing different entrypoints, and
they must not be the same image.

| | `ravel-job` | `ravel-service` |
|---|---|---|
| Lifetime | transient, hours to days | long-lived |
| Base | CUDA runtime | slim (CPU) or CUDA |
| Extras | `[extract,embed,cluster,load,gpu]` | `[extract,service]` |
| Entry | `ravel build …` | `uvicorn ravel.service:app` |
| State | checkpoints to object storage | none |
| Carries | faiss, psycopg, vLLM | fastapi, one model |

A single image gives you a service that pulls in scikit-learn and a batch job that ships
uvicorn. Shared base layer, separate final stages.

The MCP server ships as a third entrypoint on the job image (it needs the extractors, not
the loader) and normally runs over stdio, locally.

---

## 5. Hugging Face as substrate

Cloud-to-cloud means the artifacts live in the cloud, not just the models:

```
  HF: sources dataset ──► [Kaggle / Colab / Space / rented GPU] ──► HF: canon dataset
                                                                     │
  HF: model hub ────────────────────────────────────────────────────┘
                              │
                     HF: bundle dataset ──► VPS: ravel load ──► Postgres ──► Vera
```

With `fsspec`, every stage takes URIs, so `hf://`, `s3://` and `file://` are the same code
path. Hybrid is then not a mode — it is just different URIs per stage: sources local,
canon on R2, bundles on HF.

**Storage correction (supersedes the earlier `ARCHITECTURE.md` §4 sketch):** canonical
documents are stored as **parquet shards, one row per document**, not one JSON file each.
HF datasets and object stores both handle millions of small files badly — listing is slow,
rate limits bite, and per-object overhead dominates. Sharding gives the same cache
semantics with ~1000× fewer objects. Addressing within a shard is by `doc_id`, and a small
index maps `doc_id → shard`.

Suggested split: R2 for working state (queue cache, canon shards, staged vectors), HF
datasets for **published bundles** — versioning, sharing and dataset viewers are exactly
what HF is good at.

---

## 6. Registry

The registry is what all three faces share: named, versioned parser configurations.

```
registry/
├── extractors/<id>@<version>.yaml
├── cleanups/<id>@<version>.yaml
└── chunkers/<id>@<version>.yaml
```

Configs live **in git**, next to the code, because they are reviewable, versioned
alongside the schema, and a chunker config is as load-bearing as the code that reads it.
The MCP server writes into the working tree; changes land through normal review.

A registry entry is immutable once used in a published bundle: a change is a new version.
This is what keeps `config_hash` meaningful (`VARIANTS.md` §3).

---

## 7. Open

- Whether `score_sample` belongs in the MCP surface at all — it closes the quality loop
  for an agent, which is valuable, and it is the tool most likely to be abused into
  becoming the build path. (`OPEN_QUESTIONS.md` §9)
- Whether the HTTP endpoint ever needs auth, or stays strictly internal.
