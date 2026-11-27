# datasets/ — the cards of published datasets

One file per Hugging Face dataset, named as the repository: `<NAME>.md` is the `README.md`
of `Azzindani/<NAME>`. `ravel publish <corpus> -w <workspace> --repo Azzindani/<NAME>`
exports the corpus, puts this card beside it and uploads both; it refuses a dataset with no
card here. A card states what was measured, so it is written and reviewed like code and
never generated at publish time.

**Names**: `<COUNTRY>_<KIND>_<YYMM>`, the month of the snapshot.

| dataset | corpus | status |
|---|---|---|
| `CA_LAWS_2611` | `ca_laws` | published, with embeddings |
| `US_CFR_2611` | `us_cfr` | published, with embeddings |
| `JP_LAWS_2611` | `jp_egov` | published, with embeddings |
| `FR_LAWS_2611` | `fr_legi` | published, with embeddings |
| `DE_LAWS_2611` | `de_gii` | published, with embeddings |
| `ID_REG_RAG_2611` | `id_reg_markdown` | published, with embeddings (from bundle v6: `ravel publish --bundle`) |

On publishing, add the registry files the dataset was built with to
`registry/published.yaml`: they are frozen from then on (`docs/pipeline/PROFILES.md` §7).
