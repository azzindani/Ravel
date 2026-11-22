---
license: other
license_name: us-government-work
license_link: https://www.usa.gov/government-copyright
language:
  - en
pretty_name: US Code of Federal Regulations (official edition, snapshot 2026-11-21)
size_categories:
  - 100K<n<1M
task_categories:
  - text-retrieval
  - question-answering
tags:
  - legal
  - regulations
  - cfr
  - united-states
  - rag
  - embeddings
configs:
  - config_name: default
    data_files:
      - split: train
        path: data/train-*.parquet
---

# US Code of Federal Regulations — snapshot 2026-11-21

The **official annual edition** of the US Code of Federal Regulations (CFR), as published by
the US Government Publishing Office on govinfo. Every section and appendix is split into
retrieval-ready chunks. Each chunk carries its citation, agency, location and a link to the
exact source file, plus structured signals: what it cites, the part's authority and source,
its amendment history, effective-date notes, the terms it defines, and the paragraphs it
spans.

- **366,005 chunks** from 246 volumes covering all 49 titles (title 35 is reserved).
- **231,037 sections** and **4,152 appendices** read as structure; 6,655 sections and 186
  appendices are `[Reserved]` and carry no text. A further 368 `SECTION` and 26 `APPENDIX`
  elements are amendment text quoted inside an effective-date note, and are read as that
  note's text, not as sections.
- Every chunk has a citation (`identifier`, e.g. `40 CFR 52.21`).

The CFR is official: it is prima facie evidence of the original documents (44 U.S.C. 1510).
The daily eCFR is current, but it is an editorial compilation and not an official legal edition.

## Which edition of each title

Editions are published title by title and volume by volume over months. Each title uses its
**newest complete edition**: an edition is skipped if it lacks a volume number that the
edition before it had. Result: 2026 for 9 titles, 2025 for 35, 2024 for 5.

| title | skipped edition | missing volumes | used |
|---|---|---|---|
| 7 | 2026, then 2025 | 2026: 10, 11, 13 · 2025: 7 | 2024 |
| 12 | 2026 | 3, 4, 6, 7 | 2025 |
| 14 | 2026 | 1 (its `vol0` is a byte-for-byte copy of `vol3`) | 2025 |
| 16 | 2026 | 2 | 2025 |
| 29 | 2025 | 6 | 2024 |
| 33 | 2025 | 3 | 2024 |
| 40 | 2025 | 18, 37 | 2024 |
| 50 | 2025 | 3, 4, 5, 7, 8 | 2024 |

GPO does not reprint a volume nobody amended, so some volumes in a newer edition still carry an
older revision date (41 CFR vol. 2 is "Revised as of July 1, 2017"). Each chunk's `year` and
`effective_date` are those of its own volume.

## Columns

| column | example | meaning |
|---|---|---|
| `identifier` | `40 CFR 52.21` | citation of the section, appendix or part the chunk is from |
| `body` | `(a)(1) Plan disapproval. ...` | the text, verbatim from the XML |
| `part_n`, `part_of` | `1`, `39` | long sections are split on paragraph boundaries (max 1,024 tokens) |
| `heading_path` | `Title 40—Protection of Environment › CHAPTER I—... › § 52.21 ...` | full location, joined by ` › ` |
| `regulation_number` | `40 CFR Part 52` | the part |
| `enacting_body` | `ENVIRONMENTAL PROTECTION AGENCY` | the agency (the chapter's name); empty where a part has no chapter |
| `about` | `APPROVAL AND PROMULGATION OF IMPLEMENTATION PLANS` | the part's title |
| `chapter` | `PART 52—APPROVAL AND ...` | the part heading |
| `article` | `§ 52.21` / `Appendix A to Part 51` | the section or appendix |
| `locator_section` | `§ 52.21 Prevention of significant deterioration of air quality` | the chunk's own heading |
| `locator_page` | `15` | page of the printed volume |
| `year`, `effective_date` | `2024`, `2024-07-01` | the volume's revision date |
| `regulation_type` | `CFR` | constant |
| `source_url` | `https://www.govinfo.gov/bulkdata/CFR/2024/title-40/CFR-2024-title40-vol3.xml` | the exact file the text was read from |
| `source_sha256` | | SHA-256 of that file |
| `source_title` | `CFR-2024-title40-vol3` | the volume |
| `embedding` | 1,024 x float16 | the chunk's dense vector, unit length (see Embeddings) |
| `attrs` | `{"unit": "section"}` or `{"unit": "section", "kind": "table", "table_id": "t0012"}` | JSON; tables are their own chunks, rendered as Markdown |
| `id`, `doc_id`, `seq`, `block_ids`, `token_count` | | deterministic chunk id, source hash, order and blocks within the volume, token count |
| `chunker`, `chunker_version`, `config_hash`, `profile` | `markup`, `1.0`, ..., `us_cfr@1.1` | how it was built |

### Signals

Read from each chunk's own blocks by rules declared in the profile. Lists are distinct, in
order of first appearance; an empty list means none was found.

| column | type | example | share of chunks |
|---|---|---|---|
| `cfr_references` | list | `["40 CFR 52.21", "40 CFR Part 51"]`; a bare `§ 52.21` is read in the chunk's own title; the chunk's own citation is left out | 42.4% |
| `usc_references` | list | `["42 U.S.C. 7401"]` | 13.9% |
| `public_laws` | list | `["Pub. L. 104-191"]` | 2.1% |
| `fr_references` | list | `["89 FR 37052"]` (Federal Register volume and page) | 32.4% |
| `authority` | string | the part's `Authority:` note, as printed | 99.8% |
| `authority_usc` | list | the U.S.C. sections that note cites | 98.0% |
| `source` | string | the `Source:` note of the deepest unit enclosing the chunk: a subpart's own, else the part's | 97.1% |
| `amendments` | list | `["54 FR 36119, Aug. 31, 1989", "60 FR 13893, Mar. 15, 1995"]`, from the section's bracketed citation | 57.0% |
| `amendments_latest` | string | `1995-03-15`: the latest of those dates, when the section last changed | 57.0% |
| `effective_date_note` | string | the section's effective-date note (`Effective Date Note: At 89 FR ..., § 3280.2 was amended, effective ...`) | 1.9% |
| `defined_terms` | list | `["Act", "Dealer"]`: `X means ...` where `X` is set in italics in the XML | 4.3% |
| `paragraph_first`, `paragraph_last` | string | `(b)(1)`, `(b)(23)`: the designations the chunk runs from and to | 64.5% |

`paragraph_*` resolve each paragraph marker by what is in force: `(i)` after `(h)` is a letter,
after `(1)` a numeral; where both are possible, the next marker decides. About 0.5% of ranges
are mis-nested where a section's drafting departs from the usual `(a)(1)(i)(A)` order (older
OSHA text sets `( a )` in italics below `(i)`).

