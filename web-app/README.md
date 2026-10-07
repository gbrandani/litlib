# Literature Library Web Application & Word Add-in

This directory contains the React single-page application (SPA) and Microsoft Word Add-in interface for `litlib`. The frontend is served directly by the FastAPI backend server.

---

## 🚀 Quick Start: Running the Web App

To launch the web application backend and frontend together from your terminal:

```bash
# Run from the root repository directory:
python3 scripts/run_webapp.py
```

* **HTTP Access**: Open [http://localhost:8000](http://localhost:8000) in your web browser.
* **HTTPS Access**: If SSL certificates (`cert.pem` and `key.pem`) exist in the project root, the app runs at `https://localhost:8000` (required for Word Add-in iframe integration).

> **Note:** Run `scripts/run_webapp.py` rather than executing `webapp_api.py` directly; `run_webapp.py` configures static asset mounting for `web-app/dist` and handles Uvicorn SSL options.

---

## 🛠️ Frontend Development & Building

The frontend is built with **React** and **Vite**.

### Prerequisites
Node.js and npm installed on your machine.

### Building for Production
When you edit code in `web-app/src/`, recompile the static bundle for the backend:

```bash
cd web-app
npm run build
```

This outputs production assets into `web-app/dist/`, which FastAPI serves at `http://localhost:8000`.

### Development Mode (with Hot Module Replacement)
To develop with live hot-reloading:

```bash
cd web-app
npm run dev
```

* Vite dev server runs at `http://localhost:5173`.
* Ensure the backend API is running on port 8000 (`python3 scripts/run_webapp.py`).

---

## 🌟 Key Application Features

1. **Library Navigation & Search**:
   - Search across title, author, venue, DOI, abstract, and extracted PDF text.
   - Filter by item type (All, PDFs, Reference-Only), author, venue, year, or DOI.

2. **Streamlined Ingestion Wizard**:
   - **Step 1 (Select File)**: Pick untracked PDFs from `articles/` subdirectories.
   - **Step 2 (DOI Resolution)**: Automatic DOI extraction, Crossref metadata resolution, and title similarity verification.
   - **Step 3 (Manual BibTeX Paste)**: Paste or tweak BibTeX entries directly when no DOI is found.
   - **Add Reference-Only Entry**: Add bibtex entries without attaching a PDF.

3. **Paper Inspector & PDF Viewer**:
   - Inline PDF view within the application.
   - Directly edit BibTeX metadata, notes, and provenance.
   - Download citations in multiple CSL styles (Nature, IEEE, ACS, NLM, etc.).

4. **Batch Operations**:
   - Auto-ingest an entire folder under `articles/` in one click.
   - Bulk-import a collection of BibTeX entries.

5. **Supplementary Materials & Linking**:
   - Link appendixes or data tables as supplementary files attached to primary papers.

6. **Maintenance & Deduplication**:
   - Audit duplicates and merge duplicate records cleanly.
   - Refresh DOI metadata across records.

7. **Microsoft Word Add-in Interface**:
   - Served at `/word.html` (`web-app/dist/word.html`).
   - Integrates directly with Microsoft Word via task pane iframe. See root [WORD_INTEGRATION.md](../WORD_INTEGRATION.md) for sideloading instructions.

---

## 📁 Directory Layout

```
web-app/
├── src/
│   ├── App.jsx             # Main React application & modal components
│   ├── WordAddin.jsx       # Microsoft Word reference manager task pane UI
│   ├── main.jsx            # Entry point for main Web App SPA
│   ├── word.jsx            # Entry point for Word Add-in UI
│   └── index.css           # Slate-light theme styles and UI design variables
├── public/                 # Static public assets
├── index.html              # HTML shell for Web App
├── word.html               # HTML shell for Word Add-in task pane
├── vite.config.js          # Vite configuration & multi-page build routes
└── dist/                   # Compiled production bundle (served by FastAPI)
```
