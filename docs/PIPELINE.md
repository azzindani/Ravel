# PIPELINE.md — Ravel

What each layer does, what it is allowed to write, and where the profile takes over. Read
`PROFILES.md` for the file format and `CHUNKING.md` for what a chunk is.

---

## 1. Two halves

```
 KNOWLEDGE BASE  ── what the corpus SAYS ──────────────────────────────────────────
  1 source      files on disk, content-hashed
  2 extract     canonical blocks, verbatim                      (cached by source hash)
  3 clean       drop furniture                                  (deletion only)
  4 structure   which lines are headings, at what level
  5 facts       type, issuer, number, year, subject, date; chapter and article labels
  6 chunk       rows with verbatim body, provenance, seq
  7 check       the build against the profile's `expected`      ← the half ends here
 ══════════════════════════ sealed: chunks/*.parquet ═════════════════════════════
 INDEX           ── how the corpus is FOUND ───────────────────────────────────────
  8 derive      embed_text, search_text                         (from a row + its profile)
  9 keyword     BM25 vocabulary and sparse vectors
 10 embed       dense vectors
 11 cluster     anchors, centroids, assignment
 12 bundle      parquet + manifest + generated schema
 13 load        COPY into Postgres
```

The knowledge base is the product. It is built, inspected and corrected **before** anything
is embedded or indexed, because everything after layer 7 is expensive (a GPU, hours) and
nothing after it can improve a row that is wrong. Layers 1–7 run on a laptop in minutes per
thousand documents; a bad profile found at layer 7 costs a re-run, found at layer 10 it
costs a re-embed.

The index half is disposable in the same sense enrichment is: it can be dropped and
rebuilt from the sealed chunks without touching them. Changing `embed_text` is a
re-index, never a re-chunk.

> ! Enrichment that needs no vectors -- entities, reference edges, ranking factors -- is
> part of the knowledge base and runs between 6 and 7. Keyword statistics are *index*:
> they are computed from `search_text`, which only exists at layer 8.

---

## 2. The one rule

Every layer writes to exactly one of three places.

| Writes to | May | Because |
|---|---|---|
| `body` | drop whole blocks (layer 3) | `body` is what a reader checks against the source. Rewriting it makes a citation point at text the source never contained. |
| derived columns / functions (`chapter`, `article`, facts, `embed_text`, `search_text`) | normalise, repair, add context | The original is still there, and the value is recomputable. |
| sidecar tables (entities, edges, signals, summaries) | anything, including model output | Droppable and rebuildable without touching `chunks`. |

So spell-correction, OCR repair and translation are not forbidden; they are allowed only
**where they cannot change what is cited**. A repair that lives in `search_text` helps
keyword recall and leaves the quotation intact. The same repair in `body` would be a
wrong citation that looks correct.

---

## 3. What the profile decides

Everything below is data in `registry/profiles/<id>@<version>.yaml`; the engine holds no
corpus knowledge (`CLAUDE.md` §5, principle 12).

| Layer | Profile section | Status |
|---|---|---|
| 2 extract | `extract`, `match` | built |
| 3 clean | `cleanup.boilerplate` | built (applied at chunk time; the canonical document stays verbatim) |
| 4 structure | `structure.units`, `sections`, `subunits` | built |
| 5 facts, labels | `identity.*`, `structure.opening`, unit `role` / `region` / `article` | built |
| 6 chunk | `pipeline.chunk` | built |
| 7 check | `pipeline.expected` | built |
| 8 derive | `pipeline.derive` | built (functions; not yet called by an embed stage) |
| 9–13 | corpus and variant config | embedder, clustering and bundle are corpus-level, not profile |

### 3.1 `pipeline:`

```yaml
pipeline:
  chunk:                      # every field optional; see §4 for precedence
    chunker: unit
    max_tokens: 4096
    min_tokens: 8
    params: {subunits: false}

  derive:
    embed_text:               # what the embedder is shown: context, then the body
      context: ["{regulation_type} {regulation_number}/{year}", "{about}", "{chapter}", "{article}"]
      separator: " › "
    search_normalize:         # repairs applied to the copy that is MATCHED, never cited
      - {pattern: '\b(Pasal|BAB)(?=\d)', replace: '\1 '}

  expected:                   # the contract layer 7 enforces
    coverage: {about: 0.83, article: 0.83}
    max_row_tokens: 8192
    oversize_share: 0.001
```

