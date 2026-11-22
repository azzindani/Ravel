# LOOPHOLES.md — Ravel

Known failure modes of the design, each with its solution. These are the things that
break **silently** — the corpus loads, the queries run, and the answers are quietly
wrong. Review before and during build.

---

## 1. Stale cache reuse  *(highest priority)*

**Hole:** a cache key omits a parameter that changes the output — a chunker config field,
an instruction string, a cleanup version. The stage is skipped, a stale artifact is
reused, and the bundle's manifest describes something that was never built.

**Solution:** cache keys are derived from the **fully resolved config**, not a
hand-written subset (`EXECUTION.md` §3). Adding a parameter without adding it to the key
is a review-blocking bug. Every stage records its key in its output, and
`ravel bundle` re-derives every key and fails if any recorded key disagrees. Bumping a
stage version invalidates its whole cache — cheap insurance, use it freely.

---

## 2. Silent extraction failure

**Hole:** an extractor returns valid-but-empty or badly truncated output. The document
enters the corpus as a real document with no content. Nothing errors. Nobody notices
until retrieval is mysteriously bad.

**Solution:** non-empty ratio floor and structural validation at extraction time
(`CANONICAL_FORMAT.md` §6 rule 6); low-confidence pages flagged in `warnings`; per-source
character/page yield recorded and compared against the corpus's expected distribution, so
outliers surface in the run report. **A crash is better than an empty success.**

---

## 3. Missing documents that nobody counts

**Hole:** 300 of 30,000 documents fail across a multi-day run. The bundle is built,
loaded and served, and the corpus is 1% incomplete — invisibly.

**Solution:** every failure is recorded in `failed.jsonl` with a reason; the count is in
the manifest; bundle preflight fails if it exceeds the corpus's declared tolerance
(`BUNDLE.md` §5 check 4). Completeness is asserted, not assumed.

---

## 4. Embedding space mismatch

**Hole:** the corpus is embedded with one model/instruction/pooling and the query side
uses another. Cosine similarity is still computed; results are just quietly worse. No
error anywhere.

**Solution:** the manifest pins model, exact version, instruction strings, pooling and
normalization; a cosine round-trip preflight (≥ 0.999) runs **before** the bulk run; a
reference vector ships in the bundle for Vera's startup canary; load fails on dimension
or config mismatch (`EMBEDDING.md` §3).

---

## 5. Chunking destroys the unit of meaning

**Hole:** fixed-size chunking splits a pasal mid-clause, or separates an ayat from the
pasal that gives it meaning. The chunk is retrievable but not usable, and no ranking
change can repair it.

**Solution:** structure-aware chunking on the legal unit, heading path carried into every
chunk, parent context retained for dependent units (`CHUNKING.md` §3). The
eval set is what proves it worked; the `token` chunker stays in the matrix as the
baseline that keeps everyone honest.

---

## 6. Provenance that does not survive the pipeline

**Hole:** page numbers get lost in cleanup, or a chunk's locator points at the canonical
intermediate rather than the source. The citation is wrong; a human clicks through and
lands nowhere. Trust collapses — and trust is the product.

**Solution:** provenance is captured at chunk creation from the canonical document
(`CHUNKING.md` §4), carried through untouched, NOT NULL in the schema, immutable in the
database, and preflight-checked for nulls before load. Cleanup may never drop `page` or
`bbox`. Source `sha256` travels with the chunk so upstream changes are detectable.

> **This already happened.** The incumbent ID_Legal corpus has no `source_url` on any of
> its 748,558 rows, because the pipeline that built it never carried one. It cannot be
> repaired — a citation is either captured at ingest or gone. `ABSORPTION.md` §3.

---

## 7. The bundle format cannot carry a signal you invent later

**Hole:** entity or graph output has nowhere to live, so it is bolted on later — which
means re-running enrichment over 100M chunks, or a schema migration on a loaded corpus.

**Solution:** the bundle carries `signals`, `entities` and `edges` **from day one**, even
though Vera does not read them yet (`ARCHITECTURE.md` §5). Signals start as JSONB and
graduate to columns only when eval proves the ranker uses them. Format work is cheap
now and impossible later.

---

## 8. Phase A/B boundary erosion

**Hole:** a chunker needs something the canonical format lacks — font size, bbox, reading
order — so someone "just reads the PDF" inside the chunker. Now chunking depends on the
source file, the cache key is wrong, experiments cost a full re-extraction, and the
project is back to notebooks.

**Solution:** the canonical format must pass the completeness test before it is frozen
(`CANONICAL_FORMAT.md` §5). A chunker that opens a source file is a review-blocking bug.
If the format is genuinely insufficient, extend it and re-extract deliberately, with a
`canon_version` bump.

---

## 9. Checkpoint on ephemeral disk

**Hole:** a Kaggle run checkpoints to `/kaggle/working`. The session ends. The checkpoint
is gone along with nine hours of GPU time.

**Solution:** local disk is a cache, never a checkpoint (`EXECUTION.md` §4). Artifacts are
marked done only **after** the remote upload completes. Self-stop before the known wall
clock rather than being killed at it.

---

## 10. Unbalanced clusters break Vera's memory guarantee

