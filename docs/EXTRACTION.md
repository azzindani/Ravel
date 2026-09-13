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
