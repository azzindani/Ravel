# SOURCE_ID_REG_MARKDOWN.md — Ravel

A measured reading of `Azzindani/ID_REG_Markdown` and the profile written from it,
`registry/profiles/id_regulation_md@1.2.yaml`. (Measured with 1.0; 1.1 and 1.2 add the repairs in `PIPELINE.md` §8.2, and the superseded files were removed.) Everything below was counted, not recalled;
the commands that reproduce it are in §8. Nothing here has been run through
`ravel extract` / `ravel chunk` yet — the profile is tested against lines, not against a
build (§7).

---

## 1. The source

53 zip archives (`batch-N-chunk-M.zip`, 252-994 files each), 362 MB. Extracted to
`work/id_reg_markdown/<zip>/` (gitignored; `sources/` stays read-only):

| | |
|---|---|
| files | 46,497 (46,457 `.md`, 40 `.pkl`) |
| size | 1.79 GB; median 18 KB, p90 66 KB, p99 284 KB, max 19 MB |
| distinct names | 44,238 · 1,912 names occur in more than one zip |
| empty / under 500 B | 6 / 173 |
| `.pkl` | **not opened** — unpickling executes code, and nothing about the task needs them |

By file name: PERDA ~15K, PERBUP ~12K, PERWALI ~4.7K, PERGUB ~2.7K, PP ~2.5K, KEPPRES ~2.2K,
UU ~1.8K, PERPRES ~1.4K, other ~4.4K. About **70% are regional regulations** — the profile
that was written for national PDFs (`id_regulation`) saw few of them.

The Markdown is a PDF-to-Markdown converter's output (Docling-style: `<!-- image -->`
placeholders, `##` headings only). It is *not* clean text: see §3.

---

## 2. Census · all 46,451 non-empty files

Files containing at least one line of each shape (one regex per shape, line-anchored):

| shape | files | | shape | files |
|---|---|---|---|---|
| `## Pasal N` | 41,179 (88.7%) | | `Menimbang` | 38,953 (83.9%) |
| bare `Pasal N` line | 20,803 (44.8%) | | `Mengingat` | 35,921 (77.3%) |
| roman `Pasal I` (amending) | 7,373 (15.9%) | | `MEMUTUSKAN` | 40,969 (88.2%) |
| `BAB` heading | 29,780 (64.1%) | | `Menetapkan` | 42,227 (90.9%) |
| bare `BAB` line | 7,193 (15.5%) | | `Dengan Rahmat Tuhan…` | 34,460 (74.2%) |
| `Bagian` heading | 16,119 (34.7%) | | `Dengan Persetujuan Bersama` | 15,031 (32.4%) |
| `Paragraf` heading | 4,561 (9.8%) | | `Ditetapkan di` | 38,580 (83.1%) |
| `## PENJELASAN` | 10,530 (22.7%) | | `Diundangkan di` | 37,073 (79.8%) |
| `PASAL DEMI PASAL` | 10,457 (22.5%) | | `Agar setiap orang…` | 34,416 (74.1%) |
| `I. UMUM` | 8,811 (19.0%) | | `Cukup jelas` | 9,448 (20.3%) · 277,255 lines |
| `## LAMPIRAN` | 3,175 (6.8%) | | `<!-- image -->` | 43,750 (94.2%) · 294,219 lines |
| bare `LAMPIRAN` line | 8,774 (18.9%) | | table rows (`\|…\|`) | 12,255 (26.4%) · 2,391,156 lines |
| no `Pasal`-like marker at all | 2,482 (5.3%) | | contain U+FFFD | 1,449 |

Heading levels: `##` 2,317,933 lines; `#` 1,005; deeper 26. The converter never emits `###`.

**`##` is not structure.** 88.7% of files have a `## Pasal N` *and* 44.8% have bare
`Pasal N` lines, often in the same document; signatory names become headings
(`## PATRIALIS AKBAR`); `BAB II` and its title are two `##` lines in some files and one
plain line in others. The profile therefore never trusts typography
(`trust_typography` is left false).

