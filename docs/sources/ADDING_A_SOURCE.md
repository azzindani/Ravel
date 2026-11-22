# ADDING_A_SOURCE.md — Ravel

How a new regulation source becomes a Ravel corpus, a public dataset and a Vera database.
It is the process used for the CFR (`SOURCE_US_CFR.md`) and Canada (`SOURCE_CA_LAWS.md`),
written down so the next source (`SOURCES_NEXT.md`) follows it step by step.

**The rule under all of it: a source is YAML.** A corpus file says where the files are, a
dialect says what the publisher's elements mean, and a profile says how to cite, chunk and
enrich them (`CLAUDE.md` rule 12). Code changes only when a rule is missing that *any*
publisher could need, and such a change ships with proof that the corpora already built did
not move (§8).

```
survey → probe samples → corpus.yaml → dialect.yaml → profile.yaml → trial on samples
      → tests → full fetch → extract → chunk → signals → verify → SOURCE_<ID>.md
      → export dataset → publish → embed (vLLM) → bundle → Vera DB → eval
```

Commit, push and confirm CI after every step that changes the repository.

---

## 1. Survey: is it a source?

Fill in a row of `SOURCES_NEXT.md` before writing anything:

| question | why it decides | CFR | Canada |
|---|---|---|---|
| Who publishes it, and is it the **official** text? | A citation must hold. Prefer the official edition over a convenience copy | GPO annual edition, official (44 U.S.C. 1510) | Justice Canada consolidation, ✗ not official |
| Is there a **bulk**, machine-readable listing? | Ravel fetches what a listing names. It does not crawl | govinfo bulk JSON | GitHub tree |
| What **format**? | XML with structure beats HTML beats PDF | GPO XML | LIMS XML |
| What **licence**? | The dataset is published | public domain | OGL-Canada |
| How is it **updated**? | It decides the edition model | yearly, per volume | every 2 weeks, per commit |

**Pick the text deliberately.** The CFR has two: the annual edition (official, up to a year
old) and the eCFR (daily, editorial). Ravel builds both as separate corpora and says which
one a citation can rest on (`SOURCE_US_CFR.md` §1).

## 2. Probe: read 40 real files first

Download 20–50 files **into the scratchpad, not `sources/`**. Take a random sample, the
largest files, and the oddest (the Criminal Code, the Income Tax Act, the Food and Drug
Regulations, title 3 of the CFR). Then count every element and its parents:

```python
for e in root.iter(): count[e.tag] += 1; parents[e.tag][parent.tag] += 1
```

Answer these from the counts, not from the publisher's README:

- **One document per file, or many?** A CFR volume holds dozens of parts from several
  agencies, so identity is read per part. A Canadian file is one instrument.
- **Nested or flat structure?** GPO nests `PART > SUBPART > SECTION`. Justice Canada prints
  `<Heading level="2">` *beside* the sections it heads.
- **Where is each number?** In an attribute (`N=`), in the heading text (`PART 170`), or in
  its own element (`<Label>`, `<InstrumentNumber>`).
- **What is quoted?** Text an amendment inserts (`REVTXT`, `AmendedText`), or provisions of
  other enactments printed after the law (`RelatedOrNotInForce`). These carry *their own*
  section numbers and must not become structure.
- **What is furniture?** Running heads, tables of contents, page numbers.
- **Tables:** which layout (GPO `BOXHD/ROW/ENT`, HTML `TR/TD`, CALS `tgroup/entry`).
- **Notes worth a signal:** authority, source, amendment history, effective-date notes,
  defined terms, cross-references.

## 3. The corpus: `corpora/<id>.yaml`

```yaml
id: ca_laws
sources:  {root: sources/CA_LAWS, include: ["**/*.xml"], snapshot: current.txt}
fetch:    {kind: github, listing: ..., files: ..., edition: latest, paths: [...]}
provenance: {base_url: <must equal fetch.files>, note: who, what, licence, official or not}
extract:  {profile: ca_laws@1.0, extractors: [xml], failure_tolerance: 0.0}
chunk:    {chunker: markup}
embed:    {use: qwen3-embedding-0.6b@1.0}   # the shared space, registry/embedders/
```

! The vector space is **named, never copied**: `embed.use` resolves the registry file
before the corpus is validated, so every regulation corpus is in one space and one index
holds them all. A corpus may set how it is run beside `use` (`shard_rows`, `hardware`),
never what the vector is: that is a new embedder version. `tests/test_embedders.py`
refuses a corpus that copies a registry space.

| fetch kind | listing | the edition | path below `sources.root` |
|---|---|---|---|
| `govinfo_cfr` | govinfo bulk JSON | newest year per title, **held** while a volume is missing | `<year>/title-<n>/<file>` |
| `ecfr` | versioner `titles.json` | each title's own up-to-date date | `<date>/title-<n>.xml` |
| `github` | repository API, tree at one commit | the commit sha | `<sha>/<repository path>` |

Rules the loader enforces:

- `fetch.files` **is** `provenance.base_url`, so a file's citation is the URL it was read
  from.
- A fetch never overwrites a file. It writes the snapshot only when every wanted file is
  present.
- An edition is **pinned**: a year, a date or a commit, never a branch.
- `concurrency` is 1 for a publisher API and up to 8 for a CDN of static files.

A new publisher whose listing fits none of these needs a new fetch kind in
`src/sources/fetch.py`. Write it generically (`github` serves any repository) and test it
with a fake opener.

## 4. The dialect: `registry/dialects/<id>@<ver>.yaml`

What the publisher's elements mean. The `xml` extractor knows no element name.

| key | decides | example |
|---|---|---|
| `document.detect.root` | which files this dialect reads | `^(?:Statute\|Regulation)$` |
| `document.top` / `trailing` / `compose` / `date` | the outermost unit, the edition date | `FMTR//REVISED`, `.@{lims}current-date` |
| `units.tags` / `by_attr` | nested units | `PART: part`, `DIV5 TYPE=PART` |
| `units.flat` | flat headings with a level | `Heading level="1"` → `heading1` |
| `units.attrs` | values read from paths: `number`, type, dates | `[Identification/InstrumentNumber]`, `['.@regulation-type', '=Act']` |
| `headings` | where a heading is printed: `path`, `join`, `first` | `join: [[Label], [MarginalNote]]` |
| `labels` | heading → `marker`, `name`, `number` | `^(?P<marker>\d[\w.\-]*)\s*(?P<name>.*)$` |
| `skip` / `graphics` | furniture and pictures, counted | `TOC`, `Image` |
| `leaves` / `containers` / `inline` | one paragraph / wrapper with a `kind` / inline markup | `AUTH: authority` |
| `quoted` | containers whose units are text, not structure | `REVTXT`, `RelatedOrNotInForce` |
| `emphasis` | where a defined term is marked | `E T="03"`, `DefinedTermEn` |
| `label` | a short line that opens the next paragraph | `Authority:`, `(1)` |
| `tables` | layout per table element | `GPOTABLE: gpo`, `table: cals` |
| `page` | the element that sets the printed page | `PRTPAGE@P` |

An element the dialect does not name is still read for its text and counted in
`extraction.warnings`. **That count must be zero** before a full build.

## 5. The profile: `registry/profiles/<id>@<ver>.yaml`

- `extract.xml.dialects`: the dialects it reads. The loader inlines them, so
  `structure_hash` covers every rule and a corrected rule re-extracts.
- `structure.units`: one **level** per unit, with gaps where a unit can sit at two depths
  (a Canadian schedule at 2, its headings from 3). `role: chapter` / `article` name the
  units Vera's columns use.
- `identity.markup`: the `scope` unit whose facts every chunk below shares, and the
  `identifier` template per unit. A template with a missing value yields nothing, and the
  next unit out is cited instead.
- `pipeline.chunk`, `derive.embed_text` (what the embedder sees before the body),
  `expected.coverage` (the floors `ravel chunk --enforce` fails under).
- `signals`: `lists` (regex in the body), `blocks` (text of blocks of a `kind` or `tag`,
  over a scope), `definitions`, `outline`.

A change to `signals` alone does not touch the chunk cache: `ravel signals` reruns in
minutes.

## 6. Trial on the samples

Extract and chunk the probe files with a scratch script (`extract → Structurer → markup`).
Read the output before going on:

- `extraction.warnings`: no unknown element.
- Every chunk has an `identifier`.
- **Duplicate identifiers**, each explained. A section split into parts, or chunks under an
  unnumbered heading citing the unit around it, is expected. Two sections with one number
  means a quote read as structure.
- The heading paths of the first 80 blocks of a large file, read by eye.
- Kinds are counted per document (`authority`, `historical_note`, `related_provisions`).

## 7. Tests: `tests/test_<id>.py`

One inline XML per document type, small enough to read, pinning what the build relies on:
heading paths and where units close, the numbers and attributes a unit states, the
labels merged into text, quoted units *not* becoming sections, table spans, identifiers and
facts, every signal column, the fetch plan with a fake opener, and that the corpus loads.

## 8. When the extractor needs a new rule

Add it to `src/spec/xml_dialect.py` (the model) and `src/extract/xml.py` (the walk), named
for what the markup does, never for the publisher. Then prove the built corpora did not
move: run `scratchpad/golden.py`-style dumps of several existing volumes with the old and
the new code and compare them byte for byte. Canada's `join` order change was checked
on 4 CFR volumes, 59,499 blocks: identical.

## 9. Build the snapshot

```bash
ravel sources fetch <id> --dry-run          # the plan: files, editions, holds
ravel sources fetch <id>                    # rerunnable; never overwrites
W=work/<id>/<edition>
ravel extract <id> -w $W
ravel chunk   <id> -w $W --enforce          # fails under expected.coverage
ravel signals <id> -w $W
```

One workspace per snapshot. Sources are read-only and never in git.

## 10. Verify the build

