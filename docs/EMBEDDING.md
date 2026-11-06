# EMBEDDING.md — Ravel

The embedder is a **plugin**, not a constant. This doc defines the interface, the
consistency contract, and what changes when the model changes.

> Vera's `EMBEDDING.md` pins one model for one deployment. Ravel must be able to *build
> a corpus with any of them* and record precisely which one it used. Those are not in
> conflict: Ravel is the factory, Vera's manifest is the receipt.

---

## 1. The plugin interface

```python
class Embedder(Protocol):
    id: str                  # "qwen3-8b-local", "openrouter-qwen3-8b", …
    model: str               # "Qwen/Qwen3-Embedding-8B"
    model_version: str       # exact revision/commit — never "latest"
    dim: int                 # 1024 | 2560 | 4096 | …
    modality: str            # "text" | "omni"
    pooling: str             # "last_token" | "mean" | "cls"
    padding_side: str        # "left" | "right" — not cosmetic; see §3
    normalize: bool          # L2
    instruction_style: str   # "none" | "prefix" | "chat_system"
    doc_instruction: str     # applied to documents ("" for Qwen3 text)
    query_instruction: str   # applied to queries — recorded, used by Vera

    def embed_documents(self, batch: list[Input]) -> np.ndarray: ...
```

**Implemented in `src/embed/`.** Three pieces, and the shape of each is a direct answer
to `ABSORPTION.md` §11:

- `spec.py` — `EmbedderSpec` is the manifest, and it **refuses to hold the defect**: an
  instruction string supplied with `instruction_style='none'` raises, because that is
  precisely an instruction that exists and is never applied. `fingerprint()` covers every
  field that changes a vector, so `padding_side` moving invalidates a cache the way a
  model change does.
- Rendering is a **pure function**, `render_document` / `render_query`, not a method an
  implementation may forget to call. The incumbent's seam was
  `def prepare_texts(self, contents): return contents` — a hook for exactly this,
  implemented as a pass-through. An embedder that does not route through the renderer
  produces vectors that fail the round trip.
- `preflight.py` — `check_instruction_is_applied` asserts the invariant rather than
  replaying the construction (`CLAUDE.md` §15): if the manifest declares an instruction,
  the rendered input must differ from the raw text. `preflight()` runs that, the spec
  comparison, and the cosine round trip, and **skips the round trip rather than faking it**
  when the specs already disagree — comparing vectors across two spaces produces a number,
  and reporting it as a cosine result is worse than reporting nothing.

`hashing.py` provides a deterministic embedder seeded from SHA-256, so the plugin
boundary, the manifest and the preflight are all testable with no GPU, no download and no
network — alongside `RawHashEmbedder`, which is the incumbent's bug preserved as a fixture
so the check cannot quietly become unfalsifiable.

Two implementations to start:

- **Local** — transformers or vLLM on whatever GPU is available. Used for bulk corpus
  embedding: no rate limits, full control of pooling and precision.
- **API** — OpenAI-compatible endpoints (OpenRouter et al.). Used for small corpora,
  for the query side in Vera, and for the round-trip preflight.

The same interface serves both, so a variant swaps embedders by id in config.