A context part is dropped when every field in it is empty (`N/A` counts as empty), so a
document whose subject could not be read gets a shorter header, not a header with a hole.
Names in templates and in `expected.coverage` are checked against the chunk's fields when
the profile loads; a typo is an error there, not a silently empty header at embed time.

---

## 4. Precedence

```
engine default  <  profile `pipeline.chunk`  <  corpus `chunk:`  <  variant
```

"Stated" means written in the file. A corpus that never mentions `max_tokens` inherits the
profile's; one that writes `max_tokens: 512` wins. The corpus pins its profile with
`extract.profile`; a corpus that routes among several profiles has no single answer and
keeps the corpus defaults. The result is hashed into the chunk config hash, so a changed
default is a different variant and cannot reuse another's rows.

Cache keys: extraction keys on the profile's `structure_hash`, which excludes the label
fields and the whole `pipeline` section; chunking keys on `config_hash`, which covers all
of it. Correcting a label or a template therefore re-chunks and never re-extracts.

---

## 5. Rows, order and neighbours

A row is one **pasal** for regulations: the unit a reader cites and the unit the exact
identifier path resolves to. Pasals longer than `max_tokens` are split into `part n of m`
at block boundaries.

Each row carries `seq`, its position among the chunks of its document, and `doc_id`. With
the index `chunks (doc_id, seq)`, "the chunks either side of this one" and "all parts of
this pasal" are range scans; `chapter` gives "everything in this chapter". The engine's
expansion tiers (`Vera/docs/SCORING.md` §7) are keyed on those, not on
`(regulation_type, regulation_number, year)`, which is not unique across regions: `Perbup
13/2017` exists once per kabupaten.

---

## 6. Layer 7: the contract

`ravel chunk` measures the build against the pinned profile's `expected` and exits non-zero
when it falls below a floor (`--no-enforce` reports without failing).

- **`coverage[field]`** is the share of *chunks* carrying a field. Per chunk, not per file:
  a 1,000-pasal regulation weighs a thousand times a one-pasal one, and the floor is about
  what the index will see.
- **`max_row_tokens` / `oversize_share`.** The embedder's `max_length` is the natural cap.
  One unbroken block can exceed any budget, so the contract is a *share*, not zero.
- A build with no chunks fails every coverage floor. "No data" is not "all data is fine".

Floors are measured before they are written. `id_regulation_md@1.2` records its sample
(968 files, 26,576 chunks) and sets each floor about five points below what was achieved.

---

## 7. Not built

Written down so a declared-but-ignored field cannot hide in a schema.

- **Row layers.** One row per BAB or per document, up to the embedder's context, beside the
  pasal rows -- the n-layer design. Not in the profile schema until the chunker can emit it.
- **An embed stage** that calls `embed_text`, and a keyword stage that calls `search_text`.
- **Layer 3 as a profile-ordered list.** Cleanup is one list of patterns applied at chunk
  time; per-rule stage and order are not declared yet.
- **Spell repair** beyond the two OCR rewrites above. A corpus-derived dictionary (a rare
  token one edit from a frequent one) is the candidate; it is unmeasured.
- **Translation.** A second vector space, which `CLAUDE.md` §7.3 forbids mixing into one
  column; if wanted it is its own column with its own embedder.
- **A corpus file for `ID_REG_Markdown`.** Its files sit inside zips, so a path under
  `provenance.base_url` would name a location that does not exist; it needs a `url_map` or a
  zip-aware source before it can be built. Not guessed.
- **Short Bagian / Paragraf title lines** still become their own 2–6 token chunks. Chapter
  titles are consumed as labels; these have no label to go to, so they are kept as chunks
  rather than lost.

---

## 8. Verification

`tools/verify_kb.py` runs the real layers 2–6 in memory over a directory and answers one
question per document: *did anything the document says fail to reach a chunk, and where did
it go?* It is read-only, and it gates (non-zero exit) on the invariants that must hold:

