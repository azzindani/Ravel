# SOURCE_DE_GII.md — Ravel

Germany's federal laws and ordinances in force, in German: corpus `de_gii`, profile
`de_gii@1.0`, dialect `de_gii@1.0`. `SOURCES_NEXT.md` #5. Read by the generic `xml`
extractor; one generic rule was added to it (§3), the rest is YAML. Built by the process in
`ADDING_A_SOURCE.md`.

## 1. The source

| | |
|---|---|
| publisher | Bundesministerium der Justiz with juris GmbH, gesetze-im-internet.de |
| index | `gii-toc.xml`: one `<item><title/><link>…/<law>/xml.zip</link></item>` per law, 6,150 on 2026-11-22 |
| format | one `xml.zip` per law, holding the law's XML in the `gii-norm` DTD and, for some, its images |
| licence | free to reuse (the texts are official works, § 5 UrhG) |
| status | ✗ not the official text. The official text is the Bundesgesetzblatt |
| updates | in place: `bgb/xml.zip` is always the current BGB, with no version in its name |

## 2. Fetch (`kind: listing`)

    ravel sources fetch de_gii

The index is read and every `<link>` matched by `fetch.links` is downloaded. **The fetch
day is the edition**: the publisher replaces a file in place and the fetcher never
overwrites, so each snapshot is a folder of its own, `<yyyymmdd>/<law>/xml.zip`, and an
update fetches a new folder beside it (`ravel update`, `DATABASE.md` §4b).

- Each archive is unpacked beside itself, `<yyyymmdd>/<law>/xml/<file>.xml`; only the
  members `fetch.paths` names (`*.xml`) are taken, so a law's images are not sources.
- **A law is cited as the archive it was read from**: `http://www.gesetze-im-internet.de/bgb/xml.zip#BJNR001950896.xml`,
  through `url_map.jsonl`. The index's links are `http` and answer `302` to `https`; they
  are cited as the index prints them.
- `delay_s: 0.2`, `concurrency: 4`.

## 3. Extract (`xml`, dialect `de_gii`)

**The file is flat.** `<dokumente>` holds one `<norm>` per piece of the law, in order, and
nothing nests:

| norm | states | read as |
|---|---|---|
| the first | `jurabk` / `amtabk` (`BGB`), `langue` (`Bürgerliches Gesetzbuch`), `ausfertigung-datum` | the unit `law`; its footnotes are the law's text |
| an outline norm | `gliederungseinheit`: `gliederungskennzahl` (`010010`), `gliederungsbez` (`Abschnitt 1`), `gliederungstitel` (`Personen`) | a **flat** heading `division1`..`division6` |
| any other | `enbez` (`§ 7`), `titel` (`Wohnsitz; Begründung und Aufhebung`), `textdaten` | the unit `article` |

- **Depth is the outline number's length**, three digits a level: `010` > `010010` >
  `010010010`. `units.flat` reads it with `path:` and `match: regex` (`'^\S{6}$':
  division2`), and a flat heading runs to the next one as deep or shallower, as Canada's
  `Heading level` does. On 1,111 laws: 1,511 / 956 / 833 / 248 / 102 / 41 outline norms at
  lengths 3 to 18; no other length.
- **The marker is stated** (`units.attrs.article.marker: [metadaten/enbez]`): the new
  generic rule. When a unit states its marker and the heading opens with it, the rest is
  the name. `enbez` takes 75 shapes on those laws, `Anhang 1 zu Nummer 3` and `(XXXX) Art 5
  bis 7` among them, and no pattern can tell where one ends and its caption begins.
- `(XXXX)` marks a provision with no number. `(XXXX)` alone (a notice's one text) has no
  number and cites as the law; `(XXXX) §§ 16a bis 16c` is `§§ 16a bis 16c`.
- A provision titled `(weggefallen)` with no text is repealed: counted in `reserved`, not
  emitted.
- `metadaten` and `TOC` (the Inhaltsübersicht's table) are skipped; `fussnoten` are read as
  kind `footnote`; `DL`/`DT`/`DD` lists read `1. am Ort der Arbeit`; tables are CALS.

## 4. Identity

| field | value |
|---|---|
| `identifier` | `§ 7 BGB` · `Art 3 GG` · `Anlage 2 BKleingG` · `Eingangsformel ÄApprO 2002` · `BGB` |
| `regulation_number` | the abbreviation, `amtabk` else `jurabk` |
| `about` | `langue`, the full title |
| `year` | of the `Ausfertigungsdatum`, the day it was signed, ✗ the day it came into force |

## 5. Signals

| column | read from |
|---|---|
| `gazette_references` | `BGBl. I S. 1004`, `BGBl. I 2002, 2420`, `BGBl. 2023 I Nr. 411` |
| `law_references` | `§ 77 Absatz 3 SGB IX`, `§ 41 AO`: a provision of a law named by its abbreviation |

## 6. The first build

✗ Not built yet: the fetch of 2026-11-22 is running. Seven laws extracted by hand (BGB,
ÄApprO 2002, ÜAnlG, ÜblG 1, 1-DM-GoldmünzG, AARKZustAnO, a SGB IX notice) gave no
unknown element and the citations above; BGB is 2,515 chunks.
