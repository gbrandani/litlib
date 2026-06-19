# Library Usage

This file explains how to use and maintain this library over time.

It is written for both:

- humans maintaining the library manually
- machine tools or LLM agents reading from the local corpus

## Purpose

The library stores canonical local copies of papers and books. The folders under
`articles/` and `books/` are the human navigation layer. The `paper_index/`
directory is the machine-readable layer used for retrieval, text ingestion, and
bibliography management.

## Ground rules

- Do not treat raw PDF metadata as authoritative.
- Prefer canonical metadata from the local index.
- Prefer `paper_index/master_index.tsv` over inferring metadata from filenames.
- Prefer `paper_index/text/*.txt` over reading PDFs directly when sending papers to an LLM.
- Keep in mind that extracted text can contain layout artifacts.
- Keep in mind that some records are `reference_only`, so their text is a metadata stub rather than PDF-derived full text.
- Treat `paper_index/journal_abbreviations.tsv` as the canonical journal-abbreviation table.

## Current readiness

- The article library is currently fully indexed.
- `paper_index/master_index.tsv` is the canonical registry other projects should use.
- `paper_index/master.bib` is the canonical bibliography output other projects should cite from.
- `paper_index/journal_abbreviations.tsv` is the canonical source for journal abbreviations.
- `scripts/library_lookup.py` is the preferred read-only interface for finding
  records and rendering one bibliography entry on demand.
- New projects generally do not need to call the maintenance scripts unless they are adding or correcting files.

## Local Web Application

The Local Literature Library features a browser-based web application that provides a complete graphical interface for searching, browsing, ingesting, and maintaining your library.

### Starting the Application

To start the web application and its backend API:
```bash
python3 scripts/run_webapp.py
```
This runs the FastAPI server on port 8000. If SSL certificates (`cert.pem` and `key.pem`) are present in the library root, it automatically runs over secure HTTPS at **`https://localhost:8000`** (which is required by the Microsoft Word Add-in).

### Features and Workflows

#### 1. Premium Interface (Slate-Light Theme)
* The web app renders in a sleek, responsive dual-pane layout using a custom **Slate-Light** theme with Sky-blue interactive accents.
* **Main Table**: Lists up to 1,000 papers with columns for Title, Authors, Year, Venue, Type, and Relevance.
* **Column Sorting**: Click on any table header to sort results alphabetically, chronologically, or by category.
* **Folder Filtering**: Use the Folder dropdown to filter the table by a specific topic subdirectory under `articles/`.
* **Sidebar Badges**: Displays real-time counts of **Untracked PDFs**, **Duplicate Clusters**, and **Broken Entries** in the sidebar tabs. Clicking a tab opens a dedicated audit view.

#### 2. Guided Ingestion Wizard
Instead of manually typing file paths, click **Ingest PDF** or **Add Reference** to launch a unified 4-step wizard:
1. **Browse & Select**: Browse untracked PDFs under `articles/` topic folders and select a file to ingest.
2. **DOI/Crossref Resolution**: Scans the PDF text, extracts the DOI, queries Crossref, and populates full citation fields (including volume, issue, pages, and publisher).
3. **Semantic Scholar Search**: If no DOI is found, search Semantic Scholar by title or keywords and select from the candidate list.
4. **Manual BibTeX**: Direct textarea to paste a raw BibTeX entry.

#### 3. Ingestion Duplicate Handling
If the scanned PDF's SHA256 checksum matches an already indexed file, the wizard alerts you with a warning card and lets you resolve the duplicate immediately:
* **Keep at New Path & Delete Old PDF**: Swaps the index record to the new folder location and deletes the old PDF from disk.
* **Delete This Duplicate PDF**: Deletes the newly selected duplicate PDF from disk, clearing it from the untracked list.
* **View Existing Paper**: Closes the wizard and opens the metadata details of the existing paper.

#### 4. Metadata Repair & Safe Deletion
For papers flagged as "Broken" (missing author, year, title, etc.), you can repair them in-place:
* **Repair Wizard**: Opens a modal displaying the exact file path and missing fields, allowing you to resolve metadata via DOI/Crossref, Semantic Scholar search, or manual BibTeX.
* **Safe Deletion**: A double-confirm deletion modal allows you to delete the library index record. You can choose to **delete only the index record** (preserving the PDF on disk) or **delete both the record and the physical PDF file**.

#### 5. Supplementary Materials System
The library supports linking appendixes, datasets, and other supplements directly to their parent papers:
* **Ingest as Supplement**: Select "Ingest as Supplement" in the wizard, search for the parent paper, and link the PDF.
* **Link Existing**: Select any existing paper and choose "Link as Supplement..." in the sidebar.
* **Parent-Child Navigation**: The sidebar details panel displays navigation links to easily jump between parent papers and their supplements.
* **View Shortcuts**: Parents with supplements display "View Supp" buttons in the main actions grid to instantly view supplement PDFs or read extracted texts inline.
* **Exclusion from Audits**: Supplement records are automatically excluded from the main table listing and duplicate audits to keep your library clean.
* **Metadata Inheritance**: Linked supplements automatically inherit critical metadata fields (authors, year, venue) from their parent paper.

