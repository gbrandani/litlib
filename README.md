# litlib

A local, offline-first literature manager and citation controller for research papers and books. Unlike cloud-based reference managers, `litlib` keeps all PDFs, plain text extractions, index metadata, and bibliography entries locally on your machine, exposing them via a sleek self-hosted web application and a native Microsoft Word referencing add-in with zero cloud dependencies.

This directory is a local literature library containing:

- `articles/`: research papers, usually as PDFs
- `books/`: books, lecture notes, and longer reference material
- `paper_index/`: machine-readable local index and derived artifacts
- `scripts/`: local maintenance scripts for ingestion and backfill

This library is designed to remain usable in two ways at the same time:

- as a normal filesystem for a human browsing papers by topic
- as a structured corpus that can be consumed by local scripts and LLM tools

## Current state

- Article PDFs are currently fully indexed in `paper_index/master_index.tsv`
- Canonical BibTeX entries are collected in `paper_index/master.bib`
- Extracted text and chunk files are present for indexed records
- Some records are `manual_verified` because the local filename did not match the
  actual publication metadata or because the item is not a standard journal article
- Supplements that should not carry standalone citations are linked with
  `matched_supplement`

## Web Application & Word Add-in Integration

The library features a local secure web application and a Microsoft Word Reference Manager add-in:

* **Startup Script (`scripts/run_webapp.py`)**: Launches the FastAPI backend server over HTTPS (port 8000) and serves the React-based web application.
* **Web App Features**:
  - **Sleek Slate-Light Theme**: Premium slate-light design system with Sky-blue accents.
  - **Folder-Based PDF Ingestion**: Browse topic folders and ingest untracked PDFs directly in the browser.
  - **4-Step Guided Ingestion Wizard**: Resolves metadata via DOI/Crossref, Semantic Scholar, or manual BibTeX paste.
  - **Duplicate Prevention & Resolution**: Audits SHA256 checksums on ingestion and offers clean merge or deletion paths.
  - **Supplementary Materials**: Link appendixes/supplements to parent articles, with multi-word search and inline viewer shortcuts.
  - **Search Relevance**: Matches authors, journal abbreviations, DOI tokens, quoted phrases, and neighboring publication years (using distance-decay scoring).
  - **Inline PDF & Text Viewer**: Opens document previews directly in Chrome/Safari with preserved save filenames.
