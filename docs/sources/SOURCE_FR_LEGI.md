# SOURCE_FR_LEGI.md — Ravel

France's consolidated codes, laws and decrees in force, in French: corpus `fr_legi`, profile
`fr_legi@1.0`, dialect `legi@1.0`. `SOURCES_NEXT.md` #3. Read by the generic `xml`
extractor, which learned to assemble one document from many files for it (§3); the rest is
YAML. Built by the process in `ADDING_A_SOURCE.md`.

## 1. The source

| | |
|---|---|
| publisher | DILA (Direction de l'information légale et administrative), LEGI open data |
| index | `https://echanges.dila.gouv.fr/OPENDATA/LEGI/`: one full dump, `Freemium_legi_global_<yyyymmdd-hhmmss>.tar.gz`, then a daily increment per day |
| format | one folder per text, many XML files: `texte/struct/<LEGITEXT>.xml` (its table of contents), `texte/version/<LEGITEXT>.xml` (its title), `section_ta/…/LEGISCTA*.xml` (a section's table of contents), `article/…/LEGIARTI*.xml` (one version of one article) |
| licence | Licence Ouverte / Open Licence 2.0 |
| status | ✗ not the official text. Légifrance is the official reading; the Journal officiel is the official publication |
| updates | a new full dump now and then, and a daily increment after it; a corpus is one full dump |

## 2. Fetch (`kind: archive_index`)

    ravel sources fetch fr_legi

The index is read and the newest full dump is downloaded (`edition: latest`):
`Freemium_legi_global_20261108-140000.tar.gz`, 1,260,809,118 bytes. Only the members under
`legi/global/code_et_TNC_en_vigueur/` are taken: the codes and the uncodified texts
(`TNC`) in force. `code_et_TNC_non_vigueur`, the repealed ones, is not.

- A text's **documents are its `texte/struct/*.xml`**: 122,944 in this dump
  (`sources/current.txt`). Its sections, articles and title page are read through the
  struct's links, never listed as documents.
- **A file is cited as its member of the dump**, the only URL its bytes were read from:
  `https://echanges.dila.gouv.fr/OPENDATA/LEGI/Freemium_legi_global_20261108-140000.tar.gz#legi/global/…/texte/struct/LEGITEXT000006070721.xml`.
- The increments are not applied: they carry their own date as the first path element
  (`20261120-222218/`), which the corpus's glob does not match.

## 3. Extract (`xml`, dialect `legi`)

**A text is a folder of files, and the struct links them.** The dialect declares the links
(`links.rules`), and the extractor puts each linked file inside its link element, depth
first, before it reads anything:

| link | in | followed when | file |
|---|---|---|---|
| `LIEN_TXT` | `VERSION` | always | `texte/version/{id}.xml`: `TITRE`, `TITREFULL` |
| `LIEN_SECTION_TA` | `STRUCT`, `STRUCTURE_TA` | `etat="VIGUEUR"` | `section_ta{url}` |
| `LIEN_ART` | `STRUCT`, `STRUCTURE_TA` | `etat="VIGUEUR"` | `article/LEGI/ARTI/00/00/30/92/71/LEGIARTI000030927196.xml`, from the id |

- **Only the version in force is read.** A struct links every version of every article, each
  with its `etat` (`VIGUEUR`, `MODIFIE`, `ABROGE` …); only `VIGUEUR` is followed. A linked
  file that is not in the folder is counted `missing` and shows as a gap, never guessed at.
- **A document is identified by every file it read**, the struct and each member by its
  path and digest (`assembly:1`): LEGI amends an article in the article's own file, and an
  id from the struct alone would hide the edit from `bundle delta`.
- A section's depth is stated on its link, `niv="3"`, ✗ by its element: `section1` ..
  `section12`. Its heading is `TITRE_TA`, `Section 1 : Définitions`, read as the marker
  `Section 1` and the name `Définitions`. An article's heading is its `NUM`, `L233-3`.
- Article text is HTML inside `CONTENU`; `<br/>` ends a paragraph (`text.split_at`), and
  `<table>` is read as a table. `NOTA` is read as kind `nota`. `VISAS`, `SIGNATAIRES`, the
  metadata and the article's own context and versions are skipped.

**A text with nothing in force is empty, ✗ failed** (`links.empty_when_nothing_kept`): no
text, and every link it kept was found. 1,380 texts in this dump state that every article
is out of force. The first extract counted 101 more as failures (§6).

## 4. Identity

| field | value |
|---|---|
| `identifier` | `Code civil, art. 1240` · `Code de commerce, art. L233-3` · `Constitution du 4 octobre 1958, art. 1` · `Arrêté du 25 avril 1990, art. 1` · `Décret n°95-1077 du 28 septembre 1995, Annexe` · `Code des impositions sur les biens et services, PARTIE RÉGLEMENTAIRE, Livre IV, Titre VII, Chapitre Ier, Section 6` · `Arrêté du 23 février 2006` |
| `regulation_type` | `NATURE`: `CODE`, `LOI`, `DECRET`, `ORDONNANCE`, `ARRETE` … |
| `regulation_number` | the text's LEGI id, `LEGITEXT000006070721`, what Légifrance names it by; a law's own number (`83-629`) is in its title |
| `about` | `TITRE`, the title as cited |
| `year`, `effective_date` | of `DERNIERE_MODIFICATION`, the text's last modification, ✗ the day it was signed (`DATE_TEXTE`, in the title) |

- **Text a section holds outside any numbered article** (an `Annexe`, an article with no
  `NUM`) cites as the sections over it, `…, Annexe, Section II`, ✗ as the whole text. The
  first build cited all 52,005 of them as the text alone (§6).
- **The text's own text** (a text with no articles, a preamble) cites as the text: 935 chunks.

## 5. Signals

| column | read from |
|---|---|
| `article_references` | `article L. 233-3`, `art. R123-220`: an article cited by number, as `L233-3` |
| `law_references` | a law, ordinance or decree cited by number: `Loi n° 83-663`, `Décret n° 2003-1014` |
| `eu_references` | an EU regulation, directive or decision cited by number: `règlement 458/2011`, `DÉCISION 2006/751` (as the text writes the word) |
| `nota` | the text of the `NOTA` blocks under the chunk's citation |

## 6. The first build (2026-11-22)

```bash
ravel sources fetch fr_legi                        # Freemium_legi_global_20261108-140000.tar.gz: 122,944 texts in force
W=work/fr_legi/20261108
ravel extract fr_legi -w $W                        # 121,463 extracted, 1,380 empty, 101 failed (0.1%)
ravel chunk   fr_legi -w $W --enforce              # 841,034 chunks, 100% with an identifier
ravel signals fr_legi -w $W
ravel sparse  fr_legi -w $W
```

| | |
|---|---|
| texts | 121,463: 71,204 arrêtés, 45,859 décrets, 2,751 lois, 1,334 ordonnances, 80 lois organiques, 78 codes, and 157 of other types (one states none) |
| chunks | 841,034; median 120 tokens; 100% with an identifier and a locator |
| by unit | 788,094 articles, 52,005 held by a section outside any article, 935 a text's own |
| tables | 79,443 chunks |
| split | 85,714 chunks are one part of several (max 1,024 tokens) |
| `article_references` | 171,006 chunks (20.3%) |
| `law_references` | 91,050 chunks (10.8%) |
| `eu_references` | 12,700 chunks (1.5%) |
| `nota` | 112,947 chunks (13.4%) |

**The 101 failures.** Re-read with the fixed extractor (commit 87f1d55), from their 86
folders taken out of the dump:

- **41 hold nothing in force**, and are empty. Each keeps a section still `VIGUEUR` whose
  articles are all superseded, and links its JORF version's title page, which the dump
  never holds (loi 83-629: `Titre Ier` `VIGUEUR`, every article it links `MODIFIE`, the last
  ending 2003-03-19, the other titles `ABROGE` since 2012-05-01). The empty rule asked that nothing be kept; it now asks
  that nothing kept be lost.
- **60 still fail, and the gap is the dump's.** Every article each links as in force is
  absent from its folder. The three searched for are in the same `JORFTEXT` folder under
  `code_et_TNC_non_vigueur`, the repealed tree, which this corpus does not fetch
  (`LEGIARTI000043634108`, `…030076844`, `…021116300`). 60 of 122,944 texts (0.05%), under
  the corpus's tolerance of 0.1%. ✗ Not read from the repealed tree: a link that reaches
  into it would read texts the publisher files as not in force.

**Rebuilt the same day, twice.**

1. **Annexes cited by their section.** 48,311 chunks of `section1`, and 3,694 deeper, cited
   as the whole text (`Arrêté du 30 juin 2004`), so one citation named an annex and the
   text's preamble alike. The profile now cites them by the sections over them
   (`identity.markup.identifier.section1` .. `section8`).
2. **The re-chunk appended instead of replacing.** The chunk directory was keyed by the
   canonical generation, the chunker and its config, ✗ the profile, while each document's
   key folds the profile's hash in. The corrected profile found no key done, chunked all
   121,463 texts again, and wrote 841,034 rows beside the old 841,034 in one directory;
   `signals` then failed on the doubled order. The directory now folds in the profile's
   hash (commit 83c27a3), and the rebuild is generation `72c85c14fc5a`.

```bash
ravel chunk   fr_legi -w $W --force               # into the new generation, 841,034 chunks
ravel signals fr_legi -w $W
ravel sparse  fr_legi -w $W
```
