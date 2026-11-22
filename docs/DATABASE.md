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
| parents | two-level routing: √k parent centroids over the leaves, `clusters.parent_id` (from 256 leaves) | 2 s |
| layout | `chunk_vectors`: every servable vector again, sorted by cluster, `dense` stored PLAIN | ~600 s |
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
* **The routed layout is required** (`src/db/layout.py`). `chunks` cannot be in cluster order,
  because the cluster id is decided after the rows exist, and a 2 KB `halfvec(1024)` lives in
  TOAST, so a cold probe of 5 clusters is ~114K scattered reads: 7,622 ms on the 1.7M corpus.
  `chunk_vectors (cluster_id, id, dense)` holds only servable rows, sorted, inline: 2K reads,
  54 ms. Vera's dense arm reads it when present (Vera `docs/HARDWARE.md` §9). It costs
  ~2.7 KB a row of disk (4.6 GB at 1.7M). A delta keeps it exact (`sync_layout`: delete what
  left, append what arrived); appended rows sit out of order until the next full prepare, and
  `verify` refuses a database whose layout does not match `chunks`.
* **The parent level** (`src/db/routing.py`, also `ravel db parents`) lets the engine hold
  ~√k centroids instead of every leaf: 184 MB -> 15 MB at 20.6M rows (Vera
  `docs/SCALING.md` §2). It is built from 256 leaves; the engine uses it from
  `TWO_LEVEL_FROM` leaves (8,192), because below that it costs routing recall for memory
  nobody is short of.
* **The gate floor** (`src/db/calibrate.py`, `ravel db calibrate <corpus> --queries ...`)
  is measured after `prepare`, on these clusters, with the corpus's own embedder, and
  written to `corpus_meta.gate`. It needs labelled questions (≥30 in-domain, ≥10
  off-topic), so it is a separate step. Re-run it after a re-cluster: the engine refuses
  a calibration made on a different cluster count.
* **The accelerators** (RUM, pg_search) are optional. The engine probes for each and falls
  back to a slower scan. They are attempted and a failure is reported, never swallowed.
* **The region scope** (Vera migration 0007, `src/db/accelerate.py`) has two parts.
  * The BM25 index carries `enacting_body` as a literal field
    (`(enacting_body::pdb.literal)`). An index built before the field is replaced, not kept.
  * `corpus_regions` lists the regional `enacting_body` values, without two-letter
    artefacts or generic words like `KABUPATEN`.

  Vera's region arm searches within the region a question names. It needs both parts and
  stays off without them. On the 604-case eval it took Recall@5 from 59.1 to 63.6%
  (`Vera/docs/ROADMAP.md` Stage 3).

  A pg_search too old for the literal field gets the plain index (`fallback 1` in the
  report) and no list. A delta refreshes the list after it lands, since it can add a
  region. A relabel cannot: it changes only `chapter`.

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

Documents are content-addressed (`doc_id` is the sha256 of the source bytes, or of every file
a linked document read: `CANONICAL_FORMAT.md` §2), so a changed source is a new document and
the old one is gone. A delta is the difference of two sealed bundles' document sets. The new
bundle comes from the ordinary pipeline in a workspace of its own. Extraction there is a full
pass over the snapshot. Embedding takes every vector whose text the previous snapshot already
embedded (`ravel embed --reuse <old workspace>`): matched by the sha256 of the embed text, in
the same vector space only, and checked by re-embedding a sample at cosine >= 0.999 before the
first reused vector is written. Only changed text costs model time.

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

Measured on the real 1.7M-chunk database (846 clusters), a round trip: remove 300 documents
(13,304 chunks), then add them back.

| | remove | re-add |
|---|---|---|
| place | 0 s | 327 s |
| apply (one transaction) | 26 s | 85 s |
| analyze, verify | 11 s | 10 s |

The database ended at the original manifest (`add1aa61a4039dab`), 1,716,628 chunks, 1,691,419
placed, cluster row counts summing to the placed chunks, and two lineage rows in
`corpus_versions`. The new chunks sat 0.2052 from their centroids against 0.2064 for the corpus
(no drift: they are the same documents). The 327 s of placement is not the assignment (13k
vectors); it is the drift reading, `ORDER BY random()` over every row's vector, and wants a
sampled scan.

Checked against real Postgres in CI: applying a delta leaves the chunks, the derived rows
(including an unchanged document's new status) and the cluster sizes that a clean rebuild of the
new version would have, and a stale or repeated delta is refused.


## 4c. Relabels: a newer profile's text rules, without a rebuild

```
ravel bundle relabel <bundle> --out <new bundle> --corpus <id> [--profile <ref>] [--dry-run]
ravel db relabel <new bundle> --dsn ...
```

A delta refuses a changed profile, because a changed recipe is usually a new space. One kind
of profile change is not: a label decided by the chunk's **own text** (`structure.explanation`,
`src/chunk/labels.py`). The chunker applies it to every chunk it emits; `bundle relabel` applies
the same function to a built bundle, so the result carries the labels a rebuild would, and
chunk ids do not move (an id hashes the locator and blocks, never the chapter).

* **Only what changed is re-embedded.** The embed header carries `{chapter}`, so a relabelled
  chunk's vector is redone; nothing else is.
* **The embedder must reproduce the bundle first.** Unchanged chunks are re-embedded and must
  match their stored vectors at cosine >= 0.999, or nothing is written (`CLAUDE.md` §7.3).
* **A new bundle, never an edit.** Unchanged files are hard links; shards holding a changed
  row are rewritten; `relabel.parquet` lists each id with its old and new chapter; the
  manifest gets a new hash, a `relabel` block naming its parent, and a resealed inventory.
* **`db relabel` keeps the provenance guard on.** Under the same row-locked version check as
  `db apply`, it requires every row to still read its old label, then deletes and re-inserts
  those rows with the new chapter and vector (the guard forbids an `UPDATE` of labels, and is
  never disabled), updates `chunk_vectors`, and records the step in `corpus_versions`.

Measured 2026-11-15 on the 1.7M `id_reg_markdown` bundle with `id_regulation_md@1.3`: 3,077
explanation chunks in 537 documents relabelled `PENJELASAN` (their heading was lost to OCR),
embedder reproduction cosine 1.00000, mean cosine of a relabelled chunk to its old vector
0.968, 111 shards rewritten, applied to the live database in 92 s.

## 5. Measurements behind the design

* Vector copy: text route 1,353 s, binary 139 s, identical `halfvec` (all 20,000 values of one
  real shard equal, and equal to the parquet values).
* Clustering: Vera's `kmeans.py` seeded k-means++ over every row and did not finish in 18
  minutes; fitting on a sample and assigning in batches took 184 s including reading the
  vectors.
* The first `fit` on the real corpus hit its 30-iteration cap without converging
  (`converged: False`); the clusters are usable (smallest 8, largest 10,609, none empty) and
  the cap is a parameter, not a verdict.