| Gate | Asserts |
|---|---|
| no document failed | no exception in extract, structure or chunk |
| no unaccounted block | every canonical block is in exactly one bucket: emitted, heading carried by a label, consumed chapter title, dropped by a named profile rule, empty section, non-body type, empty |
| no titled heading lost | a heading that says more than its marker reaches a chunk, a path or a label |
| every chunk body is verbatim | `body` equals its own blocks' text joined, character for character |
| every zero-chunk document held only boilerplate | a document may yield nothing only if it contained nothing |
| profile's `expected` floors met | layer 7 |

Beside the gates it reports **word recall** (the multiset of source words against the
canonical blocks and against chunk bodies, with what went missing at each step), throughput
per stage, peak memory, the size distribution, fact and label coverage, and the lowest-recall
documents to open by hand.

### 8.1 Measured: `id_regulation_md@1.2` over all 46,451 files

16 workers, 167 s wall. Every gate passes.

| | |
|---|---|
| chunks | 1,327,249 (28.6 per file) |
| source words in a chunk body | 96.61% |
| lost at extraction | 0.14%: 0.13% decoded HTML entities (`&lt;` becomes `<`, still present), 0.01% encoding damage and hyphen joins, 0.00% list numbers |
| lost at chunking | 0.66%: the words of removed `<!-- image -->`, `Cukup jelas.`, repeated `PRESIDEN REPUBLIK INDONESIA` headers, `ttd` (6,618 cut-off duplicates are counted apart: their words are in the next block) |
| blocks by bucket | 80.2% emitted, 10.8% heading carried, 7.6% boilerplate (by rule, counted), 0.9% chapter title consumed, 0.4% empty section, 0.04% truncated duplicate |
| zero-chunk documents | 150, median 62 bytes, every character boilerplate: image-only scans that need OCR, not a parser fix |
| chunk tokens p10 / p50 / p90 / p99 | 14 / 91 / 545 / 4,110; 6.0% under 8, 3.4% split into parts, 31 over the 8,192 cap |
| coverage | type 82.7%, issuer 89.0%, subject 89.3%, date 74.3%, article 87.7%, chapter 100% |
| throughput | 27 files/s/core, 1.0 MB/s/core (extract 492 s, structure 282 s, chunk 961 s of CPU); peak 750 MB per worker |

What the first full run found, and the fixes: stripping `1.` list numbers dropped 1.8M words
(0.95%), now kept (`TextExtractor` 1.2); a titled heading with nothing under it, or inside a
chapter segment, reached no chunk (277K characters), now kept as its own chunk
(`attrs.orphan`, `unit` 1.2); and a heading judged carried because neighbouring labels
happened to spell it when joined, now matched against whole labels.

### 8.2 Spot check, and what 1.2 fixed

Reading chunks at random, not totals, found what the gates cannot: a gate asserts that no
word was lost, not that the structure is right. Each defect was counted over all files, then
fixed as profile data (`id_regulation_md@1.2`) plus the engine support it needed:

| defect | before | after |
|---|---|---|
| `Pasa13` read as text, not a heading | 7,621 headings, 2,001 files | headings, labelled `Pasal 3` |
| `Pasal 3 2` refused as marker + text | first line of 17,949 | one heading, `Pasal 32` |
| `Pasal 5 Cukup jelas.` as a clause | about 1,700 | heading, empty body, no chunk |
| BAB lines inside another chunk (`.`/`;` tails, long titles, glued `BABIV`) | 6,992 | 5,868, mostly inside annexes |
| cut-off catch-word lines kept beside their completion | 69,661 lines | 29,810; 6,618 dropped as exact repeats |
| dotted leader lines `.........` | kept | boilerplate |
| issuer read across `TENTANG`/`NOMOR` | wrong `enacting_body` | repaired |
| `Pebruari`, `Nopember` dates | no date | read; date coverage 71.4% to 74.3% |

Known and left, because the source is the cause or the fix is a decision, not a pattern:
bare page-number lines inside a body (93,001 lines, 24.4% of files); pasal numbers absent from
the source (43.5% of the remaining gaps); unreadable OCR scans (about 1 to 2% of files, which
word recall does not detect); `�` replacement characters already in the source (2.9% of
files); a preamble displaced when a document has no `PENJELASAN` heading; 150 image-only
files that need OCR. A document-level quality flag is the open question.

`verify_kb.py` measures the layers, not the CLI: the corpus file that `ravel chunk` needs is
the open item in §7.
