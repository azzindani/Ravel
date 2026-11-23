---
license: other
license_name: licence-ouverte-2.0
license_link: https://www.etalab.gouv.fr/licence-ouverte-open-licence/
language:
  - fr
pretty_name: French consolidated codes, laws and decrees in force (DILA LEGI, dump of 2026-11-08)
size_categories:
  - 100K<n<1M
task_categories:
  - text-retrieval
  - feature-extraction
  - question-answering
tags:
  - legal
  - regulations
  - legislation
  - france
  - french
  - rag
  - embeddings
configs:
  - config_name: default
    data_files:
      - split: train
        path: data/train-*.parquet
---

# French codes, laws and decrees in force (DILA LEGI), dump of 2026-11-08

Every French code and every uncodified law, ordinance, decree and order **in force**, in
French, as DILA publishes them in LEGI, the consolidated legislation behind Légifrance. Only
the version of each article in force is read. The data is split into retrieval-ready
chunks. Each chunk carries its citation, the text it belongs to, its location in that text
and a link to the exact file it was read from, plus the articles, laws and EU acts it cites.

- **841,034 chunks** from **121,463 texts**: 71,204 arrêtés, 45,859 décrets, 2,751 lois,
  1,334 ordonnances, 80 lois organiques, 78 codes and 157 others (the Constitution,
  délibérations, décisions, avis …).
- By unit: 788,094 article chunks, 52,005 chunks of text a section holds outside any
  numbered article (annexes above all), and 935 chunks of a text's own text. 79,443 chunks
  are tables.
- Every chunk has a citation (`identifier`, e.g. `Code civil, art. 1240`).
- The snapshot is DILA's full dump `Freemium_legi_global_20261108-140000`: the texts it
  files as in force, 122,944, of which 1,380 hold no article in force and 60 could not be
  read (below).

**Not the official text.** Légifrance is the official reading of consolidated French law,
and the Journal officiel its official publication. Use them where a citation must hold.

## Columns

| column | example | meaning |
|---|---|---|
| `identifier` | `Code civil, art. 1240` · `Code de commerce, art. L233-3` · `Constitution du 4 octobre 1958, art. 1` · `Arrêté du 25 avril 1990, art. 1` · `Décret n°95-1077 du 28 septembre 1995, Annexe` · `Arrêté du 23 février 2006` | the citation of the article the chunk is from; a section's chunk cites the sections over it; a text's own text cites the text |
| `body` | `Tout fait quelconque de l'homme, qui cause à autrui un dommage, oblige celui par la faute duquel il est arrivé à le réparer.` | the text, verbatim from the XML, with the article's `Nota` |
| `part_n`, `part_of` | `1`, `3` | long articles are split on paragraph boundaries (max 1,024 tokens), long tables by rows; 85,714 chunks are one part of several |
| `heading_path` | `Code civil › Livre III : Des différentes manières dont on acquiert la propriété › Titre III : Des sources d'obligations › Sous-titre II : La responsabilité extracontractuelle › Chapitre Ier : La responsabilité extracontractuelle en général › 1240` | full location, joined by ` › ` |
| `regulation_type` | `CODE`, `LOI`, `DECRET`, `ORDONNANCE`, `ARRETE`, `LOI_ORGANIQUE`, `CONSTITUTION` … | the text's nature as the XML states it |
| `regulation_number` | `LEGITEXT000006070721` | the text's LEGI id, what Légifrance names it by; a law's own number is in its title |
| `about` | `Code civil` | the text's title as cited |
| `chapter` | `Livre III : Des différentes manières dont on acquiert la propriété` | the outermost section the chunk is in (55.1%) |
| `article` | `1240` | the article's number (93.7%) |
| `locator_section` | `1240` | the chunk's own heading |
| `year`, `effective_date` | `2026`, `2026-07-29` | the text's last modification (`DERNIERE_MODIFICATION`), ✗ the day it was signed, which is in its title |
| `source_url` | `https://echanges.dila.gouv.fr/OPENDATA/LEGI/Freemium_legi_global_20261108-140000.tar.gz#legi/global/code_et_TNC_en_vigueur/code_en_vigueur/LEGI/TEXT/00/00/06/07/07/LEGITEXT000006070721/texte/struct/LEGITEXT000006070721.xml` | the dump the text was read from and its table of contents in it |
| `source_sha256` | | SHA-256 of the text as read: its table of contents and every file it links, each by path and digest |
| `source_title` | `LEGITEXT000006070721` | the text's file |
| `attrs` | `{"unit": "article"}` or `{"unit": "section1", "kind": "table", "table_id": "t0000"}` | JSON; `unit` is `article`, `section1` .. `section8` or `text`; tables are their own chunks, rendered as Markdown |
| `id`, `doc_id`, `seq`, `block_ids`, `token_count` | | deterministic chunk id, source hash, order and blocks within the text, token count |
| `chunker`, `chunker_version`, `config_hash`, `profile` | `markup`, `1.1`, ..., `fr_legi@1.0` | how it was built |
| `embedding` | 1,024 x float16 | the chunk's dense vector, unit length (see Embeddings) |
| `enacting_body`, `locator_page` | | empty: not read from this source |

