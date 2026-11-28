# EXTRACTION.md — Ravel

Phase A: source files → canonical documents. The expensive, run-once stage.

---

## 1. Goal

**Hyperspeed extraction at scale, with progress and checkpoints, resumable anywhere.**
Not "a script that parses PDFs" — a throughput engine whose unit of work is one document
and whose failure mode is "resume from where it stopped", always.

Extraction is where the GPU-days go, so every design choice here is about keeping the
accelerator fed and never repeating work.

---

## 2. The router

Documents are heterogeneous; one extractor for everything is either slow or wrong. A
cheap router picks per document (and, for PDFs, per page):

```
                        source file
                            │
                 ┌──────────┴──────────┐
                 │  probe: mime, text  │   free: does the PDF have a real text layer?
                 │  layer ratio, dpi   │   what fraction of pages have extractable text?
                 └──────────┬──────────┘
          ┌─────────────────┼─────────────────┐
    native text        structure needed     scanned / image
    (fast, CPU)        (SmolDocling, GPU)   (OCR, GPU)
          └─────────────────┼─────────────────┘
                            ▼
                   canonical document
```

- **Native** — PyMuPDF/docx/html text extraction. Milliseconds, no GPU. Correct for
  born-digital documents where the text layer is trustworthy. **Measured at 92% of the
  ID regulation corpus** (`ABSORPTION.md` §9), so this is the main path, not a
  fallback.

  > **The probe must test quality, not only coverage.** "Has a text layer" and "has a
  > text layer worth using" are different questions, and only the first one is free.
  > Measured over 300 source PDFs (`ABSORPTION.md` §16): 80.3% clean, **13.3% carry a
  > text layer that is fully present and damaged**, 6.3% have none. A coverage-only gate
  > — which is what `native_text_ratio` is — waves that 13.3% straight through, and they
  > are the documents that fail silently, because a damaged text layer extracts without
  > error. The probe therefore scores the extracted text as well as counting it: control
  > characters, U+FFFD, and runs of 25+ letters with no space were the three signatures
  > that separated the classes, and the third was the most common (8.7%).
  >
  > Implemented in `src/extract/quality.py`; `probe()` returns the verdict on
  > `Probe.quality` and **`Probe.native_ok` is the gate to route on**. The thresholds are
  > a `Thresholds` dataclass rather than constants, and their defaults reproduce the
  > measurement above exactly. `DAMAGED` is not a degraded yes — it routes to OCR like
  > `ABSENT`, because bytes that are present and wrong are worse than bytes that are
  > missing and known to be.
- **SmolDocling** — the default for anything where structure matters. It detects
  headings, subheadings, lists and tables and emits markdown, which is exactly the
  canonical format's shape. This is the reason the pipeline can chunk on legal units at
  all.
- **OCR** — scanned pages, stamps, signatures. Plugged in behind the same interface
  (`06_Nanonets_OCRs`, `06_QwenVL` are the candidates already on disk).

**The router's decision is recorded in `extraction.extractor`**, so a corpus can be
audited for how much of it came from which path, and a bad path can be re-run for only
the affected subset.

> Hybrid is allowed and expected: native text for the body, SmolDocling for pages the
> probe flags as structure-heavy, OCR for pages with no text layer. Mixing is
> per-page; the canonical document records which pages came from which extractor.

---

## 2b. The docling extractor: built

`src/extract/docling_pdf.py`, named `docling` in a corpus's `extract.extractors`, and
configured by `extract.docling` (`DoclingSpec`) in the corpus YAML, never in code. It
reads a PDF with one of two docling pipelines, chosen per document by the probe's
text-layer verdict:

