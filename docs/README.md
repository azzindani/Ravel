# Ravel docs — index

Every document, by what it is for. A doc is named the same everywhere, so a reference by
name alone (`PROFILES.md` §7) is unambiguous; this page says where it lives.

> ! Registry and corpus YAML (`registry/`, `corpora/`) cite docs by their flat path from
> before 2026-11-22 (`docs/PROFILES.md`). Those files are left as they are on purpose: a
> published one is frozen (`registry/published.yaml`), and their text is part of the
> config hash every stage is cached by. Read the path's file name against this index.

## Start here

| | |
|---|---|
| [`../CLAUDE.md`](../CLAUDE.md) | the rules: what Ravel is, its principles, what it must never do |
| [`design/ARCHITECTURE.md`](design/ARCHITECTURE.md) | the stage graph, artifact boundaries, the phase split |
| [`pipeline/STANDARD.md`](pipeline/STANDARD.md) | **the one chunking and embedding standard every corpus is built to** |
| [`pipeline/PIPELINE.md`](pipeline/PIPELINE.md) | what each layer does and may write, where the profile takes over |
| [`sources/ADDING_A_SOURCE.md`](sources/ADDING_A_SOURCE.md) | how a new source becomes a corpus, a dataset and a database |
| [`project/PROGRESS.md`](project/PROGRESS.md) | what is built, what is not |

## design/ — the contracts

| doc | what it fixes |
|---|---|
| [`ARCHITECTURE.md`](design/ARCHITECTURE.md) | stage graph, artifact boundaries, phase split |
| [`CANONICAL_FORMAT.md`](design/CANONICAL_FORMAT.md) | the IR: structured blocks + layout + tables; document identity |
| [`BUNDLE.md`](design/BUNDLE.md) | the output contract: parquet + manifest + generated schema |
| [`INTERFACES.md`](design/INTERFACES.md) | CLI / MCP / HTTP faces, Docker images, the URI substrate |
| [`STACK.md`](design/STACK.md) | Python 3.14, libraries, environments, no orchestrator |
| [`LOOPHOLES.md`](design/LOOPHOLES.md) | failure modes that break silently, each with its solution |

## pipeline/ — the stages, in order

| doc | stage |
|---|---|
| [`PIPELINE.md`](pipeline/PIPELINE.md) | the layers, end to end |
| [`STANDARD.md`](pipeline/STANDARD.md) | chunk 4,096 / embed 8,192, in the embedder's tokens, for every corpus |
| [`EXTRACTION.md`](pipeline/EXTRACTION.md) | Phase A: sources → canonical documents (native, XML, OCR) |
| [`PROFILES.md`](pipeline/PROFILES.md) | profiles and dialects: corpus knowledge as YAML; versioning and freezing (§7) |
| [`QUALITY.md`](pipeline/QUALITY.md) | per-document verdict: clean / degraded / unusable / absent |
| [`CHUNKING.md`](pipeline/CHUNKING.md) | chunkers; provenance capture |
| [`ENRICHMENT.md`](pipeline/ENRICHMENT.md) | signals, entities, factors, keyword statistics |
| [`RELATIONS.md`](pipeline/RELATIONS.md) | revokes / amends / based_on from citations; status |
| [`EMBEDDING.md`](pipeline/EMBEDDING.md) | pluggable embedders, the manifest, consistency |
| [`EMBED_VERIFICATION.md`](pipeline/EMBED_VERIFICATION.md) | how embedding was tested: reference checks, speed, vLLM |
| [`EXECUTION.md`](pipeline/EXECUTION.md) | ledger, checkpoints, resume, portability |
| [`CLUSTERING.md`](pipeline/CLUSTERING.md) | why clustering is Vera's; the optional offline tool |
| [`DATABASE.md`](pipeline/DATABASE.md) | bundle → database: `db prepare`; from nothing `build` (§4a); `update` (§4b); `relabel` (§4c) |

## sources/ — one per corpus

| doc | corpus | dataset |
|---|---|---|
| [`ADDING_A_SOURCE.md`](sources/ADDING_A_SOURCE.md) | the process every source follows | |
| [`SOURCES_NEXT.md`](sources/SOURCES_NEXT.md) | the sources still to add, and their status | |
| [`SOURCE_US_CFR.md`](sources/SOURCE_US_CFR.md) | `us_cfr`: the US CFR, official annual edition | `US_CFR_2611` |
| [`SOURCE_US_FR.md`](sources/SOURCE_US_FR.md) | `us_fr`: the US Federal Register, daily issues, built beside `us_cfr` | — |
| [`SOURCE_CA_LAWS.md`](sources/SOURCE_CA_LAWS.md) | `ca_laws`: Canada's federal Acts and regulations | `CA_LAWS_2611` |
| [`SOURCE_JP_EGOV.md`](sources/SOURCE_JP_EGOV.md) | `jp_egov`: Japan's laws, e-Gov XML | `JP_LAWS_2611` |
| [`SOURCE_DE_GII.md`](sources/SOURCE_DE_GII.md) | `de_gii`: Germany's federal laws, gesetze-im-internet | `DE_LAWS_2611` |
| [`SOURCE_FR_LEGI.md`](sources/SOURCE_FR_LEGI.md) | `fr_legi`: France's codes, laws and decrees in force, DILA LEGI | `FR_LAWS_2611` |
| [`SOURCE_EU_LEX.md`](sources/SOURCE_EU_LEX.md) | `eu_lex`: EU acts in force, Formex via the Cellar | — |
| [`SOURCE_ES_BOE.md`](sources/SOURCE_ES_BOE.md) | `es_boe`: Spain's consolidated laws, state and regional, BOE open-data API | — |
| [`SOURCE_ID_REG_MARKDOWN.md`](sources/SOURCE_ID_REG_MARKDOWN.md) | `id_reg_markdown`: Indonesian regulations, Markdown | `ID_REG_RAG_2611` |

The dataset cards are in [`../datasets/`](../datasets/README.md).

## eval/ — how a build is judged

| doc | |
|---|---|
| [`EVAL.md`](eval/EVAL.md) | how a variant is scored and promoted |
| [`VARIANTS.md`](eval/VARIANTS.md) | the experiment harness: named, hashed configurations |

Retrieval quality through the engine is measured in Vera: its `docs/eval/EVAL.md`.

## project/ — history and open decisions

| doc | |
|---|---|
| [`PROGRESS.md`](project/PROGRESS.md) | the progress tracker |
| [`OPEN_QUESTIONS.md`](project/OPEN_QUESTIONS.md) | decisions not yet made, each with a leaning |
| [`ABSORPTION.md`](project/ABSORPTION.md) | measured survey of the corpus and code Ravel replaced |
| [`MIGRATION.md`](project/MIGRATION.md) | what was ported from where, and the porting rules |
