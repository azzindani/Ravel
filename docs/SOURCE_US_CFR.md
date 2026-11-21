# SOURCE_US_CFR.md — Ravel

The US Code of Federal Regulations as a corpus: where it comes from, how it is fetched
again on every update, and how its XML becomes cited chunks.

---

## 1. Which text: CFR, not eCFR

| | **CFR** (annual edition) | **eCFR** |
|---|---|---|
| Legal status | **official**: prima facie evidence of the original documents (44 U.S.C. 1510) | editorial compilation; "not an official legal edition of the CFR" |
| Currency | once a year per title: 1–16 on Jan 1, 17–27 on Apr 1, 28–41 on Jul 1, 42–50 on Oct 1 | daily |
| Bulk | govinfo, one XML per printed **volume** | versioner API, one XML per **title**, pinned by date |
| Page numbers | yes (`PRTPAGE`): a chunk cites a page of the printed volume | no |

**Ravel builds the CFR.** A citation has to point at the text a court accepts. The eCFR is
supported by the same extractor and fetcher, for a corpus that has to be current to the
day, and its bundle says so in its provenance (`us_ecfr`).

An edition is published title by title and **volume by volume**, over months. So the fetcher
takes **the newest complete edition of each title**, never one year for all: an edition that
lacks a volume number the edition before it had is held back, and the hold is reported. On
2026-11-21 (`ravel sources fetch us_cfr`) that took 2026 for 9 titles, 2025 for 35 and 2024
for 5, holding back:

| title | held edition | missing volumes | used |
|---|---|---|---|
| 7 | 2026, then 2025 | 2026: 10, 11, 13 · 2025: 7 | 2024 |
| 12 | 2026 | 3, 4, 6, 7 | 2025 |
| 14 | 2026 | 1 (its `vol0` is a byte-for-byte copy of `vol3`) | 2025 |
| 16 | 2026 | 2 | 2025 |
| 29 | 2025 | 6 | 2024 |
| 33 | 2025 | 3 | 2024 |
| 40 | 2025 | 18, 37 | 2024 |
| 50 | 2025 | 3, 4, 5, 7, 8 | 2024 |

! By volume **number**, not count. The first rule compared counts, and govinfo's 2026 title 14
passed it with five files against five: `vol0` is a copy of `vol3`, and `vol1` is not there.
The section count below found it (1,585 sections counted twice in the XML, once extracted).

A title that really did lose a volume would stay held; `fetch.edition: <year>` pins it.
Mixing volumes of two editions in one title is ✗ done: volume boundaries move between
editions, and a mix could hold a part twice or not at all.

GPO does not reprint a volume nobody amended, so a volume in a newer edition's folder can
carry an older revision date: 41 CFR vol 2 in the 2025 folder is `Revised as of July 1, 2017`.
A chunk's `year` and `effective_date` are its own volume's, not its folder's.

## 2. Fetch (`ravel sources fetch <corpus>`)

`CLAUDE.md` §7.9 forbids scraping. This is not scraping, and the line is drawn here: a
corpus may declare a `fetch:` block naming a **publisher's bulk listing**; the fetcher
reads that listing and downloads exactly the files it names, to the publisher's own
paths. Crawling a website, following links, or reading HTML stays `Krawl`'s.

```
listing (JSON)  ->  wanted files  ->  missing or incomplete?  ->  download to .part
                                                                 -> size check -> rename
                ->  fetch.jsonl (append: path, url, bytes, sha256, fetched_at)
                ->  current.txt (the snapshot: newest edition per title)
```

- **The relative path is the publisher's.** `2026/title-2/CFR-2026-title2-vol01.xml`
  under `provenance.base_url = https://www.govinfo.gov/bulkdata/CFR/` is the exact URL the
  bytes came from, so provenance is exact by construction. The corpus loader refuses a
  `fetch.files` that is not `provenance.base_url`.
- **Never constructed.** File names come from the listing: 2026 title 2 is `vol01`, title
  3 is `vol1`. The ported notebook built `vol{n}` and would have fetched nothing in 2026.
- **Never overwritten.** A file already on disk at the listed size is kept. One whose size
  differs from the listing is reported as a conflict and left alone: the publisher
  re-issued it, and which copy a corpus uses is the operator's decision.
- **Resumable.** A run killed mid-file leaves a `.part`, which the next run discards.
- **Recurrent.** Run it again next quarter: it fetches the editions published since, and
  rewrites `current.txt`. Old editions stay on disk (sources are never deleted).

