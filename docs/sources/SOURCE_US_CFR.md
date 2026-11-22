# SOURCE_US_CFR.md — Ravel

> The first source built by the process now written down in `ADDING_A_SOURCE.md`.

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
beside its replacement. The vectors of unchanged text are taken from the previous snapshot
(`ravel embed us_cfr -w <new> --reuse <old>`, `embed/reuse.py`), so an update embeds only
what changed.

## 3. Extract (`xml`, dialects `gpo_cfr` and `ecfr`)

The `xml` extractor knows no element name. What the publisher's elements mean is declared
in `registry/dialects/gpo_cfr@1.0.yaml` and `ecfr@1.0.yaml`, and the profile names both
(`extract.xml.dialects`). The root element picks the dialect:

| | CFR (`CFRDOC`) | eCFR (`ECFR`) |
|---|---|---|
| units | `TITLE SUBTITLE CHAPTER SUBCHAP PART SUBPART SUBJGRP SECTION APPENDIX` | `DIV1..DIV9 TYPE=...` |
| heading | `HD SOURCE=HED`; a chapter's is in its `TOC/TOCHD` | `HEAD` |
| section | `SECTNO` + `SUBJECT` | `HEAD` (`§ 1.100 Content of this title.`) |
| tables | `GPOTABLE` (`BOXHD/CHED`, `ROW/ENT`) | HTML `TABLE` |

A dialect declares, and the code only applies:

- **units**, by tag or by an attribute (`DIV5 TYPE="PART"`), and the attribute a unit
  states its number in (`N`);
- **headings** per unit: a descendant path (`TOC/TOCHD/HD`), joined children
  (`SECTNO` + `SUBJECT`, or `[Reserved]`), or the first child past print furniture;
- **labels** per unit: the regexes that split a heading into `marker`, `name`, `number`;
- **body roles**: furniture skipped by name, graphics counted, leaves, containers and the
  `kind` each gives (`AUTH` → `authority`, `EFFDNOTP` → `effective_date_note`), inline
  markup, italic codes (`E T="03"`), the page element (`PRTPAGE@P`), table layouts;
- **quoted** containers (`REVTXT`): a unit inside is text, not structure;
- **front matter**: the outermost unit, the edition date (`FMTR//REVISED`,
  `VOLUME@AMDDATE`), and title 3's composed heading.

The loader replaces each dialect name with the file's contents before the profile is
validated, so `structure_hash` covers every rule and a corrected dialect re-extracts. The
loader refuses a dialect whose units the profile does not declare, an element given two
roles, or a dialect file that is missing.

- **Strict parse.** A malformed file fails the document. The notebook parsed with
  `recover=True`, which drops what it cannot read and reports success.
- **Nothing dropped silently.** Elements are skipped *by name*. An element the dialect does
  not name is still read for its text and counted in `extraction.warnings`, so a schema
  change shows on the first run.
- **Reserved units** (`[Reserved]`) carry no text and produce no block; they are counted.
- **Pages.** `PRTPAGE` sets the page of every block after it, so a CFR chunk cites the
  printed page. The eCFR has none.
- **Italics are kept** (`attrs.emphasis`). The CFR sets a defined term in italics, and the
  text alone cannot tell it apart from a paragraph heading.
- **Appendices are content.** The notebook skipped them; there are 4,095 in the eCFR.
- ✗ Title 3's presidential documents (`PROC`, `EXECORD` outside `TITLE`) are counted and
  not yet extracted.

**Parity, and what the move found.** On 8 sample volumes (title 3, an eCFR title, and
title 40 vol. 3 with 19,587 blocks), the dialect-driven extractor reproduced the Python one
block for block, except where the YAML deliberately corrected it:

- `EFFDNOTP` was an unknown element read with no kind. It holds **1,339** effective-date
  notes in the snapshot, against 486 in `EFFDNOT`, so 73% of the notes had no kind.
- `REVTXT` quotes the section an amendment will put in force. The Python extractor read
  the quoted `SECTION` as structure, and opened a second `§ 52.220` inside the note of
  `§ 52.41`: one chunk cited a section it was not. 342 `REVTXT` in the snapshot.

**A quote left open.** GPO sometimes never closes a `REVTXT`, so it runs on to the end of
the volume. In 40 CFR vol. 33 (2024), the quote of subpart B of part 702 holds 2,539
sections in 13 parts. In 34 CFR vol. 3 (2025), the quote of part 656 holds parts 657 to 679.
Read as quoted, all of them vanished into one note.

