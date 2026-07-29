# litlib

A local, offline-first literature manager and citation controller for research papers and books. Unlike cloud-based reference managers, `litlib` keeps all PDFs, plain text extractions, index metadata, and bibliography entries locally on your machine, exposing them via a sleek self-hosted web application and a native Microsoft Word referencing add-in with zero cloud dependencies.

## Key Features

- **Local Web Application**: Browse papers, search by title/author/venue/DOI/abstract, view PDFs inline, manage notes, and run maintenance audits via a self-hosted web interface.
- **Microsoft Word Reference Manager**: Insert citations, auto-generate dynamic bibliographies, and switch CSL styles (Nature, IEEE, ACS, NLM, NAR) in real-time inside Microsoft Word on macOS.
- **Offline-First Index**: Machine-readable `master_index.tsv` and `master.bib` local databases with per-paper JSON provenance records and extracted text stubs.
- **Automatic Ingestion & DOI Resolution**: Automatically extracts DOIs from PDF text, queries Crossref, and validates metadata against title similarity.
- **Batch Operations**: Bulk auto-ingest PDF directories and import BibTeX reference lists in one click.
- **Parent-Child Supplementary Materials**: Link appendixes and supplementary data tables directly to primary articles.
- **Metadata Maintenance & DOI Refresh**: Audit and update library metadata from authoritative Crossref DOI sources safely with dry-run support.

---

## Directory Structure

- `articles/`: Research papers as PDFs, organized by topic subdirectories (the primary human navigation layer).
- `books/`: Books, lecture notes, and longer reference materials.
- `paper_index/`: Machine-readable local index and derived artifacts:
  - `master_index.tsv`: Canonical per-file registry mapping items to metadata.
  - `master.bib`: Canonical bibliography output.
  - `journal_abbreviations.tsv`: Canonical citation-facing journal abbreviation table (`Nat. Commun.`, `J. Mol. Biol.`, etc.).
  - `records/`: Per-paper JSON provenance and matching details.
  - `text/`: Raw extracted text from PDFs (optimized for LLM reading).
  - `chunks/`: Chunked text for retrieval.
- `scripts/`: Python maintenance and lookup scripts.
- `web-app/`: React web application and Word Add-in task pane interface.

---

## Starting the Web Application

To launch the web application and backend API server from your terminal, run:

```bash
python3 scripts/run_webapp.py
```

* **HTTP Access**: Open [http://localhost:8000](http://localhost:8000) in your web browser.
* **HTTPS (SSL) Access**: If `key.pem` and `cert.pem` are present in the project root, HTTPS is enabled automatically at `https://localhost:8000` (required for Word Add-in iframe integration).

> **Note:** Do not execute `python3 scripts/webapp_api.py` directly; `run_webapp.py` initializes the Uvicorn web server with static asset routing and SSL configuration.

---

## Microsoft Word Reference Manager Integration

`litlib` includes a native reference manager add-in for Microsoft Word on macOS:

1. **Sideload the Add-in Manifest**:
   ```bash
   python3 scripts/sideload_word_plugin.py
   ```
2. **Start the Backend Server**:
   ```bash
   python3 scripts/run_webapp.py
   ```
3. **Open in Word**:
   - Open Word on macOS, create or open a document.
   - Go to **Insert > Add-ins > My Add-ins**.
   - Under **Developer Add-ins**, select **Local Literature Library** and click **Add**.
   - Use the **Literature Library** ribbon button to open the Task Pane. Search for papers, check items, insert citations, and generate dynamic bibliographies in your chosen CSL style.

For full architecture details and setup troubleshooting, see [WORD_INTEGRATION.md](file:///Users/brandani/Dropbox/documents/library/WORD_INTEGRATION.md).

---

## Adding New Papers & References

### Web App Ingestion Wizard (Recommended)
1. Open the Web App (`http://localhost:8000`) and navigate to **Upload PDF** or **Untracked PDFs**.
2. Select a PDF file from your topic subdirectories.
3. The wizard extracts the text, identifies the DOI, and resolves authoritative Crossref metadata automatically.
4. Preview and confirm the metadata to finalize ingestion. If no DOI is found, you can manually paste a BibTeX entry.

### CLI PDF Addition
```bash
python3 scripts/library_add.py pdf /path/to/paper.pdf
```

### CLI Reference-Only Addition (No PDF)
```bash
python3 scripts/library_add.py ref --query "Paper Title or DOI"
```

### Batch Ingestion (Directory & BibTeX List)
- Use the **Batch Operations** view under Maintenance in the Web App to scan an entire folder under `articles/` or paste a list of multiple BibTeX entries.

---

## CLI Maintenance & Lookup Commands

### Lookup & Search
- **Search Library**: `python3 scripts/library_lookup.py search "query terms"`
- **Show Record Details**: `python3 scripts/library_lookup.py show <paper_id-or-bibtex_key>`
- **Generate Citation**: `python3 scripts/library_lookup.py cite <paper_id|bibtex_key|query> --style nature`
- **External Fetch**: `python3 scripts/library_fetch.py --query "paper title or keywords"`

### Interactive Maintenance
- **Edit Existing Record**: `python3 scripts/library_edit.py --query "paper title or keywords"`
- **Refresh DOI Metadata**: `python3 scripts/library_refresh_doi_metadata.py --dry-run`
- **Apply DOI Metadata Upgrades**: `python3 scripts/library_refresh_doi_metadata.py --apply`

### Database Auditing & Maintenance
- **Sanity Check BibTeX**: `python3 scripts/library_bib_sanity_check.py`
- **Audit Duplicates**: `python3 scripts/library_dedup_audit.py`
- **Rebuild Master BibTeX**: `python3 scripts/library_rebuild_master_bib.py`

---

## Agent & Automation Guidelines

For AI coding assistants, scripts, and LLM automation tools, refer to [AGENTS.md](file:///Users/brandani/Dropbox/documents/library/AGENTS.md) for strict guidelines on canonical metadata sources, read-only guarantees, journal abbreviation rules, and text extraction caveats.
