# SOURCES_NEXT.md — Ravel

Open regulation sources outside the US, surveyed 2026-11-21, to be added **one at a time**,
each by the process in `ADDING_A_SOURCE.md`:
each gets a corpus file, a profile and an XML dialect in YAML (`CLAUDE.md` rule 12), its
own `SOURCE_<ID>.md`, a full build, and a dataset. ✗ None is fetched yet unless its row
says so.

| # | Source | Content | Bulk access | Format | Licence | Status |
|---|---|---|---|---|---|---|
| 1 | **Canada**, Justice Laws | federal Acts + regulations, consolidated, EN + FR | GitHub `justicecanada/laws-lois-xml`; point-in-time set too; every 2 weeks | XML, data dictionary at laws-lois.justice.gc.ca/eng/XML | Open Government Licence – Canada | built and published: HF `Azzindani/CA_LAWS_2611`, 166,569 chunks |
| 2 | **UK**, legislation.gov.uk | Acts + statutory instruments | research.legislation.gov.uk packaged sets; complete from 2011 | CLML XML (also Akoma Ntoso) | Open Government Licence v3 | **deferred**: see below |
| 3 | **France**, DILA LEGI | codes, lois, décrets, consolidated since 1945 | echanges.dila.gouv.fr, daily | XML, DTD LEGIFRANCE | Licence Ouverte 2.0 | 121,463 texts, 841,034 chunks; published with embeddings as `Azzindani/FR_LAWS_2611` (`SOURCE_FR_LEGI.md`) |
| 4 | **Japan**, e-Gov | all laws and ordinances | XML bulk zip, Law API v2 | law standard XML schema | government open data | 8,994 laws, 603,782 chunks; published with embeddings as `Azzindani/JP_LAWS_2611` (`SOURCE_JP_EGOV.md`) |
| 5 | **Germany**, gesetze-im-internet | > 6,000 federal laws + Verordnungen | `gii-toc.xml` index, one `xml.zip` per law | XML, `gii-norm.dtd` | free use; ✗ not the official text | 6,146 laws, 136,284 chunks; published with embeddings as `Azzindani/DE_LAWS_2611` (`SOURCE_DE_GII.md`) |
| 6 | **EU**, EUR-Lex | acts in force (CELEX sector 3), per language | data dump, needs EU Login | Formex XML | free reuse | |
| 7 | **Spain**, BOE legislación consolidada | consolidated state laws, every version since first publication | open REST API `boe.es/datosabiertos/api/legislacion-consolidada`, `limit=-1` lists all | XML or JSON, ELI metadata, per-block text | BOE reuse conditions | candidate |
| 8 | **Finland**, Finlex open data | statutes from 1987, treaties, authority regulations | keyless REST API `opendata.finlex.fi`, dataset zips | Akoma Ntoso XML | reported CC BY 4.0, to confirm | candidate |
| 9 | **Taiwan**, 全國法規資料庫 | all central laws and orders (法律 + 命令) | data.gov.tw datasets 18289 / 18290, monthly | XML in ZIP | Open Government Data Licence v1 | candidate |
| 10 | **Norway**, Lovdata | current acts and central regulations | keyless API `api.lovdata.no`, list then get, tar.bz2 of XML | XML | NLOD 2.0 | candidate |
| 11 | **Austria**, RIS | Bundesrecht konsolidiert + Länder law | OGD API `data.bka.gv.at/ris/api/v2.6`, ✗ no zip dump | XML/JSON | CC BY 4.0 | candidate |
| 12 | **Switzerland**, Fedlex | Classified Compilation (~17,000 acts), DE/FR/IT | SPARQL `fedlex.data.admin.ch/sparqlendpoint`; AKN only from 2022-05-30, opendata.swiss AKN set 1948-2018 | Akoma Ntoso XML, HTML, PDF | open data | candidate |
| 13 | **Poland**, Sejm ELI API | Dziennik Ustaw, Monitor Polski | `api.sejm.gov.pl/eli/acts/{DU}/{year}` | metadata JSON, text HTML/PDF | public | candidate; text mostly PDF |
| 14 | **Italy**, Normattiva | state legislation | Normattiva Open Data portal (async API), bulk AKN mirrors (CIRSFID, Senate) | Akoma Ntoso XML | IODL 2.0 | candidate |
| 15 | **Netherlands**, BWB | ~40,000 national regulations | SRU service + KOOP repository, per-regulation `toestand` XML | BWB XML | CC0 | candidate; bulk path to confirm |
| 16 | **South Korea**, law.go.kr | all statutes, article by article | Open API (OC key), data.go.kr | XML | free use | candidate; no dump, list + detail |
| 17 | **Australia**, Open Australian Legal Corpus | Commonwealth + state legislation and judgments | Hugging Face `isaacus/open-australian-legal-corpus` (232,560 docs) | JSONL | per-source, see its LICENCE | candidate; already a dataset |
| 18 | **EU**, Cellar SPARQL | EUR-Lex acts, all languages | `publications.europa.eu/webapi/rdf/sparql` lists item URLs, no EU Login reported | Formex XML, XHTML | free reuse | candidate; alternative to row 6 |
| 19 | **Africa**, Laws.Africa | legislation of many African jurisdictions (count to confirm) | Content API v3, free non-commercial | Akoma Ntoso XML | CC licences | candidate; rate-limited |
| 20 | **Brazil**, LexML / Normas.leg.br | federal norms since the Empire | Senate open data, LexML sitemap | LexML XML | open | candidate; bulk text to confirm |
| 21 | **New Zealand**, PCO | Acts, regulations | developer API (key), `.xml` per item | NZ legislation XML | API terms of use, to confirm | candidate |

**Candidates (rows 7-21, surveyed 2026-11-28 by web search, ✗ fetched).** Ranked for
Ravel by bulk access and structure: Spain, Finland, Taiwan and Norway have keyless bulk or
list-all endpoints and structured XML; Austria, Switzerland, Italy and the Netherlands
are structured but need per-document crawling; Poland's text is mostly PDF. Not
regulations but legal: case law from CourtListener (US, quarterly CSV bulk, free of known
copyright), Harvard's Caselaw Access Project, UK Find Case Law (Atom feeds, bulk on
request), and MultiLegalPile (689 GB, 24 languages, mostly CC BY-NC-SA).

**UK, deferred (2026-11-21).** research.legislation.gov.uk answers 401: the bulk sets need
an account from The National Archives. The public API (`/data.xml` per document, Atom
`data.feed` listings) is open but `robots.txt` sets `Crawl-delay: 5`. The fair-use policy
caps requests at 1,500 per 5 minutes and asks a large one-off crawl to use the bulk data
instead. At 5 s per request the 17,561 Public General Acts take about 26 h and the 109,499
UK statutory instruments about 6 days. Resume when bulk access is granted.

Not bulk: Australia's Federal Register of Legislation has a free OpenAPI, no bulk
download confirmed.

Sources: open.canada.ca dataset ff56de85-f8b9-4719-8dff-ecf362adf0af ·
laws-lois.justice.gc.ca/eng/FAQ · research.legislation.gov.uk/data ·
data.gouv.fr/datasets/legi-codes-lois-et-reglements-consolides ·
laws.e-gov.go.jp/docs/law-data-basic/8529371-law-api-v1 · gesetze-im-internet.de/hinweise.html ·
eur-lex.europa.eu/content/help/data-reuse/reuse-contents-eurlex-details.html