Case, over the sampled structural lines: `BAB` is upper-case 22,652 / Title-case 169;
`Pasal` Title-case 95,282 / upper-case 178; `Bagian` Title-case 12,187 / upper-case 57.

---

## 3. What a document looks like, and how the converter damages it

Ideal order: cover (type, number, year, `TENTANG`, about) → `DENGAN RAHMAT TUHAN YANG MAHA
ESA` → `Menimbang` → `Mengingat` → (`Dengan Persetujuan Bersama` for a Perda) →
`MEMUTUSKAN:` / `Menetapkan` → body (`BAB`, `Bagian`, `Paragraf`, `Pasal`, `(1)` ayat,
`a.` huruf) → closing (`Agar setiap orang…`, `Ditetapkan di…`, `Diundangkan di…`, `Ttd.`,
`LEMBARAN NEGARA/DAERAH…`) → `PENJELASAN` (`I. UMUM`, `II. PASAL DEMI PASAL`, per-pasal
`Cukup jelas.`) → `LAMPIRAN`.

Read in seven samples (PP 26/2011, UU 8/1970, Perda Merangin 3/2016, Perbup Mandailing
Natal 52/2016, Keppres 84/2002, Perda Tuban 20/2016, and one scan, Banyumas 5/2005):

1. **Menimbang / Mengingat labels are displaced.** UU 8/1970 has `Menimbang` and `Mengingat`
   alone on lines 1-3 and the items later as `- : a. bahwa …`. Merangin starts with
   `## Mengingat :` *above the title*. In PP 26/2011 `Mengingat:` sits *between* the items of
   Menimbang. The label cannot be trusted to precede its content; the content can (Menimbang
   items begin `bahwa`, Mengingat items cite `Undang-Undang` / `Lembaran Negara`).
2. **Title block shredded**: `Menetapkan / : / PERATURAN / TENTANG / KETERTIBAN / UMUM.` one
   word per line, `MEMUTUSKAN: DAERAH` (reordered), `NOMOR8TAHUN1970`, `NOMOR   3   TAHUN`.
3. **Page furniture inside the text**: `<!-- image -->`, a repeated authority heading
   (`PRESIDEN REPUBLIK INDONESIA`), a page number (`- 2 -`), `SALINAN`.
4. **Catch-words at a page break**: the last words of a page are repeated with an ellipsis,
   then the full line opens the next page — `Dalam . . .` / `Dalam rangka …`,
   `1. Tindakan …` / `1. Tindakan Pengamanan adalah …`, `Mengingat : …`.
5. **A sentence split across a page** becomes two paragraphs.
6. **List numbering rewritten by the converter** (UU 8/1970: `I.`, then `2.`, `3.` where the
   source had `(1)`, `(2)`).
7. **Dehyphenation damage**: `UndangUndang`, `perundangundangan`, `UNDANG UNDANG`.
8. **The explanation can be one run-on paragraph**: `Pasal 1 Cukup jelas. Pasal 2 Cukup
   jelas. Pasal 8 Ayat (1) … Ayat (2) …`, and the line-per-item form later in the same file.
9. **OCR digit damage**: `Pasa122` (= Pasal 22), `Pasai 84`, `Pasal 1 1` (= Pasal 11).
   465 / 118 / 113 lines in a 1-in-24 sample. A pattern that accepted them would invent a
   locator, so the profile leaves them unclaimed.
10. **Unusable scans**: `KAB_BANYUMAS_5_2005` is noise (`LEMBA.RA. ~ DAERAl:I`,
    `KABUPATF.t\" RAI\Yill,L'\S`, `&lt;` entities). No profile parses it; it needs the
    quality gate (`src/extract/quality.py`), not a pattern.

Lines repeating 3+ times inside one file (1-in-12 sample, 3,871 files) — page furniture is
repetition: `<!-- image -->` 1,890 files / 22,431 lines; `PRESIDEN REPUBLIK INDONESIA`
401 / 4,719; `N -` page numbers 282 / 3,477 (+ `-N -` 121 / 1,320); a lone `:` 348 / 4,066.

