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
| 22 | **Argentina**, InfoLEG | national laws, decrees, resolutions, dispositions | datos.gob.ar "Base Infoleg Normativa Nacional", zip + CSV; provincial norms in SAIJ set | CSV + text | open data | candidate; bulk zip |
| 23 | **Chile**, LeyChile (BCN) | all norms, versions since 1998 | web services (XML) + datos.bcn.cl SPARQL, JSON/RDF | XML, RDF | open data | candidate; XML export reported 401, to confirm |
| 24 | **Colombia**, SUIN-Juriscol | legislation 1821-present + case law | search portal; ✗ bulk found | HTML | free to consult | candidate; crawl only |
| 25 | **Estonia**, Riigi Teataja | gazette + consolidated acts, XML from 2010-11 | `riigiteataja.ee/avaandmed/` open-data directory; the Act requires API + bulk | XML | open data by law | candidate |
| 26 | **Czechia**, e-Sbírka | consolidated acts, legally binding e-edition since 2024 | open data + public API (vendor-stated) | to confirm | | candidate; docs to find |
| 27 | **Sweden**, Riksdagen SFS | consolidated statutes (SFS) + parliamentary documents | data.riksdagen.se API, per-document `sfs-YYYY-NNN`; dataset catalogue | XML, JSON | open data | candidate |
| 28 | **Denmark**, Retsinformation | acts, executive orders, circulars | XML since 2007; API (unofficial listing) | XML | free reuse (2014) | candidate; bulk to confirm |
| 29 | **Ukraine**, Verkhovna Rada | legislation + draft laws, > 110 parliament data sets | data.rada.gov.ua | CSV, JSON, XML | open data | candidate |
| 30 | **China**, NPC national law database | national laws and regulations | flk.npc.gov.cn, Word download per document | DOCX | | candidate; crawl only |
| 31 | **Australia (NSW)** | in-force and repealed legislation | per-title XML, `/export/day` and `/export/week` | XML (ExchangeXml) | | candidate; run outside NSW business hours |
| 32 | **Australia (Queensland)** | legislation | free API, registration | | | candidate |
| 33 | **Australia (Commonwealth)**, Federal Register of Legislation | federal legislation | keyless REST API, JSON + documents | JSON, DOCX | | candidate; replaces "not bulk" note above |
| 34 | **Singapore**, Statutes Online | Acts, subsidiary legislation | ✗ API; scrape only | HTML | | weak candidate |
| 35 | **Constitutions**, Constitute Project | ~200 national constitutions, topic-tagged | linked data + API | RDF, JSON | some translations restricted | candidate |
| 36 | **Ireland**, eISB | Acts 1922-present (SIs too) | ELI URI with `xml` suffix per Act; data.gov.ie listing | XML | Oireachtas PSI Licence (CC BY 4.0) | candidate |
| 37 | **Hong Kong**, e-Legislation (DoJ) | all current + past (from 1997) legislation, EN + Traditional Chinese | data.gov.hk ZIP packages by cap range; weekly XML list | XML in ZIP | data.gov.hk terms | candidate; strong bulk |
| 38 | **Canada (BC)**, BC Laws | all B.C. provincial legislation | CiviX API (XML, XPath) + REST API | XML | OGL-BC | candidate |
| 39 | **Lithuania**, TAR (Seimas) | Register of Legal Acts: objects, adopters, chronologies, texts | data.gov.lt dataset 2613 | open data formats | open data | candidate |
| 40 | **Luxembourg**, Legilux | national legislation | SPARQL endpoint (data.public.lu, Casemates) | RDF | CC BY 4.0 | candidate |
| 41 | **Portugal**, Diário da República | gazette from 1910, ELI URIs | `data.dre.pt/eli/...` | to confirm | | candidate; bulk to confirm |
| 42 | **Vietnam**, vbpl.vn | national legal documents, article-level since 2026 | MoJ web service `ws.vbpl.vn` (documented API) | XML/JSON | reported open | candidate |
| 43 | **Bangladesh**, BLAD | 1,484 Acts 1799-2025 | Kaggle `sakhadib/bangladesh-legal-acts-dataset`; source bdlaws.minlaw.gov.bd | JSON | CC BY-SA 4.0 | candidate; ready dataset |
| 44 | **Switzerland**, `rcds/swiss_legislation` | federal + cantonal legislation | Hugging Face | Parquet | to confirm | candidate; ready dataset |
| 45 | **Germany**, Open Legal Data (laws) | 113,537 laws | Hugging Face `openlegaldata/laws-germany` | Parquet | ODbL | overlaps de_gii |
| 46 | **Hungary**, Nemzeti Jogszabálytár | legislation in force | web only | HTML | | weak candidate |
| 47 | **Greece**, Diavgeia | every administrative act of the public sector | open API, no key for reading | XML/JSON | open | candidate; acts, ✗ statutes |
| 48 | **UAE**, UAE Legislation | federal legislation since 1971 | portal; MoJ API marketplace | | | weak candidate |
| 49 | **Qatar**, Al Meezan | laws since 1961, rulings, opinions | portal, no bulk (2015) | HTML | CC BY (2015) | weak candidate |
| 50 | **Saudi Arabia**, Bureau of Experts | laws in English translation | Word downloads per law | DOCX | | weak candidate |
| 51 | **Turkey**, mevzuat.gov.tr | consolidated legislation | site only; HF scrapes | HTML | | weak candidate |
| 52 | **Israel**, National Legislation Database | laws in force since the Mandate, Hebrew | portal | HTML | | weak candidate |
| 53 | **Canada, provinces**, A2AJ Canadian Laws | federal + 11 provinces/territories, Acts and regulations full text | HF `a2aj/canadian-laws` | Parquet | to confirm | candidate; extends ca_laws to Ontario, Alberta, Québec |
| 54 | **US**, CFR annual editions (GovInfo) | every CFR title per year, 1996-present | govinfo.gov/bulkdata/CFR/<year>, zip per title | XML | public domain | point-in-time us_cfr |
| 55 | **Pakistan**, community law sets | Penal Code, CrPC, family + property Acts; 1K-10K rows | HF `mwz/Pak-Law`; Zenodo 20509007 | Parquet, CSV | to confirm | ✗ official; check provenance |
| 56 | **Kenya**, Kenya Law corpus | 546 Acts (all Laws of Kenya) + judgments, 62K chunks | HF `Daudipdg/kenya-law-corpus` (id to confirm) | text | CC BY-NC 4.0 | non-commercial |
| 57 | **Egypt**, active laws | 479 active laws, Arabic full text | HF `Sukuna404/egyptian_laws_dataset` | Parquet | to confirm | first Arabic corpus |
| 58 | **Philippines**, Lawphil (Arellano Law Foundation) | Constitutions, Republic Acts, PDs, EOs; SC decisions from 1901 | lawphil.net HTML; 59,905 decisions inside HF `NLPinas/ph_en_text_detoxed` (Apache-2.0) | HTML, text | to confirm | candidate; crawl, English |
| 59 | **India**, NyayKosh (law as code) | provisions, rules, penalties | open JSON APIs, no registration | JSON | to confirm | structured, ✗ full Acts |
| 60 | **Romania**, legislatie.just.ro (Ministry of Justice) | national legislation, consolidated | web + SOAP API that allows bulk (2015 report) | XML | no copyright, free reuse | candidate; API status to confirm |
| 61 | **Thailand**, central law system (DGA) | Thai legislation data | data.go.th dataset `law-portal-01` | to confirm | CC BY | candidate; contents to confirm |
| 62 | **Malaysia**, Laws of Malaysia | federal Acts, English | lom.agc.gov.my (PDF); CommonLII mirror | PDF, HTML | | weak; crawl only |
| 63 | **Uruguay**, IMPO (Centro de Información Oficial) | national legislation, decrees | JSON per norm (unofficial client found) | JSON (ISO-8859-1) | open data, attribution | candidate; API to confirm |
| 64 | **US, Arkansas**, administrative rules | the state's whole rules database | sos.arkansas.gov bulk download, by agency/chapter | to confirm | public | first state admin code with bulk |
| 65 | **US, New York**, NYCRR (unofficial) | state codes, rules and regulations | data.gov entry links to the online version | HTML | | weak; crawl only |
| 66 | **Vietnam**, thuvienphapluat.vn sets | ~574K legal documents (vbpl subset) + QA, terms | HF `tmquan/thuvienphapluat-vn-*`, `tmquan/vbpl-vn` | Parquet | to confirm | ready dataset; alternative to row 42 |
| 67 | **Indonesia**, community regulation sets | 250K+ PDFs from ~350K URLs; 3.5M article rows; 90,588 norm units from 378 regulations | HF `horelulus/ID_Regulations`, `horelulus/ID_REG_Parsed`; Mendeley 8wpjvhh7g2 | PDF, Parquet | to confirm | cross-check id_reg coverage |
| 68 | **South Africa + region**, SAFLII | legislation, judgments, journals; also 10 southern African LIIs | saflii.org web | HTML | free access | crawl only; same LII family as row 19 |
| 69 | **Belgium**, Justel / Moniteur belge | consolidated acts since 1994; gazette since 1997 | ejustice.just.fgov.be web | HTML, PDF | | weak; no API found |