`sources/manifest.jsonl` lists every volume used (path, URL, bytes, SHA-256), and
`sources/current.txt` is the snapshot list. Both can be checked against govinfo.

## Not included

- Title 3's presidential documents (proclamations, executive orders) are not yet extracted;
  title 3's chapter I is included.
- Graphics and math images have no text and are not included.

## Embeddings

Every chunk carries its embedding. `embedding` is the vector of exactly this text, the
`Agency` line left out where a part has no chapter:

```
Citation: {identifier}
Agency: {enacting_body}
Location: {heading_path}

{body}
```

| | |
|---|---|
| model | [`Qwen/Qwen3-Embedding-0.6B`](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) at revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` |
| pooling | last token, left padding, L2-normalised; max length 8,192 tokens; a longer text is embedded from its first 8,192 |
| documents | embedded bare, no instruction |
| queries | prefix `Instruct: Given a legal or regulatory question, retrieve passages that answer it
Query: ` (the model card's format) |
| precision | float16 |
| space | the same as [`Azzindani/CA_LAWS_2611`](https://huggingface.co/datasets/Azzindani/CA_LAWS_2611) (embedder fingerprint `2231c38c768a08c7`): one index can hold both |

The vectors are unit length, so cosine similarity is a dot product. Five random rows,
re-embedded from the template above through vLLM, reproduce their stored vector at cosine
1.0000.

## How it was built

Built with [Ravel](https://github.com/azzindani/Ravel):

```
ravel sources fetch us_cfr                       # govinfo bulk listing -> newest complete edition per title
ravel extract us_cfr -w work/us_cfr/2026-11-21   # xml extractor + gpo_cfr dialect, strict parse
ravel chunk   us_cfr -w work/us_cfr/2026-11-21   # markup chunker, profile us_cfr@1.1
ravel signals us_cfr -w work/us_cfr/2026-11-21   # the signal columns
ravel embed   us_cfr -w work/us_cfr/2026-11-21 --base-url http://localhost:8000/v1   # vLLM
ravel publish us_cfr -w work/us_cfr/2026-11-21 --repo Azzindani/US_CFR_2611   # this dataset
```

Every element name, heading location and marker rule is declared in YAML
(`registry/dialects/gpo_cfr@1.0.yaml`), not in code; the signal patterns are in the profile
(`registry/profiles/us_cfr@1.1.yaml`). Rerun the fetch to take editions published since; it
never overwrites a file. The method is in Ravel's `docs/SOURCE_US_CFR.md`.

## Changes

**2026-11-22, us_cfr@1.1, embeddings** (this version)
- `embedding` column added: the same 366,005 chunks, text and signals unchanged.

**2026-11-21, us_cfr@1.1**
- Signal columns added (above).
- Sections whose number is printed without `§` (11,623, including all of title 48) are now
  cited by their number (`48 CFR 52.212-4`). They were cited with their subject appended
  (`48 CFR 52.212-4 Contract Terms and Conditions—Commercial Products ...`).
- Amendment text quoted in a note (`REVTXT`) is read as the note's text. It had opened a
  second section of the quoted number inside the note: a chunk cited a section it was not.
- Where GPO's XML leaves such a quote open to the end of the volume (40 CFR vol. 33, 34 CFR
  vol. 3, 16 CFR vol. 1, 12 CFR vol. 9), the quote ends at the first unit that cannot be
  part of it, so the sections after it keep their identity.
- `EFFDNOTP` effective-date notes (1,339 in the snapshot, the most common form) are tagged
  as notes. Chunk count 366,277 -> 366,005.

**2026-11-21, us_cfr@1.0**: first release.

## License

The text is a work of the US Government and is not subject to copyright in the United States
(17 U.S.C. 105).
