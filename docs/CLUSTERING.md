# CLUSTERING.md — Ravel

Phase C: building the routing structure Vera navigates — domain anchors, k-means
centroids, and chunk assignment.

> Vera's `CLUSTER_MAINTENANCE.md` covers the *live* side: drift, splits, atomic swaps.
> This doc covers the *initial* build and the re-cluster job that Ravel runs offline.

---

## 1. What Vera needs

Three layers (`Vera/ARCHITECTURE.md` §2), two of which Ravel must produce:

| Layer | Object | Ravel builds |
|---|---|---|
| 1 | `domains.anchor` — one vector per knowledge base | yes |
| 2 | `clusters.centroid` — ~10K k-means centroids | yes |
| 3 | `chunks.cluster_id` — every chunk assigned | yes |

Vera holds layers 1 and 2 hot in memory and uses them to decide **where not to look**.
Bad clustering does not produce an error; it produces silent zero-recall
(`Vera/LOOPHOLES.md` §1). This stage is quietly critical.

---

## 2. Why it cannot stream

k-means needs the whole vector set. So Phase C blocks on every vector existing, which is
the structural reason the pipeline has three phases rather than one continuous stream.

At 100M × 4096 fp16 (~0.8TB), the vectors do not fit in RAM. Options, in order of
preference:

1. **Fit centroids on a sample** (a few million vectors), then assign all rows by
   streaming. This is standard, cheap, and accurate enough at this k.
2. **Mini-batch k-means** over the full set, streamed from parquet.
3. Full Lloyd's on GPU — only viable for smaller corpora.

Default: sample-fit + streamed assign. Sample size and seed go in the manifest so the
clustering is reproducible.

---

## 3. Choosing k

Vera's leaf scan assumes ~10K rows per cluster, giving roughly `k ≈ n / 10000`:

| Corpus | rows | k |
|---|---|---|
| ID_Legal (initial) | ~4M | ~400 |
| target scale | 100M | ~10K |

Trade-off: bigger clusters mean fewer, safer routing decisions but more rows scanned per
probe (latency, RAM); smaller clusters mean cheaper scans but a higher chance the right
chunk sits outside the probed set. `clusters_probed` on Vera's side is the other half of
this dial, and the two must be tuned together against the eval set — not separately.

Balance matters as much as count: a cluster holding 40% of the corpus destroys the
memory guarantee Vera's sequential loading depends on. Report the size distribution and
split oversized clusters before publishing.

---

## 4. Domain anchors

A domain anchor is a single vector representing a knowledge base. Vera matches the query
against anchors and returns *nothing* when no anchor clears the threshold — an honest
"no match" beats a confident wrong domain.

Construction options:

1. **Centroid of the corpus** — simple, but a broad corpus gets a vague anchor.
2. **Embedding of a written domain description** — sharp, human-controlled, and the
   description is documentation. Requires the description to be in the same space
   (embed it as a document).
3. **Multiple anchors per domain**, matched by max — the honest answer for corpora that
   genuinely cover several areas.

Default: the written description, with the corpus centroid stored alongside for
comparison. Ravel must also emit the **threshold calibration data** — anchor similarity
distributions for in-domain and out-of-domain queries from the eval set — because Vera's
threshold is a number someone has to choose, and choosing it without data is guessing.

---

## 5. Assignment and generations

Assign each chunk to its nearest centroid, streaming, writing `cluster_id` into the
chunks parquet. Then:

- stamp a **generation** number on clusters (Vera's atomic swap pins a generation);
- record `row_count` per cluster and per domain;
- validate: no empty clusters, no cluster above the size ceiling, every chunk assigned.

Vera partitions `chunks` by `HASH(cluster_id)` across 64 partitions, so assignment must
happen before load — not after.

---

## 6. Incremental additions

Adding documents to an existing corpus without re-clustering:

1. Embed the new chunks with the **same** pinned embedder (non-negotiable).
2. Assign to existing centroids at the current generation.
3. Track drift: per-cluster mean distance to centroid, and the size distribution.
4. Split clusters that exceed the ceiling; re-cluster wholesale when drift passes
   threshold.

Drift is the thing to watch. Assigning new documents to stale centroids degrades routing
gradually and invisibly — the same silent-failure shape as embedding drift. Ravel reports
drift metrics on every incremental run; Vera's `CLUSTER_MAINTENANCE.md` defines when a
re-cluster is triggered.

---

## 7. Per-space clustering

If a corpus carries a second vector space (omni, `EMBEDDING.md` §4), it needs its **own**
centroids and its own assignment — centroids from one space are meaningless in another.
The bundle carries both sets, distinguished by space id.