**Candidates (rows 7-52, surveyed 2026-11-28 by web search, ✗ fetched).** Ranked for
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


## Beyond regulations: courts, bills, rulemaking, governance

Surveyed 2026-11-28 by web search, ✗ fetched. A different document kind each, so each
needs its own profile (a judgment is not chunked like a statute).

| # | Source | Content | Access | Format | Licence | Note |
|---|---|---|---|---|---|---|
| C1 | **US**, CourtListener (Free Law Project) | US federal + state case law, judges, dockets | bulk CSV, regenerated quarterly; REST API | CSV, JSON | free of known copyright restrictions | the largest open case-law set |
| C2 | **US**, Caselaw Access Project (Harvard) | 360 years of US reported case law | bulk downloads (case.law); paths now linked from CourtListener | JSON | open since 2024 | to confirm current path |
| C3 | **India**, Supreme Court judgments | judgments since 1950, ~35K, 52 GB | public AWS S3, `--no-sign-request`; sibling set for 25 High Courts | JSON + Parquet metadata, PDF | CC BY 4.0 | ready bulk |
| C4 | **France**, Judilibre (Cour de cassation) | ~534K Cassation rulings + appeal/first-instance courts | PISTE API (free key); HF mirror `antoinejeannot/jurisprudence` | JSON | Licence Ouverte 2.0 | same language as fr_legi |
| C5 | **Germany**, rechtsprechung-im-internet | 7 federal courts (BVerfG, BGH, BVerwG, BFH, BAG, BSG, BPatG) | per-decision XML zip, XML from 2010 | XML | free reuse | portal to be replaced by the new federal system |
| C6 | **Canada**, Supreme Court (Refugee Law Lab) | SCC 1877-present, ~15,500 decisions; FC and others | HF `refugee-law-lab/canadian-legal-data` | JSON, Parquet | CC BY-NC 4.0 | non-commercial |
| C7 | **EU**, CJEU via Cellar | judgments, orders, AG opinions | Cellar SPARQL + REST | XHTML, Formex | free reuse | no CURIA bulk |
| C8 | **ECHR**, HUDOC derivatives | ~20K judgments | Poznań ECHR-OD (json/csv/sqlite), HF `ecthr_cases` | JSON | to confirm | no official bulk |
| C9 | **UK**, Find Case Law (TNA) | judgments + tribunal decisions | API + Atom feeds; bulk on request | LegalDocML XML | Open Justice Licence; computational use needs a licence | |
| C10 | **Indonesia**, Direktori Putusan MA | ~10.4M decisions | search portal only; topic datasets (indo-law, 22,630 criminal) | HTML, PDF | to confirm | no bulk; Indonesian like id_reg |
| C11 | **Japan**, 裁判所 判例検索 | Supreme Court + lower courts | search portal, PDF only | PDF | | national civil-judgment DB planned (Act No. 49 of 2025) |
| C12 | **Netherlands**, Rechtspraak Open Data | > 500,000 pseudonymised judgments, ECLI, citations | open API, no key; XML dump ended 2023 | XML | CC0 | |
| C13 | **Switzerland**, entscheidsuche.ch / OpenCaseLaw | > 1M decisions, all 26 cantons + federal | daily Parquet (OpenCaseLaw), REST API | Parquet, HTML | CC0 (reported) | strong bulk |
| C14 | **Switzerland**, Federal Supreme Court dataset | 127,477 cases 2007-2024 | Zenodo | CSV/TXT | open | |
| C15 | **Germany**, Open Legal Data (courts) | 423,941 decisions + 7.4M citations (May 2026) | Hugging Face datasets; API (not for bulk) | Parquet, JSON | ODbL | strong bulk; German like de_gii |
| C16 | **Brazil**, STJ open data portal | case law, precedents, judgment sessions, e-gazette, full text | portal downloads | CSV, JSON | reuse incl. commercial | |
| C17 | **Brazil**, CNJ DataJud | procedural data, all courts | public API | JSON | | metadata, ✗ full text |
| C18 | **South Korea**, precedents | ~80K Supreme Court + lower + Constitutional Court | data.go.kr PrecedentInformationService; LBOX OPEN 147K | XML, JSON | precedents not copyrighted | |
| C19 | **Taiwan**, Judicial Yuan open data | judgments + 800 datasets | opendata.judicial.gov.tw, judgments need membership | JSON | OGDL-Taiwan 1.0 | |
| C20 | **Mexico**, SCJN Buscador Jurídico | rulings, theses (SJF) | bj.scjn.gob.mx open data section, API listed | JSON | | to confirm |
| C21 | **Spain**, CENDOJ | Supreme Court + higher courts | search, PDF; reuse by agreement | PDF | | no bulk |
| C22 | **ICJ**, CD-ICJ corpus | every published ICJ decision | Zenodo | PDF, TXT, CSV | CC0 | ready dataset |
| C23 | **WTO**, dispute settlement documents | every DSU dispute since 1995 | data.wto.org | HTML, XLSX, DOCX | | |
| C24 | **Austria**, RIS Judikatur | VfGH, VwGH, OGH, BVwG (from 2014), Landesverwaltungsgerichte | same OGD API as row 11 | XML/JSON | CC BY 4.0 | German like de_gii |
| C25 | **Finland**, Finlex case law | KKO + KHO precedents, courts of appeal | opendata.finlex.fi (row 8) | XML | to confirm | |
| C26 | **US**, Supreme Court Database (SCDB) | every SCOTUS case 1791-present, ~250 coded variables | scdb.la.psu.edu releases, by citation or docket; CAP link set on Zenodo 4344917 | CSV, SAV | free | metadata; text via CAP/CourtListener |
| C27 | **China**, CAIL2018 | > 2.6M criminal cases (SPC), facts + articles + charges | HF `china-ai-law-challenge/cail2018` | JSON | unknown | Chinese |
| C28 | **Tanzania**, TanzLII | judgments + legislation | website + APIs; bulk by email request | HTML, JSON | terms of use | bulk on request |
| C29 | **Inter-American Court of HR** | 201 cases coded: proceedings, content, compliance | research database (Cambridge J. Law & Courts); CEJIL Summa | to confirm | | |
| C30 | **International courts**, iCourts database | ECtHR, IACtHR and other international court decisions | jura.ku.dk/icourts | to confirm | research | |
| B1 | **US**, GovInfo bulk BILLS / BILLSTATUS | bills, status, public laws, Statutes at Large | govinfo.gov/bulkdata, by Congress | USLM XML | public domain | enrolled bills from the 113th |
| B2 | **US**, Federal Register + Regulations.gov | rules, proposed rules, notices; dockets + comments | GovInfo bulk XML; Regulations.gov API v4 (1,000/h) + bulk download | XML, JSON | public domain | the rulemaking behind us_cfr |
| B3 | **UK**, Parliament | bills, divisions, questions, SIs, treaties | developer.parliament.uk REST + SPARQL | JSON, RDF | Open Parliament Licence | |
| B4 | **EU**, Parliament open data | adopted texts, votes, documents | data.europarl.europa.eu API v2 (500 / 5 min) | JSON-LD | CC BY 4.0 | Parltrack for bulk |
| B5 | **US states**, Open States (Plural) | bills + votes, 50 states + DC + PR, full bill text in JSON | monthly CSV/JSON per session, PostgreSQL dump | CSV, JSON, SQL | public domain | scraped from state sites |
| B6 | **US states**, LegiScan | bills, votes, legislators, all states + Congress | weekly dataset snapshots, API key (free) | CSV, JSON | CC BY 4.0 | |
| B7 | **US**, Congress.gov API | bills, amendments, actions, text | api.congress.gov v3, api.data.gov key | JSON, XML | public domain | |
| B8 | **US**, United States Code (OLRC) | the whole Code per release point | uscode.house.gov zip per title or whole | USLM XML, XHTML, PDF | public domain | strong bulk |
| B9 | **Japan**, Diet minutes (国会会議録) | every Diet session since 1947 | NDL API (kokkai.ndl.go.jp/api.html) | XML, JSON | to confirm | |
| B10 | **Hong Kong**, LegCo Bills Database | bills by session | OData API | XML, JSON | data.gov.hk terms | |
| G1 | **FAO**, FAOLEX | national legislation on food, agriculture, environment, ~200 countries; constitutions | open data CSV downloads | CSV + links | CC BY-NC-SA 3.0 IGO | metadata + summaries, multilingual |
| G2 | **WIPO Lex** | > 50,000 IP laws and treaties, 200 jurisdictions | search only, no bulk found | HTML, PDF | | |
| G3 | **UN Treaty Collection** | UNTS, League of Nations, MTDSG | search only | HTML, PDF | open access | |
| G4 | **InforMEA** | environmental treaties, decisions, national reports | OData API | JSON | GPL toolkit; harvest by agreement | |
| G5 | **EUR-Lex-Sum** | 1,500 act/summary pairs per language, 24 languages | Hugging Face `dennlinger/eur-lex-sum` | JSON | CC BY 4.0 | ready dataset |
| G6 | **UN General Assembly** resolutions | every GA resolution to the 80th session; 916,554 recorded votes | UN Digital Library CSV (record 4060945) | CSV | open | metadata; text via ODS |
| G7 | **UN Security Council**, CR-UNSC | every UNSC resolution, full text + metadata | Zenodo DOI 10.5281/zenodo.7319780 | CSV, TXT, PDF | open (to confirm) | ready dataset |
| G8 | **Comparative Constitutions Project** | Characteristics of National Constitutions v5.0, > 13,000 country-years; chronology v6.0 | comparativeconstitutionsproject.org/download | CSV, DTA + codebook | free for research | coded data, ✗ text (text is row 35) |
| G9 | **LexGLUE** benchmark | 7 English legal tasks (ECtHR, EUR-LEX, CaseHOLD, ...) | HF `lex_glue`; github coastalcph/lex-glue | JSON | per task | evaluation, ✗ corpus |
| G10 | **MultiLegalPile** | 689 GB, 24 languages, 17 jurisdictions: native legal, EUR-Lex, Legal mC4, Pile of Law (292 GB) | HF `joelniklaus/Multi_Legal_Pile`; `_Commercial` subset CC BY-SA 4.0 | JSONL | mixed per subset | pretraining corpus; mines new sources |
| G11 | **QuantGov / RegData** | word and restriction counts per part, US federal + state admin codes, Canada | quantgov.org bulk CSV | CSV | free | statistics, ✗ text |
| G12 | **US SEC**, EDGAR full-text search | filings + exhibits since 2001 | efts.sec.gov (10,000 hits per query); bulk submissions API | JSON | public | disclosures, ✗ regulation; low priority |

Sources: open.canada.ca dataset ff56de85-f8b9-4719-8dff-ecf362adf0af ·
laws-lois.justice.gc.ca/eng/FAQ · research.legislation.gov.uk/data ·
data.gouv.fr/datasets/legi-codes-lois-et-reglements-consolides ·
laws.e-gov.go.jp/docs/law-data-basic/8529371-law-api-v1 · gesetze-im-internet.de/hinweise.html ·
eur-lex.europa.eu/content/help/data-reuse/reuse-contents-eurlex-details.html
