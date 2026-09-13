# CHUNKING.md — Ravel

Phase B, first stage: canonical documents → chunks with immutable provenance.

---

## 1. Why chunking is the highest-leverage decision

Retrieval can only return what chunking created. A regulation split mid-clause cannot be
retrieved as a clause, no matter how good the embedding model is. Every ranking
improvement downstream is bounded by this stage.

It is also the cheapest stage to re-run — which is the whole reason for the Phase A/B
boundary. Chunking is where experiments live (`VARIANTS.md`).

---

## 2. The chunker registry

A chunker is a pure function `canonical document → [chunk]`, registered by id, with a
declared version and a config schema.

```python
@chunker("unit", version="1.0")
def chunk_by_unit(doc: CanonicalDoc, builder: Builder) -> Iterable[Chunk]: ...
```

Chunkers shipped / planned:

| id | Splits on | For | Status |
|---|---|---|---|
| `unit` | the profile's deepest units, then its sub-units | regulations, contracts, anything with citable structure | **built** |
| `heading` | heading levels, configurable depth | general structured documents | **built** |
| `token` | flat token budget with overlap | the baseline to beat | **built** |
| `table` | one chunk per table (plus caption + heading) | tabular corpora | folded into `Builder.tables()` |
| `semantic` | embedding-similarity boundaries | experimental arm | planned |

**`unit` replaces the `id_regulation` chunker this document originally specified**, and
the rename is the point. Nothing in `chunk/unit.py` knows what a *pasal* is: it splits
on whichever units the document's profile declares, reads sub-unit markers from the
profile's patterns, and builds citations from the profile's abbreviation map. Pointed at
`id_regulation@1.0` it emits `Perbup 15/2011 Pasal 9 ayat (3)`; pointed at a contract
profile it would emit clause citations from the same code. A chunker with an Indonesian
word inside it would be the `ABSORPTION.md` §4 defect starting over — corpus knowledge
in code is knowledge the next corpus cannot reuse.

The registry exists so a corpus definition names a chunker rather than importing one,
and so the variant harness can sweep chunkers by id.

---

## 3. The `unit` chunker

The reference implementation, because Indonesian regulations are the first corpus and
the hardest case. Everything below is what the profile supplies, not what the code knows.

- Split on the **profile's deepest declared unit** — pasal — then on its sub-units
  (ayat, huruf, angka) — never on a token count.
- Carry the full structural context: `UU 28/2007 › Bab II › Pasal 9 › ayat (3)`.
- Keep the pasal's opening text with its ayat when the ayat is meaningless alone.
- Emit `identifier` for the exact-match path: the canonical citation string
  (`UU 28/2007 Pasal 9 ayat (3)`) plus normalized variants. This feeds Vera's global
  keyword bypass (`Vera/LOOPHOLES.md` §1) and is how a known regulation number is never
  silently lost.
- Do not resolve cross-references at chunk time; record them as enrichment edges
  (`ENRICHMENT.md` §4) so the chunk body stays verbatim.

**Size guidance:** Qwen3 tolerates long context, but bigger chunks blur retrieval
precision. Chunk to the unit, not to the model's maximum. Where a unit is very long,
split within it and mark `part n of m` so the pieces stay identifiable.

---

### 3b. Chunkers group; one place builds

A chunker decides **which blocks belong together and what to call the result**. It never
constructs a chunk. `chunk.base.Builder` does that, and it is the only constructor of
`Chunk` in the codebase: it reads provenance off the canonical document, computes the
deterministic id, splits anything over budget into `part n of m`, and refuses outright to
emit a chunk for a document with no `source_url`.

! "Never emit a chunk without full provenance" is a rule six chunkers can each forget,
and a structure only one piece of code can get wrong. Making it structural is why a new
chunker cannot introduce the defect that cost the incumbent corpus its citations.

Size adjustments happen over **plans** — blocks with a locator, before any chunk exists.
Merging finished chunks would mean concatenating bodies and re-deriving provenance from
rows that had already asserted it, which is the after-the-fact repair §4.2 forbids.

---

## 4. Provenance capture — the non-negotiable part

