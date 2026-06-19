#!/usr/bin/env python3
"""FastAPI Backend API for local Literature Library web application."""

from fastapi import FastAPI, HTTPException, status, File, UploadFile, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import sys
from pathlib import Path
import shutil
import os
import json
import datetime as dt

SCRIPT_DIR = Path(__file__).resolve().parent
LIBRARY_ROOT = SCRIPT_DIR.parent
NOTES_DIR = LIBRARY_ROOT / "notes"
FRONTEND_DIST = LIBRARY_ROOT / "web-app" / "dist"

sys.path.append(str(SCRIPT_DIR))
import library_ops as ops
import library_ingest as ingest
import library_lookup as lookup
import library_add as add_lib

app = FastAPI(title="Literature Library Web App")

# Enable CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def ensure_notes_dir():
    NOTES_DIR.mkdir(parents=True, exist_ok=True)


class BibtexUpdate(BaseModel):
    raw_bibtex: str


class MergeRequest(BaseModel):
    keep_paper_id: str
    drop_paper_id: str
    delete_drop_pdf: bool = False


class MoveRequest(BaseModel):
    destination: str


class PDFConfirmRequest(BaseModel):
    temp_filename: str
    folder: str
    title_query: str
    selected_candidate: dict | None = None
    manual_bibtex: str | None = None


class RefAddRequest(BaseModel):
    query: str
    selected_candidate: dict | None = None
    manual_bibtex: str | None = None


class NoteSaveRequest(BaseModel):
    content: str


class IngestExistingRequest(BaseModel):
    relative_path: str
    selected_candidate: dict | None = None
    manual_bibtex: str | None = None


class BatchImportDirectoryRequest(BaseModel):
    directory_path: str
    recursive: bool = False


class BatchImportBibtexRequest(BaseModel):
    raw_bibtex: str


class ResolveUntrackedDuplicateRequest(BaseModel):
    existing_paper_id: str
    relative_path: str
    action: str


class LinkSupplementRequest(BaseModel):
    parent_paper_id: str


class IngestAsSupplementRequest(BaseModel):
    relative_path: str
    parent_paper_id: str


class BibtexPreviewRequest(BaseModel):
    selected_candidate: dict


