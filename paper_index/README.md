# Local literature registry

This directory is the machine-readable index for the library.

The visible library layout under `articles/` and `books/` remains the human
navigation layer. The files in `paper_index/` are the canonical automation
layer used for text extraction, chunking, and bibliography management.

## Layout

- `master_index.tsv`: one canonical row per paper/reference
- `master.bib`: BibTeX entries for both `pdf_backed` and `reference_only` records
- `journal_abbreviations.tsv`: canonical journal-abbreviation table for citation export and filename derivation
- `search_index.jsonl`: derived metadata/abstract search cache used by `scripts/library_lookup.py`
- `records/<paper_id>.json`: full ingest record with candidate scores and errors
- `text/<paper_id>.txt`: either plain-text extraction from `pdf2text.py` or a metadata stub for a `reference_only` entry
- `chunks/<paper_id>.jsonl`: chunked text or chunked metadata stub for retrieval
- `logs/`: reserved for future batch/backfill logs

## Current policy

- Query Semantic Scholar from the filename-derived title first.
- Treat Semantic Scholar as the preferred authority for article metadata.
- If Semantic Scholar only returns a preprint-like record but the PDF text exposes a
  publisher DOI, use DOI-based Crossref metadata as a deterministic fallback for the
  BibTeX/journal record and keep the Semantic Scholar linkage for auditability.
- Do not invent metadata when the match is ambiguous.
- Supplements may be linked to a parent paper instead of being treated as fully
  standalone publication records.
- Keep unresolved records in `master_index.tsv` with `match_status` such as
  `needs_manual_review`, `not_found`, or `api_error`.
- Keep generated artifacts separate from the original PDFs.
- Store journal abbreviations canonically in a citation-facing dotted form and
  derive compact filename tokens from that form unless an explicit override is needed.
- Allow `reference_only` records when a citation should exist in the library even
  though no local PDF is attached yet.
- Keep read-only lookup derived artifacts under `paper_index/` rather than in
  project-specific caches.

## Current state

- The article registry is currently fully resolved.
- There are no remaining article rows with `needs_manual_review`, `not_found`, or `api_error`.
- Some rows are `manual_verified` because they required a deterministic manual correction.
- Some supplement PDFs are linked to parent papers with `matched_supplement`.

## Text quality warning

The extracted text in `text/` is intended for LLM ingestion and retrieval, but it
is not guaranteed to preserve PDF layout perfectly.

Known failure modes include:

- disrupted reading order in multi-column PDFs
- flattened author and affiliation blocks
- figure, table, and supplement noise
- page header/footer residue
- occasional publisher-specific formatting artifacts

In other words, the text layer is usually very useful, but not infallible.
If exact wording, local sentence order, or figure-adjacent interpretation matters,
the original PDF should remain the final reference.

For `reference_only` rows, the text layer is intentionally just a metadata stub.

## Read-only interface

The preferred read-only entry point for humans and other local agent sessions is
`scripts/library_lookup.py`.

- `search`: metadata-first lexical search over titles, authors, venues, filenames,
  and abstracts, with optional explicit full-text search
- `show`: inspect one canonical record by `paper_id` or `bibtex_key`
- `cite`: render one bibliography entry from `master.bib` using vendored CSL
  styles and `pandoc --citeproc`

The citation renderer augments temporary one-entry BibTeX payloads with
canonical journal abbreviations from `journal_abbreviations.tsv` so citation
output can use abbreviated journal names without modifying `master.bib`.

## Current scope

The first implementation is optimized for article PDFs. Books can still be
registered, but their metadata resolution is intentionally conservative and
will usually require later review or a separate resolver.
