# PROGRESS.md — Ravel

What is built, what is not. Moved out of `CLAUDE.md` on 2026-11-22; the items up to the
2026-11-22 heading are as they stood there.

## Sources and the lifecycle (2026-11-22)

- [x] XML extractor with dialects in YAML: `gpo_cfr`, `ecfr`, `justice_ca`, `legi`, `jp_law`,
      `de_gii` — `src/extract/xml.py`, `registry/dialects/`
- [x] Fetch kinds: `govinfo_cfr`, `github`, `archive_index`, `archive_url`, `listing`, with
      url maps for citation — `src/sources/fetch.py`
- [x] `ravel build` (from nothing), `ravel update` (a new snapshot as a delta; vectors
      reused by text, across snapshots and profile versions), `ravel publish` (a dataset
      with its card from `datasets/`) — `src/cli.py`, `DATABASE.md` §4a–4b, `PROFILES.md` §7
- [x] Published registry files frozen by test — `registry/published.yaml`
- [x] CJK: token counts by character, BM25 by character bigrams (`WORDS_CJK`, matched in
      Vera's `bm25.rs`)
- [x] Published with embeddings: `CA_LAWS_2611`, `US_CFR_2611`, `JP_LAWS_2611`; `ID_REG_RAG_2611`
- [~] France (`fr_legi`): extracting; Germany
      (`de_gii`): chunked, waiting to embed — `sources/SOURCES_NEXT.md`

## As tracked in CLAUDE.md until 2026-11-22

- [x] Canonical format spec + validator + round-trip test
- [x] Source scanner: discovery, content hashing, source manifest
- [x] Execution runtime: ledger, sharding, resume, bounded memory, byte-aware
      batching — `src/runtime/`
- [ ] Extractor: SmolDocling (structure → markdown), batched on GPU
- [x] Extractor: native PDF text + HTML/markdown/plain, profile-driven structuring
- [ ] Extractor fallbacks: OCR, SmolDocling, table extraction
- [x] Chunker registry + profile-driven `unit` chunker with provenance
- [x] Text-layer quality gate: clean/damaged/absent routing — `src/extract/quality.py`
- [x] URI substrate (`file://`, `hf://`, `s3://`) — `src/uris.py`
- [x] Embedder plugin interface + manifest + preflight, local (transformers) and API backends, resumable `ravel embed` stage — `src/embed/`; run end to end on 12 real documents with Qwen3-Embedding-0.6B on CPU (1.2 vectors/s: a GPU is needed for the 1.33M chunks); vLLM backend still to write
- [x] Hardware profiles, serving-engine path (`kind: api` + concurrency, `verify-backend`), remote mirror + `--worker i/N`, second vector space in the manifest, schema and load plan — `registry/hardware/`, `src/runtime/sync.py`; **all tested without a real vLLM, bucket, second machine or VL model**
- [x] Loader executor (`ravel load`) and bundle assembly (`ravel bundle build`) — `src/load/execute.py`, `src/bundle/build.py`; run against real pgvector in CI
- [x] Bundle writer: manifest, generated schema, parquet shards, inventory — `src/bundle/`
- [x] Clustering code (anchors, spherical k-means, assignment) — `src/cluster/`; **optional, Vera owns clustering at deploy and ingest** (`CLAUDE.md` §5.17)
- [~] Loader: the seven preflight checks + the generated load plan — `src/load/`; the psycopg executor still to write
- [x] Relations: revokes / replaces / amends / based_on from the verb beside each citation, resolved to documents, status derived — `src/enrich/relations.py`, `docs/pipeline/RELATIONS.md`; no NER, by decision
- [x] Quality gate: per-document verdict from the share of unknown words, `indexable` sidecar — `src/enrich/quality.py`, `docs/pipeline/QUALITY.md`; the index half does not read it yet
- [x] Lexical arm: `ravel sparse` fits `corpus.bm25.json` (Vera's `BM25_VOCAB`, required at its startup) and writes sparse vectors; bundled, staged and merged into `chunks.sparse`; run on `id_reg_markdown` — `src/enrich/sparse_run.py`
- [~] Enrichment: ranking factors, references, whole-corpus citation graph — `src/enrich/`
- [x] `ravel db prepare`: bundle → database ready to serve, 16.5 min for 1.7M chunks, binary vector COPY (10x), cluster id written with the row, canary + provenance guard + verify — `src/db/`, `docs/pipeline/DATABASE.md`; `ravel db image`/`attach` (build once, deploy by copy; 12.3 GB, 10 s volume copy, engine verified on it); `ravel bundle delta` + `ravel db apply` (add/update/remove, applied == rebuilt, checked in CI); `chunk_vectors`, the cluster-ordered routed layout (prepare builds it, apply syncs it, verify asserts it; cold dense probe 7.6 s -> 54 ms); `ravel db calibrate` writes the measured gate floor into `corpus_meta.gate`; `cluster_parents` gives Vera two-level routing (engine 184 MB -> 15 MB at 20.6M). Vera served it at 1.7M real and 5M/10M/20M replicas on 2 vCPU / 8 GB at 0.4-0.7 s p50 (Vera `docs/operations/HARDWARE.md` §9)
- [x] Variant harness: chained cache keys, matrix runs, reuse plan — `src/variants/`
- [~] Eval: coverage matching, metrics, bootstrap CI, oracle search — `src/evaluate/`;
      Vera's 50-case seed set imported to `eval/id_legal/queries@v1.jsonl` by
      `tools/import_vera_queries.py`. Still **below the gate it must pass**: 50
      cases against a 100 minimum, 66% factual against a 40% target, and the
      source set's own "STILL NEEDS DOMAIN REVIEW" is unaddressed
- [ ] MCP server: parser authoring tools (inspect, preview, diff, save)
- [~] Docker images: `ravel-job` (Linux GPU batch image, `docker/Dockerfile.job`, torch.compile verified in it); `ravel-service` and the HTTP service still to write
- [ ] Remote URI backends: R2 working state, HF datasets for published bundles
- [x] CI: ruff, hermetic suite on 3 platforms, generated SQL against real pgvector, routing over the 158 sample PDFs, CLI from an installed wheel
- [x] `ruff format` and `mypy --strict` over `src/` and `tools/`, both enforced in CI
- [ ] Rebuild `06_ID_Legal` end-to-end and beat it on the eval set