`sources.list: current.txt` makes extraction read the snapshot and nothing else. A snapshot
is built in **its own workspace** (`-w work/us_cfr/<date>`): the workspace stages
(canonical, chunks, vectors) append, so a superseded edition left in one would be indexed
beside its replacement. Reusing the vectors of unchanged chunks across snapshots is
designed, ✗ built: today an update re-embeds the corpus.

## 3. Extract (`cfr_xml`)

One extractor, two dialects, told apart by the root element:

| | CFR (`CFRDOC`) | eCFR (`ECFR`) |
|---|---|---|
| units | `TITLE SUBTITLE CHAPTER SUBCHAP PART SUBPART SUBJGRP SECTION APPENDIX` | `DIV1..DIV9 TYPE=...` |
| heading | `HD SOURCE=HED`; a chapter's is in its `TOC/TOCHD` | `HEAD` |
| section | `SECTNO` + `SUBJECT` | `HEAD` (`§ 1.100 Content of this title.`) |
| tables | `GPOTABLE` (`BOXHD/CHED`, `ROW/ENT`) | HTML `TABLE` |

Every structural element becomes a heading block whose `attrs` carry what the markup
states: `unit`, `number`, `name`. Body elements become paragraphs; `AUTH`, `SOURCE`,
`CITA`, `EDNOTE` and notes keep their kind in `attrs`. Tables become canonical tables.

- **Strict parse.** A malformed file fails the document. The notebook parsed with
  `recover=True`, which drops what it cannot read and reports success.
- **Nothing dropped silently.** Navigation (`TOC`, `CONTENTS`, running heads, `EAR`), front
  and back matter are skipped *by name*; any other element the extractor does not know is
  counted in `extraction.warnings`, so a schema change is visible on the first run.
- **Reserved units** (`[Reserved]`) carry no text and produce no block; they are counted.
- **Pages.** `PRTPAGE` sets the page of every block after it, so a CFR chunk cites the
  printed page. eCFR has none.
- **Appendices are content.** The notebook skipped them; there are 4,095 in the eCFR.
- ✗ Title 3's presidential documents (`PROC`, `EXECORD` outside `TITLE`) are counted and
  not yet extracted.

## 4. Chunk (`markup`)

A CFR volume holds many parts, from many agencies, so one document has many identities.
The `unit` chunker reads one identity per document from its opening text; the `markup`
chunker reads it **per scope** from the headings in force, using templates the profile
declares (`identity.markup`):

```yaml
identity:
  markup:
    scope: part
    facts:
      regulation_type: "CFR"
      regulation_number: "{title.number} CFR Part {part.number}"
      enacting_body: "{chapter.name}"
      about: "{part.name}"
    identifier:
      section: "{title.number} CFR {section.number}"
      appendix: "{title.number} CFR {appendix.marker}"
      part: "{title.number} CFR Part {part.number}"
```

A template with a missing value yields nothing, never a partial string. One chunk per
section or appendix, split into `part n of m` over the budget; a part's own text
(authority, source) is its own chunk. Nothing in the chunker knows a CFR word.

## 5. The first build (2026-11-21)

`ravel extract us_cfr -w work/us_cfr/2026-11-21`, then `ravel chunk us_cfr -w ...`:

| | |
|---|---|
| volumes | 246 fetched (1.2 GB), 246 extracted, 0 failed, 225 s |
| sections | **231,405 in the XML, 231,405 read**; 6,658 reserved, 224,747 with text |
| appendices | 4,178 in the XML, 4,178 read; 186 reserved |
| headings | 0 with an undeclared unit, 0 at the wrong level |
| chunks | 366,277 (342,470 text, 23,807 tables), 138 s; ids unique |
| identifier | 100.0%; median 371 tokens, 185M tokens in all; 219 over 8,192 |

The section count is independent of the extractor: every `SECTION` and `APPENDIX` element
outside a table of contents, counted with `ElementTree` per volume, against the extractor's
own count. Spot checks cite as a lawyer would: `40 CFR 52.21` (EPA, 39 parts, p. 15 of 2024
vol 3), `21 CFR 101.9` (FDA), `26 CFR 1.61-1` (IRS), `29 CFR 1910.134` (OSHA),
`45 CFR 164.502` (HHS), `12 CFR 1026.18` (CFPB).

Not yet embedded or scored: there is no CFR question set.

## 6. Size

eCFR on 2026-11-19 (structure API): 227,264 sections, 7,077 reserved, 4,095 appendices,
824 MB of XML; the largest title is 40 at 162 MB. A title-sized eCFR file is one document,
which costs memory in proportion; the CFR's volumes are 2–7 MB each.
