# ABSORPTION.md — Ravel

A survey of what already exists, measured rather than assumed. `MIGRATION.md` says *how*
to port; this says *what is there, what shape it is in, and what it is worth.*

Surveyed 2026-09-12 across `Vera/pipelines/pre_embed`, `AI_Workspace/20_Docling`,
`AI_Workspace/20_Lab_Data_Preprocess`, `AI_Workspace/21_Lab_NER`, `06_ID_Legal`.

---

## 1. The headline: the corpus already exists, and it is larger than assumed

`Vera/.test/ID_REG_DB_2511/id_regulations.db` — **11 GB SQLite**, and not a fixture:

| | |
|---|---|
| chunks | **748,558** |
| distinct regulations (type+number+year) | **16,381** |
| regulation types | 31 |
| enacting bodies | 2,700 |
| years spanned | 77 |
| mean body length | 1,234 chars (min 3, max 32,767) |
| dense embeddings | 748,558 × **1024** |
| tf-idf vectors | 748,558 × 20,000 |
| FTS5 full-text index | present |
| KG scalar features | **20 columns** per chunk |
| KG JSON payloads | **10 blobs** per chunk |

This answers `OPEN_QUESTIONS.md` §7 and resizes several decisions: at 748K chunks,
`CLUSTERING.md` §3 gives **k ≈ 75**, not 10,000. The 100M-row planning in Vera's docs is a
future scale, not this corpus.

**Top regulation types** — and note how little of it is national law:

```
PERATURAN DAERAH KABUPATEN  261,899   PERATURAN PEMERINTAH   46,280
PERATURAN BUPATI            109,002   PERATURAN WALIKOTA     43,106
Unknown                      79,764   UNDANG-UNDANG          39,129
PERATURAN DAERAH KOTA        71,978   PERATURAN GUBERNUR     25,918
```

---

## 2. Sources survive, on Hugging Face

The worst case — an unreproducible corpus whose inputs are gone — **did not happen**.

| Where | What |
|---|---|
| `Azzindani/ID_REG` (HF) | the source document zips; `batch-55-chunk-{2..5}.zip`, ~17 GB, are cached locally in `20_Docling/downloads/` |
| `Azzindani/ID_REG_KG_2511` (HF) | the enriched corpus matching the local SQLite |
| `20_Docling/00_Input/` | 998 source PDFs, 5 GB — a working sample |
| `20_Docling/02_cleaned_regulations/` | 79 SmolDocling markdown outputs |

`MIGRATION.md` §4 step 1 is therefore satisfied, and it independently validates
`INTERFACES.md` §5: the cloud-to-cloud substrate is not a proposal, it is where the
corpus already lives.

---

## 3. Measured defects of the incumbent corpus

This is the baseline Ravel has to beat, quantified.

| Defect | Rows | % |
|---|---:|---:|
| **`source_url` — absent entirely** | **748,558** | **100%** |
| `chapter = 'N/A'` | 140,871 | 18.82% |
| `regulation_type = 'Unknown'` | 79,764 | 10.66% |
| `enacting_body = 'Unknown'` | 64,521 | 8.62% |
| body < 40 chars (non-indexable) | 50,883 | 6.80% |
| `article = 'Pembukaan'` (not an article) | 54,797 | 7.32% |
| body is exactly `Cukup jelas` / `Cukup jelas.` | 30,520 | 4.08% |
| `UndangUndang` — hyphen lost in extraction | 32,177 | 4.30% |
| truncated at exactly 32,767 chars | 3,615 | 0.48% |
| body contains U+FFFD (encoding damage) | 3,394 | 0.45% |