---

## 4. The profile · `id_regulation_md@1.2`

A **separate profile, not `id_regulation@1.1`**: the family is the same, the noise model is
not, and `id_regulation@1.0` is named explicitly by `test_chunk_run`, `test_extract_run` and
`test_profiles`, and guarded against false positives on 158 PDFs.
Routes `text/markdown` only, `priority: 50` so it beats `id_regulation` whenever it applies
(a test fails if a content-hit difference could ever outweigh the gap).

**Duplication, and the guard.** A profile cannot extend another, so the ladder,
abbreviations and `scoring` block are copied from `id_regulation@1.0`.
`tests/test_profile_id_regulation_md.py` fails if any shared entry differs, if a type of
`id_regulation` is missing here, or if the added set is anything other than the documented
13 (3 `QANUN`, 10 glued forms such as `PERATURANBUPATI`).

### Measured on a 1-in-12 sample (3,871 files, 1,305,005 blocks)

Through Ravel's own Markdown extractor, `Profile.structural()` against a deliberately loose
detector. `id_regulation@1.0` → `id_regulation_md@1.2`:

| unit | claimed | loose lines not claimed |
|---|---|---|
| `pasal` | 84,442 → **90,926** | 16,335 → **9,851** (of 100,777) |
| `bab` | 20,908 → **22,289** | 2,345 → **969** (of 23,208) |
| `bagian` | 11,775 → 11,937 | 1,398 → 1,236 (the rest is `Bagian Hukum` etc., correctly refused) |
| `lampiran` | 0 → **1,254** | 1,520 → 266 (over 120 chars, or a table row) |
| `umum` / `pasal_demi_pasal` | 0 → **961** / **599** | — |
| `memutuskan` | 3,522 → 3,612 | 126 → 36 |

Remaining `pasal` misses are `Pasal N Cukup jelas.` (~1,700; gap 1 below), OCR damage (item 9)
and catch-words (`Pasal 97 …`, deliberately left to cleanup).

