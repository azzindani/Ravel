# ENRICHMENT.md — Ravel

Everything computed about a chunk that is not the chunk: entities, graph edges, factors,
scores, keyword statistics.

---

## 1. What enrichment is for

Vera's ranker is hybrid: dense vectors plus BM25, fused with RRF. Enrichment exists to
give that ranker **more to work with than the raw body text** — filters, boosts, exact
handles and a second retrieval hop.

Enrichment is *speculative by nature*. Most signals will not improve ranking. That is
fine and expected — but it dictates the storage design.

---

## 2. The mutability rule

> **Provenance is immutable and hard-columned. Enrichment is disposable and sidecarred.
> They never share a table.**

If enrichment lived in `chunks`, every improvement to an entity extractor would mean
rewriting a table that is under an immutability trigger and holds the citations people
verify against. The two have opposite lifecycles, so they get opposite storage:

| | `chunks` | `chunk_signals` / `entities` / `edges` |
|---|---|---|
| Written | once, at load | any time |
| Changed | never | freely |
| Dropped | never | routinely |
| Rebuild cost | full re-embed | a CPU pass over existing chunks |
| Schema | fixed columns, NOT NULL | JSONB, graduating to columns |

**Graduation rule:** a signal starts life as a key in `chunk_signals.data` (JSONB). It
becomes a real column only when `EVAL.md` shows the ranker measurably uses it. Designing
columns for untested signals is exactly how ID_Legal got big without getting better.

---

## 3. Signal families

### Document-level factors
Doc type, issuing body, jurisdiction, enactment/effective dates, status
(berlaku/dicabut/diubah), amendment lineage, hierarchy level (UU > PP > Permen).

These are the highest-value signals for legal retrieval, because "is this regulation
still in force?" is a question the ranker should answer, not the human afterwards. They
are extracted once per document and denormalized onto every chunk.

### Chunk-level signals
Entity mentions, defined terms used, cross-references made, numeric quantities (rates,
thresholds, penalties), language, quality score, whether the chunk is definitional,
operative or procedural.

### Keyword statistics
Corpus-level tf-idf / BM25 statistics, key phrases, an acronym and defined-term
dictionary, and a synonym/vocabulary expansion table (`06_ID_Legal` already has
`VOCABULARY_EXPANSION_GUIDE.md` — absorb it).

> Note: Postgres computes BM25 itself from `tsvector`. Ravel's keyword work is about
> what Postgres cannot infer — the vocabulary, the expansions, the domain phrases — plus
> statistics used offline for analysis and for chunk quality scoring.

### LLM-derived signals
Summaries, hypothetical questions (`notebooks/preprocess` has QAI generation
notebooks), plain-language restatements.

**Hard rule:** LLM output is a signal, never a body, never provenance. It may be indexed
as an auxiliary retrieval field; it may never be presented as source text.

---

## 4. Entities and the knowledge graph

A graph is not per-chunk data, so it does not belong in a per-chunk sidecar.

```
entities(id, type, canonical_name, aliases[], attrs, first_seen_doc)
edges(src_entity, relation, dst_entity, evidence_chunk_id, confidence)
mentions(chunk_id, entity_id, span, confidence)
```

Relations worth having for the legal corpus: `amends`, `revokes`, `implements`,
`references`, `defines`, `issued_by`, `supersedes`.

**How Vera would use it (not built yet):** as a second hop. Resolve a query entity →
find its chunks or its related regulations → search within that set. That is a routing
input, not a ranking column, which is why it is separate tables rather than a JSONB key.

`edges` must carry `evidence_chunk_id`. An edge without evidence is an assertion Ravel
cannot back up with a citation, and the whole system's premise is that everything traces
to a source.

`notebooks/ner` has four NER iterations to absorb — port them as enrichers
behind one interface, with the model named and versioned in config.

**None of this section is speculative.** The incumbent corpus already carries 20 KG scalar
columns and 10 JSON payloads per chunk across 748,558 rows — typed entities with character
positions, cross-references with captured groups, domains with confidence, sanctions and
legal actions. Read `ABSORPTION.md` §6 before designing anything here; it also records the
two fields that were columned but never computed (`kg_pagerank`, `kg_degree_centrality`) —
the per-chunk work was done, the graph-wide work never closed.

---

## 5. Execution shape

Enrichers are per-chunk (or per-document) pure functions, registered like chunkers, each
with an id and version. They run in the Phase B queue after chunking. Cheap CPU
enrichers run for every variant; expensive GPU/LLM enrichers are opt-in per variant so a
chunking experiment does not pay for entity extraction it will not evaluate.

Because signals are keyed by `chunk_id + enricher + version`, enrichment can be re-run
over an existing corpus without touching chunks or vectors — the cheap iteration loop
this design is built for.

---

## 6. Open questions

- Do document-level factors get denormalized onto every chunk (fast filtering, larger
  rows) or stay in a `documents` table Vera joins (normalized, an extra join on the
  query path)? Leaning denormalized — Vera's query path is latency-sensitive and rows
  are already large. (`OPEN_QUESTIONS.md` §5)
- Whether entity mentions should be embedded separately to support entity-vector routing.
- Which signals Vera adopts first once eval justifies them — the status/in-force flag is
  the obvious first candidate.
