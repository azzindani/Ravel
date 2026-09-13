# ABSORPTION.md — Ravel

A survey of what already exists, measured rather than assumed. `MIGRATION.md` says *how*
to port; this says *what is there, what shape it is in, and what it is worth.*

Surveyed 2026-09-12 across `Vera/dev_tools/pre_embed`, `notebooks/docling`,
`notebooks/preprocess`, `notebooks/ner`, `06_ID_Legal`.

**Second pass, 2026-09-13**, over the rest of the workspace — 35 areas, not the 5 above.
It added §§10–14, put the two sections that were numbered out of order back in order,
and added four rows to §3. It also closes `OPEN_QUESTIONS.md` on the encoder. Two findings change plans rather than decorate them: the embedding run that
produced the incumbent's vectors **never applied its instruction** (§11), and the
labelled eval set this project is measured against **does not exist in any form** (§13).
**Fifth pass, same day**, over `Vera/dev_tools/` — which four passes of sweeping
`AI_Workspace` never opened. It **retracts §13**: the labelled eval set exists, is
hand-written, and is better designed than anything proposed here. It also supplied the
check in §19.2 that the freshly written `src/embed/preflight.py` was missing.

**Fourth pass, same day**, over `80_Project_E2E` — the production lineage, which no
earlier pass had opened. It added §18 and corrected two more findings: §15's OCR
attribution (the blamed config postdates the damage by five months) and §7's claim that
the tf-idf vocabulary was lost (it was fragmented, not lost). It confirmed §11 in the file
that actually built the corpus. The rule it produced: **date the artifact before believing
the code** — lab notebooks outnumber production code and look exactly like it.

**Third pass, same day**, over the 547 archived notebooks and the extraction outputs
themselves. It added §§15–17 and **corrected two of its own earlier verdicts** (§17): the
NER notebooks are not NER, and the claim that nothing legal existed anywhere was too
strong. Its main result is §15 — the corpus's character damage is self-inflicted, by an
extractor configured to re-OCR pages it did not need to read, with every error channel
deliberately disabled.

Everything below is counted, not recalled; the queries are reproducible against
`Vera/.test/ID_REG_DB_2511/id_regulations.db`, the parquet files named in §5, and the 998
source PDFs.

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
| `Azzindani/ID_REG` (HF) | the source document zips; `batch-55-chunk-{2..5}.zip`, ~17 GB, are cached locally in `notebooks/docling/downloads/` |
| `Azzindani/ID_REG_KG_2511` (HF) | the enriched corpus matching the local SQLite |
| `notebooks/docling/00_Input/` | 998 source PDFs, 5 GB — a working sample |
| `notebooks/docling/02_cleaned_regulations/` | 79 SmolDocling markdown outputs |

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
| `enacting_body` contains `TENTANG` — the subject clause bled into the body field | 16,341 | 2.18% |
| `enacting_body` longer than 60 chars — header/footer collision | 11,823 | 1.58% |
| 25+ letter run with no space — word separation lost on the page | 5,704 | 0.76% |
| `year` outside 1945–2026 | 569 | 0.08% |

