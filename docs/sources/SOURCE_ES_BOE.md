# SOURCE_ES_BOE.md — Ravel

> Spain's consolidated legislation, state and regional, from the Agencia Estatal Boletín
> Oficial del Estado's open-data API. Corpus `es_boe` (`corpora/es_boe.yaml`), profile
> `es_boe@1.0`, dialect `boe@1.0`. Fetched with no account or key: the API lists every
> consolidated law, and each law's XML is a public URL.

## 1. The source

- **What:** every law the BOE consolidates (`legislacion-consolidada`): state law (Leyes
  Orgánicas, Leyes, Reales Decretos, Órdenes, Resoluciones, treaties) and regional law
  (`ámbito` Autonómico). The publisher counts 12,498 (probed 2026-11-28).
- **Text:** the consolidated wording, kept per block (`bloque`) with every version it has
  had. The corpus reads the newest version of each block: the law in force.
- **Format:** XML from `datosabiertos/api/legislacion-consolidada/id/<id>`, asked for as
  `Accept: application/xml`. ! With `Accept: */*` the API answers an error body
  (`<code>400</code>`) under HTTP 200.
- **Licence:** the BOE's open-data reuse conditions (Real Decreto 1495/2011). The
  consolidated text has no legal value; only the gazette as published does.

## 2. Fetch (`kind: json_api`)

The listing `legislacion-consolidada?limit=&offset=` is the publisher's own index: each item
names the law (`identificador`, `BOE-A-1982-20824`) and the date of its current
consolidation (`fecha_actualizacion`). The fetcher pages it until a page comes back short,
then fetches each law at its newest date into `<id>/<fecha_actualizacion>.xml`. A later
consolidation is a new file beside the old one: the fetcher never overwrites. Each file's
URL is the API's, kept in `fetch.urls.jsonl` (`provenance.url_map`).

`json_api` is generic: listing URL, item array, id and edition fields, URL and path
templates, paging parameters and the download `Accept` are all YAML (`FetchSpec`).

## 3. Structure (probe: 38 laws spread over the listing, 2026-11-28)

`<response><data>` holds `metadatos` (rank, number, dates, department, ELI), `analisis`
(subjects, references), the ELI RDF and `texto`. The text is a flat run of 1,533 `bloque`s:

| `tipo` | count | read as |
|---|---|---|
| `precepto` | 1,206 | an article (`Artículo 4.`, `Sexto.`), or a disposition (`Disposición adicional primera`) |
| `encabezado` | 234 | a heading: `LIBRO`, `TÍTULO`, `CAPÍTULO`, `Sección`, `Subsección`, `ANEXO` |
| `firma` · `preambulo` · `nota_inicial` | 38 · 37 · 15 | signatures, preamble, editor's note: cited as the law |
| `parte_dispositiva` · `parte_final` | 2 · 1 | parts |

- **Versions:** 130 of the 1,533 bloques hold 2-6 `version`s, every one in date order. The
  dialect reads the last (`document.latest`).
- **Headings are text:** an article's heading is its first paragraph (`<p
  class="articulo">Artículo 1. Objeto.</p>`), and a chapter's is two
  (`capitulo_num`, `capitulo_tit`). The dialect reads them as the heading and not again
  as body (`headings.*.take`).
- **Dispositions** follow the articles and belong to no chapter. They are their own unit,
  which closes the chapters before it. Read as articles, the trial cited all 222 inside
  the law's last chapter.
- **Rank and number** sit in `metadatos`, beside `texto`, and are read as `/data/metadatos/...`
  (a unit attribute from the document's root).

## 4. Identity

`{law.marker}, {article.marker}`, as Spanish practice cites: `Ley Orgánica 13/1982,
Artículo 4`; `Orden SCO/2088/2006, Disposición final primera`. A law with no official number
cites by its date: `Orden de 2 de junio de 1994, Primero`. `regulation_type` is the BOE's
`rango` (`Ley`, `Real Decreto`, `Orden` …), `regulation_number` its `numero_oficial`.

## 5. Trial on the 38 sampled laws

Through the real extractor and chunker, at the standard (`docs/pipeline/STANDARD.md`):

| | |
|---|---|
| laws | 38 extracted, 0 failed |
| chunks | 1,350, every one with an identifier and a location |
| longest | 4,039 tokens (Qwen3), 0 over the budget |
| dispositions | 222, none under a chapter |
| cited as the law itself | 52: preambles, signatures, notes |

✗ Not yet: the full fetch, build, dataset and eval.

## 6. Reproduce

```powershell
uv run ravel sources fetch es_boe
uv run ravel build es_boe --no-fetch -w work/std --out work/std/bundles/es_boe --base-url http://localhost:8000/v1
```
