---
license: other
license_name: not-copyrighted-jp-copyright-act-article-13
language:
  - ja
pretty_name: Japanese laws, Cabinet orders, ministerial ordinances and rules in force (e-Gov, 2026-11-22)
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
  - japan
  - japanese
  - rag
  - embeddings
configs:
  - config_name: default
    data_files:
      - split: train
        path: data/train-*.parquet
---

# Japanese laws in force (e-Gov 法令検索), snapshot 2026-11-22

Every Japanese law, Cabinet order, ministerial ordinance and rule **in force**, in Japanese,
as the Digital Agency publishes them on [e-Gov 法令検索](https://laws.e-gov.go.jp) in its
XML bulk download. The data is split into retrieval-ready chunks. Each chunk carries its
citation, the law it belongs to, its location in that law and a link to the exact version
it was read from, plus the laws it cites.

- **603,782 chunks** from **8,994 laws**: 2,103 Acts (法律), 2,314 Cabinet orders (政令),
  4,101 ministerial ordinances (省令), 409 rules (規則), 66 Imperial orders (勅令) and the
  Constitution.
- By unit: 301,024 article chunks (条), 159,790 articles of supplementary provisions (附則),
  103,023 supplementary provisions without articles, 30,901 appendices (別表 and the like),
  1,474 appendices of supplementary provisions, and 7,570 chunks of text outside all of
  these (a law with no articles, a preamble).
- Every chunk has a citation (`identifier`, e.g. `博物館法第二条`).
- The snapshot is the versions the publisher's own index (`all_law_list.csv`) lists as in
  force on 2026-11-22, the day the archive was fetched: the archive states no date.

**Not the official text.** The official text of a Japanese law is the one promulgated in the
官報 (Official Gazette). Use it, or e-Gov's page for the version, where a citation must hold.

## Columns

| column | example | meaning |
|---|---|---|
| `identifier` | `博物館法第二条` · `博物館法附則（令和四年四月一五日法律第二四号）第一条` · `博物館法附則（制定時）` · `消防法別表第一` · `博物館法施行規則別記第1号様式` | the citation of the article, supplementary provision or appendix the chunk is from |
| `body` | `この法律において「博物館」とは、歴史、芸術、...` | the text, verbatim from the XML; ruby readings are dropped |
| `part_n`, `part_of` | `1`, `3` | long units are split on paragraph boundaries (max 1,024 tokens); 127,073 chunks are one part of several |
| `heading_path` | `博物館法 › 第一章 総則 › 第二条 (定義)` | full location, joined by ` › ` |
| `regulation_type` | `Act`, `CabinetOrder`, `MinisterialOrdinance`, `Rule`, `ImperialOrder`, `Constitution` | the law type as the XML states it |
| `regulation_number` | `昭和二十六年法律第二百八十五号` | the law's number |
| `about` | `博物館法` | the law's title |
| `chapter` | `第一章 総則` | the chapter the chunk is in, where the law has chapters (35.2%) |
| `article` | `第二条` | the article number (81.7%) |
| `locator_section` | `第二条` | the chunk's own heading |
| `source_url` | `https://laws.e-gov.go.jp/law/326AC1000000285/20230401_504AC0000000024` | the e-Gov page of the exact version read, from the publisher's index |
| `source_sha256` | | SHA-256 of the XML file |
| `source_title` | | the file |
| `attrs` | `{"unit": "suppl_article"}` | JSON: `law`, `article`, `suppl`, `suppl_article`, `appendix`, `suppl_appendix` |
| `id`, `doc_id`, `seq`, `block_ids`, `token_count` | | deterministic chunk id, source hash, order and blocks within the law, token count (each CJK character counts as one) |
| `chunker`, `chunker_version`, `config_hash`, `profile` | `markup`, `1.1`, ..., `jp_law@1.0` | how it was built |
| `embedding` | 1,024 x float16 | the chunk's dense vector, unit length (see Embeddings) |
| `year`, `effective_date`, `enacting_body`, `locator_page` | | empty: not read from this source |

### Supplementary provisions

A law's supplementary provisions (附則) follow its main provision, one block per amending
law, and number their articles from 第一条 again. Each is cited by the amending law it came
with (`附則（令和四年四月一五日法律第二四号）第一条`), so it never cites as the law's own first
article. The block made with the law itself names no amending law and cites `附則（制定時）`,
at enactment.

### Signals

Read from each chunk's own text by a rule declared in the profile. The list is distinct, in
order of first appearance; an empty list means none was found.

| column | type | example | share of chunks |
|---|---|---|---|
| `law_references` | list | `["昭和二十四年法律第二百七号"]`: laws, orders and ordinances cited by number | 13.5% |

`sources/manifest.jsonl` lists every file used (path, URL, bytes, SHA-256), and
`sources/current.txt` is the snapshot list.

## Not included

- Versions not yet in force (`未施行` in the publisher's index) and earlier versions.
- Tables of contents and article ranges (`ArticleRange`), which repeat the headings.

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
| pooling | last token, left padding, L2-normalised; max length 8,192 tokens |
| documents | embedded bare, no instruction |
| queries | prefix `Instruct: Given a legal or regulatory question, retrieve passages that answer it\nQuery: ` (the model card's format) |
| precision | float16 |
| served by | vLLM, checked against the `transformers` reference before the run (min cosine 0.99999) |

The vectors are unit length, so cosine similarity is a dot product. They are in the same
space as `Azzindani/CA_LAWS_2611` and `Azzindani/US_CFR_2611`, so one index can hold all three.
Five random rows, re-embedded from the template above, reproduce their stored vector at
cosine 1.0000.

## How it was built

Built with [Ravel](https://github.com/azzindani/Ravel):

```
ravel sources fetch jp_egov                        # the bulk archive and the publisher's index: 8,994 laws in force
ravel extract jp_egov -w work/jp_egov/20261122     # xml extractor + jp_law dialect, 0 failed
ravel chunk   jp_egov -w work/jp_egov/20261122     # markup chunker, profile jp_law@1.0
ravel signals jp_egov -w work/jp_egov/20261122     # law_references
ravel embed   jp_egov -w work/jp_egov/20261122 --base-url http://localhost:8000/v1   # vLLM
ravel publish jp_egov -w work/jp_egov/20261122 --repo Azzindani/JP_LAWS_2611        # this dataset
```

There is no code specific to Japan. Every element name, heading location and number rule is
declared in YAML (`registry/dialects/jp_law@1.0.yaml`), and the citation forms and the signal
pattern are in the profile (`registry/profiles/jp_law@1.0.yaml`). The method, including what a
language written without spaces changed, is in Ravel's `docs/sources/SOURCE_JP_EGOV.md`.

## License

Japanese laws, Cabinet orders, ministerial ordinances and notices are not subject to
copyright (著作権法第十三条, Copyright Act Article 13). Source: Digital Agency of Japan, e-Gov
法令検索 (laws.e-gov.go.jp), XML bulk download.