**Hole:** k-means produces one cluster holding a large fraction of the corpus. Vera's
sequential per-cluster load assumes ~10K rows per cluster; a giant cluster blows the
per-request working set and the OOM guarantee with it.

**Solution:** report the cluster size distribution, enforce a size ceiling, split
oversized clusters before publishing, and fail the bundle if the ceiling is exceeded
(`CLUSTERING.md` §3).

---

## 11. LLM-derived text leaking into `body`

**Hole:** a summary, translation or generated QA pair ends up in the chunk body. A human
verifies it against the source and it is not there. The citation is now a fabrication
with a real-looking link.

**Solution:** `body` is verbatim source text, always (`CHUNKING.md` §5). LLM output is a
signal, stored separately, never presented as source. Enforce with a build-time check
that body text appears in the canonical document.

---

## 12. Two schema definitions drifting

**Hole:** Ravel writes what it thinks the schema is; Vera's migrations say something
slightly different. The corpus loads. Column semantics diverge. The failure surfaces as
"search is worse than it used to be".

**Solution:** one definition, generated (`BUNDLE.md` §2). Ravel owns it; Vera's migrations
are generated output, not hand-maintained source. `schema.sql` ships inside every bundle
so a loaded corpus always states the DDL it expects.

---

## Review rule

Any change to a cache key, the canonical format, the provenance path, the manifest, or
the cluster publishing step must be checked against the relevant loophole above before
merge.

---

## Found in practice, not in theory

Every entry below was caught by running the pipeline on the real corpus, and every one
of them was silent — nothing raised, nothing was overwritten, and the counts looked
plausible. They are recorded here because the class of bug matters more than the fix.

### A validator that replays the construction certifies its bugs

`heading_path` was built by indexing the heading stack by *depth*, but profiles assign
fixed levels with gaps on purpose (bab 3, pasal 6). A document opening at level 3 left
the stack one deep, so the next level-3 heading did not displace its sibling: real
regulations came out reading `Menimbang › Mengingat › BAB I` — three siblings recorded
as ancestors.

931 documents passed validation carrying it, because validator rule 3 re-implemented the
same walk. **A check that re-derives a value the way the code did is not a check.** The
rule now asserts the invariant instead — a path is strictly increasing in level — and
fails against the old builder.

### A cache key that is too wide is also a bug

`LOOPHOLES.md` §1 is about keys that *omit* a parameter. Overshooting is the same
failure wearing the opposite coat: hashing the whole corpus spec meant raising a memory
cap discarded 931 extracted documents to reproduce them byte for byte, and hashing the
whole profile meant correcting a citation pattern did the same. A cache invalidated by
edits that provably cannot change the result is a cache people stop trusting.

Hence `CorpusSpec.extraction_hash` and `ProfileSpec.structure_hash`: the parts that
change what a document *contains*, and only those. The burden stays on the content side
— a field that reaches the extractor or the structurer and is not in there is a
review-blocking bug.

### Resume semantics become duplication semantics when the keys change

`ShardWriter` resumes into a fresh shard index rather than overwriting, which is right:
an interrupted run must not destroy what it already wrote. But when the stage keys
change — an extractor version bump — the old shards are not superseded, they are joined.
Re-extracting appended 17 new shards beside 17 stale ones, and the next stage read every
document twice: **931 documents became 1,862, half of them carrying the exact bug the
re-run existed to fix.**

Canonical shards now live under `canon/<corpus>/<generation>/`, where the generation
hashes the whole extraction configuration. A superseded extraction becomes inert rather
than invisible: still on disk, still addressable, no longer in the path anything reads.

### `done` cannot see the run that is writing it

The resume set is read once at the start, so within a single run two sources with
identical bytes were both extracted. Both drivers now track keys seen in the current
run — and the check sits immediately before the write, so a *failure* is still counted
as a failure rather than absorbed as a duplicate.

### A memory bound checked after the fact is not a bound

Sealing a shard after adding an item makes the real bound `cap + one item`. Fine until
one item is enormous: ID_Legal contains a 3,359-page scan that extracts to 479,355
blocks, 570x the median, so a 250k cap would have held 729k blocks at its peak. The
check now happens before buffering, making the bound `max(cap, one item)` — still not
free, since a single document must fit in memory to be written at all, but it no longer
degrades exactly when the corpus throws something unusual at it.

### A fix that names a moving target has not fixed anything

Generations were introduced to stop two artifact sets sharing a directory. The chunk
stage then derived its own generation from the string `"latest"` — a placeholder meaning
"whatever extraction is newest" — so *every* canonical generation hashed to the same
chunk directory, and re-extracting plus re-chunking appended 120,878 rows beside the
previous 121,757. The fix reproduced the bug it was written to close.

A generation is resolved to a concrete name once, at construction, and hashed as that.

### A doc_id is the source, not the document

`doc_id` is the sha256 of the source *bytes*, so it is identical across two extractions
of one file — and a corrected extractor produces a completely different canonical
document under the same doc_id. The chunk stage key did not include the extraction
generation, so re-chunking a corrected extraction found every key already done, wrote
nothing, and reported success with the old chunks still in place.

The lesson generalizes: a cache key must name the *artifact* a stage consumed, not the
input that artifact was derived from.
