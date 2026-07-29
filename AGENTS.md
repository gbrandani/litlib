# Local Literature Library - Agent Instructions (`AGENTS.md`)

This file is the single canonical reference for AI coding assistants and automation scripts operating within this repository.

## Project Overview

`litlib` is a local, offline-first literature library containing research papers (mostly PDFs), books, a machine-readable index, a self-hosted FastAPI + React web application, a Microsoft Word Add-in, and Python scripts for library maintenance, search, and citation management.

The library maintains a canonical index and extracts plain text from PDFs to facilitate LLM ingestion and programmatic queries.

## Directory Structure

- **`articles/`**: Contains research papers as PDFs, organized by topic subdirectories. This is the primary human navigation layer.
- **`books/`**: Contains books, lecture notes, and longer reference materials.
- **`paper_index/`**: The machine-readable layer. This is the source of truth for all metadata:
  - `master_index.tsv`: The canonical per-file registry mapping items to metadata.
  - `master.bib`: The canonical bibliography output.
  - `journal_abbreviations.tsv`: Canonical journal-abbreviation table (citation-facing dotted form, e.g. `Nat. Commun.`).
  - `records/`: Per-paper JSON provenance and matching details.
  - `text/`: Raw extracted text from PDFs (optimized for LLM reading).
  - `chunks/`: Chunked text designed for retrieval.
- **`scripts/`**: Python maintenance and lookup scripts.
- **`web-app/`**: React web application and Microsoft Word Reference Manager Add-in interface.

## Core Workflows & Usage Commands

### Web Application
- **Start Web Application**: `python3 scripts/run_webapp.py`
  - Launches the FastAPI backend server over HTTP/HTTPS at `http://localhost:8000`. Serves the React frontend SPA and Word Add-in.

### Read-only / Lookup Commands
- **Search Library**: `python3 scripts/library_lookup.py search "query terms"`
- **Show Record Details**: `python3 scripts/library_lookup.py show <paper_id-or-bibtex_key>`
- **Generate Citation**: `python3 scripts/library_lookup.py cite <paper_id|bibtex_key|query> --style nature`
- **External Fetch**: `python3 scripts/library_fetch.py --query "paper title or keywords"`

### Interactive Maintenance Commands
- **Add PDF**: `python3 scripts/library_add.py pdf /absolute/path/to/file.pdf`
- **Add Reference-only**: `python3 scripts/library_add.py ref --query "paper title or keywords"`
- **Edit Existing Record**: `python3 scripts/library_edit.py --query "paper title or keywords"`
- **Refresh DOI Metadata**: `python3 scripts/library_refresh_doi_metadata.py --dry-run`

### Batch Processing & Auditing
- **Batch Backfill**: `python3 scripts/library_backfill.py`
- **Audit Duplicates**: `python3 scripts/library_dedup_audit.py`
- **Sanity Check BibTeX**: `python3 scripts/library_bib_sanity_check.py`
- **Rebuild Master BibTeX**: `python3 scripts/library_rebuild_master_bib.py`

## Guidelines for LLMs and Automation

1. **Metadata Authority:** Never infer metadata from file names or raw PDF embedded data. The `paper_index/master_index.tsv` and `paper_index/records/*.json` files are the absolute canonical sources of truth.
2. **Reading Papers:** When tasked with analyzing or summarizing a paper, prefer reading `paper_index/text/<paper_id>.txt` over the raw PDF.
3. **Extracted Text Caveats:** Be aware that text in `paper_index/text/` is automatically extracted and might contain artifacts like multi-column reading-order errors, header/footer residue, table noise, or flattened affiliations. Treat it as high-quality convenience data, and verify against the PDF if ordering or exact wording is crucial.
4. **Metadata Policy:**
   - For PDF-backed ingest, DOI/Crossref is the preferred canonical citation source when a trustworthy DOI can be extracted and validated against title similarity.
   - DOI/Crossref drives title, authors, year, venue, DOI, URL, volume, issue, pages, and publisher.
   - Reference-only entries are allowed and live in the same canonical `master_index.tsv`/`master.bib`/`records/` corpus as PDF-backed entries (`content_kind=reference_only`).
   - Do not invent metadata for unresolved files.
   - Journal abbreviations are stored canonically in citation-facing dotted form in `paper_index/journal_abbreviations.tsv`.
5. **No Blind Modifications:** Treat the library as read-only unless explicitly asked to perform maintenance tasks. Use lookup scripts to navigate the corpus.
