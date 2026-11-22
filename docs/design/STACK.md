# STACK.md — Ravel

The technology choices, the constraint that drives them, and what is deliberately not
used.

---

## 1. The constraint

> The same code must run on a Windows laptop with 8GB VRAM and on a rented Linux GPU box,
> and produce identical artifacts.

Windows silently lacks much of the fast-path GPU ecosystem. So every accelerated
component needs a portable baseline and an optional fast path behind the same interface.

| Capability | Windows local | Rented Linux |
|---|---|---|
| transformers + CUDA | yes | yes |
| vLLM | **no** | yes (the fast path) |
| faiss GPU | CPU only | yes |
| flash-attn / triton | no | yes |

**Rule:** vLLM and faiss-gpu are accelerators, never dependencies. If any stage cannot
run without them, local development is dead and with it the tight iteration loop this
project exists to provide.

---

## 2. Python 3.14

Standard CPython 3.14, managed by **uv**. Verified by resolution on 2026-09-12 — the
whole stack has `cp314` wheels, including vLLM on Linux:

```
torch==2.14.0         transformers==5.17.0    faiss-cpu==1.15.0
pyarrow==25.0.1       numpy==2.5.3            pymupdf==1.28.2
pydantic==2.13.5      psycopg==3.3.5          scikit-learn==1.9.1
docling==2.126.0      sentence-transformers==6.0.1
mcp==2.2.0            fastapi==0.141.1        typer==0.27.2
vllm==0.29.0 (linux)  spacy==3.8.16           datasets==5.0.1
```

### What a single-package check did not catch

The table above was produced by resolving each package **on its own**. Building the
real environment on 2026-09-12 surfaced three conflicts that only appear when
everything must coexist — worth recording, because each one is a design signal:

| Conflict | Reality |
|---|---|
| `docling` pins `typer>=0.12.5,<0.27` | the CLI's typer floor is capped by the extraction library |
| `vllm==0.29.0` pins `torch==2.13.0` **exactly** and caps `fastapi<0.137` | vLLM dictates torch and FastAPI wherever it is installed |
| `faiss-gpu-cu12` has **no `cp314` wheels** (max `cp313`) | GPU faiss is simply unavailable on 3.14 |

The last two are why there is **no `[gpu]` extra** (§6). An optional accelerator that
pins torch exactly and caps the web framework would end up choosing versions for the
entire project — the precise inversion §1 forbids. vLLM installs as its own layer on
the GPU box; GPU k-means goes through **torch**, not faiss-gpu.

> Resolution is not compatibility. Check the union you will actually install, not the
> packages one at a time.

**What 3.14 gives Ravel specifically**

- `compression.zstd` in the stdlib — parquet and checkpoint compression, no extra dep
- PEP 649 deferred annotations — cleaner pydantic models, no `from __future__` dance
- Faster interpreter — matters across millions of blocks in the cleanup passes
- `concurrent.interpreters` — a future option for CPU pools without Windows' expensive
  process spawn

**Not free-threaded (`3.14t`).** Free-threading is officially supported in 3.14 and would
genuinely suit the CPU-bound parse and cleanup stages, but `cp314t` wheels are sparse
across the ML stack — you would be building torch and faiss yourself. The process-pool
design does not block a later switch. Revisit when the wheels arrive.

**uv, not conda.** uv handles the PyTorch CUDA index split, produces a real lockfile, and
is fast on Windows. Mixing conda and uv environments is a reliable source of a lost
afternoon.

---

## 3. The stack

