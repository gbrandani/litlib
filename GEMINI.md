# Local Literature Library

## Project Overview

This directory is a local literature library containing research papers (mostly PDFs), books, a machine-readable index, and a suite of Python scripts for library maintenance and metadata retrieval. It is designed to be accessible both for human browsing via the file system and for automated reading, searching, and citation management by scripts and LLMs.

The library maintains a canonical index and extracts plain text from PDFs to facilitate LLM ingestion and programmatic queries.

## Directory Structure

- **`articles/`**: Contains research papers, typically as PDFs, organized by topic folders. This is the primary human navigation layer.
- **`books/`**: Contains books, lecture notes, and longer reference materials, organized by topic.
- **`paper_index/`**: The machine-readable layer. This is the source of truth for all metadata.
  - `master_index.tsv`: The canonical per-file registry mapping items to metadata.
  - `master.bib`: The canonical bibliography output.
  - `journal_abbreviations.tsv`: Canonical journal-abbreviation table.
  - `records/`: Per-paper JSON provenance and matching details.
  - `text/`: Raw extracted text from PDFs (optimized for LLM reading).
  - `chunks/`: Chunked text designed for retrieval.
- **`scripts/`**: Python maintenance scripts used for adding, looking up, and managing library items (e.g., Semantic Scholar lookups, DOI cross-referencing).

## Usage & Maintenance Commands

The project uses various Python scripts located in the `scripts/` directory.

### Read-only / Lookup Commands
- **Search Library**: `python3 scripts/library_lookup.py search "query terms"`
- **Show Record Details**: `python3 scripts/library_lookup.py show <paper_id-or-bibtex_key>`
- **Generate Citation**: `python3 scripts/library_lookup.py cite <paper_id|bibtex_key|query> --style nature`
- **External Fetch (Read-only)**: `python3 scripts/library_fetch.py --query "paper title or keywords"` (Add `--download-pdf` or `--extract-text` to download/process without modifying the library).

### Interactive Maintenance Commands
- **Add PDF**: `python3 scripts/library_add.py pdf /absolute/path/to/file.pdf`
- **Add Reference-only**: `python3 scripts/library_add.py ref --query "paper title or keywords"`
- **Edit Existing Record**: `python3 scripts/library_edit.py --query "paper title or keywords"` (Recommended entrypoint for moving, deleting, merging, or updating BibTeX).

### Batch Processing
- **Batch Backfill**: `python3 scripts/library_backfill.py`
- **Audit Duplicates**: `python3 scripts/library_dedup_audit.py`
- **Sanity Check BibTeX**: `python3 scripts/library_bib_sanity_check.py`

*Note: Scripts assume Python 3 is installed, `SEMANTIC_SCHOLAR_API_KEY` is present in the environment, and `/Users/brandani/Dropbox/scripts/pdf2text.py` is available for extraction.*

## Guidelines for LLMs and Automation

1. **Metadata Authority:** Never infer metadata from file names or raw PDF embedded data. The `paper_index/master_index.tsv` and `paper_index/records/*.json` files are the absolute canonical sources of truth.
2. **Reading Papers:** When tasked with analyzing or summarizing a paper, prefer reading `paper_index/text/<paper_id>.txt` over the raw PDF.
3. **Extracted Text Caveats:** Be aware that text in `paper_index/text/` is automatically extracted and might contain artifacts like multi-column reading-order errors, header/footer residue, table noise, or flattened affiliations. Treat it as high-quality convenience data, and verify against the PDF if ordering or exact wording is crucial.
4. **Reference-Only Entries:** Some items in the index are `reference_only` (no PDF backing). Their text files contain metadata stubs, not full paper text.
5. **No Blind Modifications:** Treat the library as read-only unless explicitly asked to perform maintenance tasks. Use lookup scripts to navigate the corpus.
6. **Journal Abbreviations:** Always refer to `paper_index/journal_abbreviations.tsv` for the canonical citation-facing dotted form (e.g., `Nat. Commun.`).
