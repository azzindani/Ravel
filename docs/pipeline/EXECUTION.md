# EXECUTION.md — Ravel

The runtime: work queue, checkpoints, resume, concurrency, portability. This is what
makes Ravel a pipeline instead of a script.

---

## 1. The requirement

> Hyperspeed extraction, concurrency, scale, with progress and checkpoint support, so a
> run can continue whenever needed.

And it must behave identically on: a local Windows box with one GPU, a Colab session, a
Kaggle session (9-hour limit, ephemeral disk), a Vast/RunPod box that can be reclaimed,
or a multi-GPU machine.

**Design consequence:** interruption is not an error path, it is the normal path. A run
that cannot be killed at any instant and resumed with zero lost work is broken.

---

## 2. Work queue, not a loop

```
  work items (durable)                    workers
 ┌──────────────────────┐
 │ id, stage, key,      │    lease ──►  ┌──────────────┐
 │ status, attempts,    │               │ io pool      │  scan, hash, write
 │ lease_expires, error │    ack   ◄──  │ cpu pool     │  probe, clean, chunk
 └──────────────────────┘               │ gpu worker/s │  extract, embed (batched)
                                        └──────────────┘
```

- Unit of work: **one document** in Phase A, **one shard of chunks** in Phase B.
- Items are **leased**, not popped. A worker that dies lets the lease expire and the item
  returns to the queue. No lost work, no double work.
- `attempts` drives the retry/quarantine policy (`EXTRACTION.md` §4).
- Queues are **bounded**. An unbounded prefetch queue in front of a slow GPU is an OOM
  with extra steps.

The queue is boring technology on purpose: SQLite locally, Postgres when shared. What
matters is that it is durable and leased.

---

## 3. Idempotence by content hash

Every stage output is addressed by a deterministic key:

| Stage | Key |
|---|---|
| extract | `sha256(source bytes) + extractor_id + extractor_version + params_hash` |
| cleanup | `canon_key + cleanup_id + version` |
| chunk | `canon_key + chunker_id + version + config_hash` |
| enrich | `chunk_id + enricher_id + version` |
| embed | `chunk_id + model + model_version + instruction + pooling + template` |

Three properties follow, and all three are load-bearing:

1. **Resume is free.** Restarting skips any key that already exists.
2. **Variants share work automatically.** Two variants with the same chunker but
   different embedders reuse every chunk. Two variants with different chunkers share
   every canonical document.
3. **Re-running is safe.** Writes are content-addressed, so a duplicate write is a no-op
   rather than a duplicate row.

**Never let a cache key omit a parameter that changes the output.** A silently reused
stale artifact is the worst bug class here, because the run succeeds and the corpus is
subtly wrong.

---

## 4. Checkpoints must survive the machine

Kaggle and Colab delete local disk when the session ends. Therefore:

> **Local disk is a cache, never a checkpoint.**

The checkpoint store holds: the work queue state, the canonical documents, staged
parquet shards and the run log. It must be a remote object store (S3-compatible R2/B2,
or a Hugging Face dataset repo) with a local disk cache in front of it.

**Everything stored remotely is sharded**, never one object per item. A 30K-document
corpus written as 30K canonical JSON files is 30K uploads, 30K listings on every resume,
and rate limits on HF. The same data as ~30 parquet shards is trivially fast. Shard,
then upload — never upload per item.

Write pattern: accumulate locally → seal a shard → upload → mark its items done → the
local copy becomes an evictable cache entry. Never mark done before the upload
completes; a completed-but-lost artifact is worse than a retried one. Shard size is the
trade-off between upload granularity and object count: ~256MB–1GB, matching
`BUNDLE.md` §4.

Resume on a fresh machine is then: pull queue state, pull the shards you need, continue.
`ravel status` reports exactly what would resume without re-scanning anything.

**Which store is not yet decided** (`OPEN_QUESTIONS.md` §2). R2 (no egress fees) and HF
datasets (free, versioned, already in the workflow via `10_Dataset_HF`) are the
candidates.

---

## 5. Concurrency rules

1. One GPU worker per device, fed by a queue. Never N processes per GPU.
2. Batch by token/pixel budget, never by fixed count.
3. CPU pool sized to cores; IO pool sized higher (latency-bound).
4. Bounded queues between every stage; backpressure, never drop.
5. Stage isolation: a slow writer must not stall the GPU, and a fast GPU must not
   exhaust RAM by outrunning the writer.
6. Graceful shutdown on SIGTERM/SIGINT: stop leasing, finish in-flight items, flush,
   release leases, exit. On Kaggle the timeout is known in advance — Ravel should stop
   itself cleanly *before* the wall, not get killed at it.

---

## 6. Progress

Per stage: queued / leased / done / failed / quarantined, plus throughput (items/s,
tokens/s), ETA, and a per-device utilization line. Persisted, so progress survives a
restart and `ravel status` is instant.

The run report at the end is a first-class artifact: counts in and out of every stage,
extractor distribution, failure reasons, wall time, GPU hours, estimated cost. It goes
into the bundle. A corpus you cannot account for is a corpus you cannot trust.

---

## 7. Hardware profiles

| Environment | Constraint | Ravel's answer |
|---|---|---|
| Local Windows, 1 GPU | small VRAM, no time limit | small batches, run for days, local cache + remote checkpoint |
| Colab | session limits, drive quota | remote checkpoints, frequent flush |
| Kaggle | 9h wall, ephemeral disk, 2×T4 | self-stop before the wall, two GPU workers |
| Vast / RunPod | reclaimable, fast disk, big GPU | large batches, aggressive upload |
| Multi-GPU box | many devices | one worker per device, shared queue |

Nothing about the pipeline changes between these — only config. That is the portability
claim, and it is testable: the same corpus, built in two environments, must produce
byte-identical chunk ids and equivalent bundles.

---

## Memory is a bound, not a hope

A shard has three seal conditions, and the one that matters is not the size target.

| Bound | Shapes | Default |
|---|---|---|
| `shard_target_mb` | the artifact on disk | 256 MB |
| `shard_max_blocks` | **the process** | 500,000 |
| `shard_max_docs` | the process, coarsely | 5,000 |

! `shard_target_mb` estimates the *compressed parquet* weight of a shard. The buffered
`CanonicalDoc` objects that produce it cost roughly sixty times that: a pydantic `Block`
with a bbox and an attrs dict is about 1 KB of Python object for ~30 bytes on disk. The
first full ID_Legal run held **5 GB resident** to seal a 41 MB shard. On a workstation
that is merely wasteful; on an 8 GB Kaggle box it is an OOM kill nine hours in, and
Kaggle is an environment this project is required to run in.

Counting **blocks** tracks that cost directly. Counting documents does not — documents in
this corpus range from 60 blocks to 20,000, so a document cap sized for the average is
either useless or crippling. With `shard_max_blocks: 250000`, the same corpus extracts at
**~1.0 GB resident**, steady, for as long as the run lasts.

The cost of a lower cap is more shards, and a shard is only a checkpoint — more of them
means *less* work lost to an interruption, not more. There is no reason to run this near
the edge.
