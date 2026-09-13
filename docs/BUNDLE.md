# BUNDLE.md — Ravel

The output contract. A bundle is the deliverable — a versioned, self-describing,
verifiable corpus that can be loaded into Postgres and served by Vera.

---

## 1. Why a bundle and not "write straight to Postgres"

Writing directly couples the expensive GPU run to a live database being reachable,
correct and empty. A bundle decouples them, and four properties follow:

1. **Rebuild without re-embedding.** Drop the database, reload from parquet in minutes.
2. **Shippable.** Built on a Vast box, loaded on the VPS. The artifact is portable.
3. **Comparable.** Two variants are two bundles side by side, each independently
   loadable and scorable.
4. **Verifiable.** The manifest states exactly what produced it, and the load step
   checks the claim.

`COPY` from staged files is also far faster than row inserts at 100M scale.

---

## 2. Ravel owns the schema

The corpus schema is defined **here**, and Vera consumes it. Rationale: Ravel is the only
writer, and readers do not define formats. The practical consequence is that
`Vera/migrations/*.sql` should be generated from Ravel's schema definition rather than
maintained by hand in parallel — two hand-maintained copies drift, and the drift is
silent (a corpus that loads but ranks wrong).

**Migration note:** Vera's `0001_init.sql` exists today and is the de facto v1 schema.
Ravel adopts it as-is and takes over generation from there. Divergences must be
documented, never accidental.

---

## 3. The manifest

`manifest.json` is the receipt. Without it the bundle is an undocumented pile of vectors.

```jsonc
{
  "bundle_version": "1",
  "corpus": "id_legal",
  "variant": "pasal",
  "created_at": "2026-09-12T…Z",
  "ravel_version": "…",
  "config_hash": "…",                    // hashes the full resolved variant config

  "sources":   { "count": 30142, "bytes": …, "manifest_sha256": "…" },
  "extraction":{ "canon_version": "1.0",
                 "extractors": { "smoldocling": 27110, "native": 2901, "ocr": 131 },
                 "failed": 12, "failed_manifest": "failed.jsonl" },
  "cleanup":   { "id": "id_reg_clean", "version": "1.0" },
  "chunking":  { "chunker": "id_regulation", "version": "1.0",
                 "config": { … }, "chunks": 4218773 },
  "enrichment":{ "enrichers": [ { "id": "ner_legal", "version": "3.0" } ] },

  "embedding": {
    "id": "qwen3-8b-local",
    "model": "Qwen/Qwen3-Embedding-8B",
    "model_version": "<exact revision>",
    "dim": 4096,
    "modality": "text",
    "pooling": "last_token",
    "normalize": true,
    "doc_instruction": "",
    "query_instruction": "<verbatim>",
    "dtype": "float16",
    "provider": null,
    "reference": { "text": "…", "vector_sha256": "…", "vector_path": "reference.npy" }
  },

  "clustering":{ "domains": 1, "clusters": 10000, "generation": 1,
                 "kmeans": { "algo": "…", "iters": …, "seed": 42 } },

  "counts":    { "chunks": …, "signals": …, "entities": …, "edges": … },
  "checksums": { "chunks/part-00000.parquet": "sha256:…", … }
}
```

The `embedding.reference` block is what feeds Vera's startup canary: a known string and
the vector this corpus's model produced for it. A Vera instance whose query embedder
disagrees refuses to serve rather than quietly returning worse results.

---

## 4. Layout

```
bundles/<corpus>/<variant>/<version>/
├── manifest.json
├── schema.sql            generated from the manifest (dimension is a variable)
├── reference.npy         canary vector
├── domains.parquet
├── clusters.parquet
├── chunks/part-*.parquet        id, doc_id, body, provenance, heading_path, identifier
├── vectors/part-*.parquet       chunk_id, embedding (fp16)   [joined at load]
├── signals/part-*.parquet       chunk_id, data (JSONB-ready)
├── entities.parquet
├── edges.parquet
└── failed.jsonl                 what did not make it, and why
```

Vectors are a separate file set from chunks on purpose: a re-embed replaces `vectors/`
and nothing else, and a text-only consumer never pays to read 4096-dim rows.

Shard size targets ~256MB–1GB — small enough to checkpoint and retry, large enough that
`COPY` is not dominated by per-file overhead.

---

## 5. Load-time preflight (all must pass)

1. **Dimension match** — manifest `dim` equals the target column's dimension.
2. **Consistency** — `model`, `version`, `instruction`, `pooling`, `normalize` match what
   the target deployment expects.
3. **Canary** — the query-side embedder reproduces `reference.npy` at cosine ≥ 0.999.
4. **Completeness** — row counts match the manifest; `failed.jsonl` count is within the
   corpus's declared tolerance. A bundle missing 400 documents must not load silently.
5. **Checksums** — every parquet file matches its recorded hash.
6. **Disk** — free space exceeds the projected load size plus index build headroom.
   (At ~0.8TB for 100M rows, running out mid-`COPY` is a real and ugly failure.)
7. **Provenance completeness** — zero rows with null `source_url` or `source_title`.

Fail any check → load nothing. Partial loads are the worst outcome, because the
resulting corpus looks fine.

---

## 6. Load procedure

```
create schema from schema.sql  →  COPY chunks  →  COPY vectors  →  join/merge
  →  COPY signals/entities/edges  →  build GIN + btree indexes  →  ANALYZE
  →  COPY domains + clusters  →  verify counts  →  stamp manifest into corpus_meta
```

Indexes are built **after** bulk load, not before — index maintenance during `COPY` is
the classic way to turn a 20-minute load into a 6-hour one.

The manifest is written into a `corpus_meta` table so a live database can answer "what
built you?" without the bundle being present. Vera reads it at startup.

---

## 7. Immutability and versioning

A published bundle is never edited. Any change — a re-chunk, a re-embed, a new enricher —
produces a new version directory. Versions are cheap relative to a GPU run and they are
what makes rollback possible: load the previous bundle, swap, done.

Cluster **generation** is stamped in the bundle so Vera's atomic re-cluster swap
(`Vera/CLUSTER_MAINTENANCE.md`) has a consistent version to pin against.
