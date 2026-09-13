# ABSORPTION.md — Ravel

A survey of what already exists, measured rather than assumed. `MIGRATION.md` says *how*
to port; this says *what is there, what shape it is in, and what it is worth.*

Surveyed 2026-09-12 across `Vera/pipelines/pre_embed`, `notebooks/docling`,
`notebooks/preprocess`, `notebooks/ner`, `06_ID_Legal`.

**Second pass, 2026-09-13**, over the rest of the workspace — 35 areas, not the 5 above.
It added §§10–14, put the two sections that were numbered out of order back in order,
and added four rows to §3. It also closes `OPEN_QUESTIONS.md` on the encoder. Two findings change plans rather than decorate them: the embedding run that
produced the incumbent's vectors **never applied its instruction** (§11), and the
labelled eval set this project is measured against **does not exist in any form** (§13).
Everything below is counted, not recalled; the queries are reproducible against
`Vera/.test/ID_REG_DB_2511/id_regulations.db` and the parquet files named in §5.

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
| `pre_embed/sparse.py` | **port near-verbatim** | `ravel/enrich/sparse.py` | `Bm25Vectorizer` with the vocabulary saved and hashed — directly fixes the 20K-dim tf-idf whose vocab was lost |
| `pre_embed/batching.py` | **port as-is** | `ravel/runtime/batching.py` | byte-aware batching; already `EXTRACTION.md` §3 rule 2 |
| `pre_embed/ingest.py` | **port the invariants, rewrite the code** | manifest / queue / preflight | `corpus_meta`, `ingest_progress`, round-trip gate, `indexable` and `truncated_at_source` flags are all Ravel concepts already |
| `id-reg-smoldocling-tesseract-v2-rtx3090-16.ipynb` | **port the runtime** | `ravel/runtime/gpu.py` | `AggressiveConverterPool`, VRAM checks, `GPUMonitor`, memory cleanup — hard-won operational knowledge |
| `00_ID_REG_Parser_v1.ipynb` | **port the hierarchy, replace the metadata** | `ravel/chunk/id_regulation` | pasal/ayat/huruf + `full_reference` is right; the PMK-only regex is the bug |
| `core/knowledge_graph/kg_core.py` | **port selectively** | `ravel/enrich/legal/` | entity + cross-ref extraction with positions and confidence; leave the ranking/boosting behind (that is Vera's job) |
| `document_parser/extractors/*` (8) | **reference the shape, rewrite** | `ravel/extract/` | clean ABC, but `extract() → {text, page_count, metadata, method}` is far too thin for the canonical format — no blocks, no bbox, no reading order |
| `notebooks/ner/*.ipynb` (v1–v4) | **evaluate, then port one** | `ravel/enrich/ner/` | four iterations; v4 is the candidate, but check what v1–v3 handled that it dropped |
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

## 13. There is no eval set — not a partial one, none

`EVAL.md` §6 pointed at the QAI generation notebooks as the seed for the labelled set.
They are not one, and anyone starting there loses a day:

| | `02_..._Generate_QAI_GPU_v1/v2/v3` | `20_Lab_Synthetic_Dataset` |
|---|---|---|
| corpus | `medical-o1-reasoning-SFT` (**English, medical**) | arxiv.org abstracts |
| rows | 4 · 20 · 20 | — |
| generator | `flan-t5-large` | Llama-3.2-3B |
| prompt | `"Generate a question related to: {context}"` | — |
| output on disk | — | `data/` is **empty** |

No Indonesian content, no legal content, no reviewed labels, and no surviving output.
Searching the rest of the workspace for a legal question set found nothing either.

This matters more than its size suggests. `EVAL.md` is the mechanism by which a variant is
promoted, and `MIGRATION.md` §4 step 2 requires the incumbent's score *before* anything
replaces it. Both rest on a set that has to be built from zero, by hand, in a language and
domain where synthetic labels are least trustworthy. **It is the critical path**, it is
unstarted, and it is the one item on `CLAUDE.md`'s tracker that no amount of GPU time
shortens. `EVAL.md` §6 now says so.

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

## 15. The decision this forces: rebuild or salvage?

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
