---
license: other
license_name: open-government-licence-canada
license_link: https://open.canada.ca/en/open-government-licence-canada
language:
  - en
pretty_name: Consolidated federal Acts and regulations of Canada (snapshot 2026-11-16)
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
  - canada
  - rag
  - embeddings
configs:
  - config_name: default
    data_files:
      - split: train
        path: data/train-*.parquet
---

# Consolidated federal Acts and regulations of Canada — snapshot 2026-11-16

Every consolidated federal **Act** and **regulation** of Canada in English, as the Department
of Justice publishes them in its
[`laws-lois-xml`](https://github.com/justicecanada/laws-lois-xml) repository (the data
behind [laws-lois.justice.gc.ca](https://laws-lois.justice.gc.ca)). The data is split into
retrieval-ready chunks. Each chunk carries its citation, the instrument it belongs to, its
location and a link to the exact source file at a fixed commit, plus structured signals:
the Acts a regulation is made under, the section's amendment history, the instrument's
recent amendments with dates, cited enactments, defined terms and the provisions it spans.

- **166,569 chunks** from **5,874 instruments**: 973 Acts and 4,901 regulations. By type, the
  chunks come from Acts (70,998), SOR regulations (85,117), SI orders (10,448) and 6
  directions typed `DATED`.
- **122,123 sections** and **5,071 schedules**, under 65,000 Part, Division and Subdivision
  headings.
- Every chunk has a citation (`identifier`, e.g. `C-46, s. 718.2` or `SOR/2018-108, s. 5`).
- Read at commit `855cdfc65207a6906c414e09a77d7f93267c5356` (2026-11-16). Each instrument
  is current to the date in its own `effective_date`, most of them 2026-10-19.

**Not the official version.** Under the Legislation Revision and Consolidation Act, a
consolidation is official only as that Act provides. Use the Justice Laws Website or the
Canada Gazette where a citation must hold.

## Columns

| column | example | meaning |
|---|---|---|
| `identifier` | `SOR/2018-108, s. 5` · `C-46, s. 718.2` · `A-1, Schedule I` | the citation of the section or schedule the chunk is from; the instrument's alone for text outside both |
| `body` | `(1) For the purposes of subsection 10(1) of the Act, ...` | the text, verbatim from the XML; each provision opens with its label |
| `part_n`, `part_of` | `1`, `14` | long sections are split on provision boundaries (max 1,024 tokens); long tables by rows, with the header repeated |
| `heading_path` | `Safe Food for Canadians Regulations › PART 2 Trade › 5 Subsection 10(1) of Act` | full location, joined by ` › ` |
| `regulation_type` | `Act`, `SOR`, `SI`, `C.R.C.`, `DATED` | the instrument type as the XML states it; an Act states none and is `Act` |
| `regulation_number` | `SOR/2018-108`, `C-46`, `C.R.C., c. 870` | the instrument's Justice Laws number |
| `about` | `Safe Food for Canadians Regulations` | the short title, else the long title |
| `chapter` | `PART 2 Trade` | the Part the chunk is in |
| `article` | `5` | the section number, or the schedule's label |
| `locator_section` | `5 Subsection 10(1) of Act` | the chunk's own heading: the section number and marginal note |
| `year`, `effective_date` | `2025`, `2025-09-29` | the date the consolidation of this instrument is current to |
| `source_url` | `https://raw.githubusercontent.com/justicecanada/laws-lois-xml/855cdfc.../eng/regulations/SOR-2018-108.xml` | the exact file the text was read from, at the commit |
| `source_sha256` | | SHA-256 of that file |
| `source_title` | `SOR-2018-108` | the file |
| `attrs` | `{"unit": "section"}` or `{"unit": "schedule", "kind": "table", "table_id": "t0001"}` | JSON; tables are their own chunks, rendered as Markdown |
| `id`, `doc_id`, `seq`, `block_ids`, `token_count` | | deterministic chunk id, source hash, order and blocks within the instrument, token count |
| `chunker`, `chunker_version`, `config_hash`, `profile` | `markup`, `1.1`, ..., `ca_laws@1.0` | how it was built |
| `embedding` | 1,024 x float16 | the chunk's dense vector, unit length (see Embeddings) |
| `enacting_body`, `locator_page` | | empty: the XML states neither |

### Signals

These are read from each chunk's own blocks by rules declared in the profile. Lists are
distinct, in order of first appearance. An empty list means none was found.

| column | type | example | share of chunks |
|---|---|---|---|
| `authority` | list | `["FOOD AND DRUGS ACT", "SAFE FOOD FOR CANADIANS ACT"]`: the Acts the regulation is made under, for every chunk of it | 57.9% (regulations only) |
| `amendments` | list | `["R.S., 1985, c. A-1, s. 4", "2006, c. 9, s. 143", "2019, c. 18, s. 39"]`: the section's historical note | 39.8% |
| `recent_amendments` | list | `["SOR/2025-192, 2025-09-19", "SOR/2024-244, 2024-12-18"]`: the instrument's recent amendments, with the dates they came into force | 56.6% |
| `recent_amendments_latest` | string | `2025-09-19`: the latest of those dates | 56.6% |
| `regulation_references` | list | `["SOR/2016-12", "C.R.C., c. 870"]` cited in the text | 26.6% |
| `statute_references` | list | `["R.S., 1985, c. A-1"]` cited in the text | 5.4% |
| `defined_terms` | list | `["barrier", "Canadian carrier"]`: `X means ...` where `X` is marked as a defined term in the XML | 3.2% |
| `paragraph_first`, `paragraph_last` | string | `(1)`, `(2)(a)(i)`: the provisions the chunk runs from and to (subsection, paragraph, subparagraph, clause) | 41.6% |

`sources/manifest.jsonl` lists every file used (path, URL, bytes, SHA-256), and
`sources/current.txt` is the snapshot list. Both can be checked against the repository at the
commit.

### Related provisions

After its body, an Act often prints provisions of *other* enactments: transitional rules,
and amendments not yet in force. Their sections carry the amending enactment's numbers (`5`
of 1995, c. 29, printed after section 49 of this Act). They are kept as text with
`attrs.kind` `related_provisions`, cited by the schedule or instrument they are printed in,
and never as a second section 5 of this Act. Text that an amendment inserts, or that a
provision is to be read as, is kept the same way (`amended_text`, `read_as_text`).

One section number is repeated by the law itself: N-16.62 enacts two sections `15.1`.

## Not included

- The French versions (`fra/` in the repository). They are the same instruments and will be
  a separate dataset.
- The annual statutes and the point-in-time versions.
- Images have no text and are not included. Their alternate text is included.

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

The vectors are unit length, so cosine similarity is a dot product. Five random rows,
re-embedded from the template above, reproduce their stored vector at cosine 1.0000.

## How it was built

Built with [Ravel](https://github.com/azzindani/Ravel):

```
ravel sources fetch ca_laws                          # the repository tree at one commit, 5,874 files
ravel extract ca_laws -w work/ca_laws/2026-11-16     # xml extractor + justice_ca dialect, strict parse
ravel chunk   ca_laws -w work/ca_laws/2026-11-16     # markup chunker, profile ca_laws@1.0
ravel signals ca_laws -w work/ca_laws/2026-11-16     # the signal columns
ravel embed   ca_laws -w work/ca_laws/2026-11-16 --base-url http://localhost:8000/v1   # vLLM
ravel export  ca_laws -w work/ca_laws/2026-11-16 --out . --vectors   # this dataset
```

There is no code specific to Canada. Every element name, heading location and number rule is
declared in YAML (`registry/dialects/justice_ca@1.0.yaml`), and the citation forms and signal
patterns are in the profile (`registry/profiles/ca_laws@1.0.yaml`). The method is in Ravel's
`docs/sources/SOURCE_CA_LAWS.md`, and the process every source follows is in
`docs/sources/ADDING_A_SOURCE.md`.

## License

Contains information licensed under the
[Open Government Licence – Canada](https://open.canada.ca/en/open-government-licence-canada).
Source: Department of Justice Canada, `laws-lois-xml`.
