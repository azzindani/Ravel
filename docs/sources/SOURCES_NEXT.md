# SOURCES_NEXT.md — Ravel

Open regulation sources outside the US, surveyed 2026-11-21, to be added **one at a time**,
each by the process in `ADDING_A_SOURCE.md`:
each gets a corpus file, a profile and an XML dialect in YAML (`CLAUDE.md` rule 12), its
own `SOURCE_<ID>.md`, a full build, and a dataset. ✗ None is fetched yet unless its row
says so.

**Official sources only (2026-11-28).** Every row is published by the body that makes or
holds the text: a government, a legislature, a court, an official gazette or a state
open-data portal, or an intergovernmental body (UN, FAO, WIPO, OECD, WTO, the EU
institutions). Community uploads, scrapes, commercial sets, research corpora and
nonprofit mirrors (Hugging Face, Kaggle, Zenodo derivatives, LIIs not run by a state,
CourtListener, the Caselaw Access Project, MultiLegalPile) were removed: their provenance
is a copy, and a fork of our own published dataset appeared among them. A row that only
had a third-party route now names the official one, even where that is a web crawl.

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
| 14 | **Italy**, Normattiva | state legislation | Normattiva Open Data portal `dati.normattiva.it` (async API) | Akoma Ntoso XML | IODL 2.0 | candidate |
| 15 | **Netherlands**, BWB | ~40,000 national regulations | SRU service + KOOP repository, per-regulation `toestand` XML | BWB XML | CC0 | candidate; bulk path to confirm |
| 16 | **South Korea**, law.go.kr | all statutes, article by article | Open API (OC key), data.go.kr | XML | free use | candidate; no dump, list + detail |
| 17 | **EU**, Cellar SPARQL | EUR-Lex acts, all languages | `publications.europa.eu/webapi/rdf/sparql` lists item URLs, no EU Login reported | Formex XML, XHTML | free reuse | candidate; alternative to row 6 |
| 18 | **Brazil**, LexML / Normas.leg.br | federal norms since the Empire | Senate open data, LexML sitemap | LexML XML | open | candidate; bulk text to confirm |
| 19 | **New Zealand**, PCO | Acts, regulations | developer API (key), `.xml` per item | NZ legislation XML | API terms of use, to confirm | candidate |
| 20 | **Argentina**, InfoLEG | national laws, decrees, resolutions, dispositions | datos.gob.ar "Base Infoleg Normativa Nacional", zip + CSV; provincial norms in SAIJ set | CSV + text | open data | candidate; bulk zip |
| 21 | **Chile**, LeyChile (BCN) | all norms, versions since 1998 | web services (XML) + datos.bcn.cl SPARQL, JSON/RDF | XML, RDF | open data | candidate; XML export reported 401, to confirm |
| 22 | **Colombia**, SUIN-Juriscol | legislation 1821-present + case law | search portal; ✗ bulk found | HTML | free to consult | candidate; crawl only |
| 23 | **Estonia**, Riigi Teataja | gazette + consolidated acts, XML from 2010-11 | `riigiteataja.ee/avaandmed/` open-data directory; the Act requires API + bulk | XML | open data by law | candidate |
| 24 | **Czechia**, e-Sbírka | consolidated acts, legally binding e-edition since 2024 | open data + public API (vendor-stated) | to confirm | | candidate; docs to find |
| 25 | **Sweden**, Riksdagen SFS | consolidated statutes (SFS) + parliamentary documents | data.riksdagen.se API, per-document `sfs-YYYY-NNN`; dataset catalogue | XML, JSON | open data | candidate |
| 26 | **Denmark**, Retsinformation (Civilstyrelsen) | acts, executive orders, circulars | REST API `api.retsinformation.dk`, 1 call per 10 s | JSON, XML | retsinformation.dk terms | candidate |
| 27 | **Ukraine**, Verkhovna Rada | legislation + draft laws, > 110 parliament data sets | data.rada.gov.ua | CSV, JSON, XML | open data | candidate |
| 28 | **China**, NPC national law database | national laws and regulations | flk.npc.gov.cn, Word download per document | DOCX | | candidate; crawl only |
| 29 | **Australia (NSW)** | in-force and repealed legislation | per-title XML, `/export/day` and `/export/week` | XML (ExchangeXml) | | candidate; run outside NSW business hours |
| 30 | **Australia (Queensland)** | legislation | free API, registration | | | candidate |
| 31 | **Australia (Commonwealth)**, Federal Register of Legislation | federal legislation | keyless REST API, JSON + documents | JSON, DOCX | | candidate; replaces "not bulk" note above |
| 32 | **Singapore**, Statutes Online | Acts, subsidiary legislation | ✗ API; scrape only | HTML | | weak candidate |
| 33 | **Ireland**, eISB | Acts 1922-present (SIs too) | ELI URI with `xml` suffix per Act; data.gov.ie listing | XML | Oireachtas PSI Licence (CC BY 4.0) | candidate |
| 34 | **Hong Kong**, e-Legislation (DoJ) | all current + past (from 1997) legislation, EN + Traditional Chinese | data.gov.hk ZIP packages by cap range; weekly XML list | XML in ZIP | data.gov.hk terms | candidate; strong bulk |
| 35 | **Canada (BC)**, BC Laws | all B.C. provincial legislation | CiviX API (XML, XPath) + REST API | XML | OGL-BC | candidate |
| 36 | **Lithuania**, TAR (Seimas) | Register of Legal Acts: objects, adopters, chronologies, texts | data.gov.lt dataset 2613 | open data formats | open data | candidate |
| 37 | **Luxembourg**, Legilux | national legislation | SPARQL endpoint (data.public.lu, Casemates) | RDF | CC BY 4.0 | candidate |
| 38 | **Portugal**, Diário da República | gazette from 1910, ELI URIs | `data.dre.pt/eli/...` | to confirm | | candidate; bulk to confirm |
| 39 | **Vietnam**, vbpl.vn | national legal documents, article-level since 2026 | MoJ web service `ws.vbpl.vn` (documented API) | XML/JSON | reported open | candidate |
| 40 | **Bangladesh**, Laws of Bangladesh (Ministry of Law) | Acts 1799-present | bdlaws.minlaw.gov.bd web | HTML | | candidate; crawl only |
| 41 | **Hungary**, Nemzeti Jogszabálytár | legislation in force | web only | HTML | | weak candidate |
| 42 | **Greece**, Diavgeia | every administrative act of the public sector | open API, no key for reading | XML/JSON | open | candidate; acts, ✗ statutes |
| 43 | **UAE**, UAE Legislation | federal legislation since 1971 | portal; MoJ API marketplace | | | weak candidate |
| 44 | **Qatar**, Al Meezan | laws since 1961, rulings, opinions | portal, no bulk (2015) | HTML | CC BY (2015) | weak candidate |
| 45 | **Saudi Arabia**, Bureau of Experts | laws in English translation | Word downloads per law | DOCX | | weak candidate |
| 46 | **Turkey**, Mevzuat Bilgi Sistemi | consolidated legislation | mevzuat.gov.tr web | HTML | | weak candidate |
| 47 | **Israel**, National Legislation Database | laws in force since the Mandate, Hebrew | portal | HTML | | weak candidate |
| 48 | **Canada, provinces**, official publishers | Ontario e-Laws (XML sitemap), Alberta King's Printer, LégisQuébec | each province's site; bulk to confirm | XML, HTML | per province | candidate; extends ca_laws |
| 49 | **US**, CFR annual editions (GovInfo) | every CFR title per year, 1996-present | govinfo.gov/bulkdata/CFR/<year>, zip per title | XML | public domain | point-in-time us_cfr |
| 50 | **Pakistan**, Pakistan Code (Ministry of Law) | federal Acts | pakistancode.gov.pk web | HTML, PDF | | candidate; to confirm |
| 51 | **Kenya**, Kenya Law (National Council for Law Reporting) | Laws of Kenya + judgments | kenyalaw.org web | HTML, PDF | | candidate; bulk to confirm |
| 52 | **India**, NyayKosh (law as code) | provisions, rules, penalties | open JSON APIs, no registration | JSON | to confirm | structured, ✗ full Acts |
| 53 | **Romania**, legislatie.just.ro (Ministry of Justice) | national legislation, consolidated | web + SOAP API that allows bulk (2015 report) | XML | no copyright, free reuse | candidate; API status to confirm |
| 54 | **Thailand**, central law system (DGA) | Thai legislation data | data.go.th dataset `law-portal-01` | to confirm | CC BY | candidate; contents to confirm |
| 55 | **Malaysia**, Laws of Malaysia (AGC) | federal Acts | lom.agc.gov.my | PDF | | weak; crawl only |
| 56 | **Uruguay**, IMPO (Centro de Información Oficial) | national legislation, decrees | impo.com.uy, JSON per norm (reported) | JSON (ISO-8859-1) | open data, attribution | candidate; API to confirm |
| 57 | **US, Arkansas**, administrative rules | the state's whole rules database | sos.arkansas.gov bulk download, by agency/chapter | to confirm | public | first state admin code with bulk |
| 58 | **Belgium**, Justel / Moniteur belge | consolidated acts since 1994; gazette since 1997 | ejustice.just.fgov.be web | HTML, PDF | | weak; no API found |
| 59 | **Slovakia**, Slov-Lex (Ministry of Justice) | legislation + court archives | obcan.justice.sk/opendata archives | to confirm | open data | candidate; API to confirm |
| 60 | **Malta**, Laws of Malta | national legislation, ELI adopted | legislation.mt | to confirm | | candidate |
| 61 | **US, Virginia**, Code of Virginia | state statutes | LIS data API, no key | JSON, XML, CSV | public | strong; state statutes |
| 62 | **US states**, statutes with bulk files | Utah (XML), New York (JSON API, free key), Michigan + Iowa (XML per chapter), California (yearly DB package) | per-state legislature sites | XML, JSON | per state | candidates; one profile each |
| 63 | **Georgia**, Matsne (Legislative Herald) | consolidated laws free; subordinate acts paid | matsne.gov.ge web + RSS feed of new documents | HTML, PDF | | candidate; crawl, English versions exist |
| 64 | **Bulgaria**, State Gazette | gazette PDFs, last 7 years; amendments, ✗ consolidated | dv.parliament.bg | PDF | free | weak |
| 65 | **Armenia**, ARLIS (Ministry of Justice, Official Bulletin) | all Armenian legal acts | arlis.am web, free, no registration | HTML | | candidate; no official bulk found |
| 66 | **Russia**, pravo.gov.ru (official publication portal) | federal legal acts | web, ✗ bulk, ✗ machine-readable (2015 review) | HTML | | weak |
| 67 | **Central Asia**, Kazakhstan Adilet, Uzbekistan Lex.uz, Kyrgyzstan CBD | national legislation databases | ministry open-data sections; data.egov.kz, data.gov.uz APIs | to confirm | | weak; no legislation API found |
| 68 | **US**, IRS Internal Revenue Manual | the IRS's internal procedures, per section | irs.gov/downloads/irm, zip per section | XML/HTML in zip | public domain | guidance, ✗ regulation; sits beside 26 CFR |
| 69 | **Sri Lanka**, Government Press + LawNet | Acts from 1980, bills from 2003, gazette from 2006 | documents.gov.lk; lawnet.gov.lk | PDF | | PDF; text quality to check |
| 70 | **Nepal**, Law Commission | prevailing laws, bills, archive | lawcommission.gov.np | PDF | | candidate; crawl |
| 71 | **Jordan**, open data portal | a few laws as PDFs | opendata.gov.jo | PDF | | weak |
| 72 | **UK**, HMRC internal manuals | every HMRC manual, section by section | GOV.UK Content API `/api/content/hmrc-internal-manuals/...` | JSON | OGL | guidance; no bulk, iterate the listing |
| 73 | **Indonesia**, JDIH BPK | central + regional regulations with subject and theme metadata | peraturan.bpk.go.id (partial API, reported) | HTML, PDF | | cross-check id_reg metadata |
| 74 | **France**, LEGI vectorised | LEGI codes, laws, regulations pre-chunked and embedded | data.gouv.fr | Parquet | Licence Ouverte 2.0 | publisher to confirm as a state body; other model than ours |