**The first row is the blocker.** Vera's production schema declares `source_url TEXT NOT
NULL`; `pre_embed/schema.sql` had to make it nullable for the spike, with the comment
*"this corpus has no source_url, and inventing one is forbidden."* A citation you cannot
click is not provenance, and **it cannot be back-filled after the fact** — it has to come
from ingestion.

So the existing corpus is structurally unable to load into Vera's target schema. That is
the single strongest argument for rebuilding from sources rather than salvaging.

The last four rows are from the second pass and are all the **same defect wearing four
costumes**: the extractor emitted a flat string, and the parser cut fields out of it with
regexes. Nothing told it where the page header stopped and the title began, so
`PEMERINTAH KABUPATEN ASAHAN SEKRETARIAT DAERAH LEMBARAN DAERAH KABUPATEN ASAHAN` is an
`enacting_body`, and so is `BUPATI SERAM BAGIAN TIMUR PROVINSI MALUKU TENTANG SISTEM
PENYELENGGARAAN DAN PENGELOLAAN PENDIDIKAN`.

Visible damage in a real row: `NOMOR 26 TAHUN 20092008` — two years concatenated, almost
certainly a header/footer collision during extraction. Its space-loss cousins are
`PENGESAHANPERJANJIANANTARAPEMERINTAHREPUBLIKIN` and `PERATURAN GUBERNURSUMATERA SELATAN`.
Exactly the class of failure `CANONICAL_FORMAT.md` keeps typed blocks for: a typed block
with a bbox cannot collide with the header, because the header is a different block.

### How much of `Unknown` is repairable without re-extracting

The question matters, because repairing in place would be far cheaper than a rebuild.
Classifying all 79,764 `Unknown` rows by what their `enacting_body` still holds:

| Recoverable from `enacting_body` | Rows |
|---|---:|
| `BUPATI …` → PERATURAN BUPATI | 5,842 |
| `PEMERINTAH …` → a kabupaten/kota instrument | 3,949 |
| `GUBERNUR …` → PERATURAN GUBERNUR | 2,478 |
| `WALI KOTA …` → PERATURAN WALIKOTA | 2,151 |
| `BADAN …`, `REPUBLIK …`, `PRESIDEN …`, `MENTERI …` | 2,476 |
| **nothing left to key on — both fields are `Unknown`** | **62,868** |

**16,896 rows (21.2%) are repairable; 62,868 (8.40% of the whole corpus) are not.** For
those the type was never captured, and the only place it still exists is the source PDF.
That is the salvage ceiling, measured — and the reason §9 ends where it does.

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

**The second pass put a number on it.** `notebooks/docling/parsed_regulations.parquet` is
that parser's own output, 6,849 rows still on disk:

| `regulation_type` in the parser's output | Rows |
|---|---:|
| `PMK` | 204 |
| *empty string* | 6,645 |

**The regex matched 3.0% of what it was pointed at.** And in the finished corpus
`PERATURAN MENTERI` accounts for **42 rows out of 748,558** — 0.006%. A parser was
hardcoded to the one instrument this corpus almost never contains, and the other 97%
fell through to a default. Nobody was told, because an empty string is not an error.

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

**Confirmed against the artifact, not the notebook.** `parsed_regulations.parquet`,
6,849 rows, 18 columns:

| Column | Populated | Value |
|---|---:|---|
| `full_reference` | **6,849 / 6,849 (100%)** | `Pasal 2 huruf a` — the exact-citation identifier |
| `source_file` | **6,849 / 6,849 (100%)** | `09._PERBUP__NO__09__THN_2022.md` — a provenance root |
| `hierarchy_level` | 6,849 (100%) | ayat 3,271 · huruf 3,100 · pasal 478 |
| `regulation_type` | 204 (3.0%) | all `PMK` |

Two things follow, and they are the whole argument of this project in one table.

**First: provenance existed and was dropped.** `source_file` is populated on every row of
the parser's output, and `source_url` is absent on every row of the corpus built from it.
The chain broke somewhere downstream, silently, and §3's 100% defect is the result. It is
the exact failure `CHUNKING.md` §3b answers structurally — a chunker that cannot construct
a `Chunk` cannot construct one without provenance.

**Second: the granularity trade was never made deliberately.** 478 pasal became 6,849
addressable units — **14.3× finer**. So the ayat-level variant's chunk count is not a
guess: at the incumbent's 748,558 pasal-level chunks it projects to roughly 10.7M, which
is a real budget question for `VARIANTS.md`, not a shrug.

The two parsers each got one half right — v1 the hierarchy (100%) and the identity wrong
(3%), the shipped one the identity mostly right (89%) and the hierarchy flat. Neither
combined them, because there was no seam at which to combine them. Ravel's seam is the
profile: `structure.units` and `identity.types` are separate keys in the same file.

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
| `pre_embed/sparse.py` | **port near-verbatim** | `ravel/enrich/sparse.py` | `Bm25Vectorizer` with the vocabulary saved and hashed. ~~Fixes the 20K-dim tf-idf whose vocab was lost~~ — corrected by the fourth pass (§18): the vocabulary was **not** lost, it was **fragmented**. `embed.py` calls `fit_transform` per 1,000-row chunk, so the corpus has ~1,000 different vocabularies and ~1,000 different IDF weightings, each persisted beside its own shard. One vocabulary fitted over the whole corpus is still the fix; the defect being repaired is worse than the one recorded, because fragmentation looks healthy |
| `pre_embed/batching.py` | **port as-is** | `ravel/runtime/batching.py` | byte-aware batching; already `EXTRACTION.md` §3 rule 2 |
| `pre_embed/ingest.py` | **port the invariants, rewrite the code** | manifest / queue / preflight | `corpus_meta`, `ingest_progress`, round-trip gate, `indexable` and `truncated_at_source` flags are all Ravel concepts already |
| `id-reg-smoldocling-tesseract-v2-rtx3090-16.ipynb` | **port the runtime** | `ravel/runtime/gpu.py` | `AggressiveConverterPool`, VRAM checks, `GPUMonitor`, memory cleanup — hard-won operational knowledge |
| `00_ID_REG_Parser_v1.ipynb` | **port the hierarchy, replace the metadata** | `ravel/chunk/id_regulation` | pasal/ayat/huruf + `full_reference` is right; the PMK-only regex is the bug |
| `core/knowledge_graph/kg_core.py` | **port selectively** | `ravel/enrich/legal/` | entity + cross-ref extraction with positions and confidence; leave the ranking/boosting behind (that is Vera's job) |
| `document_parser/extractors/*` (8) | **reference the shape, rewrite** | `ravel/extract/` | clean ABC, but `extract() → {text, page_count, metadata, method}` is far too thin for the canonical format — no blocks, no bbox, no reading order |
| `notebooks/ner/*.ipynb` (v1–v4) | ~~evaluate, then port one~~ → **drop the task, keep the concurrency** | `ravel/runtime/` | Corrected by the third pass (§17). All four run `pipeline("zero-shot-classification")` with ModernBERT-base over the **BBC News** dataset — English, general-domain, and **not NER at all**. There is nothing here to port into `enrich/ner`. The `ConcurrentGPUClassifier` (thread-local pipelines, double-checked locking) is real and belongs in the runtime; `bert-base-indonesian-NER` (§10) is the actual NER candidate |
| `notebooks/preprocess/*` | **port as cleanups** | `ravel/extract/cleanup/` | spell correction, markdown formatting, dataset filter |
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

## 9. Measured on the real PDFs (2026-09-12)

`tools/structure_eval.py` over 56 documents / ~1,400 pages from `notebooks/docling/00_Input`:

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

## 10. The encoder question, closed

`OPEN_QUESTIONS.md` treated the embedder as undecided and the 8 GB VRAM ceiling as the
constraint that decides it. The second pass found the models already on disk, with
weights, in `10_Encoder_Model_HF`:

| Model | On disk | Dim | Context | Fits 8 GB |
|---|---:|---:|---:|---|
| **Qwen3-Embedding-0.6B** | 1.2 GB | **1024** | 32,768 | yes |
| Qwen3-Reranker-0.6B | 1.2 GB | — | 40,960 | yes |
| **Qwen3-VL-Embedding-2B** | 4.0 GB | **2048** | 262,144 | yes |
| Qwen3-VL-Reranker-2B | 4.0 GB | — | 262,144 | yes |
| ModernBERT-base / large | 0.6 / 1.5 GB | 768 / 1024 | 8,192 | yes |
| **bert-base-indonesian-NER** | 423 MB | 768 | 512 | yes |

Three consequences:

1. **The incumbent's 1024 dims are Qwen3-Embedding-0.6B**, not a truncated 8B. The
   dimension in §1 is explained, and the baseline is reproducible on the local box.
2. **The omni path has a real candidate.** `CLAUDE.md` §7.3 forbids blending modalities in
   one column; Qwen3-VL-Embedding-2B is **2048-dim against the text model's 1024**, so the
   separate-column rule is not a precaution here, it is arithmetic.
3. **`bert-base-indonesian-NER` carries `LAW` and `REG` among its 19 entity types**
   (`CRD DAT EVT FAC GPE LAN LAW LOC MON NOR ORD ORG PER PRC PRD QTY REG TIM WOA`). The
   `notebooks/ner` iterations in §7 now have a baseline to be evaluated *against* rather
   than merely chosen between — and a 512-token window, which makes it a chunk-level
   model, not a document-level one.

---

## 11. The embedding run that never applied its instruction

This is the finding that most changes what `EMBEDDING.md` has to record.

> **Confirmed and re-attributed by the fourth pass (§18.2).** The file examined below
> is a lab notebook. The run that actually built the shipped corpus is
> `Indonesian_Regulation_RAG/Vast/Embed_20251011/embed.py` — identified by its
> `max_features=20000`, which is the incumbent's tf-idf width, and Qwen3-Embedding-0.6B
> at 1024 dims. **The finding holds there too**, so it is now confirmed independently in
> two files rather than inferred from one. Two *supporting* details below are lab-only
> and are not true of production — see §18.2.

`notebooks/embedding/00_Qwen3_Text_Embedding_v2.ipynb` builds the vectors. It defines the
instruction helper Qwen3 documents:

```python
def get_detailed_instruct(task_description: str, query: str) -> str:
    return f'Instruct: {task_description}\nQuery:{query}'
```

**It is never called.** One definition, zero call sites in the notebook; `encode()`
tokenizes raw `texts`. So the corpus side of an instruction-aware model was embedded with
no instruction at all — and if the query side ever used one, the two sides have been
sitting in different regions of the space ever since. Nothing would have reported this:
retrieval still returns its k nearest neighbours, just worse ones.

Two more of the same kind in the same file:

- `max_length = 8196` is assigned, printed as *"Using original model max_length"*, and
  never passed anywhere. `encode()` hardcodes `max_length=32768`. The knob is decorative,
  and 8196 is a typo for 8192 besides.
- Had the helper been called, `Query:{query}` is missing the space after the colon that
  Qwen3's own template specifies — a different string, therefore a different vector.

And the two local Qwen3 encoders disagree on a parameter that is not cosmetic:

| | text 0.6B (notebook) | VL-2B (`qwen3_vl_embedding.py`) |
|---|---|---|
| `padding_side` | `'left'` | `'right'` |
| pooling | last token | last token |
| instruction | prefix string (unused) | **system turn in a chat template** |
| normalize | L2 | L2 |

Last-token pooling reads `hidden[:, -1]` under left padding and
`hidden[arange(B), mask.sum(1)-1]` under right. Pick the wrong one and every text shorter
than the longest in its batch pools a PAD position — a unit-norm vector, perfectly
well-formed, meaning nothing. The VL model also normalizes its instruction by appending
`.` when it lacks trailing punctuation, so "the instruction string" is not even a string
you can copy between the two.

**What this forces:** `padding_side` and `instruction_style` are now in the manifest
(`EMBEDDING.md` §1, §3). The round-trip preflight already existed to catch exactly this
class of failure — this section is the evidence that it catches something real, and that
the failure is otherwise silent.

---

## 12. Profile coverage, measured — and the vocabulary that was not imported

`notebooks/preprocess/ID_REG_Dataset_Filter_v1.ipynb` holds a hand-built vocabulary of
~90 Indonesian regulation types split national/regional, with abbreviations — POJK, SEOJK,
PERKAP, PERJA, PERMENKO, PERDIRJEN, PERPANG and the rest. It is the most complete such
list in the workspace and the obvious thing to import wholesale into
`registry/profiles/id_regulation@1.0.yaml`.

**Checking first was the right call.** Scoring the profile's 22 types against all 31 type
values the incumbent actually contains:

| | Rows |
|---|---:|
| matched by a profile type | 667,530 |
| type value is an abbreviation of a type already listed (`PP`, `UU`, `KEPPRES`, `PERDA`, `PBI`, `PERPU`, `PERBUP`, `UUD`, `SE`) | 658 |
| type genuinely absent from the profile | 606 |

The profile already covered **99.8% of every typed row**. The ~90-type vocabulary is
*aspirational* — real instruments that do not occur in this corpus, which is 35% Perda
Kabupaten and 0.006% Peraturan Menteri. Importing it would have widened a substring match
that `identity.title` trusts completely, in exchange for nothing, against the profile's
own warning that a wrong identifier is worse than an absent one.

What was added instead is the measured tail: `KEPUTUSAN GUBERNUR` (31), `KEPUTUSAN
WALIKOTA` (46), `KEPUTUSAN BUPATI` (30), `KEPUTUSAN KEPALA` (61), and `UNDANG UNDANG`
(432) — the last being extraction damage rather than a type, listed because the
alternation is `re.escape`d literals and those 432 documents otherwise match nothing at
all. It maps to `UU` like its hyphenated twin, so the two spellings converge on one
identifier.

`SURAT EDARAN` (169 rows) was **excluded on purpose**: a circular letter is administrative
guidance, not a binding instrument under UU 12/2011, and it has no business in the list
feeding the exact-citation path. A negative case in `test_profiles.py` already pinned that
judgment, and the test caught the attempt to overturn it.

The 658 abbreviation-form rows are a separate matter — the same instrument recorded under
two type values, splitting `PP` from `PERATURAN PEMERINTAH`. Ravel parses from spelled-out
document titles, so it should not reproduce the split; folding abbreviations back to a
canonical type on *read* is a loader concern, and `IdentitySpec` has no key for it today.
Noted rather than built.

---

## 13. ~~There is no eval set~~ — wrong; it is in Vera, hand-labelled

> **RETRACTED by the fifth pass. This section was wrong, twice over, and the error was
> mine rather than the evidence's.**
>
> `Vera/dev_tools/eval/queries.json` holds **50 labelled cases across 12 question
> shapes** — Indonesian, hand-written, with `source` naming the clause that was read for
> each. Its own README states the method: *"Every label was written by opening the clause
> in the corpus and phrasing the question the way a person would ask it, deliberately
> avoiding the clause's distinctive wording so nothing leaks."* That is the precise
> opposite of the template generator §18.4 warns about, and it is better eval design than
> anything this document proposed.
>
> The shapes include `hard_negative` (with `must_not_rank_first`), `out_of_domain` (where
> the correct answer is EMPTY), `multi_tier` (a parent law *and* its implementing rule),
> `exact_ref` (scored mechanically against `answer_regulation`), and `underspecified`
> (kept deliberately, because users ask those). `dev_tools/eval/audit.py` exists to find
> bad labels by checking where each labelled target actually ranks.
>
> **Why I got it wrong:** the search was exhaustive over `AI_Workspace` and never ran
> over `Vera/dev_tools/`, while the claim made was about the whole workspace. Passes 3
> and 4 each narrowed this section; none of them widened where it looked. An absence
> claim is only as strong as its search, and this one asserted more than it had covered.
>
> What survives below: the QAI notebooks really are English-medical toys, and §18.4's
> template generator really is circular. Those are still worth knowing. **"There is no
> eval set" is not.** The set's own caveat is the one that matters now — it says
> **STILL NEEDS DOMAIN REVIEW**, because whether a clause genuinely answers a question is
> a lawyer's judgement, and a wrong label silently moves every dial `EVAL.md` gates.



`EVAL.md` §6 pointed at the QAI generation notebooks as the seed for the labelled set.
They are not one, and anyone starting there loses a day:

| | `02_..._Generate_QAI_GPU_v1/v2/v3` | `20_Lab_Synthetic_Dataset` |
|---|---|---|
| corpus | `medical-o1-reasoning-SFT` (**English, medical**) | arxiv.org abstracts |
| rows | 4 · 20 · 20 | — |
| generator | `flan-t5-large` | Llama-3.2-3B |
| prompt | `"Generate a question related to: {context}"` | — |
| output on disk | — | `data/` is **empty** |

No Indonesian content, no reviewed labels, and no surviving output.

**Corrected by the third pass:** the archives hold a third generator,
`00_Archived_Colab/04_QAI_Generation_From_Corpus_GPU_v1.ipynb`, and it *is* pointed at a
legal corpus — `joelniklaus/legal_case_document_summarization`, which is **English-language
case-law summarisation, with `.select(range(2))`**. So the earlier claim that nothing legal
exists anywhere was too strong; what exists is two rows of the wrong jurisdiction, in the
wrong language, for the wrong instrument type. The conclusion is unchanged and the reason
is worth stating precisely: an eval set for Indonesian *regulations* cannot be bootstrapped
from case-law summaries, because the query shapes have nothing in common.

~~This matters more than its size suggests... it is the critical path, it is unstarted~~ —
see the retraction above. The eval set is **started and usable**, and the real remaining
work is domain review of 50 existing labels rather than authorship of a set from zero.
That is a materially smaller and different job, and one a lawyer can do without touching
the pipeline.

---

## 14. Where the second pass deliberately found nothing

Recorded so the sweep is not repeated:

| Area | Verdict |
|---|---|
| `00_Archived_Vast_AI` (20 notebooks) | GRPO/SFT fine-tuning benchmarks across 8 GPU classes (RTX 3090/4090/5090/6000 Ada, L40S, H100 SXM, H200). Training, not corpus building — out of scope per `MIGRATION.md`. The per-GPU throughput tables are the one thing worth keeping, as rental evidence for `EXECUTION.md`. |
| `30_Lab_LLM_*` (5 areas) | fine-tuning. Out of scope, unchanged. |
| `40_Inference_LLM`, `40_LLM_Tool_Calling`, `70_User_Interface`, `90_AI_Web_UI` | serving and UI. Vera's and the agents' territory. |
| `90_AI_Implementation` (66 notebooks) | a document-AI model zoo — LayoutLMv3, TrOCR, Nanonets OCR, Florence-2, Qwen2.5-VL, Qwen2.5-Omni. **Not absorbed, but not dismissed:** these are candidate backends for the OCR and VLM fallbacks in `EXTRACTION.md`, which §9 shows are needed for only 8% of documents. Revisit when that 8% is the bottleneck, not before. |
| `20_Lab_Data_Collection` | scraping. `Krawl`'s job, per `CLAUDE.md` §7.9. |
| `STANDARDS.md` (repo root, shared) | the house MCP standard — 8 tools per server at the 8 GB target, and LOCATE→INSPECT→PATCH→VERIFY. `INTERFACES.md` §2 already complies at 7 tools and already cites the pattern. The limit is now pinned there explicitly rather than met by luck. |

---

## 15. The corpus was damaged by its own extractor, with the alarms off

> **Attribution corrected by the fourth pass (§18). Read this section with §18.3.**
> The damage measured here is real and the mechanism is real, but the configuration
> blamed below is dated **2025-12-27** and the damaged outputs are dated **2025-07-21**.
> `force_full_page_ocr=True` cannot have caused damage five months before it was
> written. The production lineage is `Indonesian_Regulation_Extract/Docling_v*/smol.py`,
> which never sets it. What that lineage does instead is in §18.3, and it is a subtler
> fault with the same signature.

The third pass went looking for where §3's character damage came from. It is not a
mystery and it is not the documents' fault. Two lines in
`notebooks/docling/id-reg-smoldocling-tesseract-v2-rtx3090-16.ipynb`:

```python
pipeline_options.ocr_options = TesseractCliOcrOptions(
    force_full_page_ocr=True,
    lang=["ind"]
)
```

`lang=["ind"]` is right, and worth saying so — the Indonesian traineddata is installed
and selected, which is the single most common way an Indonesian OCR run goes wrong.

**`force_full_page_ocr=True` is the defect.** It rasterises and re-OCRs *every page of
every document*, including the 92% that §9 measured as already carrying a full text
layer. The native text was not consulted. What shipped is a photograph of a page that
could have been read directly.

Verified end to end on eight documents whose extracted markdown contains damage, by
opening the source PDFs with PyMuPDF:

| Source document | Pages | Native chars | Per page | Had a text layer? |
|---|---:|---:|---:|---|
| `2023perbup5103039` | 34 | 103,455 | 3,043 | yes |
| `2020_PERBUP_KAB._BOYOLALI_72` | 74 | 224,553 | 3,034 | yes |
| `2022pb3203062__` | 219 | 346,872 | 1,584 | yes |
| `2023pmkeuangan106` | 6 | 11,246 | 1,874 | yes |
| `04_Tahun_2005` | 4 | 7,401 | 1,850 | yes |
| `09._PERBUP__NO__09__THN_2022` | 19 | 28,739 | 1,513 | yes |
| `22._PERBUP_NO_22__TAHUN_2022` | 13 | 18,220 | 1,402 | yes |
| `20._PERBUP__NO_20__THN_2022` | 8 | 9,347 | 1,168 | yes |

**Eight for eight.** Side by side on one of them:

```
native :  PERATURAN BUPATI NOMOR 54 TAHUN 2022 TENTANG PENJABARAN ANGGARAN
          PENDAPATAN DAN BELANJA DAERAH TAHUN ANGGARAN 2023