#### 6. Advanced Search Relevance
When you enter a search query, the app defaults to relevance sorting and displays a **Relevance Score** column (hovering over the score shows the exact point breakdown). Scoring includes:
* **Surname Boost (+40 points)**: Matches author surnames (with extra weight on the first author).
* **Journal Abbreviations (+25 points)**: Matches compact abbreviations (like `PNAS` or `Nat. Commun.`).
* **DOI Substring Match (+40 points)**: Token matches against DOI strings.
* **Quoted Phrase Match (+40 / +15 points)**: Matches exact phrases enclosed in quotes inside the title or abstract.
* **Year Proximity Decay (+35 points max)**: Proportional boost for papers published near the year specified in the search query.
* **Bucketed Chronology**: Results are grouped into score buckets and sorted chronologically within each bucket.

#### 7. Inline PDF & Text Viewer
* Clicking **View PDF** or **Read Text** opens the file inline directly inside Chrome/Safari instead of triggering automatic downloads.
* When you click the browser's native download button, the file is saved using its descriptive library name (e.g. `Author Year Journal - Title.pdf`) rather than its hex `paper_id`.

## Important warning for LLM use

The extracted plain text is usually good enough for LLM reading, but it is not
guaranteed to be perfect.

Possible issues include:

- two-column reading order errors
- flattened author and affiliation blocks
- page header and footer residue
- figure legend and table noise
- supplement PDFs with very poor text structure
- publisher-specific formatting artifacts

Because of this:

- treat extracted text as a high-quality convenience layer, not a perfect source
- if a passage looks inconsistent, verify against the PDF or another chunk
- be cautious when relying on exact ordering of sentences around figures, tables, or supplements

## Recommended workflow for humans

When adding a new article:

1. Place the PDF in the most sensible topic folder under `articles/`.
2. Use a filename that is as informative as possible.
3. Ensure `SEMANTIC_SCHOLAR_API_KEY` is available and that `/Users/brandani/Dropbox/scripts/pdf2text.py` exists.
4. Run `python3 scripts/library_add.py pdf /absolute/path/to/file.pdf`.
5. Select the right Semantic Scholar candidate, retry the search, or paste one BibTeX entry manually.
6. Confirm the normalized metadata before writing it.
7. Check the resulting `match_status` and `content_kind` in `paper_index/master_index.tsv`.
8. If unresolved, review the corresponding `paper_index/records/<paper_id>.json`.

For PDF-backed ingest, the canonical citation fields now prefer DOI/Crossref
when a trustworthy DOI can be extracted from the converted PDF text and
validated against the inferred title. Semantic Scholar remains the discovery,
fallback, and enrichment layer.

When adding a citation-only reference with no PDF:

1. Run `python3 scripts/library_add.py ref --query "paper title or keywords"`.
2. Select the correct Semantic Scholar candidate, retry, or paste one BibTeX entry manually.
3. Confirm the normalized metadata.
4. The library will create a `reference_only` row, a BibTeX entry, and a metadata-only text/chunk stub for retrieval.
5. Missing DOI, URL, or abstract are acceptable in this manual path; the entry should still be stored if the core citation fields are correct.

Quick reminder:

1. Add PDF to the right `articles/` folder.
2. Run `python3 scripts/library_add.py pdf /absolute/path/to/file.pdf`.
3. Confirm the metadata in the guided prompt.
4. Check `paper_index/master_index.tsv` and the matching record JSON.
5. If needed, clean up the filename with `scripts/library_rename_from_index.py`.

When processing many files:

1. Run `scripts/library_backfill.py`.
2. Let it index missing files in batch.
3. Review unresolved statuses after the batch completes.
4. If journal capitalization variants have accumulated over time, run `scripts/library_normalize_journals.py`.
5. If `paper_index/master.bib` ever looks suspicious or fails sanity checks, run `python3 scripts/library_rebuild_master_bib.py` and then re-run `python3 scripts/library_bib_sanity_check.py`.

When you want to audit duplicates safely:

1. Run `python3 scripts/library_dedup_audit.py` for a human-readable summary.
2. Add `--json` for a machine-readable report.
3. Add `--write-default-json` to store a timestamped report under `paper_index/logs/`.
4. Use the `classification` and `preferred_keep_paper_id` fields to review likely duplicate copies manually.
5. Do not delete files blindly from `duplicate_groups` classified as `main_plus_supplement`, `manual_review`, or `malformed_rows`.

