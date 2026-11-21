# SOURCES_NEXT.md — Ravel

Open regulation sources outside the US, surveyed 2026-11-21, to be added **one at a time**,
each by the process in `ADDING_A_SOURCE.md`:
each gets a corpus file, a profile and an XML dialect in YAML (`CLAUDE.md` rule 12), its
own `SOURCE_<ID>.md`, a full build, and a dataset. ✗ None is fetched yet unless its row
says so.

| # | Source | Content | Bulk access | Format | Licence | Status |
|---|---|---|---|---|---|---|
| 1 | **Canada**, Justice Laws | federal Acts + regulations, consolidated, EN + FR | GitHub `justicecanada/laws-lois-xml`; point-in-time set too; every 2 weeks | XML, data dictionary at laws-lois.justice.gc.ca/eng/XML | Open Government Licence – Canada | built and published: HF `Azzindani/CA_LAWS_2611`, 166,569 chunks |
| 2 | **UK**, legislation.gov.uk | Acts + statutory instruments | research.legislation.gov.uk packaged sets; complete from 2011 | CLML XML (also Akoma Ntoso) | Open Government Licence v3 | |
| 3 | **France**, DILA LEGI | codes, lois, décrets, consolidated since 1945 | echanges.dila.gouv.fr, daily | XML, DTD LEGIFRANCE | Licence Ouverte 2.0 | |
| 4 | **Japan**, e-Gov | all laws and ordinances | XML bulk zip, Law API v2 | law standard XML schema | government open data | |
| 5 | **Germany**, gesetze-im-internet | > 6,000 federal laws + Verordnungen | `gii-toc.xml` index, one `xml.zip` per law | XML, `gii-norm.dtd` | free use; ✗ not the official text | |
| 6 | **EU**, EUR-Lex | acts in force (CELEX sector 3), per language | data dump, needs EU Login | Formex XML | free reuse | |

Not bulk: Australia's Federal Register of Legislation has a free OpenAPI, no bulk
download confirmed.

Sources: open.canada.ca dataset ff56de85-f8b9-4719-8dff-ecf362adf0af ·
laws-lois.justice.gc.ca/eng/FAQ · research.legislation.gov.uk/data ·
data.gouv.fr/datasets/legi-codes-lois-et-reglements-consolides ·
laws.e-gov.go.jp/docs/law-data-basic/8529371-law-api-v1 · gesetze-im-internet.de/hinweise.html ·
eur-lex.europa.eu/content/help/data-reuse/reuse-contents-eurlex-details.html
