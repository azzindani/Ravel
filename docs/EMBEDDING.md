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
    normalize: bool          # L2
    doc_instruction: str     # applied to documents ("" for Qwen3)
    query_instruction: str   # applied to queries — recorded, used by Vera

    def embed_documents(self, batch: list[Input]) -> np.ndarray: ...
```

Two implementations to start:

- **Local** — transformers or vLLM on whatever GPU is available. Used for bulk corpus
  embedding: no rate limits, full control of pooling and precision.
- **API** — OpenAI-compatible endpoints (OpenRouter et al.). Used for small corpora,
  for the query side in Vera, and for the round-trip preflight.

The same interface serves both, so a variant swaps embedders by id in config.

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
4. `dtype` and storage precision (fp32 compute → fp16/halfvec storage).
5. `provider` + `provider_pin` when an API was used — routing across hosts can change
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
