# SOURCE_US_FR.md — the US Federal Register

The daily Federal Register as GPO publishes it on govinfo, built to sit **beside `us_cfr` in
one Vera domain**. The Federal Register is the rulemaking behind the CFR: a final rule's
REGTEXT is the CFR text it puts in force, its preamble argues the rule in the same words, and
the notices around it name the same agencies and parts. As company for the CFR's 115 eval
questions it is the hardest distractor a US domain can get, which is why it was built: it
measures how Recall falls as a domain grows, with real near-duplicates and no new
questions (Vera `SCALING.md` §4a).

## 1. The source

| | |
|---|---|
| Publisher | Office of the Federal Register (NARA) and GPO |
| Legal status | the official daily publication of rules, proposed rules and notices (44 U.S.C. 1505, 1507) |
| Bulk | `https://www.govinfo.gov/bulkdata/json/FR`: year → month → one XML per daily issue, plus a monthly zip |
| Format | GPO XML, the CFR's markup family (`gpo_cfr@1.0`): `GPOTABLE`, `PRTPAGE`, `E T="03"` |
| Licence | public domain (17 U.S.C. 105) |
| Updated | every business day; an issue never changes once listed |
| Size | 22,854 daily issues (govinfo collection count, 2026-11-28); 2025 is 247 issues |

## 2. Fetch: `govinfo_tree`

A generic fetch kind for a govinfo bulk collection published as files that never change:
it walks the JSON listing from `fetch.listing` and keeps every file whose path below
`fetch.files` matches a glob in `fetch.paths`. The globs are the edition
(`2025/*/FR-2025-??-??.xml` is the year 2025), and a folder is opened only when a glob can
match below it, so a year costs 13 listings instead of the whole tree. The monthly zips are
never fetched. `fetch.files` is `provenance.base_url`, so every chunk cites the exact issue
URL its bytes were read from.

```
ravel sources fetch us_fr            # 247 files for 2025
```

## 3. The dialect: `gpo_fr@1.0`

- **Units.** The root `FEDREG` is the `issue` (level 1), headed by its `DATE` and numbered by its
  `VOL`. `RULE`, `PRORULE`, `NOTICE` and `PRESDOCU` are each a `document` (level 2): one unit
  name, because identity is read per document. What kind it is stays in its `action` and in
  the section that holds it.
- **What the markup states** about a document: `number` (the FR Doc number, read out of
  `[FR Doc. 2024-04579 Filed …]` by a `~pattern` whose `value` group takes the number),
  `agency`, `subagency`, `cfr` (the parts it touches), `docket`, `rin`, `action`, and `page`.
- **`page` is the page the document opens on** (`@page`), which is the page it is cited at.
  ✗ its first `PRTPAGE`: a notice that opens mid-page prints no break before its text, and
  the first break inside it is the next page. ✗ the walker's running page either: 534 of 785
  breaks in three sample issues sit inside a paragraph, where the walker never sees them. So
  `@page` is the unit's own break when that comes before any of its text, else the last
  break before it in document order. Checked against each issue's own table of contents:
  294 of 305 documents agree; the 11 that differ are TOC entries listing several documents
  on one line.
- **Furniture.** The issue's running matter (`VOL`, `NO`, `DATE`, `UNITNAME`, printed again
  before every section), its contents (`CNTNTS`), `BILCOD` and `PTITLE` are not text.
- `REGTEXT`, `SECTION`, `PART` inside a document are its text, ✗ structure: the CFR sections a
  rule amends are cited in the CFR, not here.

## 4. Identity and chunks: `us_fr@1.0`

| field | from |
|---|---|
| identifier | `{issue.number} FR {document.page}`: `89 FR 15725`, as the CFR's own source notes cite it |
| regulation_type | `Federal Register` |
| regulation_number | `FR Doc. {document.number}` |
| enacting_body | the document's `AGENCY` |
| about | its `SUBJECT` |
| year, effective_date | the issue's date |

Chunked by `markup` like the CFR: one chunk per document, a longer one split into
`part n of m` at 1,024 tokens on paragraph boundaries. The embedder sees the CFR's three
context lines (`Citation:`, `Agency:`, `Location:`), so the two corpora share one vector
space and one way of being read.

## 5. Trial (2026-11-28, three sample issues)

| issue | documents | blocks | tables | errors | warnings |
|---|---|---|---|---|---|
| 2024-03-05 | 113 (12 rules, 3 proposed, 92 notices, 6 presidential) | 5,706 | 31 | 0 | 12 graphics |
| 2025-01-02 | 61 | 4,788 | 17 | 0 | 2 graphics |
| 2025-06-16 | 183 | 9,476 | 69 | 0 | 8 graphics |

2 of 357 documents state no FR Doc number in a form the pattern reads (Federal Acquisition
Regulation compilations); the profile expects 95% coverage.

## 6. Built: 2025 (2026-11-28)

```
ravel sources fetch us_fr                          # 247 issues, 548.7 MB, 2,748 s
ravel extract us_fr --workspace work/us_fr/2025    # 247 documents, 0 failed, 126.5 s
ravel chunk us_fr --workspace work/us_fr/2025      # 122,923 chunks, 70.7 s
```

| | |
|---|---|
| issues · Federal Register documents | 247 · 23,642 |
| chunks | 122,923, median 919 tokens; 100.0% with identifier and locator |
| over 1,024 tokens · over 8,192 | 7,242 (tables kept whole) · 7 (0.006%, under the profile's 0.1%) |
| chunks with no agency | 520 (presidential documents name the President instead) |

A year is a third of the CFR (366,005 chunks), so the scaling steps are years:
2023-2025 is 1x the CFR beside it, 2017-2025 is 3x. ✗ embedded yet: the GPU was held by
another extraction.

**One domain with the CFR: `us_federal`.** A Vera domain is one schema and one BM25
vocabulary, so the two corpora are read as one: root `sources/GOVINFO` with `CFR/` and
`FR/` (govinfo's own layout; on Windows, junctions to `sources/US_CFR` and
`sources/US_FR`), `snapshots: [CFR/current.txt, FR/current.txt]` so each keeps the
edition its own fetch chose, provenance the govinfo bulk root, and `profile: auto`
(`CFRDOC` -> `us_cfr@1.0`, `FEDREG` -> `us_fr@1.0`). `ravel embed --reuse` takes the
CFR's vectors from its own build instead of embedding them again.
