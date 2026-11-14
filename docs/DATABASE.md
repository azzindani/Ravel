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

## 4. Build once, deploy in a minute

```
ravel db image  <bundle> --out db.tar --password ...   # prepare in a throw-away Postgres, pack its data
ravel db attach db.tar --volume <new volume> --password ...   # unpack, start, verify
```

`image` pays the 15-minute build once. `attach` is a copy and a start-up. The snapshot is the
tar plus `db.tar.json` (the Postgres image id it was built with, the credentials' user and
database, a sha256, and the bundle manifest). `attach` refuses a Postgres other than the one
that wrote the data directory, refuses an existing volume, and starts the server and runs
`verify` before it says attached.

Measured on the 1.7M-chunk corpus (12.3 GB):

* build: prepare 872 s, pack 259 s.
* attach on the build machine (Windows, Docker Desktop): unpack 369 s, start 2 s, verify 1 s.
  The unpack is the host-file-to-VM pipe at about 33 MB/s, not the database.
* the same 12 GB copied volume to volume inside the VM: **10 s**. A Linux server unpacking a
  tar from its own disk is the second case, not the first.
* the engine started on the attached data with the bundle-reference canary at cosine 1.00000.

## 4b. Updates: add, update, remove

```
ravel bundle delta <old bundle> <new bundle> --out delta/
ravel db apply delta/ --dsn ...
```

Documents are content-addressed (`doc_id` is the sha256 of the source bytes), so a changed source
is a new document and the old one is gone. A delta is the difference of two sealed bundles'
document sets; the new bundle comes from the ordinary pipeline, where extraction and embeddings
are cached by content hash and only new documents cost anything.

* **Derived rows are compared, not assumed.** A relation row of an *unchanged* document changes
  when a document it cites arrives or leaves, and a status derived from relations changes with
  it. The delta names every document whose rows differ and replaces them.
* **The recipe must be identical.** A different embedder, chunker, profile or BM25 vocabulary
  makes old and new rows different spaces; the delta builder refuses, and the answer is a full
  rebuild.
* **The version is checked inside the transaction, under a row lock.** The database must be at
  the delta's parent: a delta applied twice, out of order, or beside another is refused.
* **New chunks go to the nearest existing centroid**; the clusters keep their generation and
  their sizes are recounted. The mean distance of the new chunks to their centroids is
  reported against the corpus's own, so a re-cluster is a decision made against a number.
* **Removal is deletion**, never an edit: the provenance guard forbids editing, and nothing
  is updated in place. The BM25 vocabulary stays fixed until a refit, which is a rebuild.
* **The lineage is recorded** in `corpus_versions` (parent, child, counts), and `corpus_meta`
  moves to the child version in the same transaction.

Checked against real Postgres in CI: applying a delta leaves the chunks, the derived rows
(including an unchanged document's new status) and the cluster sizes that a clean rebuild of the
new version would have, and a stale or repeated delta is refused.

## 5. Measurements behind the design

* Vector copy: text route 1,353 s, binary 139 s, identical `halfvec` (all 20,000 values of one
  real shard equal, and equal to the parquet values).
* Clustering: Vera's `kmeans.py` seeded k-means++ over every row and did not finish in 18
  minutes; fitting on a sample and assigning in batches took 184 s including reading the
  vectors.
* The first `fit` on the real corpus hit its 30-iteration cap without converging
  (`converged: False`); the clusters are usable (smallest 8, largest 10,609, none empty) and
  the cap is a parameter, not a verdict.