A quote opens with the unit it revises, and two things end it:
- a unit shallower than that one (a PART after a quoted SUBPART);
- when the quote revises a unit that encloses its note, another unit of that type with
  another number (PART 657 in the quote of part 656).

From there the markup is read as structure, and `extraction.params.quotes_ended` counts
each case. Inside a quote, nested containers keep the quote's kind (`revised_text`), so an
`AUTH` quoted in revised text is not the part's authority.

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

✗ Built with `markup` 1.0, which kept a table whole: the 219 chunks over 8,192 tokens are
mostly tables. `markup` 1.1 (`SOURCE_CA_LAWS.md` §6) splits them by rows; the next CFR build
takes it, and the published dataset is the 1.0 build until then.

The section count is independent of the extractor: every `SECTION` and `APPENDIX` element
outside a table of contents, counted with `ElementTree` per volume, against the extractor's
own count. Spot checks cite as a lawyer would: `40 CFR 52.21` (EPA, 39 parts, p. 15 of 2024
vol 3), `21 CFR 101.9` (FDA), `26 CFR 1.61-1` (IRS), `29 CFR 1910.134` (OSHA),
`45 CFR 164.502` (HHS), `12 CFR 1026.18` (CFPB).

**Rebuilt with us_cfr@1.1** (`xml` + `gpo_cfr@1.0`), in a fresh workspace:

| | |
|---|---|
| volumes | 246 extracted, 0 failed, 248 s |
| sections | 231,037 read as structure; 368 more are quoted amendment text, read in their note |
| appendices | 4,152 read; 26 quoted |
| quotes left open by GPO, ended at a unit they cannot hold | 5 |
| chunks | 366,005 (342,198 text, 23,807 tables), 140 s; 100.0% with identifier |
| signals | 194 s; coverage per column in §5b |

Against the first build, every identifier it cited and this one does not was checked
against the XML: 35 exist only in quoted text, 8 were subpart chunks made of note text the
quote bug had stranded, and one (`48 CFR 312.202(d)`) is now `48 CFR 312.202`. The 10,170
identifiers gained are the bare-numbered sections, now cited by number.

Not yet embedded or scored: there is no CFR question set.

## 5b. Signals (`ravel signals <corpus>`)

Six columns read from finished chunks, declared in the profile's `signals` section and
applied by `src/enrich/signals.py`, which knows no CFR word. The output is a sidecar
keyed by `chunk_id`, outside the chunk cache key: a corrected pattern re-runs this CPU pass
and touches no chunk and no vector (`ENRICHMENT.md` §2).

| column | read from |
|---|---|
| `cfr_references`, `usc_references`, `public_laws`, `fr_references` | the body; a bare `§ 52.21` is read in the chunk's own title |
| `authority`, `authority_usc`, `source` | the part's `AUTH` and `SOURCE`, for every chunk of the part |
| `amendments`, `amendments_latest` | the section's `CITA`: every dated FR citation, and the latest date |
| `effective_date_note` | the section's `EFFDNOT` / `EFFDNOTP` |
| `defined_terms` | `X means ...`, kept only when `X` is set in italics |
| `paragraph_first`, `paragraph_last` | the designations a chunk runs from and to, `(b)(1)` to `(b)(23)` |

Coverage on the 1.1 build (366,005 chunks): `cfr_references` 42.4%, `usc_references` 13.9%,
`public_laws` 2.1%, `fr_references` 32.4%, `authority` 99.8%, `authority_usc` 98.0%,
`source` 97.1%, `amendments` 57.0%, `effective_date_note` 1.9%, `defined_terms` 4.3%,
`paragraph_*` 64.5%. Of the paragraph ranges, 1,141 (0.5%) stack two roman numerals, the
signature of a mis-nested outline. That is where a section's drafting departs from
`(a)(1)(i)(A)`: older OSHA text sets `( a )` in italics below `(i)`.

`source` is `nearest`: of the part's SOURCE notes, the one printed in the deepest unit
that encloses the chunk, so a subpart with its own source is cited with it. Heading paths
are compared with `(CONTINUED)` stripped (`signals.heading_strip`), so a part that runs over
volumes still matches.

The outline reads `(i)` by what is in force: it continues the innermost sequence, else
opens a deeper level, else continues an outer one. Where `(i)` under `(h)(1)` can be either
a numeral or the next letter, the next paragraph's marker decides.

## 6. Size

eCFR on 2026-11-19 (structure API): 227,264 sections, 7,077 reserved, 4,095 appendices,
824 MB of XML; the largest title is 40 at 162 MB. A title-sized eCFR file is one document,
which costs memory in proportion; the CFR's volumes are 2–7 MB each.
