---
license: other
license_name: amtliche-werke-urhg-5
language:
  - de
pretty_name: German federal laws and ordinances in force (Gesetze im Internet, 2026-11-22)
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
  - germany
  - german
  - rag
  - embeddings
configs:
  - config_name: default
    data_files:
      - split: train
        path: data/train-*.parquet
---

# German federal laws and ordinances (Gesetze im Internet), snapshot 2026-11-22

Every German federal law and ordinance in force, in German, as the Federal Ministry of
Justice and juris publish them on [Gesetze im Internet](https://www.gesetze-im-internet.de),
one XML per law. The data is split into retrieval-ready chunks. Each chunk carries its
citation as German practice writes it, the law it belongs to, its location in that law and
a link to the exact file it was read from, plus the Federal Law Gazette and other laws it
cites.

- **136,284 chunks** from **6,146 laws and ordinances** with text (6,150 in the publisher's
  index; 4 state no text: two notices and two amending laws now merged into the law they
  amended).
- By unit: 129,342 provision chunks (`§`, `Art`, `Anlage`, `Eingangsformel` …), 6,148 chunks
  of a law's own text (its footnotes and notes), and 794 chunks of text an outline heading
  holds itself. 17,984 chunks are tables.
- Every chunk has a citation (`identifier`, e.g. `§ 7 BGB`, `Art 229 § 6 BGBEG`).
- Fetched on 2026-11-22: the publisher replaces a law's file in place, so the snapshot is
  the day it was read.

**Not the official text.** The official text of a German federal law is the one promulgated
in the Bundesgesetzblatt. Use it where a citation must hold.

## Columns

| column | example | meaning |
|---|---|---|
| `identifier` | `§ 7 BGB` · `Art 3 GG` · `Art 229 § 6 BGBEG` · `Art 2 BGBEG` · `Anlage 2 ÄApprO 2002` · `Eingangsformel ÄApprO 2002` · `BGB` | the citation of the provision the chunk is from; the law's abbreviation alone for its own text and for a provision the law prints without a number (`(XXXX)`) |
| `body` | `(1) Wer sich an einem Orte ständig niederlässt, begründet an diesem Orte seinen Wohnsitz. ...` | the text, verbatim from the XML, with the provision's footnotes |
| `part_n`, `part_of` | `1`, `3` | long provisions are split on paragraph boundaries (max 1,024 tokens), long tables by rows; 17,848 chunks are one part of several |
| `heading_path` | `Bürgerliches Gesetzbuch › Buch 1 Allgemeiner Teil › Abschnitt 1 Personen › Titel 1 Natürliche Personen, Verbraucher, Unternehmer › § 7 Wohnsitz; Begründung und Aufhebung` | full location, joined by ` › ` |
| `regulation_number` | `BGB` | the law's abbreviation, the official one where it has one |
| `about` | `Bürgerliches Gesetzbuch` | the law's full title |
| `chapter` | `Buch 1 Allgemeiner Teil` | the outermost outline heading the chunk is in (61.6%) |
| `article` | `§ 7` | the provision's number (94.9%) |
| `locator_section` | `§ 7 Wohnsitz; Begründung und Aufhebung` | the chunk's own heading |
| `year` | `1896` | the year the law was signed (`Ausfertigungsdatum`), ✗ the year it came into force |
| `source_url` | `http://www.gesetze-im-internet.de/bgb/xml.zip#BJNR001950896.xml` | the archive the text was read from and the file in it, as the publisher's index prints it |
| `source_sha256` | | SHA-256 of the XML file |
| `source_title` | `BJNR001950896` | the file |
| `attrs` | `{"unit": "article"}` or `{"unit": "article", "kind": "table", "table_id": "t0000"}` | JSON; tables are their own chunks, rendered as Markdown |
| `id`, `doc_id`, `seq`, `block_ids`, `token_count` | | deterministic chunk id, source hash, order and blocks within the law, token count |
| `chunker`, `chunker_version`, `config_hash`, `profile` | `markup`, `1.1`, ..., `de_gii@1.0` | how it was built |
| `embedding` | 1,024 x float16 | the chunk's dense vector, unit length (see Embeddings) |
| `regulation_type`, `effective_date`, `enacting_body`, `locator_page` | | empty: the XML states none of them |

### Provisions inside an article

Some laws, the introductory acts above all (EGBGB, EGInsO, EGAO), number their `§§` from 1
again inside every article. Such a provision is cited within its article, `Art 229 § 6
BGBEG`, so the `§` alone never names two provisions. An article whose text the law prints
under its heading, with no `§`, is cited as the article, `Art 2 BGBEG`.

A provision the law marks `(weggefallen)` with no text is repealed and is not included.

### Signals

Read from each chunk's own text by rules declared in the profile. Lists are distinct, in
order of first appearance; an empty list means none was found.

| column | type | example | share of chunks |
|---|---|---|---|
| `gazette_references` | list | `["BGBl. I S. 2402"]`, `["BGBl. 1991 II S. 1007"]`: Federal Law Gazette citations | 8.2% |
| `law_references` | list | `["§ 77 Absatz 3 SGB IX", "§ 145 Absatz 1 Satz 3 SGB IX"]`: provisions of a law cited by its abbreviation | 2.5% |

`sources/manifest.jsonl` lists every file used (path, URL, bytes, SHA-256), and
`sources/current.txt` is the snapshot list.

## Not included

- The tables of contents (`Inhaltsübersicht`), which repeat the headings.
- Images, which have no text.
- Earlier versions: the publisher keeps only the current one.

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
| pooling | last token, left padding, L2-normalised; max length 8,192 tokens (3 texts were longer and are embedded cut to it) |
| documents | embedded bare, no instruction |
| queries | prefix `Instruct: Given a legal or regulatory question, retrieve passages that answer it\nQuery: ` (the model card's format) |
| precision | float16 |
| served by | vLLM, checked against the `transformers` reference before the run (min cosine 1.00000) |

The vectors are unit length, so cosine similarity is a dot product. They are in the same
space as `Azzindani/CA_LAWS_2611`, `Azzindani/US_CFR_2611` and `Azzindani/JP_LAWS_2611`, so
one index can hold all four.
Five random rows, re-embedded from the template above, reproduce their stored vector at
cosine 1.0000.

## How it was built

Built with [Ravel](https://github.com/azzindani/Ravel):

```
ravel sources fetch de_gii                        # the publisher's index, one archive per law: 6,150
ravel extract de_gii -w work/de_gii/20261122      # xml extractor + de_gii dialect: 6,146 extracted, 4 empty, 0 failed
ravel chunk   de_gii -w work/de_gii/20261122      # markup chunker, profile de_gii@1.0
ravel signals de_gii -w work/de_gii/20261122      # gazette_references, law_references
ravel embed   de_gii -w work/de_gii/20261122 --base-url http://localhost:8000/v1   # vLLM
ravel publish de_gii -w work/de_gii/20261122 --repo Azzindani/DE_LAWS_2611        # this dataset
```

There is no code specific to Germany. Every element name, the outline depth rule and the
provision rule are declared in YAML (`registry/dialects/de_gii@1.0.yaml`), and the citation
forms and signal patterns are in the profile (`registry/profiles/de_gii@1.0.yaml`). The method
is in Ravel's `docs/sources/SOURCE_DE_GII.md`.

## License

German laws and ordinances are official works and are not protected by copyright (§ 5
UrhG). Source: Bundesministerium der Justiz and juris GmbH, Gesetze im Internet
(gesetze-im-internet.de).