| text layer (`Probe.layer`) | pipeline (default) | what reads the page |
|---|---|---|
| CLEAN | `standard` | the born-digital text, placed by docling's layout model (Heron); tables by TableFormer; OCR off |
| DAMAGED, ABSENT | `vlm` | a vision-language model on the rendered page: `granite_docling` (SmolDocling's successor) |

- **Why a damaged layer goes to the VLM.** It is somebody else's old OCR, baked in
  (13.3% of PDFs, §2). The standard pipeline keeps those bytes, so it keeps their typos;
  the VLM reads the image and never sees them.
- **What it emits.** Docling's items, in its reading order: titles and section headers
  as headings at docling's level, body text, list items, captions, footnotes, formulas,
  page headers and footers as furniture, tables as `Table` cells with spans plus a
  `table_ref` block, and figures as `Asset` recipes (page + bbox, no bytes). Bboxes are
  top-left origin, as the native extractor records them.
- **Structure stays with the profile (§4b).** Docling's headings are a guess about
  layout. With `id_regulation` the structurer demotes those that are not `BAB`, `Pasal`
  and the rest, exactly as it does with native's typographic guesses.
- **The hash.** Every `DoclingSpec` field is in `CorpusSpec.extraction_hash`, but only for
  a corpus that names `docling`, so no existing generation moved.
- **Where it runs.** `device: auto` takes CUDA when torch has it. `vlm_engine: api` with
  `vlm_url` sends pages to an OpenAI-compatible server, such as a vLLM container on a
  rented GPU: the same arrangement as the embedder. Docling is imported on first use, so
  the hermetic suite and every other corpus never load torch.
- **Hardest first.** `ravel extract <corpus> --limit N --largest-first` takes the N
  largest files, which have the most pages and the most scans, so a trial meets the worst
  case before the average one.

Measured on an RTX 3070 Ti Laptop (8 GB), `sources/ID_PDF`, docling 2.154:

| pipeline | pages/s | how measured |
|---|---|---|
| standard (layout + TableFormer, no OCR) | 11.8 warm (20 pages in 1.7 s); first call 18.5 s for 5 pages, model load included | docling `convert(page_range=…)` on `PERBUP_NO._1_TAHUN_2023__1_.pdf` |
| standard, whole document through `Extraction.process` | 2.3 (271 pages in 120.1 s: probe, hash, 27 tables, 91 figures, structurer, validation) | same file; 155 structural headings promoted (9 BAB, 92 Pasal), 50 demoted |
| vlm, in-process transformers | 0.044 (3 pages in 68.6 s warm) | `Perbup_Boyolali_Tahun_2011_No_41.pdf` (DAMAGED), pages 2-4 |
| vlm, vLLM server (`vlm_engine: api`, concurrency 8) | 0.33 (3 pages in 9.1 s; 24 pages in 74.1 s) | same pages; markdown agrees with transformers at 0.9992 (`difflib` ratio) |

Probe of the 50 largest files (2.0 GB, 14,652 pages): 34 CLEAN (9,791 pages), 14 DAMAGED
(4,619), 2 ABSENT (242). So the VLM reads a third of the pages and takes nearly all the time.

**Making it fast (2026-11-28).** The first top-50 run read ~1,500 VLM pages in four hours
and was stopped. Measured on `Perbup_Boyolali_Tahun_2011_No_41.pdf` (127 pages, DAMAGED):

| change | s/page | why |
|---|---|---|
| docling alone, `vlm_batch` 1, 8 or 16 | 1.9 | docling keeps 3-4 pages in flight and waits on each batch: vLLM idled at `Running: 0` |
| 8 page ranges x 4 pages in parallel (`vlm_workers`, `vlm_pages_per_call`) | **1.0** end to end, 127 pages in 127.9 s | the server batches up to 19 requests, 2,100 tokens/s |
| `vlm_max_tokens` 8,192 instead of 4,096 | 2.3 | the same 36-37 pages hit either cap: they loop, so a higher cap only writes more repetition |
| `repetition_penalty` 1.05 | rejected | loops 36 -> 29, but a tenth of clean pages under 0.70 similar and 30% of the text gone |

! **36 of 127 pages loop**, mostly tables: the model repeats a cell and leaks its location
tokens. Each such page keeps what it read before the loop, and says so in
`extraction.warnings` (`VLM output incomplete (stop_reason=length)`). ✗ solved.

The standard pipeline, warm, on the 271-page CLEAN file: 54.1 s with TableFormer accurate,
55.8 s fast, 17.5 s with no table structure. 27 tables cost ~37 s, ~1.4 s each, whatever
the mode; layout alone runs 15-17 pages/s. The 2.3 pages/s above counted the cold start.

! Two faults the first run quarantined, both fixed: a `table_ref` / `figure_ref` block that
carried its caption as text failed the render/parse round trip, and with it the document
(5 of the first 50); and docling's standard pipeline raised `list index out of range` on one
PDF, which `fallback: true` now reads once with the other pipeline, recorded in warnings.

> ! **vLLM strips DocTags unless told not to.** DocTags are special tokens; with vLLM's
> default `skip_special_tokens` every request returned 200 and docling parsed every page
> as empty (0 characters for 3 pages, against 7,219 in-process). The extractor sends
> `skip_special_tokens: false`.

**Documents in parallel, one per pipeline (2026-11-28).** A clean PDF reads on this GPU and
CPU, a scanned one on the VLM server, and one at a time each sat idle while the other
worked. `extract.workers` reads documents at once and writes them in file order (packaging,
✗ in the hash), and `DoclingExtractor` lanes allow one document per pipeline: two standard
reads would load the models twice on 8 GB, two VLM documents double the queue behind one
KV cache. Right after the restart the server held 28 requests at 1,100 tokens/s while the
standard lane read beside it. A resumed run now skips a sealed document before reading it
(it used to re-extract every one, then discard the result), and `id_pdf` seals every 5
documents.

**What docling's own GPU guide suggests, measured here** (48 pages each, the two files above,
server idle otherwise; scratch benchmarks, ✗ in the pipeline):

