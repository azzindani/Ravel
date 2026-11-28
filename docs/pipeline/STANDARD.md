# STANDARD.md — Ravel

> The one standard every corpus is chunked and embedded by. A profile or a corpus file
> may not state another value for anything here; a doc that needs one of these numbers
> links to this page instead of restating it. What may differ between corpora is said in
> §4, and nothing else does.

## 1. Why there is one

Until 2026-11-28 each corpus carried its own budget, and the budgets did not mean what
they said:

- Indonesia's profile declared 4,096 tokens, but its corpus file stated 512, and a corpus
  file wins (`ChunkSpec.to_config`). The published 1,716,628 chunks are the 512 config
  (`config_hash` `82ce5caa077e68c7`), while two docs said 4,096.
- Every XML law profile stated 1,024.
- All of them counted tokens with a word estimate, not the embedder's tokenizer. Measured
  against Qwen3's tokenizer on 1,000-1,500 chunks per corpus, the estimate was a different
  real size in every language:

| corpus | real ÷ estimate, p50 | p90 | p99 |
|---|---|---|---|
| jp_egov | 0.55 | 0.61 | 0.76 |
| ca_laws | 0.82 | 1.03 | 1.49 |
| us_cfr | 0.83 | 1.07 | 1.50 |
| us_fr | 0.87 | 1.01 | 1.30 |
| fr_legi | 1.03 | 1.22 | 1.40 |
| de_gii | 1.39 | 1.56 | 1.78 |
| id_reg_markdown | 1.44 | 1.71 | 2.36 |

So a Japanese chunk at the same "budget" was about half the real size of an Indonesian
one, and corpora built at different budgets sat in one index. Every corpus is rebuilt to
this page from the canonical documents it already has; nothing is fetched or extracted
again.

## 2. Chunking

| rule | value |
|---|---|
| the unit | one natural, citable unit: a pasal, an article, a section, an annex. The profile names it. |
| counted in | the embedder's own tokenizer, `model@revision` (`chunk.tokens`). No estimate, for any corpus with a real embedder. |
| `max_tokens` | **4,096**. A unit longer than this is split into `part n of m`, on block boundaries. |
| a table | split by rows under `max_tokens`, its header repeated in every part (`markup` 1.1). |
| a single block over 4,096 | kept whole, never cut mid-sentence. Counted by `expected.max_row_tokens` and `oversize_share`, and reported in the build. |
| `min_tokens` | **8**. Only a chunker that reads structure from text (`unit`) merges a smaller fragment forward. `markup` never merges: every unit the markup states is its own chunk. |
| `overlap_tokens` | **0**. |

The tokenizer is part of the config hash, so a chunk's id says what it was counted in.

## 3. Embedding

| rule | value |
|---|---|
| model | `qwen3-embedding-0.6b@1.0` (`registry/embedders/`): Qwen/Qwen3-Embedding-0.6B at revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, 1,024 dimensions. |
| `max_length` | **8,192** tokens: the body (at most 4,096, §2) and the profile's context header (`Citation:`, `Location:`). |
| what is embedded | each chunk with the template of the profile that made it (`embed_text`). A corpus that routes among profiles (`profile: auto`) embeds each chunk with its own (`embed.run.profile_of`). |
| engine | any that `ravel verify-backend` passes at cosine ≥ 0.999 against the reference. |

## 4. What may differ

- **Structure and identity:** each source's dialect and profile say what its units are and
  how they are cited. That is the source's own markup, not a budget.
- **Search:** Vera's algorithms (`config/algorithms.json`) and their parameters (probe,
  pool, weights) are chosen per evaluation, and documented with the run that chose them.

## 5. Changes

| date | change |
|---|---|
| 2026-11-28 | Standard written. Chunk sizes counted in the embedder's tokens (`5550035`). Every profile collapsed to one file at `1.0` stating 4,096 / 8; every corpus file resolves to it and embeds with `qwen3-embedding-0.6b@1.0` at 8,192, enforced by `tests/test_standard.py`. The profiles the `*_2611` datasets were built with are in git history only. Every published corpus is rebuilt from its canon. |