Provenance is captured **here**, at chunk creation, from the canonical document, and is
immutable thereafter. Vera enforces this in the database
(`Vera/migrations/0002_provenance_immutable.sql`); Ravel must never need that trigger to
fire.

Every chunk carries:

| Field | Source | Nullable |
|---|---|---|
| `id` | deterministic hash (§6) | no |
| `source_title` | `doc.source.title` | no |
| `source_url` | `doc.source.url` | no |
| `locator_page` | first constituent block's `page` | only if the source is pageless |
| `locator_section` | structural locator (`Pasal 9 ayat (3)`) | yes |
| `heading_path` | joined block `heading_path` | yes |
| `identifier` | canonical citation, if any | yes |
| `source_sha256` | `doc.source.sha256` | no |
| `config_hash` | the chunk config this row came from | no |

**Rules**

1. Never synthesize a URL. If a source has no citable URL, the corpus definition must
   supply a URL template or the document does not enter the corpus.
2. Never repair provenance after the fact. A wrong citation is fixed by re-chunking from
   the canonical document, producing new chunk ids.
3. Provenance points at the *source*, never at Ravel's intermediates. A human clicking
   through lands on the original document.
4. Carry the source `sha256` into the bundle so a source that later changes upstream can
   be detected rather than silently diverging (`Vera/LOOPHOLES.md` §8).

---

## 5. What a chunk is

```jsonc
{
  "id": "…",                  // deterministic, §6
  "doc_id": "sha256:9f2a…",
  "body": "…",                // verbatim text; never rewritten, never summarized
  "token_count": 412,
  "part": [1, 1],
  "block_ids": ["b0412", "b0413"],   // traceability back into the canonical doc
  "source_title": "…", "source_url": "…",
  "locator_page": 17, "locator_section": "Pasal 9 ayat (3)",
  "heading_path": "UU 28/2007 › Bab II › Pasal 9",
  "identifier": "UU 28/2007 Pasal 9 ayat (3)",
  "chunker": "unit", "chunker_version": "1.0",
  "config_hash": "4092db05fa919bc3",   // the variant this row belongs to
  "profile": "id_regulation@1.0"
}
```

`body` is **verbatim source text**. Not cleaned beyond the recorded cleanup stage, not
summarized, not LLM-rewritten. Derived text (summaries, hypothetical questions,
translations) is enrichment and lives in signals, never in `body` — because `body` is
what the human verifies against the source.

`block_ids` is what lets a debugging session go from a bad retrieval result back to the
exact blocks and bounding boxes that produced it.

---

## 6. Chunk ids must be deterministic

```
id = sha256(doc_id ‖ chunker_id ‖ chunker_version ‖ config_hash
            ‖ locator ‖ anchor ‖ body)[:32]
```

`anchor` is the chunk's first `block_id` — the document's own coordinate for it.

! It is in the formula because the version without it was wrong, and wrong in the way
that does not announce itself. It assumed a document never repeats short content at the
same locator; real ones do constantly — `KETENTUAN PENUTUP` under `BAB III`, a tariff
row under `Pasal 16 huruf a`, `PENUTUP` under `BAB VI`. Measured on ID_Legal, **710 of
121,757 chunks collided**: distinct text at distinct positions hashing to one id. Vera
keys on this, so the second row of each pair would have been rejected or would have
overwritten the first — 710 chunks leaving the corpus with no count ever looking wrong.

Consequences, all intended:

- Re-running produces identical ids → idempotent writes, safe resume.
- Changing the chunker changes every id → a variant cannot collide with another.
- Changing the body changes the id → an edited chunk is a *new* chunk, and the old one
  cannot be silently rewritten under a citation someone already recorded.

---

## 7. Open questions

- Overlap: none (clean boundaries, smaller corpus) versus small overlap (better recall at
  boundaries, more rows). Decide empirically per corpus — it is a variant dimension.
- Whether `heading_path` should also be *prepended to the embedded text* (helps the
  embedder, changes the body/embedding distinction). Also a variant dimension; see
  `EMBEDDING.md` §5.
