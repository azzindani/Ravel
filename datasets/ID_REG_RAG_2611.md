---
license: other
language:
  - id
pretty_name: Indonesian laws and regulations, retrieval-ready (ID_REG_Markdown, 2026-11)
size_categories:
  - 1M<n<10M
task_categories:
  - text-retrieval
  - feature-extraction
  - question-answering
tags:
  - legal
  - regulations
  - legislation
  - indonesia
  - indonesian
  - rag
  - embeddings
configs:
  - config_name: default
    data_files:
      - split: train
        path: data/train-*.parquet
---

# Indonesian laws and regulations, retrieval-ready (2026-11)

Indonesian national and regional regulations, in Indonesian, split into retrieval-ready
chunks. Each chunk carries its citation, the regulation it belongs to, its location in that
regulation, the file it was read from, and its embedding.

- **1,716,628 chunks** from **45,179 documents**. By the type each document states:
  9,107 Perda Kabupaten, 8,721 Peraturan Bupati, 3,677 Peraturan Walikota, 2,492 Perda
  Kota, 2,392 Peraturan Pemerintah, 2,248 Peraturan Gubernur, 2,137 Keputusan Presiden,
  1,722 Undang-Undang, 1,299 Peraturan Presiden, 925 Perda Provinsi and others; 9,242
  documents state no type the profile reads. About 70% are regional regulations.
- 81.3% of chunks carry a citation (`identifier`, e.g. `UU 1/1957 Pasal 2`); the rest come
  from documents whose type, number or year could not be read.
- This is the corpus Vera serves: Ravel bundle `id_reg_markdown` v6 (manifest
  `7d188301…`), the rows and vectors a database prepared from it holds.

**Not the official text.** The texts were collected as Markdown converted from the
published documents, some through OCR. Verify a citation against its official publication
(JDIH, peraturan.go.id) where it must hold.

## Columns

| column | example | meaning |
|---|---|---|
| `identifier` | `UU 1/1957 Pasal 2` · `Perda Kab. 4/2009 Pasal 24` | the citation of the article the chunk is from (81.3%) |
| `body` | | the text, as the Markdown holds it |
| `part_n`, `part_of` | `1`, `3` | long articles are split; 560,942 chunks are one part of several |
| `heading_path` | `BAB XIX KETENTUAN PENUTUP › Pasal 24` | location in the document, joined by ` › ` (97.3%) |
| `regulation_type` | `UNDANG-UNDANG`, `PERATURAN DAERAH KABUPATEN`, `PERATURAN BUPATI` … | the document's type as its title states it (81.3%) |
| `enacting_body` | `SLEMAN`, `BOMBANA` | the region or body that enacted it (88.0%) |
| `regulation_number`, `year` | `4`, `2009` | its number and year (81.3%) |
| `about` | `RETRIBUSI PELAYANAN PERSAMPAHAN` | its subject, the title after `TENTANG` (88.0%) |
| `effective_date` | `2009-08-12` | the date it was enacted or promulgated, where the text states one (73.1%) |
| `chapter` | `BAB XIX - KETENTUAN PENUTUP`, `PENJELASAN` | the chapter the chunk is in; an explanation (*penjelasan*) is `PENJELASAN` |
| `article` | `Pasal 24` | the article (89.1%); a *penjelasan* chunk names the article it explains |
| `locator_section` | `Pasal 24` | the chunk's own heading |
| `source_title` | | the source file's name |
| `source_url` | `hf://datasets/Azzindani/ID_REG_Markdown/batch-11-chunk-4/UU_Nomor_1_Tahun_1957.md` | the Markdown file in the `ID_REG_Markdown` collection (not public: it names the file, ✗ opens for outside readers) |
| `source_sha256` | | SHA-256 of that file |
| `attrs` | `{"unit": "pasal"}` | JSON; the unit the chunk is |
| `id`, `doc_id`, `seq`, `block_ids`, `token_count` | | deterministic chunk id, source hash, order and blocks within the document, token count |
| `chunker`, `chunker_version`, `config_hash`, `profile` | `unit`, `1.2`, …, `id_regulation_md@1.4` | how it was built |
| `embedding` | 1,024 x float16 | the chunk's dense vector, unit length (see Embeddings) |
| `locator_page` | | empty: the Markdown has no pages |