shipped:  PenjabaranAnggaranPendapatandanBelanjaDaerahTahunAnggatan2O23
```

Every space gone, `r`→`t` in *Anggaran*, and a letter `O` for the digit `0` in the year.
The native layer was clean. This is the mechanism behind §3's space-loss rows, its
`UndangUndang` hyphen losses, its `20092008` year collisions and its U+FFFD.

**And the alarms were off.** Immediately above, under a heading that says so:

```python
# DISABLE ALL DOCLING/TESSERACT LOGGING (INCLUDING ERRORS)
...
# Nuclear option - disable ALL loggers related to docling/tesseract/OCR
for logger_name in logging.root.manager.loggerDict:
    if any(x in logger_name.lower() for x in ['docling','tesseract','ocr','pdf','pil']):
        logger.disabled = True
        logger.propagate = False
logging.getLogger().setLevel(logging.CRITICAL)
warnings.filterwarnings('ignore')
```

Root at CRITICAL, `propagate = False`, warnings suppressed. A run that needlessly OCR'd
92% of its input, with every channel that could have said so switched off, for 748,558
rows. Nobody ignored a warning; there was no warning to ignore.

This is the strongest single argument in this document for the things Ravel already
insists on, and it converts three of them from taste into evidence:

- **`failure_tolerance: 0.15`** in `corpora/id_legal.yaml` is a *measured budget* — a run
  that exceeds it stops. The opposite of a disabled logger.
- **`extraction.extractor` recorded per document** (`EXTRACTION.md`) means "how much of
  this corpus came from OCR" is a query, not an archaeology project. Here it took opening
  the notebook to find out.
- **Extract-once-chunk-many** is what makes the fix affordable: this is one re-extraction,
  not a rebuild of everything downstream of it.

---

## 16. "Has a text layer" is not "has a good text layer"

§9 measured that 92% of documents have a full text layer and concluded the GPU path is
for one document in twelve. That number is correct and the conclusion drawn from it was
too generous, because coverage was measured and quality was not.

Re-measured over a random sample of **300 of the 998 source PDFs**, scoring the extracted
native text rather than merely counting it:

| Class | Share | What it means |
|---|---:|---|
| text layer, **clean** | **80.3%** | native extraction is correct and costs milliseconds |
| text layer, **damaged** | **13.3%** | present, extracts without error, and is wrong |
| **no** usable text layer | 6.3% | genuinely needs OCR or a VLM |

Damage signatures, overlapping: runs of 25+ letters with no space **8.7%**, control
characters **5.3%**, U+FFFD 0%.

The middle row is the dangerous one, and it is twice the size of the bottom row. Those
documents carry a text layer that is *fully present* — often because the PDF was itself
produced by somebody else's bad OCR years ago — so a coverage gate accepts them. Two of
the eight documents in §15 are in this class: `22._PERBUP_NO_22__TAHUN_2022` reads
`NOMOR \x01J.. TAHUN 2022` natively, control character and all, and `04_Tahun_2005` reads
`RAN DAERAH NOMOR RETRIBUSI PEMER,IKSAAN ALAT PERUBAHAN. PERTAMA ATAS 01 TAHUN 1999
TEI{TANG` — the words in the wrong order as well as the wrong shape. For those two, OCR
was not the wrong call; for the other six it was pure loss.

**What this changes.** `corpora/id_legal.yaml` sets `native_text_ratio: 0.9`, and that key
counts pages with extractable text. It cannot see this. The real routing question is
three-way, not two-way, and the cheap path is smaller than 92%:

```
clean text layer  (80.3%) → native, milliseconds
damaged text layer(13.3%) → OCR/VLM, because the bytes are there and wrong
no text layer     ( 6.3%) → OCR/VLM
```

So the expensive path covers **~20% of the corpus, not 8%** — still a minority, still not
the main path, but two and a half times the budget §9 implied. `EXTRACTION.md` now
specifies the probe as a quality score, with these three signatures as its basis.

**This is now code, and it reproduces the table exactly.** `src/extract/quality.py`
scores a text layer into those three classes, and `Probe` carries the verdict plus the
reasons that produced it; re-run over the same 300 PDFs with the same seed it returns
241 / 40 / 19 — 80.3% / 13.3% / 6.3%, to the digit. The measurement above is therefore no
longer a number in a document that someone has to trust, it is a default in a module that
can be re-run, and `Probe.native_ok` is the gate extractors should use instead of
`text_ratio >= x`, which is the gate that let the incumbent corpus in.

One threshold is worth stating because it decides three of those documents:
`max_unbroken_run = 24`, so a 25-letter run with no space is damage. Indonesian's longest
words in real legal use sit below it — `mempertanggungjawabkan` is 22 — and
`PENGESAHANPERJANJIANANTARAPEMERINTAHREPUBLIKIN` is 45.

A fourth class surfaced while measuring and is not in the table: PyMuPDF emitted
`non-page object in page tree` and `cannot find XObject resource` on several files. Structurally
malformed PDFs are rare enough not to plan around and common enough that the probe must
not crash on them.

---

## 17. Third pass: corrections to this document, and where it found nothing

Two verdicts from earlier passes were wrong and are struck above rather than quietly
edited:

1. **§7 on the NER notebooks.** They are not NER. All four iterations run
   `pipeline("zero-shot-classification")` with ModernBERT-base over `bbc_news_alltime` —
   English, general-domain topic labelling, with `category_hierarchy.csv` and a set of
   `bbc_news_*.csv` outputs beside them. "Evaluate, then port one" pointed at work that
   does not do the job it was credited with. What survives is the concurrency harness.
2. **§13 on the eval set.** The claim that no legal question generator exists anywhere
   was too strong; one does, over English case law, two rows deep. Corrected in place.

Newly surveyed, nothing to absorb:

| Area | Verdict |
|---|---|
| `00_Archived_Colab` · `_Local` · `_Kaggle` (547 notebooks) | Overwhelmingly fine-tuning (GRPO/PEFT/Unsloth legal LoRAs, 13 versions of one experiment) and one-off dashboards. Out of scope, and the ID_REG-relevant ones are superseded by the `notebooks/docling` versions already surveyed. |
| `00_Archived_Colab/{PDF_Extractor,PDF_OCR_Extractor,LLM_PDF_OCR_Extractor_v1,v2,Table_Extractor}` | Early extraction prior art, all PyPDF2/pytesseract/pdf2image — superseded by the docling harness. **One thing worth keeping:** they install `tesseract-ocr-ind` and `ocrmypdf`. The first is the Indonesian traineddata (§15 confirms the docling runs use it too); the second adds a text layer to a scan in-place on CPU, which is a cheaper option for §16's bottom 6.3% than a VLM, and worth measuring before reaching for the GPU. |
| `00_Archived_Colab/Indonesian_Regulation_Data_Preprocess_v1` | PyPDF2 + zipfile + thread pool over the regulation zips. The shape Ravel already has in `runtime/`, with a weaker PDF library. |
| `21_Lab_NER` (4 notebooks + 6 CSVs) | See correction 1 above. |
| `20_Lab_Synthetic_Dataset`, `10_Dataset_Kaggle`, `50_*`, `60_Benchmark_LLM` | Fine-tuning, quantisation, model merging, LLM benchmarks. Out of scope, unchanged. |

---

## 18. Fourth pass: the production lineage, which no earlier pass had opened

`80_Project_E2E` was never surveyed. It holds the four subprojects the incumbent corpus
was actually built by — `Indonesian_Regulation_{Extract,RAG,UI}` and
`Indonesian_Legal_QA` — so three earlier findings were resting on lab notebooks that
resemble the production code without being it. Two of them needed correcting.

### 18.1 What actually ran, and when

Dates settle most of this, so they come first.

| Artifact | Dated | OCR config | Instruction |
|---|---|---|---|
| `Docling_v20250727/Smoldocling_VAST.AI_v1.ipynb` | 2025-07-20 | `do_ocr=True` only | — |
| **damaged outputs in `02_cleaned_regulations`** | **2025-07-21** | — | — |
| `Docling_v20250727/smol.py`, `smol_fix.py` | 2025-07-22/27 | `do_ocr=True` only | — |
| `Vast/Embed_20250816/embed.py` | 2025-08-16 | — | defined, never called |
| **`Docling_v20251018/smol.py`** | 2025-10-18 | `do_ocr=True` only | — |
| **`Vast/Embed_20251011/embed.py`** | **2025-10-11** | — | **defined, never called** |
| shipped corpus `ID_REG_DB_2511` | 2025-11 | — | — |
| `notebooks/docling/id-reg-smoldocling-tesseract-*` | **2025-12-26/27** | **`force_full_page_ocr=True`**, `lang=["ind"]`, logging disabled | — |

`Embed_20251011/embed.py` is identifiable as the run behind the shipped corpus without
guessing: it sets `max_features=20000`, which is the incumbent's tf-idf width to the
digit, and embeds with Qwen3-Embedding-0.6B at 1024 dims, which is its vector width.

### 18.2 §11 holds — in the file that actually built the corpus

`Embed_20251011/embed.py`, 1,719 lines, defines `get_detailed_instruct` at line 321 and
**never calls it**. It also has the seam where the call belongs:

```python
def prepare_texts(self, contents):
    return contents
```

A named hook for exactly this transformation, implemented as a pass-through. Documents go
`contents = df_clean['Content'].tolist()` straight into `embed_dataset`. So the corpus
side of an instruction-aware model was embedded with no instruction, in production, and
§11's conclusion is now confirmed in two independent files.

Two supporting details in §11 are **lab-only and do not apply to production**: the dead
`max_length = 8196` (production plumbs `8192` correctly through `self.max_length`) and the
missing space in `Query:{query}` (production has `Query: {query}`). The headline is right;
those two sentences describe a notebook that did not ship.

And the run recorded metadata — `embedding_model`, `embedding_dimension`,
`chunk_size_config` — with **no pooling, no normalize, no instruction, no padding_side, no
model revision**. That is a stronger argument for `EMBEDDING.md` §3 than the original: a
manifest was written, and it omitted precisely the fields that decide whether the vectors
can be reproduced.

### 18.3 §15 is misattributed — the real fault is quieter

`force_full_page_ocr=True` and the disabled-logging block are real, and they are dated
**2025-12-27**. The damaged markdown that motivated §15 is dated **2025-07-21**, and the
corpus shipped in November. A December configuration did not damage a July output.

All three production extractors — `Docling_v20250727/smol.py`, its `smol_fix.py`, and
`Docling_v20251018/smol.py` — configure OCR with exactly one line:

```python
pipeline_options.do_ocr = True
```

No `ocr_options`, and therefore **no OCR language**. Docling falls back to its default
engine and its default language, and Indonesian is never requested. The later December
notebooks set `lang=["ind"]` explicitly — they read as an attempt to fix this, which then
overcorrected into forcing full-page OCR.

So the mechanism in §15 is wrong in an instructive way. The damage is not a loud,
deliberate misconfiguration; it is an **unset default**. Nobody chose to OCR Indonesian
with the wrong language — nobody chose anything, and the default chose badly. That is a
worse failure mode for Ravel to design against, and it is what
`CLAUDE.md` §6's manifest rule answers: a setting that is never recorded is a setting
nobody can review. The corrected lesson is not "do not force full-page OCR" but **"an
extractor must record the OCR engine and language it used, because the damage from
getting them wrong is indistinguishable from a bad scan."**

Also worth keeping: "smol" in these filenames is a leftover. Neither `smol.py` nor
`Smoldocling_VAST.AI_v1.ipynb` references `VlmPipeline` or any SmolDocling option; both
run docling's **standard** PDF pipeline. The corpus was never VLM-extracted, which removes
a suspect §15 left open.

### 18.4 §13: the eval trap, and where the real seed is

`Indonesian_Legal_QA/` and `Kaggle_Process/id-reg-qa-generation-v*` are the Indonesian
legal question sets earlier passes said did not exist. Both change §13 — neither rescues
it.

**The generator is circular and must not be used.** `id-reg-qa-generation-v3-p1.ipynb`
builds QA at real scale (64 parquet files, 10 pairs per row) from **10 Indonesian question
templates and 10 answer templates**:

```
"Apa bunyi lengkap {article} dalam {reg_name} Nomor {reg_number} Tahun {year}?"
  -> answer: "{reg_name} Nomor {reg_number} Tahun {year} ... {article} menyebutkan
              bahwa:\n\n{content}"
```

Every question is filled from the same metadata fields the index is keyed on, and the
answer is the chunk verbatim. A retriever doing exact-citation matching scores **100% by
construction** and has been told nothing about whether it can find anything. This is the
circularity `tools/structure_eval.py` already labels in its own oracle arm, and
`CLAUDE.md` §15 in one sentence: a validator that replays the construction certifies the
construction. It would look like a 750,000-pair eval set and measure a string format.

Credit where due: it skips rows containing `cukup jelas`, which is the same boilerplate
rule the profile carries.

**The real seed is `Indonesian_Legal_QA/`, and it is a `Krawl` job.** Six scrapers for
**hukumonline.com/klinik**, extracting `## PERTANYAAN` and `## INTISARI JAWABAN`. These
are questions real people asked, answered by lawyers who cite the regulation — which is
exactly the non-circular thing the templates cannot be, and the exact-citation arm
`EVAL.md` §4 needs. **No data survived** — 888 KB of notebooks, no output — and collection
belongs to `Krawl` per `CLAUDE.md` §7.9.

So §13's conclusion stands: there is no eval set. What is now known is the shape of the
trap and the address of the source.

### 18.5 Smaller findings

- **The `vProd` run was abandoned.** `processing_progress.json` reports
  `total_records: 994602` with `completed_chunks: [1, 2]` — 2,000 records of a million,
  last touched 2025-08-16. `ID_REG_Embedding_vProd.ipynb` is a false lead for anyone
  reconstructing the corpus; `Embed_20251011` is not.
- **994,602, not 748,558.** The extraction produced ~995K records and the corpus holds
  748,558. A quarter of the rows did not survive chunk-to-corpus, and nothing on disk says
  which or why.
- **The incumbent is no longer on disk.** `Vera/.test/ID_REG_DB_2511/` and the local
  `Qwen3-Embedding-0.6B` copy are gone; only `runs/` remains. Every measurement in §§1, 3
  and 12 was taken while it was present and is recorded here, but **it cannot currently be
  re-queried**, and `MIGRATION.md` §4 step 2 wants it scored. §2 still holds — the sources
  are on Hugging Face — so this costs a rebuild, not the corpus.
- `10_Dataset_HF` — three notebooks. `00_Jsonl_to_Parquet_v1.ipynb` is nine lines of
  `pd.read_json(lines=True).to_parquet()`: the whole corpus into RAM, no sharding, no row
  groups. Nothing to absorb, and it is the pattern `EXECUTION.md` §4 forbids.
- `40_Lab_LLM_RAG` — eleven notebooks building a `networkx` knowledge graph over the
  regulations. Same KG work already surveyed in §6; the graph metrics there
  (`kg_pagerank`, `kg_degree_centrality`) are the ones that were columned and never
  computed.
- `10_Model_HF` (147 GB) and `00_Github_Remote` (empty) — a model store and nothing.
  Inventory only.

### 18.6 What this pass says about the survey itself

Four passes, and each corrected the one before: pass 3 overturned two of pass 2's
verdicts, pass 4 overturned two of pass 3's. The common cause is not carelessness, it is
that **lab notebooks outnumber production code and look exactly like it**. `20_Docling`
holds eleven plausible ID_REG notebooks; the one that built the corpus is in a different
tree entirely, named `embed.py`, and is identifiable only by matching a hyperparameter to
a column width.

The rule this produces is worth more than any single finding above: **date the artifact
before believing the code.** A configuration cannot explain an output older than itself,
and nothing in a notebook says whether it ever ran.

---

## 19. Fifth pass: Vera's side, and a gate that could only pass

Four passes swept `AI_Workspace` and never opened `Vera/dev_tools/`, which is where the
Ravel-adjacent work actually continued. Two things there are worth more than anything in
the notebooks, and one of them retracts §13 (see the retraction in place).

### 19.1 What §11 cost, measured

`dev_tools/pre_embed/reembed.py` opens by stating the consequence this document could
only describe in the abstract:

> *The corpus was embedded through a backend whose output does not match the model.
> Measured on 39 eval queries over a 5,102-chunk pool: the stored space ranks the right
> answer at **median 857** and scores **2.6% Recall@5**, against **median 1** for the
> reference implementation. The dense arm has therefore carried **weight 0.0** since it
> was measured.*

§11 said an unapplied instruction produces "no error — only quietly worse results". This
is how much worse: **median rank 857 against a reference median of 1**, and a retrieval
arm switched off entirely. It is the strongest single justification in this document for
the manifest rule, and it was measured independently, on the serving side, by someone who
did not have §11 in front of them.

`reembed.py` also shows the right shape for the repair, and Ravel should copy it rather
than reinvent it: it writes `dense_v2` **alongside** `dense`, validates, and swaps only
after review, so a crash at 80% leaves a corpus that still serves. Its work queue is
`WHERE dense_v2 IS NULL`, which makes the run resumable and idempotent for free. That is
`EXECUTION.md` §3's "idempotent and keyed by content hash" expressed in SQL, and it is
also the answer to §18.5's missing quarter-million rows: a re-run that continues rather
than restarts never has to explain what happened to the gap.

### 19.2 The gate that could only pass — now a check in `src/embed/`

`dev_tools/pre_embed/dense_probe.py` names the failure directly:

> *This is the check whose ABSENCE cost the dense arm. The ingest pipeline's round-trip
> gate compared the serving backend **against itself**, scored 0.999992, and certified a
> vector space that ranks the right answer at median 32. **A gate that can only pass is
> worse than no gate: it buys confidence.***

This landed the same day `src/embed/preflight.py` was written, and the new code had the
same hole: `preflight(embedder, embedder, samples)` scored cosine 1.000000 and reported
three green checks. A cosine floor is necessary and not sufficient — identical inputs
through identical code agree perfectly however wrong that code is.

`check_independent_backends` now refuses a round trip whose two sides are the same object
or the same class, and `preflight(..., allow_same_backend=True)` is an explicit escape
hatch whose report says the run *does not certify the vector space*. `ReferenceHashEmbedder`
exists so the honest case has something to compare against, mirroring what the real check
compares: the model's reference implementation against whatever backend serves it.

The general lesson, which is `CLAUDE.md` §15 in a sharper form: a validator must not only
avoid replaying the construction, it must be **capable of failing**. Both of this
project's worst measured defects — the unapplied instruction and the self-comparing gate
— passed every check that existed at the time.

### 19.3 Smaller findings

- **`Vera/dev_tools/pre_embed/` no longer exists**; it is `Vera/dev_tools/pre_embed/`.
  Every reference in these documents has been repointed. The directory has also grown
  files no earlier pass saw: `dense_probe.py`, `reembed.py`, `chunking.py` and
  `test_chunking.py`.
- **`eval_arms.py` is honest about its own bias**, and says so in its docstring: its
  auto-built query set favours the lexical arms because a regulation's `about` field
  shares wording with its body. It is kept because the bias is identical across arms, so
  it remains a valid A/B for the one question it asks — last-token versus mean pooling.
  That is the right way to use a circular set, and the contrast with §18.4's template
  generator is instructive: the problem is never that a set is synthetic, it is that a
  synthetic set gets used for a question its construction already answers.

---

## 20. The decision this forces: rebuild or salvage?

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