| Layer | Choice | Why |
|---|---|---|
| Packaging | **uv** + lockfile | reproducible across all five environments |
| Config | **pydantic v2** + YAML | validation, JSON Schema, and a canonical serialization — which is exactly what `config_hash` needs |
| CLI | **Typer** + Rich | progress tables free; matches the `ravel <verb>` surface |
| Storage I/O | **pyarrow** + **fsspec** | one URI scheme everywhere: `file://`, `s3://` (R2), `hf://`. This *is* the portability mechanism |
| Canonical format | pydantic models → parquet | schema generated from models, never hand-written |
| PDF / native text | **PyMuPDF** | text, layout, bbox and page rasters in one fast library |
| Structure extraction | **docling** driving SmolDocling (+ Tesseract OCR); vLLM on Linux | the harness every working notebook already uses (`ABSORPTION.md` §8); fits 8GB; emits the markdown structure the canonical format wants |
| Raw-transformers escape hatch | transformers | when docling's abstraction is in the way |
| Fallback extractors | native PyMuPDF, OCR (Nanonets/QwenVL) | behind the router interface |
| Embedding | transformers / sentence-transformers; **TEI** (Docker, works on Windows); vLLM remote; `openai` client for APIs | four backends, one `Embedder` protocol. TEI is what `pre_embed/ingest.py` already uses |
| Clustering | **faiss-cpu** everywhere; **torch** for GPU k-means | faiss-gpu has no cp314 wheels. At ID_Legal's 748K rows and k≈75 this is not close to a bottleneck (`ABSORPTION.md` §1) |
| Enrichment | spaCy or transformers, per enricher | pluggable, never core |
| Postgres | **psycopg3**, binary `COPY` | the only loader worth using at this scale |
| MCP | **FastMCP** (official SDK) | matches Vera's tool-surface style |
| HTTP | FastAPI + uvicorn | separate image from the batch runner |
| Tests | pytest + small fixture PDFs | canon round-trip is the critical test |

---

## 4. The no-orchestrator decision

**No Airflow, Prefect, Dagster, Ray or Dask.**

Durability comes from **content-addressed idempotence**, not from an orchestrator's
state (`EXECUTION.md` §3). Given that, the work queue is *derivable*: what still needs
doing equals required keys minus existing keys. The local SQLite queue is therefore a
**cache of remote object state** — rebuildable from a listing, so losing it when a Kaggle
session dies costs a re-listing, not a re-run.

That is strictly better than synchronizing a stateful queue database to object storage,
which is a distributed-consistency problem with no upside here. It also keeps the runtime
to `asyncio` + a process pool + one worker per GPU: a few hundred lines you own, rather
than a framework you fight on Windows.

Ray earns its place at genuine multi-node scale. Nothing here closes that door — the
queue interface is small enough to reimplement over a shared Postgres when two rented
boxes need to cooperate on one build.

---

## 5. Environments

| Name | Where | Purpose |
|---|---|---|
| `dev` | Windows, RTX 3070 Ti 8GB | full pipeline at small scale: SmolDocling + Qwen3-0.6B/1024 |
| `gpu` | rented Linux (Vast/RunPod) | production builds: vLLM + Qwen3-8B/4096 + faiss-gpu |
| `notebook` | Kaggle / Colab | opportunistic batch work; self-stops before the wall clock |
| `service` | Docker | the HTTP parsing endpoint |
| `ci` | GitHub Actions, CPU | lint, unit tests, canon round-trip, fixture builds |

8GB VRAM settles a question that was open: **Qwen3-Embedding-8B cannot run locally**
(~16GB fp16). Local development is 0.6B/1024, production is 8B/4096 on rented GPU. This
is a feature — it forces the embedder plugin boundary to be real from day one rather than
aspirational.

---

## 6. Dependency groups

Keep the base install small; the endpoint image should not carry k-means.

```
ravel            core: pydantic, pyarrow, fsspec, typer, numpy
ravel[extract]   pymupdf, transformers, torch, docling
ravel[embed]     transformers, sentence-transformers, openai
ravel[cluster]   faiss-cpu, scikit-learn
ravel[load]      psycopg[binary]
ravel[mcp]       mcp
ravel[service]   fastapi, uvicorn
ravel[remote]    huggingface-hub, s3fs
ravel[dev]       pytest, ruff, mypy

docker/requirements-gpu.txt      vllm — a SEPARATE layer, never an extra
```

**There is deliberately no `[gpu]` extra.** vLLM pins torch exactly and caps fastapi;
including it makes every other version in the project vLLM's decision. It is installed
after the project, in its own layer, on the one environment that needs it.

---

## 7. Version policy

- **Pin exactly in the lockfile, float in `pyproject.toml`.** Reproducibility comes from
  the lock, not from pessimistic pins that fight each other.
- **Model versions are pinned separately, in the corpus manifest** (`BUNDLE.md` §3) — not
  in the lockfile. A library upgrade must never silently change a model revision.
- **Upgrading torch or transformers invalidates nothing by itself**, but any change that
  alters embedding output is an embedder version bump and therefore a new cache key
  (`LOOPHOLES.md` §1). When in doubt, run the round-trip preflight.