**Candidates (rows 7-74, surveyed 2026-11-28 by web search, ✗ fetched).** Ranked for
Ravel by bulk access and structure: Spain, Finland, Taiwan and Norway have keyless bulk or
list-all endpoints and structured XML; Austria, Switzerland, Italy and the Netherlands
are structured but need per-document crawling; Poland's text is mostly PDF.

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
| C1 | **US**, federal court opinions (GovInfo USCOURTS) | opinions of federal appellate, district and bankruptcy courts | GovInfo collection + API | PDF, XML metadata | public domain | to confirm coverage |
| C2 | **France**, Judilibre (Cour de cassation) | ~534K Cassation rulings + appeal/first-instance courts | PISTE API (free key) | JSON | Licence Ouverte 2.0 | same language as fr_legi |
| C3 | **Germany**, rechtsprechung-im-internet | 7 federal courts (BVerfG, BGH, BVerwG, BFH, BAG, BSG, BPatG) | per-decision XML zip, XML from 2010 | XML | free reuse | portal to be replaced by the new federal system |
| C4 | **EU**, CJEU via Cellar | judgments, orders, AG opinions | Cellar SPARQL + REST | XHTML, Formex | free reuse | no CURIA bulk |
| C5 | **ECHR**, HUDOC (European Court of Human Rights) | judgments, decisions, communicated cases | hudoc.echr.coe.int search | HTML, PDF | | no official bulk |
| C6 | **UK**, Find Case Law (TNA) | judgments + tribunal decisions | API + Atom feeds; bulk on request | LegalDocML XML | Open Justice Licence; computational use needs a licence | |
| C7 | **Indonesia**, Direktori Putusan MA | ~10.4M decisions | putusan3.mahkamahagung.go.id search portal | HTML, PDF | | no bulk; Indonesian like id_reg |
| C8 | **Japan**, 裁判所 判例検索 | Supreme Court + lower courts | search portal, PDF only | PDF | | national civil-judgment DB planned (Act No. 49 of 2025) |
| C9 | **Netherlands**, Rechtspraak Open Data | > 500,000 pseudonymised judgments, ECLI, citations | open API, no key; XML dump ended 2023 | XML | CC0 | |
| C10 | **Brazil**, STJ open data portal | case law, precedents, judgment sessions, e-gazette, full text | portal downloads | CSV, JSON | reuse incl. commercial | |
| C11 | **Brazil**, CNJ DataJud | procedural data, all courts | public API | JSON | | metadata, ✗ full text |
| C12 | **South Korea**, precedents | ~80K Supreme Court + lower + Constitutional Court | data.go.kr PrecedentInformationService; law.go.kr Open API | XML, JSON | precedents not copyrighted | |
| C13 | **Taiwan**, Judicial Yuan open data | judgments + 800 datasets | opendata.judicial.gov.tw, judgments need membership | JSON | OGDL-Taiwan 1.0 | |
| C14 | **Mexico**, SCJN Buscador Jurídico | rulings, theses (SJF) | bj.scjn.gob.mx open data section, API listed | JSON | | to confirm |
| C15 | **Spain**, CENDOJ | Supreme Court + higher courts | search, PDF; reuse by agreement | PDF | | no bulk |
| C16 | **ICJ**, International Court of Justice | every judgment, advisory opinion, order | icj-cij.org, PDF per decision | PDF | | crawl |
| C17 | **WTO**, dispute settlement documents | every DSU dispute since 1995 | data.wto.org | HTML, XLSX, DOCX | | |
| C18 | **Austria**, RIS Judikatur | VfGH, VwGH, OGH, BVwG (from 2014), Landesverwaltungsgerichte | same OGD API as row 11 | XML/JSON | CC BY 4.0 | German like de_gii |
| C19 | **Finland**, Finlex case law | KKO + KHO precedents, courts of appeal | opendata.finlex.fi (row 8) | XML | to confirm | |
| C20 | **Tanzania**, TanzLII (Judiciary + Attorney General) | judgments + legislation | website + APIs; bulk by email request | HTML, JSON | terms of use | bulk on request |
| C21 | **US**, GAO Comptroller General decisions | bid protests, appropriations law, 1989-2008 | GovInfo bulk; later on gao.gov | XML, PDF | public domain | |
| C22 | **US**, AG/BIA precedent decisions (EOIR) | immigration precedent decisions, by volume | justice.gov/open data.gov entry | PDF + listing | public domain | |
| C23 | **US**, NLRB Board decisions | every Board decision, searchable index | nlrb.gov; FOIA E-Library | PDF, HTML | public domain | no bulk found |
| C24 | **France**, Conseil d'État + administrative courts open data | all CE, CAA, TA decisions; CE daily since 2021 | opendata.justice-administrative.fr; data.gouv.fr monthly zip; JADE (DILA) | XML | Licence Ouverte | French like fr_legi |
| C25 | **Colombia**, Corte Constitucional relatoría | ~30,000 rulings (23K tutela, 7K constitutionality) | corteconstitucional.gov.co/relatoria HTML per ruling; statistics on datos.gov.co | HTML | public | crawl; Spanish |
| C26 | **Indonesia**, Mahkamah Konstitusi | constitutional review decisions; case statistics | mkri.id/perkara/persidangan/putusan; data.go.id statistics (JSON, XLSX) | PDF, JSON | public | Indonesian like id_reg |
| C27 | **Malaysia**, court judgments | Malaysian Judgments Portal (limited) + Federal Court grounds of judgment | judgments.my; kehakiman.gov.my | HTML, PDF | | |
| B1 | **US**, GovInfo bulk BILLS / BILLSTATUS | bills, status, public laws, Statutes at Large | govinfo.gov/bulkdata, by Congress | USLM XML | public domain | enrolled bills from the 113th |
| B2 | **US**, Federal Register + Regulations.gov | rules, proposed rules, notices; dockets + comments | GovInfo bulk XML; Regulations.gov API v4 (1,000/h) + bulk download | XML, JSON | public domain | the rulemaking behind us_cfr |
| B3 | **UK**, Parliament | bills, divisions, questions, SIs, treaties | developer.parliament.uk REST + SPARQL | JSON, RDF | Open Parliament Licence | |
| B4 | **EU**, Parliament open data | adopted texts, votes, documents | data.europarl.europa.eu API v2 (500 / 5 min) | JSON-LD | CC BY 4.0 | |
| B5 | **US**, Congress.gov API | bills, amendments, actions, text | api.congress.gov v3, api.data.gov key | JSON, XML | public domain | |
| B6 | **US**, United States Code (OLRC) | the whole Code per release point | uscode.house.gov zip per title or whole | USLM XML, XHTML, PDF | public domain | strong bulk |
| B7 | **Japan**, Diet minutes (国会会議録) | every Diet session since 1947 | NDL API (kokkai.ndl.go.jp/api.html) | XML, JSON | to confirm | |
| B8 | **Hong Kong**, LegCo Bills Database | bills by session | OData API | XML, JSON | data.gov.hk terms | |
| B9 | **Israel**, Knesset open data | bills, votes, committees | Knesset OData API | XML, JSON | to confirm | |
| B10 | **EU**, Council public voting register | Council votes on legislative acts | consilium.europa.eu / data.europa.eu | CSV, JSON | EU open data | |
| G1 | **FAO**, FAOLEX | national legislation on food, agriculture, environment, ~200 countries; constitutions | open data CSV downloads | CSV + links | CC BY-NC-SA 3.0 IGO | metadata + summaries, multilingual |
| G2 | **WIPO Lex** | > 50,000 IP laws and treaties, 200 jurisdictions | search only, no bulk found | HTML, PDF | | |
| G3 | **UN Treaty Collection** | UNTS, League of Nations, MTDSG | search only | HTML, PDF | open access | |
| G4 | **InforMEA** | environmental treaties, decisions, national reports | OData API | JSON | GPL toolkit; harvest by agreement | |
| G5 | **UN General Assembly** resolutions | every GA resolution to the 80th session; 916,554 recorded votes | UN Digital Library CSV (record 4060945) | CSV | open | metadata; text via ODS |
| G6 | **UN Security Council** resolutions | every UNSC resolution | UN Digital Library + un.org/securitycouncil, PDF per resolution; ODS | PDF, MARC metadata | open access | |
| G7 | **US SEC**, EDGAR full-text search | filings + exhibits since 2001 | efts.sec.gov (10,000 hits per query); bulk submissions API | JSON | public | disclosures, ✗ regulation; low priority |
| G8 | **OECD**, Compendium of Legal Instruments | every OECD Decision, Recommendation, Declaration, in force or abrogated | legalinstruments.oecd.org, PDF per instrument | PDF | OECD terms | ~ 270 instruments; crawl |
| G9 | **EU DG Competition**, case data | antitrust, cartels, mergers, state aid, DMA, FSR cases | data.europa.eu, JSON updated daily | JSON | Commission reuse notice (commercial ok) | metadata + decisions links |
| G10 | **OECD**, tax treaty statistics + MLI matching database | > 5,300 bilateral treaties; MLI effect per treaty | oecd.org/tax/treaties | XLSX | OECD terms | metadata, ✗ treaty text |

Sources: open.canada.ca dataset ff56de85-f8b9-4719-8dff-ecf362adf0af ·
laws-lois.justice.gc.ca/eng/FAQ · research.legislation.gov.uk/data ·
data.gouv.fr/datasets/legi-codes-lois-et-reglements-consolides ·
laws.e-gov.go.jp/docs/law-data-basic/8529371-law-api-v1 · gesetze-im-internet.de/hinweise.html ·
eur-lex.europa.eu/content/help/data-reuse/reuse-contents-eurlex-details.html
