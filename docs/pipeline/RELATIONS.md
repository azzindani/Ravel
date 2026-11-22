# Relations — which regulation replaces, changes or rests on which

The knowledge graph is built from **citations and the verb beside each one**, not from a
model guessing entity spans. A regulation says what it replaces in so many words, and names
the target by type, number and year, which is what the profile already reads. No NER model
is needed, and every edge carries the sentence it came from.

> Layer: **enrichment**, read from the sealed chunks (`PIPELINE.md`). Sidecar tables,
> disposable; `chunks` is never touched (`CLAUDE.md` §5.4).

## 1. What is built

```
chunks ──► RelationExtractor ──► edges (source doc, relation, target citation, sentence, chunk)
                                   │
 all docs' identities ─► Index ────┴─► resolve ─► edges with target_doc_id (or none, and why)
                                                    │
                                                    └─► derive_status ─► revoked | partially_revoked | amended | none_known
```

`src/enrich/relations.py`; run over a corpus with `tools/build_relations.py`, which writes
`relations.parquet` and `doc_status.parquet` and prints the measurements in §4.

| Relation | Meaning | Where it is read |
|---|---|---|
| `revokes` | this document ends the target | `mencabut X`, `X … dicabut / dinyatakan tidak berlaku`, title `Pencabutan X` |
| `replaces` | this document takes the target's place | `menggantikan X`, `X digantikan` |
| `amends` | this document changes the target | title `Perubahan atas X`, `mengubah X`, `X diubah sebagai berikut` |
| `based_on` | the target is a stated legal basis | the `Mengingat` list |
| `references` | mentioned, no act | everything else, including history and considerations |