| knob (docling docs, "GPU support") | pages/s | verdict |
|---|---|---|
| VLM, our 8 page ranges x 4 (current) | **1.68** | kept |
| VLM, one converter, `settings.perf.page_batch_size` 32 / 64 (= concurrency) | 1.36 / 1.53 | slower: one converter renders and parses its pages serially |
| standard, `layout_batch_size` 4 / 16 / 64 (`ThreadedPdfPipelineOptions`) | 4.50 / 4.62 / 4.51, identical text | no gain: already at the guide's RTX 5070 figure (4.2) |
| vLLM `--max-num-seqs 64`, or `--enable-chunked-prefill --max-num-batched-tokens 8192` | did not start | at 0.40 of 8 GB no memory is left for the KV cache (-0.34 GiB); the guide's flags assume a card the server has to itself (`--gpu-memory-utilization 0.9`) |

**Tables are the standard pipeline's real cost.** The first five documents of the restarted
top-50 run (1,703 pages) took 33 minutes, and one clean file took 1,766 s for 340 pages.
Alone on an idle GPU, 48 of its pages (42 tables) measured:

| TableFormer | pages/s | tables structured (of 42) |
|---|---|---|
| accurate (default) | 0.20 | 31 |
| accurate, decode limit 512 instead of 1,024 | 0.20 | 31 |
| **fast** (`docling.table_mode`, `id_pdf`) | **0.43** | **36** |
| off | 7.01 | 0 |

