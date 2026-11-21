# SOURCE_CA_LAWS.md — Ravel

Canada's consolidated federal Acts and regulations, in English: corpus `ca_laws`, profile
`ca_laws@1.0`, dialect `justice_ca@1.0`. The second source after the CFR
(`SOURCES_NEXT.md` #1), and the first one read with **no code of its own**: the generic `xml`
extractor gained three generic rules (§3), and the rest is YAML.

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

## 6. The first build

✗ Not yet run on the full snapshot. The 45 sample files (17 Acts, 28 regulations, including the
Criminal Code, the Income Tax Act and the Food and Drug Regulations) extract with no
unknown element. They give 8,383 chunks, every one with an identifier.
