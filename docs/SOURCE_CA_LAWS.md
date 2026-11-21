# SOURCE_CA_LAWS.md — Ravel

Canada's consolidated federal Acts and regulations, in English: corpus `ca_laws`, profile
`ca_laws@1.0`, dialect `justice_ca@1.0`. The second source after the CFR
(`SOURCES_NEXT.md` #1), and the first one read with **no code of its own**: the generic `xml`
extractor gained three generic rules (§3), and the rest is YAML. Built by the process in
`ADDING_A_SOURCE.md`.

## 1. The source

| | |
|---|---|
| publisher | Department of Justice Canada, the data behind laws-lois.justice.gc.ca |
| repository | `github.com/justicecanada/laws-lois-xml`, updated every two weeks |
| licence | Open Government Licence – Canada (`LICENSE.md` in the repository) |
| status | ✗ not the official version. A consolidation is official only as the Legislation Revision and Consolidation Act provides |
| content | `eng/acts/*.xml` 973 Acts (142 MB), `eng/regulations/*.xml` 4,901 regulations (195 MB), at commit `855cdfc` (2026-11-16) |
| ✗ not taken | `fra/` (the French versions, the same instruments), `annual-statutes-lois-annuelles/`, `lookup/` |

## 2. Fetch (`kind: github`)

    ravel sources fetch ca_laws

The repository API lists the tree at one commit. Every file under `fetch.paths` is
downloaded from `raw.githubusercontent.com/<owner>/<repo>/<commit>/<path>` and saved at
`<commit>/<path>` below `sources.root`. **The commit is the edition**: a file cited at a
branch would later resolve to text it was not read from. A newer commit is a new folder
beside the old one, and `current.txt` names the files of the newest.

- `edition: latest` takes the default branch's head; a sha pins an older one.
- A truncated tree is refused, because a partial listing is a corpus that looks complete.
- Sizes come from the tree and are checked on download, as govinfo's are.
- `concurrency: 8`. The raw host is a CDN serving static files; govinfo and the eCFR stay at 1.

## 3. Extract (`xml`, dialect `justice_ca`)

One file is one instrument: `<Statute>` or `<Regulation>`, both unit `instrument`.

| unit | element | heading | number |
|---|---|---|---|
| `instrument` | `Statute`, `Regulation` | `ShortTitle`, else `LongTitle` | `Chapter/ConsolidatedNumber` (`A-1`) or `InstrumentNumber` (`SOR/2018-108`) |
| `heading1`..`heading6` | `Heading level="n"` (**flat**) | `Label` + `TitleText` (`PART 1 Access to Government Records`) | from the label (`1`) |
| `section` | `Section` | `Label` + `MarginalNote` (`2 Purpose of Act`) | the label |
| `schedule` | `Schedule` | `ScheduleFormHeading` (`SCHEDULE I (Section 2) ...`) | from the heading |

Three rules were added to the dialect model, each generic:

- **Flat units** (`units.flat`). A Part or Division heading is printed *beside* the sections
  it heads, with a `level`. It opens its unit, which runs to the next heading of the same or
  a shallower level, or to the end of the element holding it. A Part does not run into the
  Schedules, because the `Body` holding it ends first.
- **Unit attributes** (`units.attrs`): values read from paths relative to the unit's own
  element, such as the instrument's number, its `regulation-type`, and a section's
  `lims:lastAmendedDate`. `=Act` is a literal, the last resort of a list: an Act states no
  type.
- **CALS tables** (`tables.elements: {table: cals}`): `tgroup`/`colspec`/`entry`, with
  `namest`/`nameend` and `morerows` spans.

`join` now joins in slot order, not document order: the marginal note is printed before the
section number, and the heading reads `2 Purpose of Act`. On 4 CFR volumes (59,499 blocks)
the output is byte-identical to before.

A provision's `Label` (`(1)`, `(a)`, `(ii)`) opens the text that follows it, so a paragraph
reads `(2) In furtherance of that purpose,` and the outline signal reads it as the CFR's.

**Related provisions are quoted.** `BillPiece`, `RegulationPiece` and `RelatedOrNotInForce`
print provisions of other enactments after the Act, such as transitional rules and
amendments not yet in force. Their sections carry the amending Act's numbers: `5` of
1995, c. 29, printed after section 49 of this Act. Read as structure, they would cite as a
second section 5. They are read as text of kind `related_provisions` instead. `AmendedText`,
`ReadAsText` and `QuotedText` are quoted the same way.

## 4. Identity

| field | value |
|---|---|
| `identifier` | `A-1, s. 2` · `SOR/2018-108, s. 5` · `C.R.C., c. 870, s. B.01.001` · `A-1, Schedule I` |
| `regulation_type` | `Act`, `SOR`, `C.R.C.`, `SI` |
| `regulation_number` | `A-1`, `SOR/2018-108` |
| `about` | the short or long title |
| `effective_date` | `lims:current-date`, the date the consolidation is current to |

## 5. Signals

| column | read from |
|---|---|
| `statute_references` | `R.S.C., 1985, c. A-1`, `S.C. 2019, c. 18` in the body |
| `regulation_references` | `SOR/2016-12`, `SI/2004-48`, `C.R.C., c. 870` in the body |
| `authority` | `EnablingAuthority`: the Acts a regulation is made under, for each of its chunks |
| `amendments` | the section's `HistoricalNote`: `R.S., 1985, c. A-1, s. 2; 2019, c. 18, s. 2` |
| `recent_amendments`, `recent_amendments_latest` | the instrument's `RecentAmendments`, with dates |
| `defined_terms` | `X means ...` where `X` is a `DefinedTermEn` |
| `paragraph_first`, `paragraph_last` | `(1)` to `(2)(a)(i)`: subsection, paragraph, subparagraph, clause |

## 6. The first build (commit `855cdfc`, current to 2026-10-19)

```bash
ravel sources fetch ca_laws                         # 5,874 files, 307 MB, 888 s, 0 failed
W=work/ca_laws/2026-11-16
ravel extract ca_laws -w $W                         # 5,874 extracted, 0 failed, 107 s
ravel chunk   ca_laws -w $W --enforce               # 166,569 chunks, 43 s
ravel signals ca_laws -w $W                         # 39 s
```

| | |
|---|---|
| instruments | 5,874: 973 Acts, 4,901 regulations (`SOR` 81,874 chunks, `Act` 70,836, `SI` 10,422, `DATED` 6) |
| units | 122,123 sections, 5,071 schedules; headings 29,558 / 22,366 / 9,450 / 3,396 / 223 at levels 1–5 |
| tables | 4,189 |
| chunks | 166,569, **100% with an identifier**; 7,620 of them tables |
| tokens | 39.2M; median 108, p99 1,030, max 12,800; 1 chunk over 8,192 |
| unknown elements | 0. `quotes_ended` 0 |

**What the first full extract found**, each now in the dialect:

- 20 elements never seen in the 45 samples, about 1,100 occurrences. `Group1-Part`,
  `Group2-Division` and `Group3-Subdivision` wrap a Part in an element instead of printing a
  flat heading (H-5.6). `AmendedContent` and `ReadAsContent` are quotes. The rest are MathML,
  `ConventionAgreementTreaty` (a treaty's text in a schedule), `ProvisionHeading`,
  `OtherAuthority`, and `PageBreak` / `Separator` / `StatuteYear` (furniture).
- **16 quotes ended early.** A related provision that quotes a whole amending schedule
  (N-3.4, SOR-2002-227, SOR-96-332) met the runaway-quote rule written for GPO, which ends a
  quote at a shallower unit. Its sections then read as this Act's: 26 repeated section
  numbers in 3 instruments. Justice Canada closes every quote, so the dialect says
  `quotes_run_on: false`.
- **Tables of 245,616 tokens.** Schedules of boundary coordinates (SOR/2013-202) and species
  lists were one chunk each: 53 of the 54 chunks over 8,192 tokens. `markup` 1.1 splits a
  table longer than `max_tokens` by rows and repeats its header.
- `recent_amendments_latest` read no ISO date. The signals' date reader now takes
  `2026-10-01` as well as `Oct. 1, 2026`.

One repeated section number remains, and it is the text's: N-16.62 enacts two sections
`15.1`.

| signal | coverage | why |
|---|---|---|
| `authority` | 57.9% | regulations only: an Act is made under none |
| `recent_amendments` (+ `_latest`) | 56.6% | instruments amended recently enough to list |
| `amendments` | 39.8% | sections with a historical note |
| `paragraph_first` / `_last` | 41.6% | sections with labelled subsections; a one-sentence section has none |
| `regulation_references` | 26.6% | |
| `statute_references` | 5.4% | the text names Acts by title more often than by chapter |
| `defined_terms` | 3.2% | definitions sit in interpretation sections (CFR: 4.3%) |