`replaces` ends a document's life like `revokes`. A `partial` edge (`Pasal 114 ayat (6) …
dicabut`) affects part of the target and gives `partially_revoked`.

## 2. How a sentence becomes an edge

All of it is the profile's `relations:` section (`PROFILES.md`); the module is mechanism only.

1. **Find citations.** `relations.reference` reads `Peraturan Bupati Lahat Nomor 15 Tahun
   2017`, `UU No. 40 Tahun 2007`, `Undang-Undang Republik Indonesia Nomor 23 Tahun 2014`:
   case-insensitive, short forms, and the issuing region as whatever capitalised words sit
   before `Nomor`. (`identity.citation` is for a title block in capitals and misses these.)
2. **Bind each to its nearest cue, on the right side.** Every rule says which `side` its
   cue may stand on. This is the lesson of the first run: with cues allowed on either side,
   the title of a cited history instrument (`… tentang Perubahan Ketiga atas Peraturan
   Daerah Nomor 10`) claimed every citation near it, and `amends` was 2.6x too large.
3. **Demote context to `references`.**
   - `demote`: `sebagaimana telah diubah dengan Y` makes Y history; `peraturan pelaksanaan
     dari X` is about X's implementing rules, not X.
   - `negate`: `sepanjang belum diganti dengan …` says what would end a regulation.
   - `title_marker`: a cue after `tentang` and a citation of ANOTHER instrument belongs to
     that instrument's title (`Perpres 6/2000 tentang Pencabutan Inpres 14/1967`).
   - `demote_unless_adjacent`: `tentang <title> … Perda Y` makes Y part of a title, unless a
     cue stands right before it.
4. **Zones in the preamble.** After `bahwa` a citation is `references`; after `Mengingat`
   (or a numbered item that begins with an instrument) it is `based_on`; a verb in either
   zone is a cited title's, not this document's. The `Menimbang` and `Mengingat` words are
   often headings that never reach a chunk body, so the zones are also found by structure.
   A "preamble" longer than `preamble_max_chars` is a document whose structure was not
   found; it gets no zones, or they would swallow the operative articles.
5. **Lists inherit.** After a cue sentence ending `:`, each item beneath it carries the cue.
6. **Skip explanations and annexes** (`skip_chapters`): they discuss instruments, they do
   not act on them.

## 3. Resolving a citation to a document

The index is `(number, year)`; then the type is compared as a prefix (`PERATURAN DAERAH`
fits `PERATURAN DAERAH KABUPATEN`, `PERDA` fits `Perda Kab.`), then the region. A region
that contains another (`Bangka Tengah` and `Bangka`) resolves to the longer.

The corpus holds some regulations more than once (a scan and its re-typed copy). Copies
are one instrument: a citation of it is not ambiguous between them, and a revocation of it
marks every copy.

| Resolution | Confidence | When |
|---|---|---|
| `resolved` | 1.0 | one document fits, region included; or a national instrument |
| `inferred` | 0.7 | the citation names no region; the citing document's own region is assumed |
| `inferred` | 0.6 | the sentence about the cited instrument names exactly one of the regions that fit (`… Kabupaten Tapin`) |
| `ambiguous` | 0 | several fit and nothing separates them; **linked to nothing** |
| `unresolved` | 0 | none fits, including "a different region's" |

An unresolved edge is kept: "this revokes Perda 11/1979" is true and citable even when
11/1979 is not in the corpus to be marked.

## 4. Status, and what it cannot say

`derive_status` reads only resolved edges pointing at a document. **Absence of an edge is
not validity.** The status is `revoked`, `partially_revoked`, `amended` or `none_known`;
never "in force", because the revoker may not be in the corpus.

Measured on all 46,451 files (`id_regulation_md@1.2`):

| | |
|---|---|
| documents that can be cited (title block gave type, number, year) | 36,819 (79.5%) |
| `based_on` | 423,556 edges in 39,862 documents |
| `references` | 292,243 edges in 41,344 documents |
| `revokes` | 9,375 edges in 8,749 documents; 24% resolved, 3% inferred, 2% ambiguous, 71% unresolved |
| `amends` | 7,197 edges in 5,466 documents; 43% resolved, 5% inferred, 1% ambiguous, 51% unresolved |
| `replaces` | 44 edges (mostly article-level `diganti dengan`, which is partial) |
| status | 2,252 revoked (5.0%), 50 partially revoked, 1,708 amended (3.8%), 41,169 none known (91.1%) |
| mutual revocations / revoking a later-year document | 1 / 1, both one pair of partial edits between a Konawe regulation and its amendments |

The 71% unresolved is **the corpus, not the parser**: a regulation that revokes a 2008
Perbup usually cites one that is not among these files. Splitting the unresolved, 6,130 of
6,653 revokes have a document with that number and year but a different type or region, so
the target is simply elsewhere. The statuses are therefore a floor.

Read by hand, 10 of 10 sampled `revokes/resolved` edges were correct, including partial
ones; `amends/resolved` samples are the documents' own titles; a sample of `ambiguous`
edges were citations with no region from a citing document whose own region was unknown.

## 5. Left, and why

- **Ambiguity (2% of revokes).** Every remaining one is a citing document with no identity
  or no region of its own (20% of documents have none, `SOURCE_ID_REG_MARKDOWN.md`), citing
  a number and year that several regions share, in a sentence that names none of them.
- **Consolidated text.** An amending regulation holds only its changes, so the chunk for
  the old pasal stays as written and carries `amended_by`, not the merged text.
- **Vera.** The tables are in the bundle and the load plan (below); Vera does not read them
  yet. The intended use: show `revoked_by` / `amended_by` on a result and rank a revoked
  regulation below its replacement, without hiding it.
- **Temporal check.** A revoker whose year precedes its target's is a wrong link and is
  counted (1 today), not repaired.

## 6. In the bundle

`relations.parquet` and `doc_status.parquet` sit beside `chunks/` (`BUNDLE.md` §4) and load
into `doc_relations` and `doc_status`, declared in the generated `schema.sql`. The load plan
stages them (`corpus_id` is assigned by the merge, not carried by the file), copies with an
explicit column list, merges inside the transaction, and builds `doc_relations_target_idx`
("what revoked this?") and `doc_relations_source_idx` after the load. There is no foreign key
to `chunks` or between documents: an edge to an instrument outside the corpus is a fact
about the corpus. `tools/ci_bundle_check.py` loads a two-document fixture through the
generated SQL on a real pgvector and answers "what revoked this?" from it.