@app.get("/api/papers")
async def list_papers():
    try:
        rows = ingest.load_master_index_rows()
        return rows
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/papers/search")
async def search_papers(
    q: str = "",
    author: str = "",
    year: str = "",
    venue: str = "",
    doi: str = "",
    has_pdf: bool = False,
    reference_only: bool = False,
    scope: str = "auto",
    limit: int = 1000,
):
    try:
        results = ops.search_results(
            q,
            author=author,
            year=year,
            venue=venue,
            doi=doi,
            has_pdf=has_pdf,
            reference_only=reference_only,
            scope=scope,
            limit=limit,
        )
        # Convert search results to a clean response format
        output = [lookup.search_output_record(r) for r in results]
        return output
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/papers/untracked")
async def list_untracked_papers():
    try:
        rows = ingest.load_master_index_rows()
        indexed_pdfs = set()
        for r in rows:
            pdf_path = r.get("pdf_path")
            if pdf_path:
                indexed_pdfs.add(Path(pdf_path).as_posix().lower())
        
        articles_dir = LIBRARY_ROOT / "articles"
        untracked = []
        if articles_dir.exists():
            for root, dirs, files in os.walk(articles_dir):
                if "temp_uploads" in dirs:
                    dirs.remove("temp_uploads")
                for file in files:
                    if file.lower().endswith(".pdf") and not file.startswith("."):
                        full_path = Path(root) / file
                        rel_path = ingest.relative_to_library(full_path)
                        if rel_path.lower() not in indexed_pdfs:
                            untracked.append({
                                "filename": file,
                                "relative_path": rel_path,
                                "folder": str(Path(root).relative_to(articles_dir))
                            })
        return untracked
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/papers/duplicates")
async def list_duplicates():
    try:
        import library_dedup_audit as dedup
        rows = dedup.load_rows()
        report = dedup.build_report(rows)
        # Map fields for frontend compatibility
        report["duplicate_components"] = report.get("duplicate_groups", [])
        report["component_reports"] = report.get("duplicate_groups", [])
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/papers/broken")
async def list_broken_papers():
    try:
        rows = ingest.load_master_index_rows()
        broken = []
        for r in rows:
            issues = []
            title = r.get("resolved_title") or r.get("title_query") or ""
            authors = r.get("authors") or ""
            year = r.get("year") or ""
            venue = r.get("venue") or ""
            doi = r.get("doi") or ""
            bibtex_key = r.get("bibtex_key") or ""
            
            if not title.strip() or title.lower() in {"none", "n/a", "null"}:
                issues.append("Missing title")
            if not authors.strip() or authors.lower() in {"none", "n/a", "null", "author"}:
                issues.append("Missing or generic authors")
            if not year.strip() or not year.isdigit() or year.lower() in {"none", "n/a", "null"}:
                issues.append("Missing or invalid year")
            if not venue.strip() or venue.lower() in {"none", "n/a", "null"}:
                issues.append("Missing journal/venue")
            if not bibtex_key.strip():
                issues.append("Missing BibTeX key")
                
            if issues:
                broken.append({
                    "paper_id": r.get("paper_id"),
                    "title": title or r.get("title_query") or "Untitled",
                    "authors": authors,
                    "year": year,
                    "venue": venue,
                    "doi": doi,
                    "bibtex_key": bibtex_key,
                    "issues": issues,
                    "pdf_path": r.get("pdf_path"),
                    "content_kind": r.get("content_kind")
                })
        return broken
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/papers/scan-pdf-metadata")
async def scan_pdf_metadata(path: str, exclude_paper_id: str = None):
    abs_path = LIBRARY_ROOT / path.strip("/")
    if not abs_path.exists():
        raise HTTPException(status_code=400, detail=f"File not found: {path}")
        
    try:
        title_query = ingest.derive_title_query(abs_path)
        text_status, text_error, text_content = ingest.extract_pdf_text(abs_path)
        
        sha256 = ingest.sha256_path(abs_path)
        existing_by_sha = ingest.load_master_index_row_by_pdf_sha256(sha256)
        
        doi_metadata = None
        doi_score = None
        doi_found = ""
        doi_bibtex = ""
        
        if text_status == "ok" and text_content:
            filename_hints = ingest.derive_filename_hints(abs_path)
            title_targets = [title_query] + ingest.derive_text_title_candidates(text_content)
            doi_res = ingest.resolve_pdf_doi_metadata(
                pdf_path=abs_path,
                extracted_text=text_content,
                filename_hints=filename_hints,
                title_targets=title_targets
            )
            if doi_res.get("best_metadata"):
                doi_metadata = doi_res["best_metadata"]
                doi_score = doi_res.get("best_score")
                doi_found = doi_res.get("best_candidate_doi")
                doi_bibtex = make_doi_bibtex(doi_metadata)
                
        if existing_by_sha and exclude_paper_id and existing_by_sha.get("paper_id") == exclude_paper_id:
            existing_by_sha = None

        # Check for duplicates by sha256 or DOI/title/year reference match
        matched_row = None
        if existing_by_sha and existing_by_sha.get("content_kind") == ingest.CONTENT_KIND_PDF:
            matched_row = existing_by_sha
        else:
            doi_to_match = doi_found or (doi_metadata.get("DOI", "") if doi_metadata else "")
            title_to_match = ""
            year_to_match = ""
            if doi_metadata:
                title_to_match = doi_metadata.get("title", "")
                if isinstance(title_to_match, list):
                    title_to_match = title_to_match[0] if title_to_match else ""
                
                issued = doi_metadata.get("issued", {})
                date_parts = issued.get("date-parts", [[None]])
                if date_parts and date_parts[0] and date_parts[0][0]:
                    year_to_match = str(date_parts[0][0])
            
            if not title_to_match:
                title_to_match = title_query
                
            cand_row = ingest.find_existing_reference_match(
                doi=doi_to_match,
                title=title_to_match,
                year=year_to_match
            )
            if cand_row and exclude_paper_id and cand_row.get("paper_id") == exclude_paper_id:
                cand_row = None
            if cand_row and cand_row.get("content_kind") == ingest.CONTENT_KIND_PDF:
                matched_row = cand_row

        if matched_row:
            # Self-healing: if the matched row is missing title or sha256 in the index, refresh it!
            if not matched_row.get("resolved_title") or not matched_row.get("pdf_sha256"):
                try:
                    refreshed = ops.refresh_record_from_row(matched_row)
                    matched_row = refreshed["row"]
                except Exception:
                    pass

        return {
            "title_query": title_query,
            "sha256": sha256,
            "is_duplicate": matched_row is not None,
            "existing_paper_id": matched_row.get("paper_id") if matched_row else None,
            "existing_title": (matched_row.get("resolved_title") or matched_row.get("title_query") or "Untitled") if matched_row else None,
            "existing_pdf_path": matched_row.get("pdf_path") if matched_row else None,
            "doi_found": doi_found,
            "doi_metadata": doi_metadata,
            "doi_score": doi_score,
            "doi_bibtex": doi_bibtex,
            "text_extracted": bool(text_content)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/papers/{paper_id}")
async def get_paper_details(paper_id: str):
    try:
        row = ops.resolve_target_row(paper_id=paper_id)
        record = ops.load_record_payload(paper_id)
        # Fetch actual bibtex key and raw entry if exists
        bib_entries = lookup.load_master_bib_entries()
        bibtex_key = row.get("bibtex_key")
        raw_bibtex = ""
        if bibtex_key and bibtex_key in bib_entries:
            raw_bibtex = bib_entries[bibtex_key].get("raw_bibtex", "")
        
        detail = lookup.show_record(row)
        detail["raw_bibtex"] = raw_bibtex
        detail["record_payload"] = record
        return detail
    except SystemExit as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/papers/{paper_id}/cite")
async def get_citation(paper_id: str, style: str = "nature"):
    try:
        row = ops.resolve_target_row(paper_id=paper_id)
        bib_entries = lookup.load_master_bib_entries()
        bibtex_key = row.get("bibtex_key")
        if not bibtex_key or bibtex_key not in bib_entries:
            raise HTTPException(status_code=404, detail="BibTeX entry not found for paper")
        
        parsed = bib_entries[bibtex_key]
        style_name, style_info = lookup.resolve_style_alias(style)
        abbrev_map = lookup.load_journal_abbreviations()
        citation = lookup.render_citation(parsed, style_name, style_info, abbrev_map)
        return {"citation": citation, "style": style_name}
    except SystemExit as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CitationItem(BaseModel):
    id: str
    paper_ids: list[str]


class FormatCitationsRequest(BaseModel):
    style: str
    citations: list[CitationItem]


@app.post("/api/citations/format")
async def format_citations(request: FormatCitationsRequest):
    try:
        import tempfile
        import subprocess
        import re

        style_name = request.style
        try:
            style_canonical, style_info = lookup.resolve_style_alias(style_name)
        except SystemExit as e:
            raise HTTPException(status_code=400, detail=str(e))

        # Load master index to map paper_id to bibtex_key
        rows = ingest.load_master_index_rows()
        paper_to_bibkey = {}
        for r in rows:
            p_id = r.get("paper_id")
            b_key = r.get("bibtex_key")
            if p_id and b_key:
                paper_to_bibkey[p_id] = b_key

        # Resolve keys and collect all referenced keys for generating the temp bibliography
        bib_entries = lookup.load_master_bib_entries()
        abbrev_map = lookup.load_journal_abbreviations()
        
        referenced_keys = set()
        citations_with_keys = []
        for cit in request.citations:
            keys = []
            for pid in cit.paper_ids:
                key = paper_to_bibkey.get(pid)
                if key and key in bib_entries:
                    keys.append(key)
                    referenced_keys.add(key)
            citations_with_keys.append((cit.id, keys))

        # If there are no citations, return empty
        if not request.citations:
            return {"citations": [], "bibliography": []}

        # Generate a temporary directory to run pandoc
        with tempfile.TemporaryDirectory(prefix="library_cite_") as tmpdir_text:
            tmpdir = Path(tmpdir_text)
            bib_path = tmpdir / "entry.bib"
            md_path = tmpdir / "entry.md"
            
            # Write only the cited/referenced bib entries to our temp bib file
            bib_content = ""
            for k in sorted(referenced_keys):
                parsed = bib_entries[k]
                bib_content += lookup.entry_text_with_short_journal(parsed, abbrev_map) + "\n\n"
            bib_path.write_text(bib_content, encoding="utf-8")
            
            # Write markdown file
            md_content = "---\n\n---\n\n"
            for cit_id, keys in citations_with_keys:
                if keys:
                    cite_str = "; ".join(f"@{k}" for k in keys)
                    md_content += f"[START-CITE:{cit_id}][{cite_str}][END-CITE:{cit_id}]\n\n"
                else:
                    md_content += f"[START-CITE:{cit_id}][][END-CITE:{cit_id}]\n\n"
                    
            md_path.write_text(md_content, encoding="utf-8")
            
            cmd = [
                "pandoc",
                str(md_path),
                "--from",
                "markdown",
                "--to",
                "plain",
                "--wrap=none",
                "--citeproc",
                "--bibliography",
                str(bib_path),
                "--csl",
                str(style_info["csl_path"]),
            ]
            
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                raise HTTPException(status_code=500, detail=f"Pandoc error: {proc.stderr}")
                
            stdout = proc.stdout
            
            # Parse in-text citations
            cit_matches = re.findall(r"\[START-CITE:([^\]]+)\](.*?)\[END-CITE:\1\]", stdout, re.DOTALL)
            parsed_cites = {}
            for cid, txt in cit_matches:
                parsed_cites[cid] = txt.strip()
                
            # For any citation IDs that were not found in the output, default to empty
            res_citations = []
            for cit in request.citations:
                res_citations.append({
                    "id": cit.id,
                    "text": parsed_cites.get(cit.id, "")
                })
                
            # Extract bibliography
            bibliography_items = []
            last_end_tag = "[END-CITE:"
            last_idx = stdout.rfind(last_end_tag)
            if last_idx != -1:
                close_idx = stdout.find("]", last_idx)
                if close_idx != -1:
                    bib_section = stdout[close_idx + 1:].strip()
                    # Split by blank lines
                    items = [item.strip() for item in re.split(r'\n\s*\n', bib_section) if item.strip()]
                    bibliography_items = items
            
            return {
                "citations": res_citations,
                "bibliography": bibliography_items
            }
            
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/papers/{paper_id}")
async def update_paper_bibtex(paper_id: str, body: BibtexUpdate):
    try:
        row = ops.resolve_target_row(paper_id=paper_id)
        res = ops.replace_bibtex_for_row(row, body.raw_bibtex, allow_lossy=True)
        return {"status": "success", "data": res}
    except SystemExit as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/papers/{paper_id}")
async def delete_paper(paper_id: str, delete_pdf: bool = False):
    try:
        row = ops.resolve_target_row(paper_id=paper_id)
        res = ops.delete_record(row, apply=True, delete_pdf=delete_pdf)
        return {"status": "success", "data": res}
    except SystemExit as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/papers/merge")
async def merge_papers(body: MergeRequest):
    try:
        keep_row = ops.resolve_target_row(paper_id=body.keep_paper_id)
        drop_row = ops.resolve_target_row(paper_id=body.drop_paper_id)
        res = ops.dedup_records(keep_row, drop_row, apply=True, delete_drop_pdf=body.delete_drop_pdf, force=True)
        return {"status": "success", "data": res}
    except SystemExit as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/papers/{paper_id}/move")
async def move_paper(paper_id: str, body: MoveRequest):
    try:
        row = ops.resolve_target_row(paper_id=paper_id)
        dest_path = ops.resolve_destination_path(body.destination, row.get("filename", ""))
        res = ops.move_record(row, dest_path, apply=True)
        return {"status": "success", "data": res}
    except SystemExit as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pdf/{paper_id}")
@app.get("/api/pdf/{paper_id}/{filename}")
async def get_pdf(paper_id: str, filename: str = None):
    try:
        row = ops.resolve_target_row(paper_id=paper_id)
        pdf_path = row.get("pdf_path")
        if not pdf_path:
            raise HTTPException(status_code=404, detail="Paper has no PDF attached")
        
        abs_path = LIBRARY_ROOT / pdf_path
        if not abs_path.exists():
            raise HTTPException(status_code=404, detail=f"PDF file not found on disk: {pdf_path}")
        
        return FileResponse(abs_path, media_type="application/pdf", headers={"Content-Disposition": "inline"})
    except SystemExit as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/text/{paper_id}")
@app.get("/api/text/{paper_id}/{filename}")
async def get_text(paper_id: str, filename: str = None):
    try:
        row = ops.resolve_target_row(paper_id=paper_id)
        text_path = row.get("text_path")
        if not text_path:
            raise HTTPException(status_code=404, detail="Paper has no extracted text")
        
        abs_path = LIBRARY_ROOT / text_path
        if not abs_path.exists():
            pdf_path = LIBRARY_ROOT / row.get("pdf_path", "")
            if pdf_path.exists():
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                ingest.run_pdf2text(pdf_path, abs_path)
            else:
                raise HTTPException(status_code=404, detail="Extracted text not found and original PDF is missing")
        
        return FileResponse(abs_path, media_type="text/plain", headers={"Content-Disposition": "inline"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/folders")
async def list_folders():
    articles_dir = LIBRARY_ROOT / "articles"
    folders = []
    if articles_dir.exists():
        for path in articles_dir.glob("**/"):
            try:
                rel = path.relative_to(articles_dir)
                if str(rel) != "." and not str(rel).startswith("."):
                    folders.append(str(rel))
            except ValueError:
                pass
    return sorted(folders)


@app.get("/api/candidates")
async def search_candidates(q: str = ""):
    if not q.strip():
        return []
    try:
        res = ingest.semantic_scholar_search(q.strip(), limit=5)
        return res.get("data") or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def semantic_candidate_to_bibtex(candidate: dict) -> str:
    metadata = ingest.semantic_candidate_to_metadata(candidate)
    
    author_name = "author"
    authors_str = metadata.get("authors", "")
    if authors_str:
        first_author = authors_str.split(",")[0].strip()
        if " " in first_author:
            author_name = first_author.split(" ")[-1]
        else:
            author_name = first_author
            
    author_token = ingest.preferred_title_token(author_name)
    year_token = metadata.get("year", "undated")
    title_token = ingest.preferred_title_token(metadata.get("title", "title"))
    bibtex_key = f"{author_token}{year_token}{title_token}"
    
    fields = {
        "title": metadata.get("title", ""),
        "author": " and ".join(a.strip() for a in authors_str.split(",") if a.strip()),
        "year": metadata.get("year", ""),
        "doi": metadata.get("doi", ""),
        "url": metadata.get("url", ""),
    }
    if metadata.get("venue"):
        fields["journal"] = metadata.get("venue")
        
    return ingest.build_bibtex_entry_from_fields("article", bibtex_key, fields)


def make_doi_bibtex(doi_metadata: dict) -> str:
    author_token = "author"
    if doi_metadata.get("author"):
        authors_list = doi_metadata.get("author")
        if isinstance(authors_list, list) and authors_list:
            first = authors_list[0]
            if isinstance(first, dict):
                author_token = first.get("family") or first.get("given") or "author"
    author_token = ingest.preferred_title_token(author_token)
    year_token = ingest.crossref_year(doi_metadata) or "undated"
    title_token = ingest.preferred_title_token(ingest.crossref_title(doi_metadata))
    bibtex_key = f"{author_token}{year_token}{title_token}"
    return ingest.build_crossref_bibtex_entry(bibtex_key, doi_metadata)


@app.post("/api/candidates/bibtex")
async def get_candidate_bibtex(body: BibtexPreviewRequest):
    try:
        bibtex = semantic_candidate_to_bibtex(body.selected_candidate)
        return {"bibtex": bibtex}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/papers/upload-scan")
async def upload_scan(file: UploadFile = File(...)):
    temp_dir = LIBRARY_ROOT / "articles" / "temp_uploads"
    temp_dir.mkdir(parents=True, exist_ok=True)
    safe_filename = Path(file.filename).name
    temp_path = temp_dir / safe_filename
    
    try:
        with temp_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        title_query = ingest.derive_title_query(temp_path)
        sha256 = ingest.sha256_path(temp_path)
        existing_by_sha = ingest.load_master_index_row_by_pdf_sha256(sha256)
        
        # Extract DOI first
        text_status, text_error, text_content = ingest.extract_pdf_text(temp_path)
        doi_metadata = None
        doi_score = None
        doi_found = ""
        doi_bibtex = ""
        
        if text_status == "ok" and text_content:
            filename_hints = ingest.derive_filename_hints(temp_path)
            title_targets = [title_query] + ingest.derive_text_title_candidates(text_content)
            doi_res = ingest.resolve_pdf_doi_metadata(
                pdf_path=temp_path,
                extracted_text=text_content,
                filename_hints=filename_hints,
                title_targets=title_targets
            )
            if doi_res.get("best_metadata"):
                doi_metadata = doi_res["best_metadata"]
                doi_score = doi_res.get("best_score")
                doi_found = doi_res.get("best_candidate_doi")
                doi_bibtex = make_doi_bibtex(doi_metadata)
                
        return {
            "temp_filename": safe_filename,
            "title_query": title_query,
            "sha256": sha256,
            "is_duplicate": existing_by_sha is not None,
            "existing_paper_id": existing_by_sha.get("paper_id") if existing_by_sha else None,
            "doi_found": doi_found,
            "doi_metadata": doi_metadata,
            "doi_score": doi_score,
            "doi_bibtex": doi_bibtex,
            "text_extracted": bool(text_content)
        }
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/papers/upload-confirm")
async def upload_confirm(body: PDFConfirmRequest):
    temp_path = LIBRARY_ROOT / "articles" / "temp_uploads" / body.temp_filename
    if not temp_path.exists():
        raise HTTPException(status_code=400, detail="Temporary file not found or expired")
    
    target_dir = LIBRARY_ROOT / "articles" / body.folder.strip("/")
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / body.temp_filename
    
    try:
        shutil.move(str(temp_path), str(target_path))
        parsed_bibtex = None
        metadata = {}
        metadata_source = "semantic_scholar"
        
        if body.manual_bibtex:
            try:
                parsed_bibtex = ingest.parse_bibtex_entry(body.manual_bibtex)
                parsed_bibtex = add_lib.canonicalize_manual_bibtex(parsed_bibtex)
                metadata = parsed_bibtex["normalized"]
                metadata_source = "manual_bibtex"
            except Exception as e:
                shutil.move(str(target_path), str(temp_path))
                raise HTTPException(status_code=400, detail=f"Invalid Manual BibTeX: {e}")
        elif body.selected_candidate:
            metadata = ingest.semantic_candidate_to_metadata(body.selected_candidate)
        else:
            metadata = {"title": body.title_query}
            metadata_source = "title_only"
            
        # Check for duplicates before finalizing
        sha256 = ingest.sha256_path(target_path)
        existing_by_path = ingest.load_master_index_row_by_pdf_path(target_path)
        existing_by_sha = ingest.load_master_index_row_by_pdf_sha256(sha256)
        matched_row = ingest.find_existing_reference_match(
            semantic_scholar_paper_id=metadata.get("semantic_scholar_paper_id", "") or metadata.get("semantic_scholar_id", ""),
            doi=metadata.get("doi", ""),
            title=metadata.get("title", ""),
            year=metadata.get("year", ""),
        )
        if existing_by_sha and existing_by_sha.get("content_kind") == ingest.CONTENT_KIND_PDF:
            matched_row = existing_by_sha

        if matched_row and matched_row.get("content_kind") == ingest.CONTENT_KIND_PDF:
            # Self-healing: if the matched row is missing title or sha256 in the index, refresh it!
            if not matched_row.get("resolved_title") or not matched_row.get("pdf_sha256"):
                try:
                    refreshed = ops.refresh_record_from_row(matched_row)
                    matched_row = refreshed["row"]
                except Exception:
                    pass
            registered_pdf_path = matched_row.get("pdf_path")
            if registered_pdf_path:
                registered_abs_path = (LIBRARY_ROOT / registered_pdf_path.strip("/")).resolve()
                if target_path.resolve() != registered_abs_path and target_path.exists():
                    target_path.unlink()
            return {
                "status": "duplicate",
                "message": "This paper is already indexed with a PDF. The duplicate file was deleted from disk.",
                "data": matched_row
            }
        
        res = add_lib.finalize_pdf_addition(
            pdf_path=target_path,
            title_query=body.title_query,
            metadata=metadata,
            semantic_attempts=[],
            semantic_merged=[],
            selected_candidate=body.selected_candidate,
            parsed_bibtex=parsed_bibtex,
            chunk_chars=2000,
            overlap_chars=200,
            addition_mode="pdf_guided",
            metadata_source_hint=metadata_source
        )
        
        if res != 0:
            raise HTTPException(status_code=500, detail="Ingestion pipeline failed")
        
        sha256 = ingest.sha256_path(target_path)
        new_row = ingest.load_master_index_row_by_pdf_sha256(sha256)
        return {"status": "success", "data": new_row}
    except Exception as e:
        if target_path.exists():
            target_path.unlink()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/papers/ref")
async def add_reference(body: RefAddRequest):
    try:
        parsed_bibtex = None
        metadata = {}
        metadata_source = "semantic_scholar"
        
        if body.manual_bibtex:
            parsed_bibtex = ingest.parse_bibtex_entry(body.manual_bibtex)
            parsed_bibtex = add_lib.canonicalize_manual_bibtex(parsed_bibtex)
            metadata = parsed_bibtex["normalized"]
            metadata_source = "manual_bibtex"
        elif body.selected_candidate:
            metadata = ingest.semantic_candidate_to_metadata(body.selected_candidate)
        else:
            raise HTTPException(status_code=400, detail="Either selected_candidate or manual_bibtex is required.")
        
        bibtex_key = metadata.get("bibtex_key", "")
        if not bibtex_key:
            author_token = ingest.preferred_title_token(metadata.get("authors", "author"))
            year_token = metadata.get("year", "undated")
            title_token = ingest.preferred_title_token(metadata.get("title", "title"))
            bibtex_key = f"{author_token}{year_token}{title_token}"
        
        paper_id = ingest.reference_paper_id_from_metadata(metadata.get("semantic_scholar_paper_id"), bibtex_key)
        
        res = add_lib.finalize_reference_addition(
            query=body.query,
            metadata=metadata,
            semantic_attempts=[],
            semantic_merged=[],
            selected_candidate=body.selected_candidate,
            parsed_bibtex=parsed_bibtex,
            chunk_chars=2000,
            overlap_chars=200,
        )
        
        if res != 0:
            raise HTTPException(status_code=500, detail="Ingestion pipeline failed")
        
        new_row = ingest.load_master_index_row_by_paper_id(paper_id)
        return {"status": "success", "data": new_row}
    except SystemExit as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/notes")
async def list_notes():
    ensure_notes_dir()
    notes = []
    for path in NOTES_DIR.glob("*.md"):
        content = path.read_text(encoding="utf-8")
        title = path.stem
        for line in content.splitlines():
            if line.strip().startswith("# "):
                title = line.strip("# ").strip()
                break
        
        stat = path.stat()
        notes.append({
            "filename": path.name,
            "title": title,
            "mtime": stat.st_mtime,
            "size": stat.st_size
        })
    notes.sort(key=lambda x: x["mtime"], reverse=True)
    return notes


@app.get("/api/notes/{filename}")
async def get_note(filename: str):
    ensure_notes_dir()
    note_path = NOTES_DIR / filename
    if not note_path.exists():
        raise HTTPException(status_code=404, detail="Note not found")
    return {
        "filename": filename,
        "content": note_path.read_text(encoding="utf-8")
    }


@app.post("/api/notes/{filename}")
async def save_note(filename: str, body: NoteSaveRequest):
    ensure_notes_dir()
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not filename.endswith(".md"):
        filename += ".md"
        
    note_path = NOTES_DIR / filename
    note_path.write_text(body.content, encoding="utf-8")
    return {"status": "success", "filename": filename}


@app.delete("/api/notes/{filename}")
async def delete_note(filename: str):
    ensure_notes_dir()
    note_path = NOTES_DIR / filename
    if not note_path.exists():
        raise HTTPException(status_code=404, detail="Note not found")
    note_path.unlink()
    return {"status": "success"}


@app.post("/api/papers/ingest-existing")
async def ingest_existing(body: IngestExistingRequest):
    pdf_path = LIBRARY_ROOT / body.relative_path.strip("/")
    if not pdf_path.exists():
        raise HTTPException(status_code=400, detail=f"File not found: {body.relative_path}")
        
    try:
        title_query = ingest.derive_title_query(pdf_path)
        
        parsed_bibtex = None
        metadata = {}
        metadata_source = "semantic_scholar"
        
        if body.manual_bibtex:
            parsed_bibtex = ingest.parse_bibtex_entry(body.manual_bibtex)
            parsed_bibtex = add_lib.canonicalize_manual_bibtex(parsed_bibtex)
            metadata = parsed_bibtex["normalized"]
            metadata_source = "manual_bibtex"
        elif body.selected_candidate:
            metadata = ingest.semantic_candidate_to_metadata(body.selected_candidate)
        else:
            metadata = {"title": title_query}
            metadata_source = "title_only"
            
        # Check for duplicates before finalizing
        sha256 = ingest.sha256_path(pdf_path)
        existing_by_path = ingest.load_master_index_row_by_pdf_path(pdf_path)
        existing_by_sha = ingest.load_master_index_row_by_pdf_sha256(sha256)
        matched_row = ingest.find_existing_reference_match(
            semantic_scholar_paper_id=metadata.get("semantic_scholar_paper_id", "") or metadata.get("semantic_scholar_id", ""),
            doi=metadata.get("doi", ""),
            title=metadata.get("title", ""),
            year=metadata.get("year", ""),
        )
        if existing_by_sha and existing_by_sha.get("content_kind") == ingest.CONTENT_KIND_PDF:
            matched_row = existing_by_sha

        if matched_row and matched_row.get("content_kind") == ingest.CONTENT_KIND_PDF:
            # Self-healing: if the matched row is missing title or sha256 in the index, refresh it!
            if not matched_row.get("resolved_title") or not matched_row.get("pdf_sha256"):
                try:
                    refreshed = ops.refresh_record_from_row(matched_row)
                    matched_row = refreshed["row"]
                except Exception:
                    pass
            registered_pdf_path = matched_row.get("pdf_path")
            if registered_pdf_path:
                registered_abs_path = (LIBRARY_ROOT / registered_pdf_path.strip("/")).resolve()
                if pdf_path.resolve() != registered_abs_path and pdf_path.exists():
                    pdf_path.unlink()
            return {
                "status": "duplicate",
                "message": "This paper is already indexed with a PDF. The duplicate file was deleted from disk.",
                "data": matched_row
            }
            
        res = add_lib.finalize_pdf_addition(
            pdf_path=pdf_path,
            title_query=title_query,
            metadata=metadata,
            semantic_attempts=[],
            semantic_merged=[],
            selected_candidate=body.selected_candidate,
            parsed_bibtex=parsed_bibtex,
            chunk_chars=2000,
            overlap_chars=200,
            addition_mode="pdf_guided",
            metadata_source_hint=metadata_source
        )
        if res != 0:
            raise HTTPException(status_code=500, detail="Ingestion pipeline failed")
            
        sha256 = ingest.sha256_path(pdf_path)
        new_row = ingest.load_master_index_row_by_pdf_sha256(sha256)
        return {"status": "success", "data": new_row}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/papers/resolve-untracked-duplicate")
async def resolve_untracked_duplicate(body: ResolveUntrackedDuplicateRequest):
    new_abs_path = (LIBRARY_ROOT / body.relative_path.strip("/")).resolve()
    if not new_abs_path.is_relative_to(LIBRARY_ROOT) or not new_abs_path.exists():
        raise HTTPException(status_code=400, detail=f"New file not found: {body.relative_path}")
        
    try:
        if body.action == "delete_new_file":
            # Physically delete the duplicate PDF
            new_abs_path.unlink()
            return {"status": "success", "message": "Duplicate file deleted from filesystem."}
            
        elif body.action == "use_new_path":
            # Update the existing index row to use the new path, and delete the old PDF file
            row = ops.resolve_target_row(paper_id=body.existing_paper_id)
            record = ops.load_record_payload(body.existing_paper_id)
            
            old_pdf_path = row.get("pdf_path")
            if old_pdf_path:
                old_abs_path = (LIBRARY_ROOT / old_pdf_path.strip("/")).resolve()
                if old_abs_path.exists() and old_abs_path != new_abs_path:
                    old_abs_path.unlink()
            
            # Update path in master_index row and record payload
            new_rel_path = ingest.relative_to_library(new_abs_path)
            row["pdf_path"] = new_rel_path
            row["filename"] = new_abs_path.name
            
            record["pdf_path"] = new_rel_path
            record["filename"] = new_abs_path.name
            
            # Save updated record JSON
            ingest.write_record_json(ingest.record_path_for_paper_id(body.existing_paper_id), record)
            
            # Upsert the master index
            ingest.upsert_master_index(row)
            
            return {"status": "success", "data": row, "message": "Updated library index to use new path. Old PDF deleted."}
        else:
            raise HTTPException(status_code=400, detail=f"Invalid action: {body.action}")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/papers/ingest-as-supplement")
async def ingest_as_supplement(body: IngestAsSupplementRequest):
    pdf_path = LIBRARY_ROOT / body.relative_path.strip("/")
    if not pdf_path.exists():
        raise HTTPException(status_code=400, detail=f"File not found: {body.relative_path}")
        
    try:
        resolved_parent = ops.resolve_target_row(paper_id=body.parent_paper_id)
        parent_row = ingest.load_master_index_row_by_paper_id(resolved_parent["paper_id"])
        if not parent_row:
            raise HTTPException(status_code=404, detail="Parent paper not found in index")
        
        sha256 = ingest.sha256_path(pdf_path)
        paper_id = f"sha256-{sha256[:16]}"
        
        # Extract text and write artifacts
        text_status, text_error, text_content = ingest.extract_pdf_text(pdf_path)
        text_sha256 = ""
        chunk_count = 0
        text_path_rel = ""
        chunk_path_rel = ""
        
        if text_content:
            artifacts = ingest.write_text_artifacts(
                paper_id=paper_id,
                text_content=text_content,
                chunk_chars=2000,
                overlap_chars=200,
            )
            text_sha256 = artifacts["text_sha256"]
            chunk_count = artifacts["chunk_count"]
            text_path_rel = ingest.relative_to_library(artifacts["text_path"])
            chunk_path_rel = ingest.relative_to_library(artifacts["chunk_path"])
        
        # Create supplement row
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        rel_pdf_path = ingest.relative_to_library(pdf_path)
        item_type = ingest.detect_item_type(pdf_path)
        
        supplement_row = ingest.master_index_row_defaults({
            "paper_id": paper_id,
            "item_type": item_type,
            "content_kind": ingest.CONTENT_KIND_PDF,
            "pdf_path": rel_pdf_path,
            "pdf_sha256": sha256,
            "filename": pdf_path.name,
            "title_query": pdf_path.stem,
            "resolved_title": parent_row.get("resolved_title") or parent_row.get("title_query") or "Untitled",
            "authors": parent_row.get("authors", ""),
            "year": parent_row.get("year", ""),
            "venue": parent_row.get("venue", ""),
            "doi": "",
            "match_status": "matched_supplement",
            "bibtex_type": parent_row.get("bibtex_type") or "article",
            "canonical_url": parent_row.get("canonical_url", ""),
            "bibtex_key": parent_row.get("bibtex_key", ""),
            "text_path": text_path_rel,
            "chunk_path": chunk_path_rel,
            "date_indexed": now,
        })
        
        ingest.upsert_master_index(supplement_row)
        
        # Save record JSON
        record_path = ingest.record_path_for_paper_id(paper_id)
        record = {
            "paper_id": paper_id,
            "content_kind": ingest.CONTENT_KIND_PDF,
            "addition_mode": "manual_supplement_link",
            "pdf_path": str(pdf_path),
            "pdf_sha256": sha256,
            "filename": pdf_path.name,
            "item_type": item_type,
            "title_query": pdf_path.stem,
            "date_indexed": now,
            "match_status": "matched_supplement",
            "metadata_source": "manual_supplement_link",
            "supplement_parent": parent_row,
            "artifacts": {
                "record_path": str(record_path),
                "text_path": str(text_path_rel),
                "chunk_path": str(chunk_path_rel),
                "master_index": str(ingest.MASTER_INDEX),
                "master_bib": str(ingest.MASTER_BIB),
                "text_kind": "pdf_text",
            },
            "text_extraction": {
                "status": text_status,
                "error": text_error,
                "text_sha256": text_sha256,
                "chunk_count": chunk_count,
                "chunk_target_chars": 2000,
                "chunk_overlap_chars": 200,
                "converter": str(ingest.PDF2TEXT),
            }
        }
        ingest.write_record_json(record_path, record)
        
        return {"status": "success", "data": supplement_row}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/papers/{paper_id}/link-supplement")
async def link_supplement(paper_id: str, body: LinkSupplementRequest):
    try:
        resolved_supp = ops.resolve_target_row(paper_id=paper_id)
        resolved_parent = ops.resolve_target_row(paper_id=body.parent_paper_id)
        
        supplement_row = ingest.load_master_index_row_by_paper_id(resolved_supp["paper_id"])
        parent_row = ingest.load_master_index_row_by_paper_id(resolved_parent["paper_id"])
        
        if not supplement_row:
            raise HTTPException(status_code=404, detail="Supplement paper not found in index")
        if not parent_row:
            raise HTTPException(status_code=404, detail="Parent paper not found in index")
            
        supplement_id = supplement_row["paper_id"]
        
        parent_title = parent_row.get("resolved_title") or parent_row.get("title_query") or ""
        supplement_row.update({
            "resolved_title": parent_title,
            "title_query": parent_title,
            "authors": parent_row.get("authors", ""),
            "year": parent_row.get("year", ""),
            "venue": parent_row.get("venue", ""),
            "doi": "",
            "match_status": "matched_supplement",
            "canonical_url": parent_row.get("canonical_url", ""),
            "bibtex_key": parent_row.get("bibtex_key", ""),
            "abstract_source": "",
        })
        ingest.upsert_master_index(supplement_row)
        
        record_path = ingest.record_path_for_paper_id(supplement_id)
        payload = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}
        payload["match_status"] = "matched_supplement"
        payload["metadata_source"] = "manual_supplement_link"
        payload["supplement_parent"] = parent_row
        ingest.write_record_json(record_path, payload)
        
        return {"status": "success", "data": supplement_row}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def split_multiple_bibtex(raw_content: str) -> list[str]:
    import re
    entries = []
    pattern = re.compile(r"@[A-Za-z0-9_:-]+\s*\{", re.S)
    pos = 0
    length = len(raw_content)
    while True:
        match = pattern.search(raw_content, pos)
        if not match:
            break
        start_idx = match.start()
        depth = 1
        idx = match.end()
        while idx < length and depth > 0:
            char = raw_content[idx]
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
            idx += 1
        if depth == 0:
            entries.append(raw_content[start_idx:idx].strip())
            pos = idx
        else:
            pos = match.end()
    return entries


@app.post("/api/papers/batch-import-bibtex")
async def batch_import_bibtex(body: BatchImportBibtexRequest):
    try:
        entries_raw = split_multiple_bibtex(body.raw_bibtex)
        total_parsed = len(entries_raw)
        imported = []
        skipped = []

        existing_rows = ingest.load_master_index_rows()
        existing_dois = {r.get("doi").lower().strip() for r in existing_rows if r.get("doi")}
        existing_keys = {r.get("bibtex_key").lower().strip() for r in existing_rows if r.get("bibtex_key")}
        existing_titles = {r.get("resolved_title").lower().strip() for r in existing_rows if r.get("resolved_title")}

        for entry_str in entries_raw:
            try:
                parsed_bibtex = ingest.parse_bibtex_entry(entry_str)
                parsed_bibtex = add_lib.canonicalize_manual_bibtex(parsed_bibtex)
                metadata = parsed_bibtex["normalized"]
                
                doi = metadata.get("doi", "").lower().strip()
                key = metadata.get("bibtex_key", "").lower().strip()
                title = metadata.get("title", "").lower().strip()
                year = metadata.get("year", "").strip()

                is_duplicate = False
                reason = ""
                if doi and doi in existing_dois:
                    is_duplicate = True
                    reason = f"DOI {doi} already exists."
                elif key and key in existing_keys:
                    is_duplicate = True
                    reason = f"BibTeX key '{key}' already exists."
                elif title and title in existing_titles:
                    is_duplicate = True
                    reason = f"Title '{title}' matches an existing paper."
                
                if is_duplicate:
                    skipped.append({
                        "key": key or "unknown",
                        "title": title or "unknown",
                        "reason": reason
                    })
                    continue

                if not key:
                    author_token = ingest.preferred_title_token(metadata.get("authors", "author"))
                    year_token = year or "undated"
                    title_token = ingest.preferred_title_token(title or "title")
                    key = f"{author_token}{year_token}{title_token}"
                    metadata["bibtex_key"] = key

                paper_id = ingest.reference_paper_id_from_metadata(
                    metadata.get("semantic_scholar_paper_id"), key
                )

                res = add_lib.finalize_reference_addition(
                    query=title,
                    metadata=metadata,
                    semantic_attempts=[],
                    semantic_merged=[],
                    selected_candidate=None,
                    parsed_bibtex=parsed_bibtex,
                    chunk_chars=2000,
                    overlap_chars=200,
                )

                if res == 0:
                    imported.append({
                        "key": key,
                        "title": title,
                        "paper_id": paper_id
                    })
                    if doi: existing_dois.add(doi)
                    if key: existing_keys.add(key)
                    if title: existing_titles.add(title)
                else:
                    skipped.append({
                        "key": key,
                        "title": title,
                        "reason": "Reference finalization pipeline failed."
                    })
            except Exception as entry_err:
                skipped.append({
                    "key": "unknown",
                    "title": "Parse error",
                    "reason": f"Failed to parse entry: {str(entry_err)}"
                })

        return {
            "status": "success",
            "total_parsed": total_parsed,
            "imported": imported,
            "skipped": skipped
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/papers/batch-import-directory")
async def batch_import_directory(body: BatchImportDirectoryRequest):
    try:
        import os
        dir_path = (LIBRARY_ROOT / body.directory_path.strip("/")).resolve()
        if not dir_path.is_relative_to(LIBRARY_ROOT) or not dir_path.is_dir():
            raise HTTPException(status_code=400, detail="Invalid directory path or directory not found")
        
        pdf_paths = []
        if body.recursive:
            for root, dirs, files in os.walk(dir_path):
                for f in files:
                    if f.lower().endswith(".pdf"):
                        pdf_paths.append(Path(root) / f)
        else:
            for f in os.listdir(dir_path):
                full_f = dir_path / f
                if full_f.is_file() and f.lower().endswith(".pdf"):
                    pdf_paths.append(full_f)
                    
        total_found = len(pdf_paths)
        already_indexed = 0
        imported = []
        skipped = []
        
        existing_rows = ingest.load_master_index_rows()
        existing_paths = {r.get("pdf_path").lower().strip("/") for r in existing_rows if r.get("pdf_path")}
        existing_shas = {r.get("pdf_sha256") for r in existing_rows if r.get("pdf_sha256")}
        
        for pdf_path in pdf_paths:
            try:
                rel_path = ingest.relative_to_library(pdf_path).lower().strip("/")
                sha256 = ingest.sha256_path(pdf_path)
                
                if rel_path in existing_paths or sha256 in existing_shas:
                    already_indexed += 1
                    continue
                    
                title_query = ingest.derive_title_query(pdf_path)
                text_status, text_error, text_content = ingest.extract_pdf_text(pdf_path)
                
                if text_status != "ok" or not text_content:
                    skipped.append({
                        "filename": pdf_path.name,
                        "path": str(ingest.relative_to_library(pdf_path)),
                        "reason": f"Text extraction failed: {text_error or 'No text extracted'}"
                    })
                    continue
                    
                filename_hints = ingest.derive_filename_hints(pdf_path)
                title_targets = [title_query] + ingest.derive_text_title_candidates(text_content)
                doi_res = ingest.resolve_pdf_doi_metadata(
                    pdf_path=pdf_path,
                    extracted_text=text_content,
                    filename_hints=filename_hints,
                    title_targets=title_targets
                )
                
                accepted_crossref = doi_res.get("accepted_metadata")
                if not accepted_crossref:
                    score = doi_res.get("best_score")
                    reason = f"No DOI resolved (best score: {score or 'n/a'})"
                    skipped.append({
                        "filename": pdf_path.name,
                        "path": str(ingest.relative_to_library(pdf_path)),
                        "reason": reason
                    })
                    continue
                    
                metadata = ingest.crossref_to_bibtex_fields(accepted_crossref)
                
                doi_resolution = {
                    "candidate_dois": doi_res.get("candidate_dois", []),
                    "candidate_details": doi_res.get("candidate_details", []),
                    "crossref_attempts": doi_res.get("crossref_attempts", []),
                    "best_candidate_doi": doi_res.get("best_candidate_doi", ""),
                    "best_metadata": accepted_crossref,
                    "best_score": doi_res.get("best_score"),
                    "warnings": doi_res.get("warnings", []),
                    "error": doi_res.get("error", ""),
                    "accepted_metadata": accepted_crossref,
                    "review_metadata": None,
                    "accepted_source": "crossref_doi",
                    "matched_by": "doi_text_validation",
                }
                
                res = add_lib.finalize_pdf_addition(
                    pdf_path=pdf_path,
                    title_query=title_query,
                    metadata=metadata,
                    semantic_attempts=[],
                    semantic_merged=[],
                    selected_candidate=None,
                    parsed_bibtex=None,
                    chunk_chars=2000,
                    overlap_chars=200,
                    addition_mode="pdf_guided",
                    metadata_source_hint="crossref_doi_primary",
                    doi_resolution_hint=doi_resolution
                )
                
                if res == 0:
                    new_row = ingest.load_master_index_row_by_pdf_sha256(sha256)
                    imported.append({
                        "filename": pdf_path.name,
                        "title": new_row.get("resolved_title") or title_query,
                        "key": new_row.get("bibtex_key", ""),
                        "paper_id": new_row.get("paper_id", "")
                    })
                    existing_paths.add(rel_path)
                    existing_shas.add(sha256)
                else:
                    skipped.append({
                        "filename": pdf_path.name,
                        "path": str(ingest.relative_to_library(pdf_path)),
                        "reason": "PDF addition finalization pipeline failed."
                    })
            except Exception as file_err:
                skipped.append({
                    "filename": pdf_path.name,
                    "path": str(ingest.relative_to_library(pdf_path)),
                    "reason": f"Internal error: {str(file_err)}"
                })
                
        return {
            "status": "success",
            "total_found": total_found,
            "already_indexed": already_indexed,
            "imported": imported,
            "skipped": skipped
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Fallback to serve React Frontend Single Page Application
@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API route not found")
    
    file_path = FRONTEND_DIST / full_path
    if file_path.is_file():
        return FileResponse(file_path)
    
    index_file = FRONTEND_DIST / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)
    
    return JSONResponse(status_code=404, content={"message": "Frontend assets not found. Run standard build first."})
