# CODEX

This directory is a local literature library.

## What is canonical

- Human browsing layer:
  - `articles/`
  - `books/`
- Machine-readable layer:
  - `paper_index/master_index.tsv`
  - `paper_index/master.bib`
  - `paper_index/records/*.json`
  - `paper_index/text/*.txt`
  - `paper_index/chunks/*.jsonl`

When working in this library, prefer the `paper_index/` metadata over inferring
facts from filenames or PDF embedded metadata.

The article index is currently complete, so new sessions should assume the
library is ready for read-only use by other projects unless the user explicitly
asks for maintenance.

## Text caveat

`paper_index/text/*.txt` is usually suitable for LLM ingestion, but may contain:

- multi-column reading-order issues
- flattened author and affiliation blocks
- page header/footer residue
- table and supplement noise
- publisher layout artifacts

If exact wording or local ordering matters, check the original PDF.

## Expected workflow

- For cross-project use: treat this library as read-only and consume
  `master_index.tsv`, `text/`, `chunks/`, `master.bib`, and
  `paper_index/journal_abbreviations.tsv`
- For cross-project search, lookup, and one-off citation rendering: prefer
  `scripts/library_lookup.py`
- For read-only external Semantic Scholar lookup, optional PDF download, and
  optional text extraction: use `scripts/library_fetch.py`
- Downloaded/extracted project-local artifacts from `library_fetch.py` are
  named from the selected BibTeX key, with a compact venue suffix when present,
  so they can be matched back to local `.bib` entries without a local index
- Keep `scripts/library_fetch_bib.py` only for bib-only compatibility
- Prefer style-family names at the CLI boundary: `nature`, `nlm`, `acs`, `ieee`
  rather than journal-specific names
- For safe duplicate review: use `scripts/library_dedup_audit.py` before making
  any manual consolidation changes
- For actual interactive record maintenance after review: use
  `scripts/library_edit.py`
- Keep `scripts/library_manage.py` for lower-level scripted move/delete/dedup
  operations and rely on its dry-run default before `--apply`
- For one new PDF or one new reference: use `scripts/library_add.py`
- For one PDF in deterministic non-interactive mode: use `scripts/library_ingest.py`
- For many files: use `scripts/library_backfill.py`
- For resolved-file renaming: use `scripts/library_rename_from_index.py`
- For project-local BibTeX export: use `scripts/library_export_bib.py`
- For venue capitalization cleanup: use `scripts/library_normalize_journals.py`
- For BibTeX integrity checks: use `scripts/library_bib_sanity_check.py`
- For canonical BibTeX recovery or cleanup: use `scripts/library_rebuild_master_bib.py`
- For manual metadata edits after changing one record JSON: use `scripts/library_refresh_record.py`
- For simple one-record BibTeX replacement: use `scripts/library_edit_bib.py`
- In multiline add/edit prompts, the canonical completion gesture is a final
  line containing only `END`
- Maintenance scripts assume `SEMANTIC_SCHOLAR_API_KEY` and
  `/Users/brandani/Dropbox/scripts/pdf2text.py`

## Metadata policy

- For PDF-backed ingest, DOI/Crossref is the preferred canonical citation
  source when a trustworthy DOI can be extracted and validated against the
  inferred title.
- Semantic Scholar remains the default discovery and enrichment source.
- If both are available, DOI/Crossref should drive title, authors, year,
  venue, DOI, URL, volume, issue, and pages, while Semantic Scholar may still
  contribute abstract, paper ID, and open-access hints.
- Manual verification is acceptable when deterministic Semantic Scholar matching is insufficient.
- Reference-only entries are allowed and should live in the same canonical
  `master_index.tsv`/`master.bib`/`records/` corpus as PDF-backed entries.
- Do not invent metadata for unresolved files.
- Supplements do not require standalone DOI resolution; they may be linked to a
  parent paper.
- Journal abbreviations are stored canonically in citation-facing dotted form.
- Compact filename tokens are derived from the citation abbreviation by removing
  spaces and periods, except for explicit overrides such as `PNAS`, `NAR`,
  `PRL`, `PRE`, and `PRX`.

## Maintenance policy

- Keep the folder hierarchy useful for human browsing.
- Keep generated metadata and text artifacts in `paper_index/`.
- Improve naming gradually after indexing.
- For cleanup of older Semantic-Scholar-derived records, use
  `scripts/library_reconcile_metadata.py` in dry-run mode first, then apply
  only reviewed DOI/Crossref upgrades.
- Treat `job_type=enrich_existing_entry` as batchable after review. Treat
  `job_type=rewrite_entry` as a targeted correction that needs
  `--apply-rewrites`.
- Prefer conservative automation with provenance over heuristic guessing.
- Treat `paper_index/search_index.jsonl` as a disposable derived cache that can
  be regenerated from `master_index.tsv` and `records/`.
