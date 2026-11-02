# FIXME — known metadata issues (resolved)

Found 2026-10-26 while exporting a project bibliography for
`~/Dropbox/documents/papers/26_COSB_chromatin_remodelling_and_3D_structure`. All issues
have been addressed in the canonical library index, records, scripts, and web application.

## Systemic (code / tables)

- [x] **Author lists are not valid BibTeX.** Normalized to standard `Family, Given and Family, Given` across all records in `master.bib` and `master_index.tsv`. Fixed BibTeX author parser/formatter (`library_ingest.normalize_authors_bibtex`, `format_crossref_author_list`, `split_single_given_family`) with support for multi-word surnames, compound particles (`van`, `de`, `dos santos`), and accented characters. Verified with `library_bib_sanity_check.py` (0 format issues).
- [x] **Crossref article numbers dropped.** Updated `library_ingest.crossref_pages()` to fall back to `article-number` when `page` is missing, with `normalize_pages_value()` formatting. Rebuilt across all affected records (`chen2025tune`, `fouziya2024nucleosome`, `gu2026based`, `jain2026specific`, `klempahn2024attractors`, `li2023disassembly`, `moore2025dependent`, `oberbeckmann2021elements`, `oberbeckmann2021nucleosomes`, `sabantsev2019direct`, `sia2025during`, `winger2018atp`, etc.).
- [x] **Preprint venue becomes "openRxiv".** `crossref_container()` and `crossref_publisher()` now check `institution[].name` (e.g. bioRxiv, medRxiv, Research Square) and DOI prefix fallback (`10.64898/`, `10.1101/`). Rebuilt affected records (`dalakishvili2025binding`, `linder2025chromatin`, `raj2026active`).
- [x] **Journal abbreviation table.** Cleaned in `paper_index/journal_abbreviations.tsv`: `Mol. Cell` and `Phys. Rev. Res.` canonicalized, trailing periods audited across all 155 entries.
- [x] **BibTeX keys drop non-ASCII letters.** `build_bibtex_key_from_fields` and `fold_unicode_to_ascii()` now properly transliterate accented characters (e.g., `ć`→`c`, `ö`→`o`, `ü`→`u`, `é`→`e`) instead of stripping them. All 17 affected keys (`foldes2026bridging`, `noe2019learning`, `mobius2013flanking`, `kohler2022matching`, `slavnikova2025helices`, `gomezgarcia2021differentiation`, `mullerspath2010can`, `martinezgarcia2023during`, `loven2013enhancers`, `farregil2024atomistic`, `ingolfsson2022driven`, `schopflin2013position`, `giniunaite2022modelling`, `lopeziniesta2023p53`, `hoie2022large`, `bombin2024fault`, `kulic2003chromatin`) have been updated in `master_index.tsv`, records, and `master.bib`.
- [x] **Semantic Scholar search returns nothing.** Fixed API query structure, added User-Agent header, exponential backoff on HTTP 429, and automatic OpenAlex fallback for abstract enrichment.

## Individual records

- [x] `linder2025chromatin` (bioRxiv 10.64898/2025.12.11.693632): Author order corrected to match PDF byline (`Linder, Garp and Horky, Daniela and Zernia, Sarah and Stigler, Johannes`).
- [x] `raj2026active`: Replaced manual placeholder with Crossref bioRxiv DOI `10.64898/2026.09.30.755691`, posted 2026-10-01, with authors `Raj, Ritu and Padinhateeri, Ranjith and Kumar, P. B. Sunil` and cleared bogus date in pages.
- [x] `dalakishvili2025binding`: Venue updated from "openRxiv" to "bioRxiv".
- [x] `oo2024complex` (bioRxiv 10.1101/2024.03.21.585172): Upgraded to published Nature Communications journal article (`10.1038/s41467-024-55539-6`, 2025).

## Missing from the library

- [x] Knoll et al. 2018 Nat. Struct. Mol. Biol. 25:823–832, 10.1038/s41594-018-0115-8: Ingested PDF into `articles/chromatin_remodelers/` as `knoll2018actin`.
- [x] Park, Wu & Louder 2026 Sci. Adv. (`park2026structural`): Crossref metadata refreshed (DOI `10.1126/sciadv.aei7728`, vol. 12, no. 31, eaei7728, title fixed to "H2A.Z").
- [x] Lobbia et al. 2025 J. Mol. Biol. (`lobbia2025dynamic`): Author list normalized to standard BibTeX format.
- [x] van Holde & Yager 1985, "Nucleosome Motion: Evidence and Models", in *Structure and Function of the Genetic Apparatus* (Springer US), pp. 35–53, 10.1007/978-1-4684-5024-8_4: Ingested as reference-only `@incollection` (`vanholde1985motion`).
- [x] van Holde & Yager 2003 Biochem. Cell Biol. 81:169–172, 10.1139/o03-038 ("Models for chromatin remodeling: a critical comparison"): Ingested as reference-only `@article` (`vanholde2003remodeling`) with parsed author names (`van Holde, K. and Yager, T.`).
- [x] Kulić & Schiessel 2003 Phys. Rev. Lett. 91:148103, 10.1103/physrevlett.91.148103 ("Chromatin Dynamics: Nucleosomes go Mobile through Twist Defects"): Ingested as reference-only `@article` (`kulic2003chromatin`).
- [x] Supplement of Oberbeckmann et al. 2021 Nat. Commun. 12:3231 (`korber_2021_NatCommun_INO80_genome_information_processing-SI.pdf`): Ingested into `articles/chromatin_remodelers/` and linked as supplement to `oberbeckmann2021nucleosomes`.
- [x] Wiese, Marenduzzo, Brackley 2019 PNAS (`10.1073/pnas.1817829116`): Ingested PDF into `articles/chromatin_structure_and_dynamics/` as `wiese2019alone`.
