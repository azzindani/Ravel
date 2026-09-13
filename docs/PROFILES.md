# PROFILES.md — Ravel

Document profiles: the declarative half of parsing. How to read one, how to write one,
and why none of this is Python.

---

## 1. The rule

> **The engine is generic. The corpus knowledge is data.**

A **profile** is everything Ravel knows about a *kind of document*: how to recognize it,
what its structure markers look like, how to read its identity, what counts as
boilerplate. It lives in `registry/profiles/<id>@<version>.yaml` and is loaded,
validated and hashed — never imported.

This is not tidiness. `ABSORPTION.md` §4 records the failure it prevents: the knowledge
needed to parse Indonesian regulations *already existed* in
`06_ID_Legal/core/legal_vocab.py`, while the parser — living in a notebook — hardcoded
`PERATURAN MENTERI KEUANGAN`. The result was **10.66% `Unknown` regulation_type across
748,558 rows**. Knowledge that lives in code is knowledge that does not reach the person
who needs it.

A profile describes a **kind of document, not a file format.** The same
`id_regulation` profile finds `Pasal 9` in a born-digital PDF, a scraped HTML page, a
Markdown export and an OCR transcript, because it reads canonical blocks rather than
bytes.

---

## 2. Anatomy

```yaml
id: id_regulation          # [a-z][a-z0-9_]*
version: "1.0"             # major.minor; the file must be named <id>@<version>.yaml
title: Indonesian laws and regulations
description: >
  What this covers, and anything a future reader needs in order to change it safely.

match:                     # how the router decides this profile applies
  mime: [application/pdf, text/html, text/plain, text/markdown]
  content: ['(?i)\bundang-undang\b']     # tried against the first few KB
  min_hits: 1
  priority: 0              # tie-breaker; the generic fallback uses -100

structure:
  max_line_chars: 120      # longer than this is prose, never a heading
  max_title_chars: 80      # a longer continuation is prose, not a section title
  prose_tail: [".", ":", ";", ","]
  units:                   # the nesting hierarchy, outermost first
    - name: bab
      level: 3
      pattern: '^BAB\s+(?:[IVXLCDM]+|\d+)\b'
    - name: pasal
      level: 6
      pattern: '^PASAL\s+\d+[A-Z]?\b'
      marker_only: true    # see §4
  sections:                # headings that are not part of the hierarchy
    - {name: memutuskan, level: 3, pattern: '^MEMUTUSKAN\s*:?\s*$'}
  subunits:                # markers INSIDE a unit; not headings
    ayat: '^\((\d+)\)\s+'

identity:                  # read the document's own identity out of its text
  types: [UNDANG-UNDANG, PERATURAN BUPATI, ...]
  title: '(?P<type>{types})\s+...NOMOR\s+(?P<number>[\w./-]+)...'
  cross_ref: 'Pasal\s+(?P<pasal>\d+[A-Z]?)...'
  flags: [ignorecase]

cleanup:
  boilerplate: ['^\s*cukup\s+jelas\.?\s*$']
  min_indexable_chars: 40

extract:                   # per-extractor overrides and engine switches
  native: {heading_size_ratio: 1.12}
  trust_typography: false  # see §5
```

`{types}` in any identity pattern expands to an alternation of the `types` list, so the
list is maintained once and every pattern stays in sync with it. Types are matched
**longest-first**, which is why `PERATURAN DAERAH KABUPATEN` never truncates to
`PERATURAN DAERAH`.

---

## 3. Levels

Levels are **fixed per unit**, not derived from nesting depth. Two documents chunked at
different depths therefore produce comparable heading paths, and a corpus that mixes
document shapes still sorts consistently. Indonesian regulations use:

```
2 buku      3 bab      4 bagian      5 paragraf      6 pasal
```

Leave gaps. A level you did not think of is cheaper to insert than a whole corpus is to
renumber.

---

## 4. `marker_only` — the flag that matters most

Set it on any unit whose name commonly opens a sentence.

```
Pasal 9                              -> heading
Pasal 9 dihapus.                     -> prose  (an amendment clause)
Pasal 12 diubah sehingga berbunyi:   -> prose
BAB II KETENTUAN UMUM                -> heading (a title may follow BAB)
```

