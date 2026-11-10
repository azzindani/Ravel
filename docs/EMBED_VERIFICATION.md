# EMBED_VERIFICATION.md — Ravel

How the embedding stage was tested, what was measured, and what each number does and does not
prove. Measured 2026-11-08 to 2026-11-10 on one machine: RTX 3070 Ti Laptop GPU (8 GB), Windows 11
with Docker Desktop (WSL2), model `Qwen/Qwen3-Embedding-0.6B` at revision
`97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, fp16 on the GPU.

The rule this page exists to keep: **a vector is verified against code that is not Ravel's.**
Comparing Ravel's code with itself scores 1.0 and certifies nothing (it let a defect through once,
§1).

---

## 1. The tokenization defect (audit of the first corpus)

**Finding.** Qwen3's tokenizer already ends every input with `<|endoftext|>` (id 151643). The first
embedder appended `eos_token_id` after it, but the tokenizer's eos is `<|im_end|>` (151645). Every
vector in the first 1.7M-chunk run was pooled at that extra token: a different space from the
standard recipe.

**How it was shown.** The reference is the model card's own code (`README.md` of the model:
`AutoTokenizer(padding_side='left')`, `max_length 8192`, `last_token_pool`, L2 normalisation), in
fp32, with no Ravel code on the model side. 30 stored chunks from 5 vector shards (first, 25, 50, 75,
last) spread over the length range (2nd to 98th percentile), embedded text taken from
`chunk.derive.embed_text`:

| Recipe computed independently | min cosine vs stored | mean | max |
|---|---|---|---|
| Standard (tokenizer closes once) | 0.7263 | 0.7847 | 0.8418 |
| Standard + one extra `<|im_end|>` | 1.0000 | 1.0000 | 1.0000 |

So the stored vectors are exactly "standard + extra token", and that is the only difference: batch 1
and batch 8 agree at 1.00000 for both recipes (left padding is handled correctly), and the sample
spans five shards, so the shards share one recipe. The bundle's canary sentence agreed the same way
(cosine 0.64 against a plain tokenizer call).

**Consequences.**
- `append_eos` is now an explicit, declared corpus option (`EmbedSpec.append_eos`, default off), so
  the old recipe stays reproducible and has its own fingerprint. The standard recipe is the default.
- The standard corpus must be re-embedded; the legacy vectors stay on disk untouched (generation
  `95c78dc6bd98`) and are never continued by the standard config.
- Minor deviation, not changed: Ravel and Vera use `Query: {text}` (a space) while the model card's
  code uses `Query:{text}`. Cosine between the two forms is 0.964–0.970 on three test questions.

## 2. Ravel's standard path against the reference

After the fix and again after the speed work, Ravel's own code path (`Embedding.embed_texts`, the
code `ravel embed` runs) was compared with the model-card code in fp32 on the same chunks:

| Check | Chunks | min cosine | mean |
|---|---|---|---|
| Ravel standard fp16 vs reference fp32 (after the fix) | 30 | 1.00000 | 1.00000 |
| Ravel token batching + fused RMSNorm vs reference fp32 | 150 | 0.999995 | 0.999998 |
| Ravel `torch.compile` (in the image) vs reference fp32 | 150 | 0.999997 | 0.999999 |
| Reference fp16 (eager) vs reference fp32, for scale | 150 | 0.999995 | 0.999997 |

The speed work changes the maths no more than fp16 itself does.

## 3. Fingerprint check

A vector's fingerprint (the key that stops a resumed run from mixing spaces) carried the
`tokenization` marker for the local backend only. A serving engine's standard vectors had an empty
marker, the same as the legacy `append_eos` vectors, so a resumed run could have blended the two.
The marker is now the same for every backend (`tests/test_embed_run.py`:
`test_the_standard_recipe_has_one_fingerprint_marker_for_every_backend`). Standard fingerprint
`2231c38c768a08c7`; legacy `55190fd42d174e7f`.

## 4. Speed

### Method
3,000 real chunks (375 from each of 8 shards chosen with seed 7), text as embedded in the corpus,
the same chunks for every path. Throughput is chunks per second over a full pass after a warm-up
pass. Variants alternate (old, new, old, new, ...) on the same warmed GPU, because the card throttles
as it heats: SM clock fell from about 1780 MHz cold to about 1000 MHz hot, and a number without its
clock is not comparable. Each result lists the mean SM clock; "@1785" divides by clock and multiplies
by the maximum (an approximation that assumes throughput scales linearly with clock).

### Where the time went (baseline profile)
Eager path, one mid-size batch: matrix multiplies 49% of GPU time, attention (memory-efficient
kernel, with the padding mask) 17%, element-wise and dtype-cast kernels 19%. The byte-based batching
spent **32.3%** of the GPU's work on padding (1.48M padded tokens for 1.00M real).

### Results
| Change | Measured | Notes |
|---|---|---|
| Pad-free token batching (`batch_tokens`) | 26.7 to 37.1 chunks/s (1.39x) | padding 32.3% to 2.5% |
| Fused RMSNorm (`fused_norm`) | about +16% clock-normalised | cosine 0.999995 vs fp32, no worse |
| Both, alternating on one warmed GPU (Windows) | 21.0 to 33.0 (1.57x raw, 1.74x normalised) | 3 passes each |
| Fused gate/up matmul | no gain | not kept |
| Mask-free attention (equal-length batches) | 1.01x | small test; not kept |
| `torch.compile` in the `ravel-job` image | eager 36.3 to compiled 42.1 (1.16x raw, 1.17x normalised) | hot GPU, 3 passes |

### Cooling (fans at maximum), same chunks, 3 passes each
| Path | chunks/s per pass | mean | SM clock | normalised @1785 |
|---|---|---|---|---|
| Image, eager | 54.0 / 48.2 / 46.8 | 49.7 | 1570 to 1340 MHz | 61 to 62 |
| Image, `compile: true` | 60.6 / 57.2 / 56.2 | **58.0** | 1460 to 1370 MHz | 73 to 74 |
| vLLM server through `kind: api` | 68.6 / 65.9 / 60.8 | 65.1 | 1690 to 1420 MHz | 73 to 77 |

Against the original path (21.0 chunks/s, hot): compiled 2.8x, vLLM 3.1x raw. At equal clock the
compiled path and vLLM are the same within noise, so vLLM's raw lead is its higher clock during its
run. Cooling alone moved compiled from 42.1 to 58.0 chunks/s (+38%).

**Projection.** 1,716,628 chunks at 58 chunks/s is about 8.2 hours; clocks keep falling as the card
heats, so expect 8 to 9. This is an extrapolation from a 3,000-chunk benchmark, not a full run.
The 5x target (about 105 chunks/s) was not reached on this card.

### Why a serving engine does not help here
Mean chunk length is about 334 tokens, so attention is a small share of the compute; the matrix
multiplies dominate and run on the same vendor libraries either way; token batching already removed
the padding; `torch.compile` fused the element-wise kernels. What remains is the card's compute and
clock. A bigger card, where batching across requests pays, is where vLLM is expected to help
(`docker/compose.vllm.yml`).

## 5. Serving engine verification (vLLM)

`ravel verify-backend id_reg_markdown --base-url http://localhost:8000/v1` embeds real chunk text and
questions through the endpoint and through the `transformers` reference and exits non-zero below a
cosine floor of 0.999. Run against the pinned vLLM image
(`vllm/vllm-openai@sha256:d825baf9ce4b1b6bfc5ecc7b79880c7e6748c65a72d697ce3d47607aafa84c6e`), 32
chunks + 3 queries, reference computed on the CPU in fp32:

`OpenAICompatEmbedder vs TransformersEmbedder: agree, min cosine 1.00000, mean 1.00000`

vLLM resolved last-token pooling and normalisation from the model's own sentence-transformers
config. The reference runs on the CPU because the server holds most of the GPU.

**Not confirmed:** which attention backend vLLM selected. The startup log was not read for it, so
"FlashAttention" for this run is expected on an Ampere card, not observed.

## 6. Context length

Embedder cap: `max_length 8192`, the model card's value. Chunk budget: the profile
`id_regulation_md@1.2` splits at `max_tokens: 4096` by Ravel's own `token_count`, which is an estimate:
on 2,011 sampled chunks the real Qwen token count was a median 1.44x `token_count` (p95 2.2x, max
6x). By Ravel's count, 25 of 1,716,628 chunks exceed 8192 tokens (largest 20,737), so up to that
many are truncated when embedded; none of the 2,011 sampled chunks exceeded 8192 real tokens (the
largest was 7,341). Left as is: under 0.002% of the corpus, and changing the chunk budget would
re-chunk everything. An exact count over all chunks is possible (CPU only, about 10 minutes) and has
not been run.

## 7. Reproducing

- Image: `docker build -f docker/Dockerfile.job -t ravel-job:gpu .` (installs from `uv.lock`; a C
  compiler is added in the last layer for Triton).
- Run in the image: `docker run --rm --gpus all --ipc=host -e HF_HUB_OFFLINE=1 -v <work>:/work
  -v <hf cache>:/hf ravel-job:gpu <command>`; `compile: true` in the hardware profile takes effect
  there and is skipped, with a warning, on a machine without Triton and a compiler.
- Backend check before building on an engine: `ravel verify-backend <corpus> --base-url ...`.
- The measurement harnesses (A/B timing with a clock sampler, the independent fp32 reference) were
  scratch scripts and are not committed; §4 describes the method so they can be rewritten.

## 8. What is not proven
- No full corpus has been embedded with the standard recipe yet; every speed figure is a
  3,000-chunk benchmark.
- Throughput depends on cooling and clock; the numbers above are for one machine and one cooling
  state.
- Retrieval quality of the standard space against the legacy space has not been measured; that needs
  the labeled eval set (`docs/EVAL.md`), which is still below its gate.
