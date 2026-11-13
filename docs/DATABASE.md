# DATABASE.md — a bundle becomes a database that is ready to serve

> One process turns a sealed bundle into a database the retrieval engine can start on.
> Ravel runs it to build a deployable database; Vera runs the same code to deploy one. A
> second implementation in another repository is how the two drift apart, so there is not one.

```
ravel db prepare <bundle> --dsn ... [--replace] [--rows-per-cluster 2000]
                 [--tolerance 0.001] [--vocabulary-to <path the engine reads>]
```

## 1. The stages

| Stage | What it does | Measured, 1.7M chunks |
|---|---|---|
| preflight | checksums, completeness, provenance, disk | 5 s |
| place | fit the routing structure from the bundle's vectors; assign every servable chunk | 184 s |
| load | schema, canary, COPY (binary for vectors), merge **with the cluster id**, base indexes | 636 s |
| accelerate | the provenance guard, then RUM and BM25 where the server can build them | 161 s |
| verify | asserted from the database against the manifest | 0.5 s |

About 16.5 minutes end to end, from parquet. It reports ready only when `verify` says so.

## 2. What "ready" means

`verify` reads the state the engine will read and compares it with the sealed manifest:
chunk count, a dense vector on every chunk, a sparse vector on every chunk when the manifest
declares one, every servable chunk in a cluster, the cluster row counts agreeing with the
placed chunks, a canary at the corpus width, and the provenance guard installed.

## 3. Where each decision lives

* **The bundle** stays cluster-free (`CLAUDE.md` §5.17). It is the dataset and the key
  factors for algorithmic search.
* **The database** is clustered, because a database is a deployment. `--rows-per-cluster` is
  the deployment's setting; the fit is Ravel's `cluster.fit`, on a seeded sample of
  `SAMPLE_PER_CLUSTER` rows per centroid, and every chunk is then assigned to its nearest
  centroid in the metric the embedder declares.
* **Only servable chunks are placed.** A chunk of a document with an unusable text layer is in
  no cluster, and the engine never scans it.
* **The cluster id is written with the row**, in the merge. An UPDATE afterwards would write a
  second version of every row.
* **The canary** is the bundle's reference sentence and vector, recorded by the load in
  `corpus_canary`. The engine embeds the sentence through its own query path and must land on
  the vector.
* **The accelerators** (RUM, pg_search) are optional. The engine probes for each and falls
  back to a slower scan. They are attempted and a failure is reported, never swallowed.

## 4. What is not here yet

* `pack` and `attach`: snapshot the finished database and restore it, which is the path to a
  deploy measured in minutes instead of a rebuild.
* Updates: delta bundles (add, update, remove) and `ravel db apply`.

## 5. Measurements behind the design

* Vector copy: text route 1,353 s, binary 139 s, identical `halfvec` (all 20,000 values of one
  real shard equal, and equal to the parquet values).
* Clustering: Vera's `kmeans.py` seeded k-means++ over every row and did not finish in 18
  minutes; fitting on a sample and assigning in batches took 184 s including reading the
  vectors.
* The first `fit` on the real corpus hit its 30-iteration cap without converging
  (`converged: False`); the clusters are usable (smallest 8, largest 10,609, none empty) and
  the cap is a parameter, not a verdict.