**The first row is the blocker.** Vera's production schema declares `source_url TEXT NOT
NULL`; `pre_embed/schema.sql` had to make it nullable for the spike, with the comment
*"this corpus has no source_url, and inventing one is forbidden."* A citation you cannot
click is not provenance, and **it cannot be back-filled after the fact** — it has to come
from ingestion.

So the existing corpus is structurally unable to load into Vera's target schema. That is
the single strongest argument for rebuilding from sources rather than salvaging.

Visible damage in a real row: `NOMOR 26 TAHUN 20092008` — two years concatenated, almost
certainly a header/footer collision during extraction. Exactly the class of failure
`CANONICAL_FORMAT.md` keeps typed blocks for.

---

## 4. Root cause: the parsers were written for one ministry

`00_ID_REG_Parser_v1.ipynb` and `v2` both key their metadata extraction on a hardcoded
regex:

```python
r'PERATURAN\s+MENTERI\s+KEUANGAN\s+REPUBLIK\s+INDONESIA\s+NOMOR\s+(\d+)\s+TAHUN\s+(\d+)'
```

Against a corpus that is ~35% *Peraturan Daerah Kabupaten* and ~15% *Peraturan Bupati*,
that regex cannot match — which is precisely the 10.66% `Unknown` type and 8.62% `Unknown`
enacting body.

**And the fix already existed.** `06_ID_Legal/core/legal_vocab.py` contains
`REGULATION_TYPE_PATTERNS` covering the general case. The knowledge was in the repository;
it just never reached the parser, because the parser lived in a notebook and the
vocabulary lived in the app.

That is the ID_Legal disease in one sentence, and the reason Ravel has a registry.

### A live landmine

`00_ID_REG_Parser_v2.ipynb` contains:

```python
core_content_text = re.sub(r'r\s*', '', core_content_text)   # "Remove 'r ' character"
```

This deletes **every lowercase `r`** in the document. Verified it did *not* reach the
corpus (696,280 of 748,558 rows still contain a lowercase `r`), so v2 was an abandoned
experiment — but it is sitting in a notebook that looks runnable. A cleanup rule that
destroys text while producing plausible-looking output is exactly why
`MIGRATION.md` rule 3 requires validated output, not "it ran".

---

## 5. Chunking granularity: the better parser was not the one used

The corpus is chunked at **pasal level** (`article` = `Pasal 1`, `Pasal 2`, …), plus
non-article sections (`Pembukaan` 54,797; `Pengesahan` 27,516). No ayat, no huruf.

But `Parser_v1` already implemented the full hierarchy — pasal → ayat → huruf — and
emitted a `full_reference` field (`Pasal 5 ayat (2) huruf a`), which is exactly
`CHUNKING.md`'s `identifier` and exactly what Vera's exact-match bypass needs.

So the finer chunker exists, was written, and was not what shipped. Recovering it is
`ravel/chunk/id_regulation` v1, and pasal-vs-ayat becomes the first real variant sweep.

---

## 6. What the KG layer actually contains

Not speculative — 748,558 rows of it. `ENRICHMENT.md` §3's "signal families" were
describing something that already exists.

**Scalar columns:** `kg_entity_count`, `kg_cross_ref_count`, `kg_primary_domain` (28
distinct), `kg_domain_confidence`, `kg_cluster_count`, `kg_cluster_diversity`,
`kg_authority_score`, `kg_hierarchy_level`, `kg_temporal_score`, `kg_years_old`,
`kg_legal_richness`, `kg_legal_complexity`, `kg_has_obligations`, `kg_has_prohibitions`,
`kg_has_permissions`, `kg_completeness_score`, `kg_connectivity_score`, `kg_pagerank`,
`kg_degree_centrality`.

**JSON payloads:** entities (typed, with character positions), cross-references (with
captured groups), legal domains with confidence, concept clusters, legal actions,
sanctions, action/sanction types, a 32-dim concept vector, citation impact.

Sample entity:

```json
{"text": "Undang-Undang Nomor 39 Tahun 2007", "type": "regulation_reference",
 "metadata": {"regulation_type": "UNDANG-UNDANG", "number": "39",
              "year": "2007", "position": 364}}
