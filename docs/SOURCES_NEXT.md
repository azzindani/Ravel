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
| 3 | **France**, DILA LEGI | codes, lois, décrets, consolidated since 1945 | echanges.dila.gouv.fr, daily | XML, DTD LEGIFRANCE | Licence Ouverte 2.0 | extracting; to be published as `Azzindani/FR_LAWS_2611` |
| 4 | **Japan**, e-Gov | all laws and ordinances | XML bulk zip, Law API v2 | law standard XML schema | government open data | extracted and chunked: 8,994 laws, 603,782 chunks; embedding; to be published as `Azzindani/JP_LAWS_2611` (`SOURCE_JP_EGOV.md`) |
| 5 | **Germany**, gesetze-im-internet | > 6,000 federal laws + Verordnungen | `gii-toc.xml` index, one `xml.zip` per law | XML, `gii-norm.dtd` | free use; ✗ not the official text | extracted and chunked: 6,146 laws, 136,308 chunks; to be published as `Azzindani/DE_LAWS_2611` (`SOURCE_DE_GII.md`) |
| 6 | **EU**, EUR-Lex | acts in force (CELEX sector 3), per language | data dump, needs EU Login | Formex XML | free reuse | |

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
