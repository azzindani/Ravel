# MIGRATION.md — Ravel

What Ravel absorbs, from where, and the rules for porting it.

---

## 1. What is being absorbed

| Source | Contains | Becomes |
|---|---|---|
| `Vera/dev_tools/pre_embed/` | `ingest.py`, `batching.py`, `sparse.py`, `sample.py`, `eval_arms.py`, `query.py`, `schema.sql` | the seed of `ravel/` — Vera keeps only the Rust engine |
| `notebooks/docling/` | SmolDocling runners (plain, vLLM), ID regulation parsers v1/v2, downloader, cleaner | `ravel/extract/` + `ravel/chunk/id_regulation` |
| `notebooks/preprocess/` | parallel SmolDocling, spell correction (CPU), translation (GPU), markdown formatting, QAI generation, dataset filter | `ravel/extract/cleanup/` + `ravel/enrich/` |
| `notebooks/ner/` | four NER iterations, category hierarchy, concurrent GPU inference | `ravel/enrich/ner/` |
| `notebooks/embedding/` | Qwen3 embedding runners, MiniLM encode/rerank | `ravel/embed/` |
| `notebooks/rag/` | end-to-end RAG knowledge base notebooks | reference only — the design it validated |
| `06_ID_Legal/document_parser/` | eight extractors (pdf, docx, html, image, csv, json, url, text), context builder, storage | `ravel/extract/` fallback extractors |
| `06_ID_Legal/VOCABULARY_EXPANSION_GUIDE.md` | domain vocabulary and expansion approach | `ravel/enrich/vocab/` |
| `06_Nanonets_OCRs/`, `06_QwenVL/` | OCR and VLM experiments | OCR extractor implementations |
| `Krawl/` | crawling | **stays separate** — Ravel does not collect |

---

## 2. Porting rules

A notebook is not a stage. Porting means extracting the logic and discarding the
workflow around it.

1. **One notebook → one registered component** (extractor, cleanup, chunker, enricher,
   embedder) with an `id` and a `version`.
2. **Parameters become config.** Every hardcoded path, batch size, model name, threshold
   and regex moves into the component's config schema. Anything left hardcoded is
   invisible to the cache key and therefore a correctness bug (`LOOPHOLES.md` §1).
3. **Output is validated.** An extractor's output passes `canon validate` or it does not
   ship. Notebook output that "looked right" is not evidence.
4. **No cell-order dependence.** A component is a pure function of its inputs.
5. **Progress and checkpoint come from the runtime**, not from the component. Components
   do not own loops, retries, `tqdm`, or file writing.
6. **Keep the notebook.** Archive it under `notebooks/` as the provenance of the port.
   It documents what was tried, including the versions that were rejected.
7. **Port the latest working version, review the earlier ones.** `NER_v4` is probably the
   one to keep — but v1–v3 often contain handling for cases v4 quietly dropped.
8. **Expect a transformers 4.x → 5.x migration.** Ravel targets `transformers==5.17.0`
   (`STACK.md` §2); the notebooks were written against 4.x. This is a real API migration,
   not a copy-paste — pipeline construction, generation config, processor APIs and
   `trust_remote_code` handling all moved. Budget it per notebook, and validate the port
   by output equivalence on a fixture, not by "it runs".

---

## 3. Vera's `pre_embed` hand-off

`Vera/dev_tools/pre_embed/` moves to Ravel and Vera keeps only `crates/` (the Rust
engine) plus its own docs. After the move:

- Vera's `PRE_EMBEDDING.md` becomes a **pointer** to Ravel, not a spec. Its content is
  the origin of `ARCHITECTURE.md`, `EMBEDDING.md` and `BUNDLE.md` here, and two copies
  will drift.
- `Vera/migrations/*.sql` becomes generated output from Ravel's schema definition
  (`BUNDLE.md` §2). Until that generator exists, Vera's `0001_init.sql` is the source of
  truth and Ravel must match it exactly.
- `Vera/.test/ID_REG_DB_2511/id_regulations.db` and the `0.6B` model stay as Vera's test
  fixtures — Ravel produces bundles, Vera tests against a small one.

Do this move early. Every week `pre_embed` stays in Vera is a week of Vera's CI, docs and
standards compliance covering code that belongs elsewhere.

---

## 4. Rebuilding ID_Legal — the acceptance test

Ravel is not done until it rebuilds `06_ID_Legal` from sources and the result **beats the
existing corpus on the labeled eval set**. That is the acceptance test for the whole
project.

Order:

1. **Sources: confirmed present.** `Azzindani/ID_REG` on Hugging Face holds the source
   zips (~17GB cached locally in `notebooks/docling/downloads/`), plus 998 PDFs in
   `notebooks/docling/00_Input/`. The worst case — lost sources — did not happen.
   See `ABSORPTION.md` §2.
2. **Load the incumbent as a baseline bundle.** The existing 748,558-chunk corpus lives
   in `Vera/.test/ID_REG_DB_2511/id_regulations.db` and loads via
   `pre_embed/schema.sql`. Publish it as `id_legal/incumbent/v0`, flagged
   provenance-incomplete, build the labeled eval set against it (`EVAL.md` §6) and score
   it. That score is the number to beat. Hours, not GPU-days.
3. `ravel extract id_legal` — full extraction on GPU, resumable.
4. Build the `pasal` and `token512` variants on a stratified sample; compare.
5. Build the winner full-size, bundle, load, score in routed mode.
6. Record the promotion (`EVAL.md` §5).

Step 2 is the one that gets skipped and must not be: without the incumbent's score,
"better" is an opinion, and ID_Legal's replacement inherits its original defect.

**Why a rebuild is unavoidable, not merely nicer** (`ABSORPTION.md` §3): the incumbent has
**no `source_url` on any of its 748,558 rows**, and a citation cannot be back-filled.
Vera's schema declares it NOT NULL. Add 10.66% `Unknown` regulation_type from a parser
hardcoded to one ministry, and the absence of any canonical intermediate to re-chunk from,
and salvage stops being an option for production — while staying exactly the right thing
to do for the baseline.

---

## 5. What is deliberately not absorbed

- **Crawling and downloading** — `Krawl` and ID_Legal's downloader notebooks. Sources
  arrive as files.
- **Serving, API, UI** — ID_Legal's `api/`, `conversation/`, Gradio notebooks. Vera
  serves; agents converse.
- **Fine-tuning** — `notebooks/finetune/`. A fine-tuned embedder could become an embedder
  plugin, but training it is not Ravel's job.
- **ID_Legal's `config.py`** (47KB). Read it for the domain knowledge encoded in it —
  regulation hierarchies, vocabulary, patterns — and re-express that as corpus config and
  enricher data. Do not port the file.