The time is not in the long decodes that get discarded (55 tables hit the 1,024-step limit in
the run's log): halving the limit saved nothing. Fast mode halves the cost and structures
more tables; on a page range with no tables the two are equal (4.59 / 4.45 pages/s). Cell
accuracy between the modes is ✗ measured. The overlap of the two lanes was not the cause:
the same pages read at 0.20 pages/s with the GPU to themselves.

**Evaluated on the first five of the top 50 (2026-11-28, 1,703 pages).** Docling against the
native PyMuPDF reader on the same files, judged per page by the word list
(`lexicons/id_regulation_md@1.txt`, `QUALITY.md` §2):

| file | layer, pipeline | verdict docling / native | damaged pages docling / native | unknown words docling / native | tables docling / native |
|---|---|---|---|---|---|
| PERBUP_NO._1_TAHUN_2023__1_ | clean, standard | degraded / degraded | 1 of 204 / 4 of 270 | 2.5% / 3.1% | 27 / 0 |
| Perbup_Sleman_Nomor_42_Tahun_2023 | clean, standard | unusable / unusable | 143 of 222 / 296 of 339 | 81.5% / 84.2% | 130 / 0 |
| Perbup_Boyolali_Tahun_2011_No_41 | damaged, vlm | degraded / degraded | 10 of 124 / 33 of 127 | 5.6% / 24.6% | 46 / 0 |
| PERBUP_Sleman_No_46_Tahun_2023_part_1 | damaged, vlm | degraded / degraded | 100 of 775 / 316 of 800 | 23.6% / 16.3% | 445 / 0 |
| Salinan_2022_PERDA_KAB._BOYOLALI_16 | clean, standard | clean / unusable | 0 of 165 / 152 of 165 | 1.0% / 63.3% | 0 / 0 |

- **The VLM cuts damaged pages by a factor of 3** on both scans (33 -> 10, 316 -> 100). The
  800-page scan's higher unknown share is its short lines, names and list items, ✗ damage:
  1 of its 583 judged blocks is damaged (a formula).
- **Docling's own PDF parser reads a font PyMuPDF cannot:** the PERDA's native text is 63%
  unknown words, docling's 1.0%, with 21 BAB and 482 Pasal against none.
- **Structure.** On PERBUP 1 the native reader finds 177 Pasal headings, 94 distinct: its text
  layer carries the regulation twice. Docling finds 88 distinct and no repeats; the 6 missing
  are ✗ checked yet.
- **One clean layer was old OCR** (`Nornor`, `Lingkw1gan`, annex pages of symbol soup): the
  probe judges characters, never words, so it went to the standard pipeline, kept that text
  and spent 1,766 s on its tables. `docling.reread_unusable` now judges a CLEAN layer page by
  page with the word list first: unusable goes to the VLM. On the three clean files above it
  re-routes only that one (96% of words in damaged pages, 3.1 s to judge); PERBUP 1 and the
  PERDA stay on the standard pipeline.

**On bigger hardware, nothing is compiled in.** Each knob is read from the corpus YAML or the
environment, and none of the ones that only change speed moves the generation:

| what scales | knob | 8 GB laptop |
|---|---|---|
| documents in flight | `extract.workers` | 3 |
| documents per pipeline, each with its own models | `docling.standard_lanes`, `docling.vlm_lanes` (✗ in the hash) | 1, 1 |
| page ranges per VLM document, requests per range | `vlm_workers`, `vlm_concurrency` | 8, 4 |
| the server's share of the card and its batch | `VLLM_DOCLING_GPU_MEMORY`, `VLLM_DOCLING_MAX_SEQS` | 0.40, 32 |
| more GPUs | one VLM server each, or docling's own distributed runner (Docling Jobkit: Ray, Kubeflow), ✗ wired here | one |

The server sees up to `vlm_lanes x vlm_workers x vlm_concurrency` requests; keep that at or
under `--max-num-seqs`, or pages wait in the queue until they time out.

So on this laptop the VLM server is memory-bound: 0.40 of the card is 2.15 GiB of KV cache
(100,368 tokens), and the extraction process's own docling models hold ~3.5 GB beside it.
The next factor is a bigger GPU, where `VLLM_DOCLING_GPU_MEMORY` and the guide's flags apply
(the guide reports 2.0-3.2 pages/s on an RTX 5070, 3.6-4.5 on a 5090).

**Is granite-docling good enough for Indonesian?** Its model card lists English only. Measured
against the born-digital text of 48 CLEAN pages of `PERBUP_NO._1_TAHUN_2023__1_.pdf` (pages
5-52, 15,871 words; word-multiset precision and recall, lowercased):

| reader | pages/s | precision | recall | F1 |
|---|---|---|---|---|
| granite-docling, vLLM server | 1.43 | 0.962 | 0.968 | **0.965** |
| RapidOCR PP-OCRv6 small, `lang: [id]`, full-page OCR, CPU onnxruntime | 0.22 | 0.949 | 0.940 | 0.945 |

granite-docling reads these Indonesian pages better than an OCR engine that lists Indonesian,
and 6.5x faster, so scans stay on it. (A clean page is an easier image than a damaged scan;
this bounds the model's language error, ✗ its error on bad scans.)

---

## 3. Throughput

The pipeline is a staged queue, not a loop, because CPU and GPU stages have different
optimal concurrency:

```
 scan/hash ──► probe/route ──► [GPU batch] ──► postprocess ──► write canon
  (io pool)     (cpu pool)      (1 worker/    (cpu pool)        (io pool)
                                 device,
                                 batched)
```

Rules:

1. **One GPU worker per device**, fed by a queue. Never N processes fighting over one
   GPU — that is how a 3× slowdown looks like a 3× speedup in `nvidia-smi`.
2. **Batch by token/pixel budget, not by count.** A fixed batch size either OOMs on
   dense pages or wastes VRAM on sparse ones.
3. **CPU stages scale to cores**; IO stages scale higher (they are latency-bound).
4. **Bounded queues everywhere.** An unbounded prefetch queue in front of a slow GPU is
   just an OOM with extra steps.
5. **vLLM for the VLM path** when available — continuous batching is a large win for
   variable-length page inputs. `notebooks/docling` already has a vLLM SmolDocling
   notebook to port.
6. **Sort work by cost before batching.** Grouping similar-sized pages cuts padding
   waste substantially.

Progress is reported per stage (queued / running / done / failed) and persisted, so
`ravel status` answers "what would resume" without re-scanning anything.

---

## 4. Failure policy

Documents fail. At 30K documents, ~1% failure is 300 documents, and the wrong policy
either stops the run or silently loses them.

| Failure | Policy |
|---|---|
| Transient (OOM, CUDA error, timeout) | retry with backoff, reduced batch; then quarantine |
| Deterministic (corrupt PDF, unsupported) | quarantine immediately, no retry |
| Empty-output (valid but no text) | **fail loudly** — `CANONICAL_FORMAT.md` §6 rule 6 |
| Low confidence | write, flag in `warnings`, surface in the run report |

Quarantined documents go to `state/<corpus>/failed.jsonl` with the reason and are
**counted in the run summary**. A build whose bundle silently contains 29,700 of 30,000
documents is the failure mode this policy exists to prevent — the count is a preflight
check at bundle time (`BUNDLE.md` §5).

---

## 4b. Structure is a separate pass, and typography is not the signal

Measured on 56 real Indonesian regulation PDFs: structural markers are **2.7% bold**,
set at **body size**, **58.4% centered**. `Pasal 9` is typeset exactly like a sentence.
Typography-based heading detection scores **15.4% recall at 4.0% precision**.

So heading assignment does not belong inside the extractor. It is a **separate,
versioned pass** over canonical blocks (`extract/structure.py`) using corpus patterns
plus layout — and a structurer that claims a corpus **owns heading assignment
outright**, demoting the extractor's typographic guesses, because on this corpus they
amount to ~7,000 false headings per 50 documents.

The general rule this establishes: an extractor reports **what is on the page** (text,
position, size, weight); a structurer decides **what it means**. A different corpus
swaps the second without re-running the first — the Phase A/B boundary applied one
level down.

The exception proves the rule. Where the source's **markup** states the structure (the CFR's
XML, `SOURCE_US_CFR.md` §3), there is nothing to infer: `structure.from_markup` keeps the
extractor's headings, which name their unit and close where their element closes, and the
structurer only checks that each names a declared unit at its declared level. The extractor
that reads it is generic: `xml` takes every element name from a declared dialect
(`registry/dialects/`), so a second publisher's XML is a YAML file, not a new extractor.

---

## 5. Cleanup is a separate, recorded step

Extraction records what was on the page. Cleanup decides what to keep. They are
different stages because cleanup is opinionated and will change, while extraction is
expensive and should not.

Cleanup operates on canonical blocks and is a pure function:

- drop repeated page headers/footers (detected by cross-page repetition, not regex)
- de-hyphenate line breaks
- normalize whitespace, quotes, ligatures
- fix encoding damage
- spell/OCR correction (a model pass; `notebooks/preprocess` has both CPU and GPU
  variants to absorb)

Cleanup output is written as a **new canonical document with a cleanup id and version in
`extraction.params`**, never by editing the extracted one. Two variants can then use
different cleanup levels from the same extraction. This is "extract once, chunk many"
applied one level deeper.

---

## 6. Absorbing the notebooks

`notebooks/docling` and `notebooks/preprocess` contain the working logic:
SmolDocling runners (plain, vLLM, parallel), ID regulation parsers v1/v2, cleaners, and
markdown formatting. Porting rules in `MIGRATION.md`; the short version is that a
notebook becomes an extractor class behind the router interface, with its parameters
lifted into config and its output validated against the canonical schema.

---

## 7. Open questions

- SmolDocling-256M quality on dense Indonesian legal pages versus a larger VLM — measure
  before committing the whole corpus (`OPEN_QUESTIONS.md` §4).
- Whether page rasters are retained for a future omni path (`CANONICAL_FORMAT.md` §7).
