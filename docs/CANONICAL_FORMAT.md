# CANONICAL_FORMAT.md — Ravel

The intermediate representation. One document, one JSON file, produced by extraction and
consumed by everything downstream.

> This is the highest-leverage spec in the project. Every extractor targets it; every
> chunker, table builder, enricher and eval reads it. Get it vague and Ravel becomes
> another pile of notebooks.

---

## 1. Why an IR at all

The naive pipeline is `PDF → chunks`. It fails for a structural reason: extraction is
expensive and chunking is experimental, so they must be separable. The only way to
separate them is a format in the middle that is:

- **complete** — no chunker ever needs the original file
- **stable** — a new extractor does not change downstream code
- **inspectable** — a human can read it and see what the model saw
- **hashable** — it is a cache key and a provenance anchor

---

## 2. Shape

```jsonc
{
  "canon_version": "1.0",
  "doc_id": "sha256:9f2a…",              // sha256 of the SOURCE bytes, not this file
  "source": {
    "path": "sources/id_legal/UU_28_2007.pdf",
    "url": "https://peraturan.go.id/…",  // the citable origin; provenance root
    "title": "Undang-Undang Nomor 28 Tahun 2007",
    "mime": "application/pdf",
    "bytes": 2481203,
    "sha256": "9f2a…"
  },
  "extraction": {
    "extractor": "smoldocling",
    "extractor_version": "…",
    "model": "ds4sd/SmolDocling-256M-preview",
    "params": { "…": "…" },
    "extracted_at": "2026-09-12T…Z",
    "pages": 84,
    "warnings": ["page 41: low OCR confidence"]
  },
  "blocks": [ … ],                       // §3 — the document body, in reading order
  "tables": [ … ],                       // §4 — structured, referenced from blocks
  "assets": [ … ]                        // figures/images, if retained
}
```

`doc_id` is the source hash, deliberately. The canonical document is a *derivation* of
the source; its identity is the source's identity plus the extraction config (which is
recorded, and forms the cache key — see `EXECUTION.md` §3).

**Storage:** the JSON above is the *logical* shape — the schema, the validator and the
MCP `inspect_document` view all speak it. On disk it is stored as **parquet, one row per
document**, with `blocks` and `tables` as nested columns. Never one file per document:
object stores and HF datasets both degrade badly at millions of small objects
(`INTERFACES.md` §5). The pydantic models are the single definition; JSON Schema and the
parquet schema are both generated from them.

---

## 3. Blocks

The body is a flat, ordered list of blocks — not a tree. Hierarchy is expressed by
`level` and by the materialized `heading_path`, which is what makes chunking a linear
scan instead of a tree walk.

```jsonc
{
  "id": "b0412",
  "type": "heading" | "paragraph" | "list_item" | "table_ref" | "figure_ref"
        | "caption" | "footnote" | "page_header" | "page_footer" | "formula",
  "level": 3,                            // headings only
  "text": "Pasal 9",
  "markdown": "### Pasal 9",             // rendered form
  "heading_path": ["UU 28/2007", "Bab II", "Pasal 9"],
  "page": 17,
  "bbox": [72.0, 431.2, 523.5, 468.9],   // pdf points, origin top-left
  "reading_order": 412,
  "confidence": 0.98,
  "attrs": { "numbering": "9", "is_ayat": false }
}
```

**Rules**

- `heading_path` is materialized on **every** block, not just headings. Chunkers must
  never have to reconstruct it.
- `page` and `bbox` are mandatory when the source has pages. They are the raw material
  for provenance and for a future highlight-in-source UI.
- `page_header` / `page_footer` are **kept, not dropped**. Cleanup is a downstream
  decision; extraction records what was there. Deleting at extraction time is
  irreversible and violates "extract once".
- `type` is a closed set. A new document family gets a new `attrs` key, not a new type,
  unless the type is genuinely structural.

---

## 4. Tables

Tables are the single most common cause of silent corpus damage: a merged-cell table
flattened into prose reads fine and retrieves wrong.

```jsonc
{
  "id": "t007",
  "page": 22,
  "bbox": [ … ],
  "caption": "Tarif Pajak Penghasilan",
  "n_rows": 12, "n_cols": 4,
  "header_rows": 1,
  "cells": [
    { "r": 0, "c": 0, "rowspan": 1, "colspan": 2, "text": "Lapisan Penghasilan" }
  ],
  "markdown": "| Lapisan … |",           // rendered, lossy, for embedding
  "confidence": 0.91
}
```

Both representations are stored on purpose: `cells` is the truth (spans preserved,
machine-readable, the input to the tabular builder), `markdown` is the lossy rendering
used for embedding and display. Downstream code that needs correctness reads `cells`;
code that needs text reads `markdown`.

A `table_ref` block holds the table's position in reading order so a chunker can decide
whether a table belongs with its preceding heading.

---

## 5. Completeness test

Before the format is frozen, it must pass this: **name every chunking, enrichment and
eval strategy you might plausibly want, and check the format can serve it without
touching the source file.**

| Strategy | Needs | Present |
|---|---|---|
| Chunk on pasal/ayat | headings, numbering, order | `heading_path`, `attrs.numbering` |
| Chunk on token count with heading carry | text + path | yes |
| Chunk per table | tables + refs | yes |
| Strip running headers/footers | typed blocks | yes (`page_header`) |
| Layout-aware chunking (columns) | bbox | yes |
| Page-accurate citation | page | yes |
| Highlight the source span in a viewer | bbox + page | yes |
| Detect scanned vs native | extractor + confidence | yes |
| Cross-reference resolution ("lihat Pasal 4") | text + heading index | yes |
| Multimodal/omni embedding of a page image | `assets` + page raster | **decide** — see §7 |

Anything answered "no" is a re-extraction later. Re-extraction is allowed, but only as a
deliberate, versioned event (`canon_version` bump), never as an accident.

---

## 6. Validation

`ravel canon validate` enforces, and CI runs it on a fixture set:

1. Schema conformance (a JSON Schema lives beside the implementation).
2. `reading_order` is a contiguous permutation.
3. Every `table_ref` resolves to a table id; every table is referenced exactly once.
4. `heading_path` is monotonically consistent with `level` transitions.
5. Round-trip: rendering all blocks to markdown and re-parsing yields the same block
   sequence (catches renderer/parser drift).
6. Non-empty text ratio above a floor — a canonical document that is 95% empty means
   extraction silently failed and must be reported, not stored.

Rule 6 matters more than it looks: **a failed extraction that produces valid-but-empty
output is worse than a crash**, because it enters the corpus as a real document with no
content and nobody notices until retrieval is quietly bad.

---

## 7. Open: assets and the omni path

If an omni/multimodal embedder is used (`EMBEDDING.md` §4), it needs the page raster,
not the text. That means `assets` must either retain page images or record enough to
regenerate them deterministically from the source.

Storing rasters for 30K documents is large; regenerating them requires the source file
to still exist, which breaks the "canonical is self-sufficient" property.

**Not yet decided.** Leaning: store a deterministic *recipe* (page, DPI, colorspace,
renderer version) rather than bytes, and treat source files as permanent. Recorded in
`OPEN_QUESTIONS.md` §3.