**Built.** `embed/local.py` (`TransformersEmbedder`) and `embed/api.py`
(`OpenAICompatEmbedder`), chosen by the corpus's `embed:` section (`kind: local | api |
hash`) through `embed/factory.py`. Both import their heavy dependencies when an embedder is
*built*, so a machine without torch still imports `embed` and runs every other stage.

- *Local* pools `last_token` / `mean` / `cls`, honours `padding_side` (the rule is a pure
  function, `last_token_index`, tested without torch), appends the end-of-text token itself
  for last-token models (Qwen3's tokenizer does not), pins the revision, and **halves a batch
  on out-of-memory** instead of dying hours in, because a legal corpus has 3-character
  chunks and 30,000-character ones in one shard.
- *API* reads the key from an environment variable the config names (never the config),
  retries 429/5xx with backoff, orders the response by `index`, and sends `provider_pin` as
  the only allowed provider with fallbacks off.

**Fixed while building it: a document was rendered with an empty instruction.** Qwen3 puts
the instruction on the query and embeds a document bare (`doc_instruction` is `""`), but
`render_document` under `instruction_style: prefix` produced `Instruct: 
Query: <body>` for
it, a space no query is asked from. An empty instruction now renders the text unchanged.

---

## 2. Dimension is a variable, so the schema is generated

Vera's `0001_init.sql` hardcodes `halfvec(4096)` because Vera is one deployment. Ravel
cannot: a 0.6B model is 1024, a 4B is 2560, an 8B is 4096, an MRL truncation is whatever
you chose.

Therefore **Ravel generates `schema.sql` from the manifest** and ships it inside the
bundle (`BUNDLE.md` §4). The bundle is self-describing: it carries the DDL that will
hold it.

Corollary: **a bundle and a database are married by dimension.** Loading a 1024-dim
bundle into a 4096-dim database must fail at preflight, loudly. It is a one-line check
and it prevents a class of silent disaster.

---

## 3. The consistency contract

A vector-space mismatch produces **no error** — only quietly worse results. Everything
here exists to make that failure loud.

The manifest records, and the loader verifies:

1. `model` + `model_version` — the exact revision. A floating alias can update under you.
2. `doc_instruction` and `query_instruction` — verbatim strings. Qwen3 is
   instruction-aware: the instruction goes on queries, never on documents, and the query
   instruction must be identical to the one the corpus was built against.
3. `pooling` and `normalize`.
4. `padding_side`, whenever `pooling` is `last_token`. With left padding the last token
   is `hidden[:, -1]`; with right padding it is at `attention_mask.sum(1) - 1`. Read the
   wrong end and you pool a PAD embedding — a well-formed vector, silently meaningless,
   for every text in the batch shorter than the longest. The two local Qwen3 encoders
   disagree on this (`ABSORPTION.md` §11): the text one is loaded `padding_side='left'`,
   the VL one `padding_side='right'`. It is a property of the tokenizer as loaded, not of
   the model, so it cannot be recovered from the checkpoint afterwards — it has to be
   recorded at build time.
5. `instruction_style`, because "the instruction string" is not always a string.
   Qwen3 text takes a prefix (`Instruct: {task}