* **Microsoft Word Add-in**: A Mendeley-like citation manager for macOS Word. Insert citations, add dynamic bibliographies, auto-refresh numbering/formatting, and change CSL styles in real-time. See [WORD_INTEGRATION.md](file:///Users/brandani/Dropbox/documents/library/WORD_INTEGRATION.md) for setup.

## New PDF Checklist

When you manually add one new article PDF to the library:

1. Put the PDF in the right topic folder under `articles/`.
2. Make sure `SEMANTIC_SCHOLAR_API_KEY` is available.
3. Run `python3 scripts/library_add.py pdf /absolute/path/to/file.pdf`.
4. Select the correct Semantic Scholar candidate, retry the query, or paste one BibTeX entry manually.
5. Confirm the normalized metadata before it is written.
6. Check the new row in `paper_index/master_index.tsv`.
7. If the match is good, optionally run `python3 scripts/library_rename_from_index.py --downloaded-only --apply` or `--touchup-only --apply` if the filename still needs cleanup.
8. If the match is unresolved or suspicious, inspect `paper_index/records/<paper_id>.json` before doing anything else.

For PDF-backed ingest, the system now prefers DOI/Crossref metadata whenever a
trustworthy DOI can be extracted from the converted PDF text and validated
against the inferred title. Semantic Scholar remains the discovery and
enrichment layer.

For non-interactive batch processing, keep using `python3 scripts/library_ingest.py /absolute/path/to/file.pdf` or `python3 scripts/library_backfill.py`.

## Reference-only Checklist

When you want a citation record and abstract in the library without attaching a PDF:

1. Run `python3 scripts/library_add.py ref --query "paper title or keywords"`.
2. Select the correct Semantic Scholar result, retry with a better query, or paste one BibTeX entry manually.
3. Confirm the normalized metadata.
4. The record will be stored as `content_kind=reference_only`.
5. A metadata-only text stub and chunk file will still be written under `paper_index/` so LLM tools can search the abstract and citation metadata.

## Recommended entry points

For humans:

- Start with [LIBRARY_USAGE.md](/Users/brandani/Dropbox/documents/library/LIBRARY_USAGE.md)
- Read [WORD_INTEGRATION.md](/Users/brandani/Dropbox/documents/library/WORD_INTEGRATION.md) for Microsoft Word reference manager integration
- Use [paper_index/README.md](/Users/brandani/Dropbox/documents/library/paper_index/README.md) for the index layout
- For interactive maintenance of an existing record, start with `scripts/library_edit.py`

For scripts and LLMs:

- `scripts/library_lookup.py` is the preferred read-only interface for search,
  lookup, and citation rendering
- `paper_index/master_index.tsv` is the canonical per-file registry
- `paper_index/master_index.tsv` now contains one canonical record per paper or reference, with `content_kind` distinguishing `pdf_backed` from `reference_only`
- `paper_index/master.bib` is the canonical bibliography output
- `paper_index/journal_abbreviations.tsv` is the canonical journal-abbreviation table
- `paper_index/search_index.jsonl` is a derived metadata/abstract search cache
- `paper_index/records/<paper_id>.json` contains per-paper provenance and matching details
- `paper_index/text/<paper_id>.txt` contains raw extracted text for LLM ingestion
- `paper_index/chunks/<paper_id>.jsonl` contains chunked text for retrieval

## Maintenance scripts

- `scripts/library_add.py`: guided human-in-the-loop addition for PDFs and reference-only entries
- `scripts/library_edit.py`: unified interactive maintenance for existing records
- `scripts/library_ingest.py`: ingest one PDF
- `scripts/library_backfill.py`: batch backfill unindexed PDFs
- `scripts/library_migrate_reference_model.py`: one-time schema migration for the guided/reference-aware model
- `scripts/library_rename_from_index.py`: rename resolved files to the canonical filename convention
- `scripts/library_export_bib.py`: export project-local `.bib` files with optional journal abbreviation rewriting
- `scripts/library_lookup.py`: search the library, inspect one record, and render one bibliography entry in a supported citation style
- `scripts/library_fetch.py`: read-only Semantic Scholar lookup that can emit canonical BibTeX/metadata and optionally download a PDF or extract text without modifying the library; when a PDF is downloaded/extracted it may upgrade the final metadata via DOI/Crossref
- `scripts/library_fetch_bib.py`: bib-only compatibility wrapper over `library_fetch.py`
- `scripts/library_reconcile_metadata.py`: dry-run or apply DOI/Crossref upgrades for existing PDF records that were originally indexed from Semantic Scholar metadata
- `scripts/library_normalize_journals.py`: normalize canonical journal-name capitalization across the index and bibliography
- `scripts/library_bib_sanity_check.py`: validate `paper_index/master.bib` for malformed or duplicate entries
- `scripts/library_rebuild_master_bib.py`: rebuild a clean canonical `paper_index/master.bib` from the indexed records
- `scripts/library_refresh_record.py`: refresh one edited record JSON back into `master_index.tsv` and `master.bib`
- `scripts/library_edit_bib.py`: lower-level one-record BibTeX replacement wrapper
- `scripts/library_dedup_audit.py`: audit likely duplicate or malformed records without changing the library
- `scripts/library_manage.py`: lower-level scripted move/delete/dedup wrapper with dry-run by default

## Interactive editing

For existing records, the recommended human-facing entrypoint is:

- `python3 scripts/library_edit.py --query "paper title or keywords"`
- `python3 scripts/library_edit.py --bibtex-key KEY`
- `python3 scripts/library_edit.py --paper-id PAPER_ID`
- `python3 scripts/library_edit.py --pdf-path "articles/.../file.pdf"`

After selecting a record, it can:

- replace the BibTeX entry
- refresh derived metadata from the record JSON
- move the tracked PDF
- delete the indexed record
- merge one duplicate into another
- show the raw record JSON

When a multiline BibTeX editor opens in either `library_add.py`, `library_edit.py`, or `library_edit_bib.py`:

- normal arrow-key navigation is available
- large paste is supported
- finish by typing a final line containing only `END`

## Read-only lookup

When you want to search the library or paste one citation elsewhere:

- `python3 scripts/library_lookup.py search "query terms"`
- `python3 scripts/library_lookup.py show <paper_id-or-bibtex_key>`
- `python3 scripts/library_lookup.py cite <paper_id|bibtex_key|query> --style nature`
- `python3 scripts/library_fetch.py --query "paper title or keywords" --best`
- `python3 scripts/library_fetch.py --query "paper title or keywords" --download-pdf`
- `python3 scripts/library_fetch.py --query "paper title or keywords" --download-pdf --extract-text`

Fetched project-local artifacts are named from the selected BibTeX key so they
stay easy to match back to `references.bib`:

- PDF/text stem: `bibtexkey_JouAbb`
- the venue suffix is built by taking the first 3 letters of each venue word,
  capitalizing the first letter, and skipping words shorter than 3 letters
- if the venue is missing, the stem falls back to just `bibtexkey`

Useful flags:

- `--json` for model-friendly structured output
- `--author`, `--year`, `--venue`, `--doi` for deterministic filtering
- `--has-pdf` or `--reference-only` to restrict by record kind
- `--scope fulltext` to search extracted paper text explicitly

Year filters accept:

- `2020`
- `2020-2025`
- `2020-`
- `-2020`

Supported citation styles in v1:

- `nature`
- `nlm`
- `acs`
- `ieee`

Accepted compatibility aliases:

- `natcommun`, `srep` -> `nature`
- `science`, `scienceadv`, `pnas`, `ploscompbiol` -> `nlm`
- `nar` -> `nar`
- `jctc` -> `acs`
- `prl` -> `ieee`

The CLI is now style-first rather than journal-first. Journal-like names remain
accepted only as compatibility aliases. The renderer still uses the vendored
local CSL families in `paper_index/csl/`, so exact journal-specific output may
remain an approximation when no dedicated CSL is present locally.

## Journal abbreviations

The canonical stored form for journal abbreviations is now a citation-facing form
with spaces and periods where words are actually abbreviated, for example
`Nat. Commun.` or `J. Mol. Biol.`.

- `paper_index/journal_abbreviations.tsv` stores:
  - `citation_abbreviation`: the canonical abbreviation for references and BibTeX export
  - `filename_abbreviation`: only explicit overrides for compact filename tokens such as `PNAS`, `NAR`, `PRL`, `PRE`, and `PRX`
- Filename abbreviations are otherwise derived automatically from the citation form
  by removing spaces and periods
- Project `.bib` files can be exported either with dotted abbreviations or with
  dots stripped later depending on the citation style

## Environment prerequisites

The maintenance scripts currently assume:

- Python 3
- `SEMANTIC_SCHOLAR_API_KEY` is available in the environment
- `pdf2text.py` exists at `/Users/brandani/Dropbox/scripts/pdf2text.py`
- network access is available when metadata needs to be fetched

These assumptions matter if another Codex session or another local tool is asked
to maintain the library rather than just read from it.

For read-only fetches outside the library:

- `library_fetch.py` does not touch `master_index.tsv`, `master.bib`, or `records/`
- metadata-only is the default behavior
- downloaded PDFs and extracted text go to the caller's current directory unless
  `--output-dir` is passed
- PDF-to-text extraction uses the canonical `~/Dropbox/scripts/pdf2text.py`
  backend

For cleanup of older Semantic-Scholar-derived records:

- Start with a dry-run, for example `python3 scripts/library_reconcile_metadata.py --only-semantic --limit 25`
- Inspect proposed DOI/Crossref upgrades before applying them
- `job_type=enrich_existing_entry` is for additive cleanup of an otherwise correct record
- `job_type=rewrite_entry` is for identity/version-changing corrections such as preprint DOI to journal DOI or year/title/first-author changes
- `--apply` blocks rewrite cases by default; add `--apply-rewrites` only after targeted review
- Apply one target with `--paper-id ... --apply`, or apply a reviewed batch with `--only-semantic --limit N --apply`
- Manual, Zotero-verified, and supplement records are skipped by default unless `--include-manual` is explicitly passed

The current indexing workflow is article-focused. Books are kept in the library,
but their metadata handling is more conservative and may require separate logic.

## Known limitations

- Extracted text is usually good enough for LLM use, but PDF layout can still
  produce column-order, header/footer, table, or supplement artifacts.
- Live metadata lookup depends on Semantic Scholar availability and correctness.
- DOI extraction is heuristic and validated against title similarity; not every
  DOI found inside a PDF belongs to the paper itself.
- Record JSON keeps verbose provenance and artifact paths; copying the library to
  a new root is not a supported workflow unless the paths are rebuilt.
