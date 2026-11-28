# SOURCE_EU_LEX.md — Ravel

> The EU's legal acts in force, in English, from the Publications Office's Cellar (the
> store behind EUR-Lex). Corpus `eu_lex` (`corpora/eu_lex.yaml`), profile `eu_lex@1.0`,
> dialect `eu_formex@1.0`. Fetched with no account: the Cellar's SPARQL endpoint lists the
> files, and each is a public URL.

## 1. The source

- **What:** every act in force in CELEX sector 3 (regulations, directives, decisions and
  the rest of secondary law), as its newest consolidation where the Publications Office
  made one (sector 0), else as adopted. Corrigenda (`R(`) are left out.
- **Format:** Formex 4, the Publications Office's XML. One package (`<manifestation>/zip`)
  per act: the act's file, one file per annex or attached text, images, and a `.doc.xml`
  descriptor.
- **Licence:** reuse authorised by Commission Decision 2011/833/EU. Only the Official
  Journal is authentic.
- **Not covered:** 17,973 acts in force have no English Formex, only XHTML or PDF. A later
  XHTML dialect would read them.

## 2. Fetch (`kind: sparql`)

Two queries live in the corpus YAML, and the fetcher pages them from outside (a subquery
under `LIMIT`/`OFFSET`, since Virtuoso refuses an `ORDER BY` past 10,000 rows, SR353):

1. **Acts in force.** These name the set. An act with no English as adopted is still named
   (`OPTIONAL`, empty URL). This matters for 4,296 acts whose only English Formex is a
   consolidation, among them `1958R0001`, the languages regulation.
2. **Consolidations.** These can only give a named act a newer text, never add an act. A
   repealed act's consolidations carry no in-force flag of their own.

Editions are dated `00000000` for "as adopted", so any consolidation is newer. Each package
is unpacked beside its zip, and a document is cited as its member:
`<cellar>/<id>/zip#<file>.xml`. The listing named 29,396 packages (`work/eu_lex_fetch.log`).

## 3. Extract (`xml`, dialect `eu_formex`)

**The descriptor is the document.** `documents: ["*.doc.xml"]`. Its `DOC.MAIN.PUB` and
`DOC.SUB.PUB` name the act's file and each annex's, in reading order. Each `REF.PHYS` is
followed as a link (`links`), so the act and its annexes are walked as one tree.

This choice came from the packages themselves. In a sample of 887, 216 held annexes that
the act's own `INCLUSIONS` never names, so reading the act alone would lose them. Read as
documents of their own, an annex could not be cited: the annex file states no act.

- **Units.** The `DOC` is the `act`. Nested `DIVISION`s become `division1..5` (TITLE >
  CHAPTER > Section). `ARTICLE` is `article`; `ANNEX` and `CONS.ANNEX` are `annex`. An
  annex inside an annex is an `appendix`. An annex's own articles are `annex_article`,
  never the act's. `AGR` (an agreement the act concludes) and `GENERAL` (an attached
  resolution) are units of their own, since their articles start again at 1.
- **A consolidation** the descriptor links (`CONS.ACT`) is an unheaded `consolidation`
  unit inside the act. Six of 1,398 packages swap the names, so their `.doc.xml` *is* the
  `CONS.ACT`. It is then read as the act, from its own front matter.
- **Dropped by path** (`document.drop`, new):
  - the descriptor's `DATE`, because Formex's `DATE` is also inline text
    (`of <DATE>20 May 1996</DATE>`);
  - the act's own `TITLE`, which repeats the descriptor's.
- **Tables:** the `formex` layout (`TBL/CORPUS/ROW/CELL`, `COL`, `COLSPAN`, `ROWSPAN`,
  `BLK` row groups). Notes (`GR.NOTES`) follow the table.
- **Footnotes** are printed where they are called, read in place as `^...`.
- **Kinds:** `preamble`, `recital`, `final`, `signature`, `quoted_text` (`QUOT.S`,
  amending text), `annotation`, `corrigendum`.

## 4. Identity

`Regulation (EU) 2016/679, Article 6` · `Decision 93/626/EEC, Annex II, Article 1` ·
`Directive 70/157/EEC, Annex I`.

- **Kind:** the first kind of act the title names (`Common Position`, `Regulation`, ...).
  The lowercase form is accepted as printed. The fallback is the legal value (`REG`).
- **Number:** the first source that states one, in this order:
  1. the number after the kind, in the title: `(EC) No 1907/2006`, `2006/112/EC`;
  2. the number older titles print last in brackets: `(70/157/EEC)`;
  3. the act's `NO.DOC`, composed from its parts (`+{YEAR}/{NO.CURRENT}/{COM}`, new).
     Directives and decisions put the year first and regulations the number first. Years
     before 1999 print with two digits;
  4. `Regulation No 1` style titles;
  5. the CELEX number;
  6. the Journal reference the descriptor prints (`1961/P 65/01`), for an act that states
     neither.
- **CELEX** is kept as `celex`. ! A descriptor may state none: 183 of 900 acts as adopted
  in one sample, published 2004–2005.

On 612 packages read from the fetch: 603 cite by number and 9 by CELEX. That gave 9,706
chunks, median 189 tokens, all with an identifier (60 lacked one before source 6). No package failed. One package of
1,621 keeps its files in folders the descriptor does not name (`0005/05/DOC_2/`). It fails
by name, which is why `failure_tolerance` is 0.0005.

## 5. Status

Dialect, profile and tests (`tests/test_eu_formex.py`, `tests/test_eu_lex.py`) are built.
The full fetch, extract, chunk and embed are not, and no EU eval set exists yet: every
chunk size here is chosen, ✗ measured.