Query: {query}`); Qwen3-VL takes a
   **system turn in a chat template**, and normalizes it by appending `.` when it does not
   already end in punctuation. A manifest that records only `query_instruction: str`
   cannot tell Vera which of those to reproduce, and the two do not produce the same
   vector.
6. `dtype` and storage precision (fp32 compute → fp16/halfvec storage).
7. `provider` + `provider_pin` when an API was used — routing across hosts can change
   serving config.

**Round-trip preflight (mandatory before a large run):** embed the same sample with the
build-side embedder and the query-side embedder Vera will use; require cosine ≥ 0.999.
Do not spend GPU-days until it passes. Store the sample and the reference vector in the
bundle so Vera's startup canary has something to check against.

---

## 4. Omni / multimodal is a second space, never a blend

An omni embedder (page image, audio, video) produces vectors in a **different space**
from the text model. They cannot be compared with one query vector. Concretely:

- separate column (`embedding_omni`), separate dimension, separate manifest block
- its own query-side embedder on Vera's side
- fusion happens at **rank level** (RRF across two result lists), never by averaging or
  concatenating vectors
- clustering is per space: a corpus with two spaces has two sets of centroids

The bundle format must be able to carry a second vector column from day one even though
the first corpora will not use it — retrofitting a second space into a 100M-row bundle
format is the kind of migration that does not happen.

Scanned pages, stamped/signed contract pages and complex tables are where this pays off,
matching Vera's deferred v2 note (`Vera/PRE_EMBEDDING.md` §6).

---

## 5. What gets embedded

Not necessarily `body`. The **embedding input template** is a variant dimension:

| Template | Input |
|---|---|
| `body` | the chunk body alone |
| `path+body` | `heading_path` ‖ body — gives the model structural context |
| `title+path+body` | plus the document title |
| `body+summary` | body plus an LLM-derived summary signal |

`path+body` usually helps because a bare ayat is meaningless without its pasal. It is
also a real trade-off: repeated path text is repeated tokens in every vector. Measure
it; do not assume it (`EVAL.md`).

The template is the profile's `pipeline.derive.embed_text` (`PIPELINE.md` §3.1). It is part
of every window's key, so changing it re-embeds and never re-chunks.

**Whatever the template, `body` stored in the bundle stays verbatim.** The template
affects what the model saw, not what the human verifies. The template string is recorded
in the manifest.

---

## 6. Batching and throughput

- Batch by **token budget**, not row count — legal chunks vary enormously.
- Sort by length within a shard to cut padding waste.
- Checkpoint every N shards; a vector shard is the resume unit (`EXECUTION.md`).
- Vectors are staged to parquet as fp16 before load, so the database can be rebuilt
  without re-embedding — the single most important cost-saving property of the pipeline.
**Built: `ravel embed <corpus> [--variant v]`** (`embed/run.py`).

- The resume unit is a window of `embed.shard_rows` vectors (default 20,000). The shard is
  sealed on disk before its key reaches the ledger, so a resumed run cannot skip work that
  was never written. A dead box costs at most one window.
- Every key carries the embedder's fingerprint, the template hash and the chunk
  generation; so does the output directory. A different model, padding side, instruction or
  template cannot reuse another's vectors.
- Longest texts first, batched by payload bytes and item count (`runtime.batching`), results
  returned to their own positions. An out-of-memory then surfaces in the first minute.
- Every window is checked before it is written: the declared width, finite numbers, and unit
  norm if the manifest says normalised. A NaN is a stack trace, not a corpus.
- **Nothing is skipped.** A document the quality gate rejected is embedded too (about 2.4% of
  the work): the bundle joins vectors to chunks one to one, and `indexable` already keeps it
  out of search (`QUALITY.md` §6).
- **Measured, on CPU only.** 12 real regulations (434 chunks) through `extract`, `chunk` and
  `embed` with Qwen3-Embedding-0.6B: 434 vectors, unit norm to 4e-5, a second run does
  nothing, and a question about retribution tariffs ranks the tariff articles first. A
  text embeds the same alone and inside a padded batch (cosine > 0.99), and pooling the
  wrong end visibly breaks the short text (the negative control, `tests/test_embed_local_real.py`,
  run with `RAVEL_TEST_MODEL`). **Speed was 1.2 vectors/s on CPU, so the 1.33M chunks of
  `id_reg_markdown` need a GPU; no GPU run has been done.** A CUDA torch wheel is 2.6 GB.
- Cost is planning-relevant, not incidental: ~100M chunks × ~500 tokens ≈ 50B tokens is
  a multi-GPU-day job. Treat a re-embed as a scheduled event, not a crisis.

---

## 7. Re-embedding

A pinned model deprecating, a dimension change, or a better model appearing all mean a
re-embed. It is affordable because chunks and canonical documents survive: only the
vector stage re-runs, from the same chunk ids, producing a new bundle version. Old
bundle stays loadable until the new one passes eval.

---

## 8. Open questions

- v1 model: 0.6B/1024 to prove the pipeline cheaply, then 8B/4096 for production? The
  pipeline is model-agnostic, so this is a sequencing question, not an architectural one.
  (`OPEN_QUESTIONS.md` §1)
- Whether to also stage a truncated MRL vector (1024) alongside the full 4096 to enable
  Vera's "rescore pattern" without re-embedding. Cheap to do at build time, impossible to
  add later without a re-read of the whole corpus. Leaning yes.