### How a text is read

A LEGI text is a folder of files: its table of contents links its sections and every
version of every article, each with its state. Only the sections and article versions
marked in force (`VIGUEUR`) are read. An annex, or an article without a number, is cited
by the sections over it (`…, Annexe, Section II`), so it never cites as the whole text.

### Signals

Read from each chunk's own text by rules declared in the profile. Lists are distinct, in
order of first appearance; an empty list means none was found.

| column | type | example | share of chunks |
|---|---|---|---|
| `article_references` | list | `["L138-1"]`, `["L3113-1"]`: articles cited by number (`article L. 233-3` becomes `L233-3`) | 20.3% |
| `law_references` | list | `["Loi n° 83-663"]`, `["Décret n° 2003-1014"]`: laws, ordinances and decrees cited by number | 10.8% |
| `eu_references` | list | `["règlement 458/2011"]`: EU regulations, directives and decisions cited by number | 1.5% |
| `nota` | text | the `Nota` under the chunk's citation: `Loi n° 2003-706 du 1er août 2003 article 46 V 1° et 2° : …` | 13.4% |

`sources/manifest.jsonl` lists every file used (path, URL, bytes, SHA-256), and
`sources/current.txt` is the snapshot list.

## Not included

- Texts the dump files as not in force (`code_et_TNC_non_vigueur`), and every version of an
  article that is not the one in force.
- The daily increments published after the dump.
- Visas and signatories, and images, which have no text.
- 60 texts in force whose every article in force the dump files under the repealed tree:
  0.05% of the texts.

## Embeddings

Every chunk carries its embedding. `embedding` is the vector of exactly this text:

```
Citation: {identifier}
Location: {heading_path}

{body}
```

| | |
|---|---|
| model | [`Qwen/Qwen3-Embedding-0.6B`](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) at revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` |
| pooling | last token, left padding, L2-normalised; max length 8,192 tokens (7 texts were longer and are embedded cut to it) |
| documents | embedded bare, no instruction |
| queries | prefix `Instruct: Given a legal or regulatory question, retrieve passages that answer it\nQuery: ` (the model card's format) |
| precision | float16 |
| served by | vLLM, checked against the `transformers` reference before the run (min cosine 1.00000) |

The vectors are unit length, so cosine similarity is a dot product. They are in the same
space as `Azzindani/CA_LAWS_2611`, `Azzindani/US_CFR_2611`, `Azzindani/JP_LAWS_2611` and
`Azzindani/DE_LAWS_2611`, so one index can hold all five.
Five random rows, re-embedded from the template above, reproduce their stored vector at
cosine 1.0000.

## How it was built

Built with [Ravel](https://github.com/azzindani/Ravel):

```
ravel sources fetch fr_legi                        # the newest full dump: 122,944 texts in force
ravel extract fr_legi -w work/fr_legi/20261108     # xml extractor + legi dialect: 121,463 extracted, 1,380 empty
ravel chunk   fr_legi -w work/fr_legi/20261108     # markup chunker, profile fr_legi@1.0
ravel signals fr_legi -w work/fr_legi/20261108     # article, law and EU references, nota
ravel embed   fr_legi -w work/fr_legi/20261108 --base-url http://localhost:8000/v1   # vLLM
ravel publish fr_legi -w work/fr_legi/20261108 --repo Azzindani/FR_LAWS_2611        # this dataset
```

There is no code specific to France. The links between a text's files, every element
name and the state that marks a version in force are declared in YAML
(`registry/dialects/legi@1.0.yaml`), and the citation forms and signal patterns are in the
profile (`registry/profiles/fr_legi@1.0.yaml`). The method is in Ravel's
`docs/sources/SOURCE_FR_LEGI.md`.

## License

Licence Ouverte / Open Licence 2.0 (Etalab). Source: DILA (Direction de l'information
légale et administrative), LEGI open data (echanges.dila.gouv.fr/OPENDATA/LEGI), dump
`Freemium_legi_global_20261108-140000`.