- **Counts**: documents, failed (must be 0), units, reserved, skipped, `quotes_ended`,
  unknown elements (0).
- **Chunks**: total, tokens (median, the largest, the share over `max_row_tokens`),
  identifier coverage.
- **Identifiers against the XML**: when a build replaces an earlier one, every identifier
  lost or gained is traced to the markup. For the CFR: 35 existed only in quoted text, 8 were
  artefacts of stranded notes, and 10,170 were gained from bare-numbered sections now cited
  correctly.
- **Signal coverage** per column (the share of chunks with a value), with the reason for any
  surprisingly low one.

## 11. Document it: `docs/SOURCE_<ID>.md`

Write it in this order: the source (with licence and official status), fetch, extract (a
units table, the generic rules it needed, what is quoted and why), identity (example
identifiers), signals (a column → read-from table), and the build (every number with the
command that produced it). Then update `SOURCES_NEXT.md`.

## 12. Publish the dataset

```bash
ravel export <id> -w $W --out <dir>         # data/*.parquet + sources/manifest.jsonl + snapshot
```

With vectors (after `ravel embed`, §13), the same command adds an `embedding` column:

```bash
ravel export <id> -w $W --out <dir> --vectors   # each chunk's dense vector, joined by id
```

The card then states the model and revision, the exact text that was embedded (the profile's
`derive.embed_text` template), and the query instruction. Re-embed a few rows from that
template and check that cosine is 1.0 before upload: it proves the card's recipe is the one
the vectors were made with (`CA_LAWS_2611`, 2026-11-22: five rows, 1.0000).

Write the dataset card in `datasets/<NAME>.md` (`datasets/README.md`), then
`ravel publish <id> -w $W --repo Azzindani/<NAME>` exports, puts the card beside the data
and uploads both. The card covers: source and licence, official status, columns, signal coverage,
build commands, changes since the last version. Upload with `HfApi.upload_folder` and
`delete_patterns` for the old shards. **Verify on the Hub**: the new commit exists, and the
row and column counts read back from the parquet metadata. A transfer can finish while the
commit never arrives; a rerun then sends 0 bytes and commits.

## 13. Into Vera

```bash
ravel verify-backend <id> -w $W --base-url http://localhost:8000/v1   # the vLLM container
ravel embed  <id> -w $W --base-url http://localhost:8000/v1 --concurrency 8
ravel sparse <id> -w $W
ravel bundle build  <id> -w $W --out $B
ravel bundle verify $B
ravel load $B --dsn $DATABASE_URL                                     # all or nothing
```

Then k-means, gate and canary, and the eval: **Recall @5 / @10 / @15 / @20 / @25**, all
five, on a question set for the source (Vera `docs/eval/EVAL.md`). A source without an eval set
says so: "determined, ✗ measured".

---

## Lessons, so they are not learned twice

| source | what happened | the rule now |
|---|---|---|
| CFR | govinfo publishes an edition volume by volume: in Nov 2026, title 7 of 2026 had 12 of 14 volumes | an edition missing a volume the older one has is held, and the hold is reported |
| CFR | 2026 title 14 lists `vol0`, a copy of `vol3`, and no `vol1`: five files against five | volumes are compared by number, not by count |
| CFR | the notebook parsed with `recover=True` and reported success on text it dropped | strict parse; a malformed file fails |
| CFR | `REVTXT` left open to the end of a volume swallowed 2,539 sections into one note | a quote ends at a unit that cannot belong to it; counted as `quotes_ended` |
| CFR | `EFFDNOTP` was an unknown element: 73% of effective-date notes had no kind | unknown elements are counted and must be zero |
| CFR | 11,623 sections print a bare number (all of title 48) and were cited with their subject | label patterns are checked against every section form in the snapshot |
| CFR | a subpart's own `Source:` was merged with the part's | `nearest: true` on a block signal |
| Canada | Parts and Divisions are flat siblings of sections | `units.flat` |
| Canada | related provisions print sections numbered by the amending Act | they are `quoted` |
| Canada | the marginal note is printed before the section number | `join` reads in slot order |
| Canada | raw GitHub at 1 request at a time: 0.6 files/s, 2.5 hours | `fetch.concurrency` |
| Canada | 45 samples showed none of 20 rarer elements, about 1,100 occurrences | the first full extract is a probe too: total the warnings, fix the dialect, re-extract |
| Canada | the GPO runaway-quote rule ended well-formed quotes holding a schedule | `quotes_run_on: false` for a publisher whose quotes close |
| Canada | coordinate schedules became 245,616-token table chunks | `markup` 1.1 splits a table by rows under `max_tokens` |
| Canada | `2026-10-01` was no date to the signals | ISO dates are read as well as `Oct. 1, 2026` |
| Canada | enabling Acts printed as adjacent inline links read as one name, `FOOD AND DRUGS ACTSEEDS ACT` | `items`: each child its own paragraph; read the signal values before publishing |
| HF | an upload sent every byte and stalled before its commit | verify the commit on the Hub, never the exit code |
