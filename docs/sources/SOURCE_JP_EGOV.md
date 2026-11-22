# SOURCE_JP_EGOV.md — Ravel

Japan's laws, Cabinet orders, ministerial ordinances and rules in force, in Japanese: corpus
`jp_egov`, profile `jp_law@1.0`, dialect `jp_law@1.0`. `SOURCES_NEXT.md` #4. Read by the
generic `xml` extractor. The first source not written in spaces, which changed three
things outside YAML (§5). Built by the process in `ADDING_A_SOURCE.md`.

## 1. The source

| | |
|---|---|
| publisher | Digital Agency, e-Gov 法令検索 (laws.e-gov.go.jp) |
| bulk | one archive of every law's XML, `bulkdownload?file_section=1&only_xml_flag=true`, 277 MB on 2026-11-22, no date in it |
| index | `all_law_list.csv` inside the archive: one row per version, `未施行` marking a version not yet in force, `本文URL` its page |
| format | the standard law schema, `XMLSchemaForJapaneseLaw_v3.xsd`, one file per version |
| licence | laws are not copyrighted (Copyright Act, Article 13) |

## 2. Fetch (`kind: archive_url`)

    ravel sources fetch jp_egov

One URL, one archive. **The fetch day is the edition**: the archive states no date, so it
is saved as `all_xml_<yyyymmdd>.zip` and unpacked into a folder of that name.

- **The publisher's index picks the snapshot** (`fetch.index`). Rows where `未施行` is
  empty are in force; `member` reads the law id and version from `本文URL`, and `document`
  names the file. A row naming a file the archive lacks stops the fetch: a snapshot
  missing a law it lists would look complete.
- **A version is cited at its own page**, `https://laws.e-gov.go.jp/law/326AC1000000285/20230401_504AC0000000024`,
  through `url_map.jsonl`. A path the map does not name has no citation, and none is made up.

## 3. Extract (`xml`, dialect `jp_law`)

Structure nests as the schema does: 編 Part > 章 Chapter > 節 Section > 款 Subsection > 目
Division > 条 Article, each article holding its 項, 号 and イロハ.

- **Supplementary provisions** (`SupplProvision`, 附則) follow the main provision, one per
  amending law, and number their articles from 第一条 again. `units.inside: {suppl:
  {article: suppl_article}}`, the rule added for this source, reads an article inside one
  as a unit of its own, so it never cites as the law's first article.
- Ruby readings (`<Rt>`) are dropped; the table of contents and `ArticleRange` are skipped.

## 4. Identity

| field | value |
|---|---|
| `identifier` | `博物館法第二条` · `博物館法附則（令和四年四月一五日法律第二四号）第一条` · `博物館法附則（制定時）` · `消防法別表第一` |
| `regulation_type` | `Act`, `CabinetOrder`, `MinisterialOrdinance`, `Rule`, `ImperialOrder`, `Constitution` |
| `regulation_number` | `昭和二十六年法律第二百八十五号` |
| `about` | the law's title |

The supplementary provision made with the law states no amending law: it cites `制定時`, at
enactment.

## 5. What a language without spaces changed

- **Token counts.** The estimator read a run of kanji and kana as one word, so a whole
  article counted as a few tokens and never split: median 17 "tokens". Each CJK character
  now counts as its own piece. Median 193 after.
- **BM25.** `\w\w+` read a clause as one term no query repeats. New fits use `WORDS_CJK`:
  a run of CJK characters becomes its overlapping bigrams (`博物館法` → `博物 物館 館法`), a
  lone character stays itself, and text with no CJK tokenises exactly as before. The
  artifact records its `token_pattern`, and Vera's `bm25.rs` picks the same tokeniser by
  that name.
- **The identifier** has no space: `{law.name}{article.number}`.

## 6. The first build (2026-11-22)

```bash
ravel sources fetch jp_egov                        # 8,994 laws in force in the snapshot
W=work/jp_egov/20261122
ravel extract jp_egov -w $W                        # 8,994 extracted, 0 failed
ravel chunk   jp_egov -w $W --enforce              # 603,782 chunks, 100% with an identifier
ravel signals jp_egov -w $W                        # law_references 81,463 (13.5%)
ravel sparse  jp_egov -w $W                        # WORDS_CJK, vocab sha 444c336ffceac91f
ravel embed   jp_egov -w $W --base-url http://localhost:8000/v1
```

`verify-backend` agreed with the reference at cosine 0.99999 before the embed. The embed
stalled at window 27 of 32 on the 8 GB laptop (vLLM at 0.80 of the card, Windows paging GPU
memory while the desktop decoded video: 1 text/s) and finished after vLLM was recreated at
0.70 (`VLLM_GPU_MEMORY`, `docker/compose.vllm.yml`): 603,782 vectors.

```bash
ravel publish jp_egov -w $W --repo Azzindani/JP_LAWS_2611   # 4 shards, 1,278 MB on the Hub
```

Published as `Azzindani/JP_LAWS_2611` (card: `datasets/JP_LAWS_2611.md`). Five random rows,
re-embedded through vLLM from the card's template, reproduce their stored vector at cosine
1.0000. ✗ No bundle or eval set exists yet.
