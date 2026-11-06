# Quality — is this document worth indexing?

A Markdown corpus arrives already extracted, years ago, by somebody else's OCR. Nothing
about the file says whether that OCR worked. `extract/quality.py` asks the question of a
PDF's text layer before extraction; this asks it of the finished **chunks**, because they
are what a reader will search.

> Layer: **enrichment**, read from the sealed chunks (`PIPELINE.md`). A sidecar, disposable;
> `chunks` is never touched (`CLAUDE.md` §5.4). It changes no chunk and moves no cache key.

## 1. What is built

```
chunks ──► QualityGate.score ──► verdict per document: clean | degraded | unusable | absent
                                  └─► indexable = verdict not in profile.quality.not_indexable
```

`src/enrich/quality.py`; run over a corpus with `tools/build_quality.py`, which writes
`doc_quality.parquet` and prints the verdict split with a sample of each verdict's text.
The word list is `tools/build_lexicon.py`'s output, committed as data in
`registry/lexicons/`.

| Verdict | Meaning | Indexed |
|---|---|---|
| `clean` | readable throughout | yes |
| `degraded` | some chunks damaged, or typos throughout; keyword search will miss what they hide | yes |
| `unusable` | more than half the words sit in damaged chunks | no |
| `absent` | almost no text (`<!-- image -->` and nothing else): needs OCR, not indexing | no |

`indexable` is the **profile's** decision (`quality.not_indexable`), not the engine's. The
chunks of an unindexable document stay in the knowledge base: it holds what the source
said, and the verdict is what a query or a repair pass reads to decide what to do with it.

## 2. How a document is judged

1. **A chunk is damaged** when 30% of its judged words are not in the word list, or it holds
   10 U+FFFD per 1000 characters. Judged words are letters only, three or more long, not an
   all-capital acronym of up to five (`DPN`, `KSPN`), and not a table row. A chunk with
   fewer than 12 judged words is never called damaged: three words is not a share.
2. **A document** is `absent` below 12 words in all; `unusable` when the damaged chunks hold
   at least half its judged words; `degraded` when any chunk is damaged, or 10% of its words
   are unknown, or it carries 2 replacement characters per 1000; otherwise `clean`.

The unit is the chunk because damage is local: a stamp, a page, an annex of figures. Judging
the file by its worst page would discard a 250-page regulation for one bad scan; judging it
by its average would index a document that is half noise.

## 3. Why these signals, and where the thresholds came from

Measured over all 46,451 files before any threshold was set. Unknown-word share separates
the classes by an order of magnitude; everything else overlaps legitimate documents.

| Signal | Median | 99th pct | Verdict |
|---|---|---|---|
| words not used in any clean document | 0.6% | 30% | **used** |
| U+FFFD per 1000 characters | 0 | 0.7 | used, as a second signal (1,449 files have one) |
| share of 1-2 letter words | 4.6% | 45% | dropped: tables and lists reach it |
| non-letter share of characters | 8% | 55% | dropped: 960 tables fall under 0.6 and are fine |
| bare-number lines | 1.3% | 24% | dropped: annexes of figures |

Thresholds were set by reading samples at each level, not by counts:

- **0.10 to 0.20 unknown**: readable, with typos (`Pcmerintah`, `scbagairnana`). Indexable.
- **0.20 to 0.30**: noisy but a person reads it. Chunks here are mostly not damaged.
- **0.30 and above**: `Peroluron Doeroh Kobupolen Kediri`, `PERATURAf.T tsUPATf SLEIfiAII`.
  Fine to a person, matches nothing a person types. This is the chunk threshold.

Measured on `id_regulation_md@1.2`:

| | |
|---|---|
| clean | 40,895 (88.0%) |
| degraded | 4,431 (9.5%) |
| unusable | 924 (2.0%) |
| absent | 201 (0.4%) |
| damaged chunks | 14,786 of 1,328,121 (1.1%) |
| documents with a damaged chunk | 4,439, of which 3,515 stay indexable |

Reading the borderline `unusable` documents (damaged share 0.5 to 0.75, 170 of them) shows
text like `pcrtama`, `prcvcntif`, `Pclimpahan`, which is damage and not names.

**False positives found and fixed on the way**, each by reading a sample:

- A 7,800-character annex of fertiliser quotas per sub-district read as 63% noise: place
  names are in no word list. Table rows are not judged.
- `DPN dan KSPN`, `NIP` read as unknown. Short all-capital words are not judged.
- A one-sentence decree (Perpres 12/1965, 20 words) read as `absent` at a 30-word limit. The
  limit is 12.

## 4. The word list, and its limit

`registry/lexicons/id_regulation_md@1.txt` holds 15,631 words that appear in at least 25
documents **that already read as clean** (at most 1% unknown against a first-pass list).

One pass is not enough: a scanner batch makes the same mistake in every document it
touches, and `pcmerintah` appeared in 25+ files of the unfiltered list. The second pass
removes most of those. A few survive (`pcmerintah` itself, `ditctapkan`); the gate is a
little more lenient for them, which is the safe side. A stricter pass (0.5%) removes more
but flags 1,000 more clean documents as degraded.

**Limit: it is an Indonesian list.** A French or English annex inside an Indonesian
regulation (Perpres 69/2014 carries a customs convention) reads as unknown words and is
judged `unusable`. The verdict's reason says why; it is not hidden. A second list per
language would fix it and is not built.

## 5. Bundle and load

`doc_quality.parquet` sits beside `chunks/` (`BUNDLE.md` §4) and loads into `doc_quality`,
declared in the generated `schema.sql`. The load plan stages it (`corpus_id` is assigned by
the merge), copies with an explicit column list and merges inside the transaction.
`tools/ci_bundle_check.py` loads a two-document fixture on a real pgvector and asks for the
documents to leave out.

Hashing: `quality` is excluded from `config_hash` and `structure_hash`, so correcting a
threshold re-judges and never re-chunks or re-extracts. `QualityGate.hash` covers the
thresholds and the word list's SHA-256.

## 6. Left

- **The index half does not read it yet.** There is no embed or keyword stage to skip the
  unindexable documents; when there is, it asks `indexable` (or `chunk_damaged` per chunk).
  Vera can already filter on `doc_quality.indexable`.
- **Repair is not attempted.** The 924 unusable and 201 absent documents are the work list
  for the OCR extractor (`EXTRACTION.md`): the same files, re-read from the page.
- **No second language** (§4), and no look at tables beyond leaving them out.