When you want to change one existing indexed record safely:

1. Start with `python3 scripts/library_edit.py --query "paper title or keywords"` or target one record directly with `--bibtex-key`, `--paper-id`, or `--pdf-path`.
2. Select the record once.
3. Use `edit-bib` to replace the BibTeX entry and refresh the record automatically.
4. Use `refresh-record` to rebuild the master index row and BibTeX entry from the record JSON.
5. Use `move-pdf` to move the tracked PDF and update the index consistently.
6. Use `delete-record` to remove the indexed record and optionally the tracked PDF.
7. Use `dedup-merge` to merge the current record into another or keep the current record and drop the other one.
8. Use `show-record` if you want to inspect the raw JSON before deciding.

Notes:

- `library_edit.py` is the recommended human-facing maintenance entrypoint.
- `library_manage.py` remains available as a lower-level scripted wrapper and is dry-run by default.
- dedup refuses supplement-like drops unless you explicitly force it.
- move/delete/dedup update the index and record JSON so you do not need to edit `master_index.tsv` by hand.
- the search cache is derived and will refresh later automatically.

When you want to edit one record by hand without using a metadata-entry script:

1. Edit `paper_index/records/<paper_id>.json` directly.
2. Run `python3 scripts/library_refresh_record.py --paper-id PAPER_ID`.
3. You can also target the same refresh by `--bibtex-key KEY` or `--pdf-path "articles/..."`.
4. This rewrites the matching `master_index.tsv` row and affected `master.bib` entry from the JSON record.
5. Use this when you need small fixes such as author names, DOI, venue, BibTeX key, or other verified metadata fields.

When you simply want to replace one BibTeX entry more directly:

1. Run `python3 scripts/library_edit_bib.py --query "paper title or keywords"` or target one record directly with `--bibtex-key`, `--paper-id`, or `--pdf-path`.
2. If multiple matches are found, select the record to modify.
3. Review the current BibTeX entry shown by the script.
4. The multiline editor supports normal cursor navigation and large paste.
5. Finish by typing a final line containing only `END`.
6. Confirm the parsed summary.
7. The script updates `manual_override.raw_bibtex` in the record JSON and refreshes `master_index.tsv` and `master.bib` automatically.

The same `END` rule is used by `library_add.py` and the `edit-bib` action inside `library_edit.py`.

When you want to search the library or paste one citation elsewhere:

1. Run `python3 scripts/library_lookup.py search "query terms"` for a human-readable shortlist.
2. Add `--author`, `--year`, `--venue`, `--doi`, `--has-pdf`, or `--reference-only` when you already know deterministic filters.
3. Run `python3 scripts/library_lookup.py show <paper_id-or-bibtex_key>` to inspect one exact record.
4. Run `python3 scripts/library_lookup.py cite <paper_id|bibtex_key|query> --style nature` to emit one bibliography entry.
5. Add `--json` when the caller is another local tool or an LLM session.
6. Add `--scope fulltext` only when you explicitly want body-text search; the default remains metadata-first.

When you want a read-only Semantic Scholar fetch without adding anything to the library:

1. Run `python3 scripts/library_fetch.py --query "paper title or keywords"` for interactive candidate selection.
2. Add `--best` to choose the top-ranked candidate automatically.
3. Add `--select N` to choose a specific 1-based candidate rank deterministically.
4. Add `--json` for machine-readable output.
5. Add `--list` if you only want the candidate shortlist and not the final selected record.
6. Add `--download-pdf` to download an open-access PDF when one is available.
7. Add `--extract-text` to run the canonical `pdf2text.py` pipeline after download.
8. Add `--output-dir /path/to/dir` to write the downloaded PDF and extracted text somewhere other than the caller's current directory.
9. This script does not modify `master_index.tsv`, `master.bib`, or `records/`.
10. Downloaded PDF/text artifacts are named from the selected BibTeX key:
    `bibtexkey_JouAbb.pdf` and `bibtexkey_JouAbb.txt` when venue metadata is
    available, otherwise `bibtexkey.pdf` and `bibtexkey.txt`.

If you only want the old bib-only behavior, `python3 scripts/library_fetch_bib.py ...`
still works as a compatibility wrapper.

When you want to clean up older records that may have been indexed from a
Semantic Scholar preprint or incomplete journal record:

1. Run `python3 scripts/library_reconcile_metadata.py --only-semantic --limit 25` first.
2. Review the proposed DOI/Crossref upgrade, accepted DOI, score, job type, and fields that would change.
3. Apply one reviewed target with `python3 scripts/library_reconcile_metadata.py --paper-id <paper_id> --apply`.
4. For a reviewed batch, use `--only-semantic --limit N --apply`.
5. `job_type=enrich_existing_entry` means the PDF DOI validates the same article and the tool is mainly adding missing citation fields or normalized Crossref names.
6. `job_type=rewrite_entry` means identity/version fields would change, such as preprint DOI to journal DOI, year changes, low title similarity, or a different first author.
7. `--apply` blocks `rewrite_entry` cases by default; use `--apply-rewrites` only after targeted review.
8. The tool skips manual, Zotero-verified, and supplement records by default; use `--include-manual` only when that is intentional.
9. This is the preferred reusable cleanup path before considering hand edits.

The `--year` filter accepts either an exact year or a range:

- `2020`
- `2010-2020`
- `1998-`
- `-2020`

The supported style names are now the style families themselves:

- `nature`
- `nlm`
- `acs`
- `ieee`

Older journal-oriented names such as `science`, `nar`, `pnas`, `prl`, `jctc`,
`natcommun`, and `srep` are still accepted as compatibility aliases.

## Recommended workflow for LLMs and tools

When asked to use the library:

1. Read `paper_index/master_index.tsv` to find the relevant paper record.
2. Use `paper_index/text/<paper_id>.txt` for full-text reading.
3. Use `paper_index/chunks/<paper_id>.jsonl` for retrieval or targeted extraction.
4. Use `paper_index/master.bib` or the per-record metadata for citations.
5. Warn the user that extracted text may contain layout artifacts if exact interpretation matters.

If another local project wants to use this library, the safest pattern is:

1. Start with `scripts/library_lookup.py search ...` or `show ...`.
2. Read the resolved text or chunks via `paper_id`.
3. Pull citation data from `paper_index/master.bib`.
4. Use `scripts/library_fetch.py` when the project needs a read-only Semantic Scholar lookup for an external paper, optional PDF download, or optional plain-text extraction. If a PDF is downloaded or extracted, the final emitted metadata may be upgraded through DOI/Crossref.
5. Treat this library as read-only unless the project is explicitly performing library maintenance.

## Journal abbreviation policy

- The canonical stored abbreviation is the citation form with spaces and periods,
  for example `Nat. Commun.` or `J. Mol. Biol.`
- Compact filename journal tokens are not stored redundantly except for a few
  explicit overrides such as `PNAS`, `NAR`, `PRL`, `PRE`, and `PRX`
- If `filename_abbreviation` is blank, the compact filename token is derived by
  removing spaces and periods from the citation abbreviation
- For project-local bibliography export:
  - `python3 scripts/library_export_bib.py OUTPUT.bib --abbreviate-journals`
    writes dotted citation abbreviations
  - `python3 scripts/library_export_bib.py OUTPUT.bib --abbreviate-journals --strip-abbrev-dots`
    writes the same abbreviations with periods removed for styles that prefer that
- If a journal abbreviation is unknown or still disputed, the table may retain a
  full journal title temporarily rather than storing an unverified abbreviation

Do not:

- assume filenames are canonical titles
- assume all supplements are standalone papers
- assume raw extracted text preserves original PDF reading order perfectly

## Match statuses

Common statuses in `paper_index/master_index.tsv`:

- `matched`: accepted directly from Semantic Scholar
- `matched_via_doi`: DOI/Crossref supplied the canonical citation fields; Semantic Scholar may still be present as enrichment
- `matched_supplement`: supplement or supporting-information PDF linked to a parent paper
- `manual_verified`: manually verified against the PDF text and an external primary source
- `needs_manual_review`: ambiguous enough that human review is still needed
- `not_found`: no acceptable canonical match found
- `api_error`: temporary metadata lookup failure

## Canonical files

The most important local files are:

- `paper_index/master_index.tsv`
- `paper_index/master.bib`
- `paper_index/records/*.json`
- `paper_index/text/*.txt`
- `paper_index/chunks/*.jsonl`

`master_index.tsv` now includes:

- `content_kind`: `pdf_backed` or `reference_only`
- `bibtex_type`: canonical BibTeX entry type
- `canonical_url`: stable URL from Semantic Scholar, Crossref, or a manual override

## Long-term maintenance policy

- Keep the visible folder hierarchy useful for human browsing.
- Keep the canonical metadata and text artifacts in `paper_index/`.
- Improve naming gradually after indexing, not before.
- Re-run indexing or selective reprocessing after major naming cleanup.
- Preserve provenance: do not replace raw extracted text with silently edited text.

If an improved cleaned-text layer is added later, it should exist alongside the
raw extraction, not replace it.

## Known limitations

- PDF-to-text conversion is deterministic, but source layout can still introduce
  reading-order noise, table flattening, or header/footer residue.
- Read-only external BibTeX lookup depends on Semantic Scholar being reachable
  and returning the correct record.
- Per-record JSON files contain verbose provenance and path references, so a
  copied library root should be treated as a rebuild operation rather than a
  drop-in relocation.
