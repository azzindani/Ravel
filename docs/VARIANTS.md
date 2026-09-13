# VARIANTS.md — Ravel

The experiment harness. A variant is a named, hashed configuration that produces its own
bundle from the same sources.

---

## 1. The problem it solves

ID_Legal's real defect was not its chunking — it was that nobody could find out whether
its chunking was good. Without a second corpus to compare against, every retrieval
complaint is unfalsifiable and every proposed fix is an opinion.

A variant makes "is chunking on pasal better than 512-token windows?" a question with an
answer.

---

## 2. What is fixed and what varies

**Fixed across all variants of a corpus:**
- the source set (same files, same hashes)
- extraction and cleanup (Phase A) — expensive, shared, cached

**Varies per variant:**

| Dimension | Examples |
|---|---|
| chunker + config | `id_regulation` / `heading` / `token(512, overlap 64)` |
| embedding input template | `body` / `path+body` / `title+path+body` |
| embedder | `qwen3-0.6b-local` / `qwen3-8b-local` / an API embedder |
| dimension / MRL truncation | 4096 / 2560 / 1024 |
| enrichers enabled | ner only / ner + KG / none |
| clustering params | k, seed, domain split |

If an experiment requires changing extraction, it is **not a variant** — it is a new
`canon_version` and a deliberate re-extraction. Keeping that line sharp is what keeps
experiments cheap.

---

## 3. Definition

```yaml
# corpora/id_legal.yaml
corpus: id_legal
sources:
  root: sources/id_legal
  include: ["**/*.pdf"]
  url_template: "https://peraturan.go.id/id/{stem}"

extraction:
  router: { native_if_text_ratio: 0.9 }
  extractor: smoldocling
  cleanup: id_reg_clean@1.0

variants:
  pasal:
    chunker: { id: id_regulation, version: "1.0", max_tokens: 1024 }
    embed:   { id: qwen3-8b-local, template: path+body }
    enrich:  [ner_legal, doc_factors, vocab]
    cluster: { k: 10000, seed: 42 }

  token512:
    chunker: { id: token, size: 512, overlap: 64, carry_heading: true }
    embed:   { id: qwen3-8b-local, template: body }
    enrich:  [doc_factors]
    cluster: { k: 10000, seed: 42 }
```

`config_hash` is the hash of the **fully resolved** config — defaults expanded, versions
pinned. It names the work directory and goes into the manifest. Two variants that hash
the same are the same variant, whatever they are called.

---

## 4. Cache reuse is automatic, never implicit

Reuse falls out of the content-addressed keys in `EXECUTION.md` §3:

```
pasal and token512          → share canon/ entirely (Phase A)
same chunker, new embedder  → share chunks and signals; re-embed only
same chunker, new enricher  → share chunks and vectors; enrich only
different chunker           → share only canon/
```

**Rule:** a cache key contains every parameter that can change the output. A variant
silently inheriting another's artifacts because a key was incomplete produces a bundle
whose manifest is a lie — the failure this whole design exists to prevent.

---

## 5. Matrix runs

```
ravel build id_legal --matrix chunker=id_regulation,token512 template=body,path+body
```

Expands to four variants, ordered so shared work runs once. Sensible discipline:

- **Sweep on a sample first.** A 2% stratified sample of the corpus answers most
  chunking questions at 2% of the cost. Only the finalists run full-size.
- **One dimension at a time** unless you specifically want interactions.
- **Always keep a baseline arm** (`token512`) in the matrix. Without it, results drift
  with every other change and you lose the ability to say whether things got better.

`Vera/pipelines/pre_embed/eval_arms.py` is the seed of this; `sample.py` is the seed of
sampling.

---

## 6. Promotion

A variant becomes the production corpus when:

1. it beats the incumbent on the labeled eval set (`EVAL.md`), by a margin bigger than
   the set's noise;
2. it passes bundle preflight (`BUNDLE.md` §5);
3. its cost and row count are acceptable — a 3% recall gain for 2.5× the rows may not be
   worth it, and that is a judgment call that should be made explicitly, with the
   numbers in front of you.

Promotion is recorded: which variant, which bundle version, which eval run, what the
margin was. The losing bundles are kept until disk pressure says otherwise — they are the
cheapest possible rollback.