```

This is regex + heuristics, not a trained NER — and for legal citations that is the right
tool. Character positions mean mentions can be mapped back to spans, which is what
`ENRICHMENT.md` §4 wants for `mentions`.

**Two caveats:** `kg_pagerank` and `kg_degree_centrality` are **NULL** — graph-wide
metrics were designed, columned, and never computed. And `kg_citation_impact_json` is
empty. The graph was built per-chunk; the *graph* part never closed.

---

## 7. Absorption verdicts

| Source | Verdict | Target | Why |
|---|---|---|---|
| `06_ID_Legal/core/legal_vocab.py` | **port as data** | `ravel/enrich/vocab/` | 23 domain synonym sets, `LEGAL_DOMAINS`, `INDONESIAN_STOPWORDS`, `REGULATION_TYPE_PATTERNS`. Pure data, zero risk, fixes §4's root cause |
| `pre_embed/sparse.py` | **port near-verbatim** | `ravel/enrich/sparse.py` | `Bm25Vectorizer` with the vocabulary saved and hashed — directly fixes the 20K-dim tf-idf whose vocab was lost |
| `pre_embed/batching.py` | **port as-is** | `ravel/runtime/batching.py` | byte-aware batching; already `EXTRACTION.md` §3 rule 2 |
| `pre_embed/ingest.py` | **port the invariants, rewrite the code** | manifest / queue / preflight | `corpus_meta`, `ingest_progress`, round-trip gate, `indexable` and `truncated_at_source` flags are all Ravel concepts already |
| `id-reg-smoldocling-tesseract-v2-rtx3090-16.ipynb` | **port the runtime** | `ravel/runtime/gpu.py` | `AggressiveConverterPool`, VRAM checks, `GPUMonitor`, memory cleanup — hard-won operational knowledge |
| `00_ID_REG_Parser_v1.ipynb` | **port the hierarchy, replace the metadata** | `ravel/chunk/id_regulation` | pasal/ayat/huruf + `full_reference` is right; the PMK-only regex is the bug |
| `core/knowledge_graph/kg_core.py` | **port selectively** | `ravel/enrich/legal/` | entity + cross-ref extraction with positions and confidence; leave the ranking/boosting behind (that is Vera's job) |
| `document_parser/extractors/*` (8) | **reference the shape, rewrite** | `ravel/extract/` | clean ABC, but `extract() → {text, page_count, metadata, method}` is far too thin for the canonical format — no blocks, no bbox, no reading order |
| `21_Lab_NER/*.ipynb` (v1–v4) | **evaluate, then port one** | `ravel/enrich/ner/` | four iterations; v4 is the candidate, but check what v1–v3 handled that it dropped |
| `20_Lab_Data_Preprocess/*` | **port as cleanups** | `ravel/extract/cleanup/` | spell correction, markdown formatting, dataset filter |
| `00_ID_REG_Clean_v1.ipynb` | **port with review** | `ravel/extract/cleanup/` | `reorder_pasal1_definitions` is a genuinely smart domain rule |
| `00_ID_REG_Parser_v2.ipynb` | **drop** | — | PMK-hardcoded, plus the `r`-deletion landmine (§4) |
| `document_parser/{parser,storage}.py` | **drop** | — | session-scoped upload handling for a chat UI, not corpus building |
| `06_ID_Legal/{api,ui,conversation,core/search,core/generation}` | **drop** | — | ~50K LOC of serving; Vera's territory |
| `06_ID_Legal/config.py` (1,059 lines) | **mine, do not port** | corpus config | read it for encoded domain knowledge, re-express as config |
| `Krawl` | **stays separate** | — | collection is not Ravel's job |

---

## 8. Stack corrections this survey forces

1. **docling is the extraction harness, not a fallback.** Every working SmolDocling
   notebook drives it through `docling` / `docling_core` with Tesseract for OCR, not raw
   transformers. `STACK.md` §3 lists docling as a fallback extractor — it should be the
   primary harness, with raw transformers as the escape hatch.
2. **TEI is a fourth embedder backend.** `pre_embed/ingest.py` embeds against a local
   Text Embeddings Inference server (`http://localhost:8080/embed`). It runs in Docker on
   Windows, which makes it a genuinely good local option next to transformers.
3. **Postgres text search config is `indonesian`, not `simple`.** `pre_embed/schema.sql`
   documents why: Indonesian is heavily affixed, and `simple` indexes `dikenakan` and
   `dikenai` as unrelated terms. Vera's `0001_init.sql` still says `simple` — a real
   divergence between Vera's two schemas, and the `indonesian` one is correct.
4. **Vera has two schemas.** `migrations/0001_init.sql` (halfvec 4096, domains/clusters,
   `simple` FTS) and `pipelines/pre_embed/schema.sql` (parameterised dims, `corpus_meta`,
   `ingest_progress`, pgvector `sparsevec` BM25, `indonesian` FTS). The second is the one
   with working code behind it. `BUNDLE.md` §2 says Ravel owns the schema — it must
   reconcile these two rather than pick one silently.
5. **Sparse vectors are pgvector `sparsevec`, not only `tsvector`.** `ENRICHMENT.md` §3
   claims Postgres computes BM25 itself; in the working pipeline BM25 is computed in
   Python and stored as a sparse vector, with `tsvector` alongside. Both exist, and the
   sparse-vector path is the one whose recipe travels with the corpus.

---

## 10. Measured on the real PDFs (2026-09-12)

`tools/structure_eval.py` over 56 documents / ~1,400 pages from `20_Docling/00_Input`:

| | typography | pattern + layout |
|---|---:|---:|
| heading recall | 15.4% | (circular) |
| heading precision | 4.0% | (circular) |
| **clean pasal sequence** | **9%** of docs | **43%** of docs |

Plus two facts that resize the extraction plan:

- **92% of documents have a full text layer**; 8% have none. The GPU/VLM path is for
  one document in twelve, not the corpus.
- **Structural markers are 2.7% bold, 1.0x body size, 58.4% centered.** Indonesian
  regulations do not typeset structure. Detail in `EXTRACTION.md` §4b.

Native extraction runs at ~28 pages/second on one CPU core, so the 92% costs minutes,
not GPU-hours.

---

## 9. The decision this forces: rebuild or salvage?

**Both, in this order.**

**Salvage first, as the baseline.** The SQLite corpus is the incumbent, and
`MIGRATION.md` §4 step 2 requires its score before anything replaces it. It loads today
via `pre_embed/schema.sql`, it has working vectors, and scoring it costs hours, not
GPU-days. Without that number, "better" stays an opinion.

**Then rebuild for production.** Three defects cannot be repaired in place:

1. **`source_url` is absent in 100% of rows** and cannot be invented. Vera's whole premise
   is a human clicking through to the source.
2. **10.66% `Unknown` regulation_type** is a parser defect fixable only by re-parsing,
   with `REGULATION_TYPE_PATTERNS` this time.
3. **Pasal-level chunking was never compared against ayat-level**, and the corpus cannot
   be re-chunked without re-extraction — there is no canonical intermediate. That is the
   Phase A/B boundary's absence, made concrete.

The 20K-dim tf-idf vectors are unusable regardless (vocabulary never saved) and the 1024-dim
embeddings are fine but tied to a corpus whose text will change.

**Practical path:** load the SQLite as bundle `id_legal/incumbent/v0` — provenance-incomplete
and flagged as such — score it, then run the real pipeline from `Azzindani/ID_REG` sources
and compare. The incumbent's flaws are not an embarrassment; they are the measuring stick,
and they are why this project exists.
