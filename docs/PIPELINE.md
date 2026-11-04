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

Floors are measured before they are written. `id_regulation_md@1.1` records its sample
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
- **Short Bagian / Paragraf title lines** still become their own 2–6 token chunks (about
  4.6% of rows on the sample). Chapter titles are consumed as labels; these have no label to
  go to.