`sources/manifest.jsonl` lists every file read (path, bytes, SHA-256).

## Not included

- 25 documents that produced no chunks.
- The document relations (what revokes, amends or is based on what), document status and
  text-quality verdicts. They are built from these chunks and loaded with them into Vera's
  database (Ravel `ravel bundle build`), and were tables of earlier revisions of this
  dataset; they are not part of the chunk layout every Ravel dataset now shares.
- About 857 documents have an unusable or empty text layer and would need OCR; their chunks
  are included as read.

## Embeddings

Every chunk carries its embedding. `embedding` is the vector of the chunk's body behind a
header the profile (`registry/profiles/id_regulation_md@1.4.yaml`, `embed_text`) builds:

```
Peraturan: {regulation_type} {enacting_body}
Nomor: {regulation_number} Tahun {year}
Rujukan: {identifier}
Tentang: {about}
Bab: {chapter}
Letak: {heading_path}
Isi:

{body}
```

A line whose fields are all empty is left out; `Berkas: {source_title}` stands in when the
type, number and year are all empty; placeholder values (`N/A`, a bare `KABUPATEN` …) count
as empty; and `about` is cut where a preamble (`DENGAN RAHMAT …`, `MENIMBANG`) runs on
after the title. Ravel's `chunk.derive.embed_text` is the exact rule.

| | |
|---|---|
| model | [`Qwen/Qwen3-Embedding-0.6B`](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) at revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` |
| pooling | last token, left padding, L2-normalised; max length 8,192 tokens |
| documents | embedded bare, no instruction |
| queries | prefix `Instruct: Given a legal or regulatory question, retrieve passages that answer it\nQuery: ` (the model card's format) |
| precision | float16 |
| served by | vLLM, checked against the `transformers` reference |

The vectors are unit length, so cosine similarity is a dot product. They are in the same
space as `Azzindani/CA_LAWS_2611`, `Azzindani/US_CFR_2611`, `Azzindani/JP_LAWS_2611`,
`Azzindani/DE_LAWS_2611` and `Azzindani/FR_LAWS_2611`, so one index can hold all six.
Five random rows, re-embedded from the header rule above (Ravel's `embed_text`, profile
`id_regulation_md@1.4`), reproduce their stored vector at cosine 1.0000.

## Revisions

- **3 · 2026-11-23 · the shared layout, and bundle v6.** One table, the embedding a column
  of each chunk, as every Ravel dataset is published. The rows are bundle v6: profile
  `id_regulation_md@1.4`, whose embedded header names the region and place and adds
  `Rujukan`, `Berkas` and a clean `Tentang`. The separate `vectors`, `sparse`, `relations`,
  `doc_status` and `doc_quality` tables of revisions 1-2 are removed.
- **2 · 2026-11-15 · explanations labelled as explanations.** 3,077 *penjelasan* chunks
  whose `PENJELASAN` heading OCR had lost are labelled `PENJELASAN` and re-embedded.
- **1 · 2026-11-13** · first publication, as a Ravel bundle.

## How it was built

Built with [Ravel](https://github.com/azzindani/Ravel):

```
ravel extract id_reg_markdown -w work/ws          # Markdown, 45,179 documents
ravel chunk   id_reg_markdown -w work/ws          # unit chunker, profile id_regulation_md@1.4
ravel embed   id_reg_markdown -w work/ws --base-url http://localhost:8000/v1   # vLLM
ravel bundle build id_reg_markdown -w work/ws --out work/bundle_id_reg_markdown_v6
ravel publish id_reg_markdown -w work/ws --bundle work/bundle_id_reg_markdown_v6 --repo Azzindani/ID_REG_RAG_2611
```

The method is in Ravel's `docs/sources/SOURCE_ID_REG_MARKDOWN.md`.

## License

The texts are Indonesian laws and regulations, public official documents. `license: other`
because the collection's exact licence is not fixed; verify each text against its official
publication before redistributing it commercially.