Amending regulations are full of lines that open with a marker and are prose. Without
this flag the chunker splits a document at every amendment clause — and, worse, any
*measurement* built on the same detector is corrupted in the same direction, so the
error hides itself.

For units that do take a title on the same line (`BAB`, `BAGIAN`), the continuation is
accepted only if it is short and does not end in sentence punctuation. Crude on purpose:
the alternative is a grammar, and this only has to be right often enough.

---

## 5. `trust_typography`

Whether the extractor's typographic heading guesses survive the structure pass.

Measured on 56 real Indonesian regulations, structural markers are **2.7% bold**, set at
**1.0x body size**, **58.4% centered**. Typography-based detection scores **15.4% recall
at 4.0% precision** there — so `id_regulation` sets this **false**, and the structurer
demotes ~7,000 false headings per 50 documents.

`generic` sets it **true**, because a profile with no corpus patterns of its own has
nothing better to go on than font size.

The rule: a profile that *has* real structure patterns should own heading assignment
outright.

---

## 6. Writing one

```bash
ravel profiles list                              # what exists
ravel profiles show id_regulation                # units and patterns
ravel profiles try generic "CHAPTER IV" "3.2 Scope" "This chapter explains..."
ravel profiles route path/to/document.html       # which profile wins, and by how much
```

`profiles try` is the loop: paste real lines from the corpus — including the ones that
*look* like headings but are not — and iterate until the classification is right. The
MCP authoring tools (`INTERFACES.md` §2) wrap this same loop for an agent.

**Checklist for a new profile**

1. Collect 20–30 real lines: headings, near-misses, and prose that opens with a marker.
2. Write `structure.units` outermost-first, assigning levels with gaps.
3. Set `marker_only` on anything that can open a sentence.
4. Add `match.content` patterns specific enough not to steal other corpora's documents.
5. Run `profiles try` over the collected lines; every near-miss must classify as `None`.
6. Add the near-misses to `tests/test_profiles.py` — they are corpus regressions, and
   they are the reason a future edit will not quietly undo this work.
7. Run the false-positive guard: `pytest -m samples`.

### The false-positive guard

Step 5 checks near-misses you thought of. `tests/test_routing_samples.py` checks the ones
you did not, against **158 real PDFs of which 144 are not regulations** — contracts,
resumes, invoices, US federal documents, exchange filings. None of them may route to
`id_regulation`; the Indonesian ones must.

This is the half of routing that fails silently. Widening `match.content` produces no
error and no crash — it produces a *plausible* parse of a document the profile should
never have seen, and `identity.title` then trusts whatever it extracted from it. A résumé
routed to `id_regulation` yields a chunk claiming to be `Pasal 3` of something.

The guard is calibrated, not decorative: adding one generic pattern —
`'(?i)(agreement|pursuant to|whereas)'` — to `match.content` still compiles, still passes
every test in `test_profiles.py`, still routes all the Indonesian documents correctly,
**and fails 53 of these**. That is the margin it protects.

It is marked `samples` and excluded from the default suite, because it reads real PDFs and
needs the `extract` extra; the hermetic suite stays under four seconds so it keeps being
run. `ABSORPTION.md` §12 is the near-miss that motivated it.

---

## 7. Versioning

A registry entry is **immutable once used in a published bundle**. A change means a new
version file, because `config_hash` — the SHA-256 of the fully resolved spec — is folded
into every downstream cache key (`LOOPHOLES.md` §1).

This is a feature while iterating: edit a pattern, and every artifact built from the old
one is invalidated automatically rather than silently reused. It only becomes a rule once
a bundle ships.

The filename must agree with the contents. A file named `x@1.0.yaml` declaring version
`1.1` is refused at load, because that ambiguity lands directly in a cache key.

---

## 8. What does not belong in a profile

- **Anything corpus-specific in Python.** If you are adding a regex to a `.py` file, it
  probably belongs here instead.
- **Extractor mechanics.** How to read a PDF is the extractor's job; what its lines
  *mean* is the profile's.
- **Chunk sizing and embedding choices.** Those are variant config (`VARIANTS.md`), not
  document knowledge — the same profile should serve every variant.
- **Anything that needs to run code.** A profile is data. A rule that genuinely needs
  logic is a new engine capability, added generically and driven by a new profile field.