**Identity** (`profile.cite()` over the chunker's `CITATION_BLOCKS` window), against the
number the file *name* carries. A name is not ground truth (an amendment is named for what
it amends), so agreement is a lower bound:

| | cited | number agrees | type family agrees |
|---|---|---|---|
| `id_regulation@1.0` | 88.6% | 2,137 / 2,624 (81.4%) | 89.3% |
| md, case-insensitive | 90.2% | 2,226 / 2,674 (83.2%) | 91.3% |
| **md, case-sensitive (shipped)** | 78.9% | **2,231 / 2,341 (95.3%)** | **98.9%** |

The shipped profile cites *less* and is right *more*. Case-insensitive matching returned the
first law **cited in Mengingat** (`Undang-Undang Nomor 28 Tahun 2009`) for a Perda about
something else; the document's own title is in capitals and the cited laws are not. The
count of correct citations is the same (2,231 vs 2,226); 245 fewer are wrong. An
`identifier` the exact-match path trusts completely should be absent before it is wrong.
The number must also start with a digit (`J8`, `4S` are OCR'd digits).

**Cleanup** removes 7.10% of blocks (92,629): `Cukup jelas` 21,634; `<!-- image -->`
24,753; page numbers 5,969; `PRESIDEN REPUBLIK INDONESIA` headers 5,860; `SALINAN` 517;
`ttd` 1,880; lone symbols 25,329; catch-words 6,687. Lone *digits* are kept on purpose: a
bare `Pasal` followed by its number on the next line exists (114 in a 1-in-24 sample).
Accepted cost: a form blank such as `Nama : …` is also a short fragment ending in an ellipsis
and is dropped with the catch-words.

---

## 5. Bugs found in the reference notebook (`ID_REG_Markdown_Parse_v1.ipynb`)

Found by reading it against the samples; **not executed**.

1. `split_regulation_sections` takes the *first case-insensitive* `LAMPIRAN` / `PENJELASAN`
   anywhere as the section boundary. PP 26/2011 Pasal 2(2) reads "…sebagaimana tercantum
   dalam **Lampiran** yang merupakan bagian…", so the main content would end there and
   Pasal 3-6 would be lost. The profile's `penjelasan` and `lampiran` are case-sensitive and
   line-anchored.
2. `SKIP_PATTERNS` are one regulation's (BPK) sentences, and one of them deletes
   `Peraturan ini mulai berlaku pada tanggal diundangkan.` from **every** document — PP
   26/2011 Pasal 6 is exactly that line. Corpus knowledge in code (`CLAUDE.md` §12).
3. Articles are de-duplicated by number and sorted, which breaks amending regulations
   (`Pasal 1a` quoted inside `Pasal I`) and repeated OCR numbers (Merangin ends
   `Pasal 37`, `38`, `38`).
4. Strategy 3 of article extraction pulls neighbouring lines when an article is empty, so
   unrelated text can be attached to it.
5. Output rows carry no source line or offset: no provenance (`CLAUDE.md` §5.3).

---

## 6. Engine gaps this exposed

Not fixed here, and each is a generic capability, ✗ a corpus patch.

1. **A marker and its clause on one line.** `structural()` accepts a marker plus a *short
   title*, not a marker plus a clause. 1,141 of 3,794 `Menimbang` lines
   (`Menimbang : a. bahwa …`), 1,826 of 3,817 `Menetapkan` lines and ~1,700 per-article
   explanations (`Pasal 5 Cukup jelas.`) are unclaimed. Widening the regex would turn
   amendment clauses into headings. Pinned by `test_documented_gap_…`.
2. **Displaced Menimbang / Mengingat** (§3.1) needs classification by content, not by label.
3. **The Markdown extractor** (`extract/text.py::_markdown_blocks`): keeps `<!-- image -->`
   and `2 -` as ordinary paragraphs (they should be `PAGE_HEADER` / `FIGURE_REF` blocks, which
   the structurer already refuses to promote); strips the `1.` list number, so `angka` never
   fires; reads 2.39M table rows in 12,255 files as paragraphs.
4. **The citation window counts furniture.** 18% of files stay uncited largely because
   `<!-- image -->`, a displaced `Menimbang` and one-character layout lines fill the first
   `CITATION_BLOCKS = 12` blocks before the title does.
5. ~~**Synthetic section labels.**~~ Done -- see §6.1.
6. **A run-on explanation paragraph** (§3.8) needs a splitter on `Pasal N` inside a block.
7. **Duplicates across zips** (1,912 names) — `sources scan` hashes content, so true
   duplicates should collapse; not verified on this corpus.

### 6.1 The notebook's algorithm, ported as data

`ID_REG_Markdown_Parse_v1.ipynb` does four jobs: clean the text, read the document's own
facts (type, issuing body, number, year, subject, date), split it into regions
(Pembukaan, body, Pengesahan, Penjelasan, Lampiran) and label every row `chapter` / `article`.
The first was ported earlier as `cleanup.boilerplate`. The rest are now generic capabilities
driven by the profile:

| Notebook | Ravel |
|---|---|
| `extract_regulation_type_and_body`, `_number`, `_year`, `_about` | `Profile.describe()` over `identity.{citation,enacting_body,about}` |
| `extract_effective_date` (60 patterns, first hit) | `identity.dates` (4, most specific first, validated) + `identity.months` |
| `split_regulation_sections`, `parse_closing_content`, `parse_appendix_content` | `role` / `region` / `article` on the profile's units; a `penutup` section |
| `Chapter` = `BAB R - TITLE` / `N/A` / `PENUTUP` / `PENJELASAN` / `LAMPIRAN` | `unit._where()` -- same strings |
| `Article` = `Pasal N` / `Pembukaan` / `Pengesahan` / `LAMPIRAN` | same |

Measured on the markdown corpus (sample of 1,936 files, `probe_markdown_corpus.py facts`):

| field | found |
|---|---|
| regulation_type / number / year | 79.6% |
| enacting_body | 87.0% |
| about | 89.5% |
| effective_date | 65.4% |

The number agrees with the file name in 95.6% of the files that have one. The incumbent
corpus had 9.4% `Unknown` type on the 40,000 rows in its first four shards; here an unreadable
field is NULL (20% of files for the type, which is the citation-window gap above plus scans).
The missing dates are mostly documents whose signing block is not in the markdown, or whose
digits are OCR-damaged (`2Ol7`, `Novehber`); the validator rejects those rather than guess.

Against the incumbent rows (first four shards, joined on type + issuing body + number + year:
355 documents in both): the issuing body is equal by construction, `about` is equal in 79% and
one contains the other in 94% (the incumbent's is usually the one cut at a line end), and the
date agrees in 216 of 235 (92%).

Labels on 968 files / 90,052 chunks (`probe_markdown_corpus.py labels`): `Pasal N` 64,789,
`LAMPIRAN` 5,651, `Pembukaan` 5,336, `Pengesahan` 3,524, no article 9,419 (10.5% -- chunks
under a chapter or section heading that is not itself citable). Chapters: `N/A` 11,383,
`LAMPIRAN` 5,835, `PENUTUP` 3,723, `PENJELASAN` 3,229, `BAB R ...` the rest. 2,438 chunks sit
under a `BAB R` with no title, where the title is longer than `max_title_chars` or two lines.

`id_regulation_md@1.2` adds the `pipeline:` section (`PIPELINE.md`): one chunk per pasal,
`max_tokens` 4096, a context header for the embedder, and floors measured per chunk on the
same sample. On the sample that is 26,576 chunks (the default 1.0 build gives 90,052), 12.9%
of them under 8 tokens before chapter titles were consumed as labels and 6.2% after.

Not ported, deliberately: the notebook's per-pasal explanation parser and its sequence validator
(`validate_article_sequence`). The first needs the run-on splitter (gap 6); the second
repairs OCR'd numbering by guessing, which this project does not do.

---

## 7. Not done

- No `ravel sources scan` / `extract` / `chunk` over this corpus. The profile is validated
  against lines and identity windows, not against a build or an eval set.
- The census is on all files; the profile measurements are on a 1-in-12 sample (deterministic,
  sorted order). A second sample at a different stride was not taken.
- The `.pkl` files were not inspected.
- Unusable scans were unmeasured here; `QUALITY.md` measures them: 924 files (2.0%) are
  unusable and 201 (0.4%) have no text, out of 46,451.

## 8. Reproduce

```bash
# extract (zip-slip safe; sources/ untouched)
python - <<'EOF'
import zipfile, pathlib
src, dst = pathlib.Path("sources/ID_REG_Markdown"), pathlib.Path("work/id_reg_markdown")
for z in sorted(src.glob("*.zip")):
    out = dst / z.stem; out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(z) as zf:
        for i in zf.infolist():
            t = (out / i.filename).resolve()
            if not i.is_dir() and out.resolve() in t.parents:
                t.parent.mkdir(parents=True, exist_ok=True); t.write_bytes(zf.read(i))
EOF

uv run python tools/probe_markdown_corpus.py structure id_regulation@1.0    work/id_reg_markdown
uv run python tools/probe_markdown_corpus.py structure id_regulation_md@1.2 work/id_reg_markdown
uv run python tools/probe_markdown_corpus.py cite      id_regulation_md@1.2 work/id_reg_markdown
uv run python tools/probe_markdown_corpus.py cleanup   id_regulation_md@1.2 work/id_reg_markdown
uv run python tools/probe_markdown_corpus.py facts    id_regulation_md@1.2 work/id_reg_markdown
uv run python tools/probe_markdown_corpus.py labels   id_regulation_md@1.2 work/id_reg_markdown --every 48
uv run pytest tests/test_profile_id_regulation_md.py tests/test_document_facts.py
```
