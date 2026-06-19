#!/usr/bin/env python3
"""Guided human-in-the-loop addition for the local literature library."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import library_ingest as ingest
from library_cli import prompt_multiline_text


def eprint(*parts: object) -> None:
    print(*parts)


def dedupe_queries(queries: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for query in queries:
        normalized = ingest.normalize_title(query)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(" ".join(query.split()).strip())
    return ordered


def print_candidates(candidates: list[dict[str, Any]], limit: int) -> None:
    if not candidates:
        eprint("No Semantic Scholar candidates found.")
        return
    for index, entry in enumerate(candidates[:limit], start=1):
        metadata = ingest.semantic_candidate_to_metadata(entry["candidate"])
        authors = metadata["authors"]
        author_preview = authors if len(authors) <= 90 else authors[:87] + "..."
        eprint(
            f"[{index}] score={entry['score']:.4f} year={metadata['year'] or 'n/a'} "
            f"venue={metadata['venue'] or 'n/a'} doi={metadata['doi'] or 'n/a'}"
        )
        eprint(f"    title: {metadata['title'] or 'n/a'}")
        eprint(f"    authors: {author_preview or 'n/a'}")


def prompt_multiline_bibtex() -> str:
    return prompt_multiline_text("Paste one BibTeX entry.", end_marker="END")


def canonicalize_manual_bibtex(parsed: dict[str, Any]) -> dict[str, Any]:
    canonical = ingest.canonicalize_bibtex_entry(parsed)
    ignored_fields = list(canonical.get("ignored_fields") or [])
    if ignored_fields:
        eprint(f"Warning: ignoring unsupported BibTeX fields: {', '.join(ignored_fields)}")
    return canonical


def confirm_metadata(fields: dict[str, str], bibtex_type: str, bibtex_key: str, content_kind: str) -> bool:
    eprint("")
    eprint("Normalized metadata:")
    eprint(f"  content_kind: {content_kind}")
    eprint(f"  bibtex_type: {bibtex_type or 'n/a'}")
    eprint(f"  bibtex_key: {bibtex_key or '(to be generated)'}")
    eprint(f"  title: {fields.get('title', '') or 'n/a'}")
    eprint(f"  authors: {fields.get('authors', '') or 'n/a'}")
    eprint(f"  year: {fields.get('year', '') or 'n/a'}")
    eprint(f"  venue: {fields.get('venue', '') or 'n/a'}")
    eprint(f"  doi: {fields.get('doi', '') or 'n/a'}")
    eprint(f"  url: {fields.get('url', '') or 'n/a'}")
    eprint(f"  abstract: {'present' if fields.get('abstract') else 'absent'}")
    response = input("Confirm these fields? [y/N]: ").strip().lower()
    return response in {"y", "yes"}


def print_doi_resolution_summary(doi_resolution: dict[str, Any]) -> None:
    candidate_details = list(doi_resolution.get("candidate_details") or [])
    if not candidate_details:
        eprint("DOI extraction: no DOI candidates found in the converted PDF text.")
        return
    eprint("DOI extraction: found candidate DOI(s):")
    for entry in candidate_details[:5]:
        eprint(
            f"  - {entry.get('doi', '')} "
            f"[region={entry.get('source_region', '')} score={entry.get('priority_score', '')}]"
        )
    accepted = doi_resolution.get("accepted_metadata")
    if accepted is not None:
        eprint(
            "DOI/Crossref match accepted:"
            f" doi={accepted.get('DOI', '')}"
            f" score={doi_resolution.get('best_score', 'n/a')}"
        )
    elif doi_resolution.get("best_metadata") is not None:
        eprint(
            "DOI/Crossref match was found but not accepted:"
            f" doi={doi_resolution.get('best_candidate_doi', '')}"
            f" score={doi_resolution.get('best_score', 'n/a')}"
        )
    elif doi_resolution.get("error"):
        eprint(f"DOI/Crossref resolution failed: {doi_resolution.get('error')}")
    else:
        eprint("DOI/Crossref resolution: no trustworthy DOI match found.")


def metadata_from_crossref_resolution(
    crossref_message: dict[str, Any],
    semantic_candidate: dict[str, Any] | None,
) -> dict[str, str]:
    crossref_fields = ingest.crossref_to_bibtex_fields(crossref_message)
    semantic_meta = ingest.semantic_candidate_to_metadata(semantic_candidate or {})
    merged = {
        **crossref_fields,
        "abstract": crossref_fields.get("abstract") or semantic_meta.get("abstract", ""),
        "semantic_scholar_paper_id": semantic_meta.get("semantic_scholar_paper_id", ""),
        "bibtex_key": semantic_meta.get("bibtex_key", ""),
        "bibtex_type": semantic_meta.get("bibtex_type", ""),
    }
    merged["url"] = ingest.canonical_url_from_sources(
        crossref_meta=crossref_message,
        best_candidate=semantic_candidate,
    )
    return merged


def json_ready_semantic_attempts(attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for attempt in attempts:
        prepared.append(
            {
                "query": str(attempt.get("query", "")),
                "status": str(attempt.get("status", "")),
                "api_error": str(attempt.get("api_error", "")),
                "warnings": list(attempt.get("warnings") or []),
                "best_score": attempt.get("best_score"),
                "candidate_count": attempt.get("candidate_count"),
                "best_candidate": attempt.get("best_candidate"),
                "scored_candidates": [asdict(candidate) for candidate in (attempt.get("scored_candidates") or [])],
            }
        )
    return prepared


def json_ready_semantic_merged(merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for entry in merged:
        prepared.append(
            {
                "identity": str(entry.get("identity", "")),
                "candidate": entry.get("candidate"),
                "query": str(entry.get("query", "")),
                "score": entry.get("score"),
                "title_similarity": entry.get("title_similarity"),
                "candidate_year": entry.get("candidate_year"),
            }
        )
    return prepared


def search_loop(
    initial_queries: list[str],
    filename: str,
    item_type: str,
    filename_hints: dict[str, Any],
    semantic_limit: int,
    autonomous: bool,
) -> dict[str, Any]:
    queries = dedupe_queries(initial_queries)
    while True:
        attempts, merged = ingest.collect_semantic_candidates(
            queries=queries,
            filename=filename,
            item_type=item_type,
            filename_hints=filename_hints,
            semantic_limit=semantic_limit,
        )
        print_candidates(merged, semantic_limit)

        if autonomous:
            if not merged:
                return {"action": "skip", "attempts": attempts, "merged": merged}
            top = merged[0]["candidate"]
            return {
                "action": "candidate",
                "attempts": attempts,
                "merged": merged,
                "candidate": top,
                "metadata": ingest.semantic_candidate_to_metadata(top),
            }

        eprint("")
        eprint("Actions: number = select candidate, r = retry with new query, b = paste BibTeX, s = skip, c = cancel")
        response = input("> ").strip()
        if not response:
            continue
        if response.lower() == "r":
            retry_query = input("New search query: ").strip()
            if retry_query:
                queries = dedupe_queries(queries + [retry_query])
            continue
        if response.lower() == "b":
            raw_bibtex = prompt_multiline_bibtex()
            parsed = canonicalize_manual_bibtex(ingest.parse_bibtex_entry(raw_bibtex))
            return {
                "action": "manual_bibtex",
                "attempts": attempts,
                "merged": merged,
                "parsed_bibtex": parsed,
                "metadata": parsed["normalized"],
            }
        if response.lower() == "s":
            return {"action": "skip", "attempts": attempts, "merged": merged}
        if response.lower() == "c":
            return {"action": "cancel", "attempts": attempts, "merged": merged}
        if response.isdigit():
            index = int(response)
            if 1 <= index <= min(len(merged), semantic_limit):
                candidate = merged[index - 1]["candidate"]
                return {
                    "action": "candidate",
                    "attempts": attempts,
                    "merged": merged,
                    "candidate": candidate,
                    "metadata": ingest.semantic_candidate_to_metadata(candidate),
                }
        eprint("Unrecognized action.")


def finalize_pdf_addition(
    pdf_path: Path,
    title_query: str,
    metadata: dict[str, str],
    semantic_attempts: list[dict[str, Any]],
    semantic_merged: list[dict[str, Any]],
    selected_candidate: dict[str, Any] | None,
    parsed_bibtex: dict[str, Any] | None,
    chunk_chars: int,
    overlap_chars: int,
    addition_mode: str,
    metadata_source_hint: str = "",
    doi_resolution_hint: dict[str, Any] | None = None,
) -> int:
    ingest.ensure_registry_layout()
    pdf_path = ingest.ensure_path_inside_library(pdf_path, "pdf")
    rel_pdf_path = ingest.relative_to_library(pdf_path)
    item_type = ingest.detect_item_type(pdf_path)
    pdf_sha256 = ingest.sha256_path(pdf_path)
    sha_paper_id = f"sha256-{pdf_sha256[:16]}"
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    semantic_attempts_json = json_ready_semantic_attempts(semantic_attempts)
    semantic_merged_json = json_ready_semantic_merged(semantic_merged)

    existing_by_path = ingest.load_master_index_row_by_pdf_path(pdf_path)
    existing_by_sha = ingest.load_master_index_row_by_pdf_sha256(pdf_sha256)
    matched_row = ingest.find_existing_reference_match(
        semantic_scholar_paper_id=metadata.get("semantic_scholar_paper_id", ""),
        doi=metadata.get("doi", ""),
        title=metadata.get("title", ""),
        year=metadata.get("year", ""),
    )
    if existing_by_sha and existing_by_sha.get("content_kind") == ingest.CONTENT_KIND_PDF:
        matched_row = existing_by_sha

    if matched_row and matched_row.get("content_kind") == ingest.CONTENT_KIND_PDF:
        eprint(f"Duplicate warning: {pdf_path} already maps to paper_id={matched_row.get('paper_id', '')}. No change made.")
        return 0
    if existing_by_path and existing_by_path.get("content_kind") == ingest.CONTENT_KIND_PDF:
        eprint(f"Duplicate warning: {pdf_path} is already indexed as {existing_by_path.get('paper_id', '')}. No change made.")
        return 0

    existing_row = matched_row or existing_by_sha or existing_by_path or ingest.load_master_index_row_by_paper_id(sha_paper_id)
    paper_id = matched_row["paper_id"] if matched_row and matched_row.get("content_kind") == ingest.CONTENT_KIND_REF else sha_paper_id
    record_path = ingest.record_path_for_paper_id(paper_id)
    existing_record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}

    text_status, text_error, text_content = ingest.extract_pdf_text(pdf_path)
    if text_status != "ok" or not text_content:
        eprint(f"PDF text extraction failed for {pdf_path}")
        eprint(text_error or "No text was extracted from the PDF.")
        return 1
    text_sha256 = ""
    chunk_count = 0
    text_path: Path | None = None
    chunk_path: Path | None = None
    if text_content:
        artifacts = ingest.write_text_artifacts(
            paper_id=paper_id,
            text_content=text_content,
            chunk_chars=chunk_chars,
            overlap_chars=overlap_chars,
        )
        text_sha256 = artifacts["text_sha256"]
        chunk_count = artifacts["chunk_count"]
        text_path = artifacts["text_path"]
        chunk_path = artifacts["chunk_path"]

    doi_resolution = doi_resolution_hint or {
        "candidate_dois": [],
        "candidate_details": [],
        "crossref_attempts": [],
        "best_candidate_doi": "",
        "best_metadata": None,
        "best_score": None,
        "warnings": [],
        "error": "",
        "accepted_metadata": None,
        "review_metadata": None,
        "accepted_source": "",
        "matched_by": "",
    }
    metadata_source = metadata_source_hint or ("manual_bibtex" if parsed_bibtex else ("semantic_scholar_primary" if selected_candidate else "manual_bibtex"))
    selected_candidate_for_record = selected_candidate
    if not doi_resolution_hint and selected_candidate and not parsed_bibtex and text_content:
        filename_hints = ingest.derive_filename_hints(pdf_path)
        title_targets = [title_query] + ingest.derive_text_title_candidates(text_content)
        doi_resolution = ingest.resolve_pdf_doi_metadata(
            pdf_path=pdf_path,
            extracted_text=text_content,
            filename_hints=filename_hints,
            title_targets=title_targets,
        )
        accepted_crossref = doi_resolution["accepted_metadata"]
        if accepted_crossref:
            crossref_fields = ingest.crossref_to_bibtex_fields(accepted_crossref)
            metadata = {
                **metadata,
                **crossref_fields,
                "abstract": crossref_fields.get("abstract") or metadata.get("abstract", ""),
            }
            selected_candidate_for_record = ingest.choose_semantic_candidate_for_doi(
                semantic_merged,
                str(accepted_crossref.get("DOI") or ""),
                None,
            )
            metadata_source = (
                "crossref_doi_plus_semantic_enrichment"
                if selected_candidate_for_record is not None
                else "crossref_doi_primary"
            )

    bibtex_type = (existing_row or {}).get("bibtex_type", "") or (parsed_bibtex or {}).get("entry_type", "") or ("book" if item_type == "book" else "article")
    existing_bibtex_key = (existing_row or {}).get("bibtex_key", "")
    bibtex_key = existing_bibtex_key or (parsed_bibtex or {}).get("bibtex_key", "") or ingest.build_bibtex_key_from_fields(
        metadata.get("authors", ""),
        metadata.get("year", ""),
        metadata.get("title", ""),
        fallback_stem=pdf_path.stem,
    )
    bibtex_entry = (
        ingest.build_bibtex_entry_with_preserved_fields(
            bibtex_type,
            bibtex_key,
            (parsed_bibtex or {}).get("fields", {}),
            metadata,
        )
        if parsed_bibtex
        else ingest.build_bibtex_entry_from_fields(bibtex_type, bibtex_key, metadata)
    )
    ingest.upsert_master_bib(paper_id, bibtex_entry)
    if existing_row and existing_row.get("bibtex_key") and existing_row.get("bibtex_key") != bibtex_key:
        ingest.remove_master_bib_entry(existing_row.get("bibtex_key", ""))

    abstract_source = (
        "crossref"
        if metadata_source.startswith("crossref_doi") and ingest.crossref_abstract(doi_resolution["accepted_metadata"] or {})
        else ("semantic_scholar" if selected_candidate_for_record and metadata.get("abstract") else ("manual_verified" if metadata.get("abstract") else ""))
    )
    row = ingest.master_index_row_defaults(
        {
            "paper_id": paper_id,
            "item_type": item_type,
            "content_kind": ingest.CONTENT_KIND_PDF,
            "pdf_path": rel_pdf_path,
            "pdf_sha256": pdf_sha256,
            "filename": pdf_path.name,
            "title_query": title_query,
            "resolved_title": metadata.get("title", ""),
            "authors": metadata.get("authors", ""),
            "year": metadata.get("year", ""),
            "venue": metadata.get("venue", ""),
            "doi": metadata.get("doi", ""),
            "semantic_scholar_paper_id": (ingest.semantic_candidate_to_metadata(selected_candidate_for_record or {}).get("semantic_scholar_paper_id", "") or metadata.get("semantic_scholar_paper_id", "")),
            "match_confidence": "",
            "match_status": "matched_via_doi" if metadata_source.startswith("crossref_doi") else ("matched" if selected_candidate else "manual_verified"),
            "bibtex_type": bibtex_type,
            "canonical_url": metadata.get("url", ""),
            "bibtex_key": bibtex_key,
            "text_path": ingest.relative_to_library(text_path) if text_path and text_path.exists() else "",
            "chunk_path": ingest.relative_to_library(chunk_path) if chunk_path and chunk_path.exists() else "",
            "abstract_source": abstract_source,
            "date_indexed": now,
            "notes": text_error,
        }
    )
    ingest.upsert_master_index(row)

    record_payload = existing_record
    record_payload.update(
        {
            "paper_id": paper_id,
            "content_kind": ingest.CONTENT_KIND_PDF,
            "addition_mode": addition_mode,
            "library_root": str(ingest.LIBRARY_ROOT),
            "pdf_path": str(pdf_path),
            "pdf_sha256": pdf_sha256,
            "filename": pdf_path.name,
            "item_type": item_type,
            "title_query": title_query,
            "date_indexed": now,
            "match_status": row["match_status"],
            "match_confidence": None,
            "metadata_source": metadata_source,
            "doi_candidates": doi_resolution["candidate_dois"],
            "doi_candidate_details": doi_resolution["candidate_details"],
            "semantic_scholar": {
                "query": title_query,
                "limit": len(semantic_merged_json),
                "attempts": semantic_attempts_json,
                "selected_paper_id": ingest.semantic_candidate_to_metadata(selected_candidate_for_record or {}).get("semantic_scholar_paper_id", ""),
                "best_candidate": selected_candidate_for_record,
                "merged_candidates": semantic_merged_json,
            },
            "crossref": {
                "error": doi_resolution["error"],
                "metadata": doi_resolution["best_metadata"],
                "accepted_score": doi_resolution["best_score"] if doi_resolution["accepted_metadata"] is not None else None,
                "accepted": doi_resolution["accepted_metadata"] is not None,
                "accepted_doi": str((doi_resolution["accepted_metadata"] or {}).get("DOI") or ""),
                "best_candidate_doi": doi_resolution["best_candidate_doi"],
                "crossref_attempts": doi_resolution["crossref_attempts"],
                "accepted_source": doi_resolution["accepted_source"],
                "matched_by": doi_resolution["matched_by"],
            },
            "artifacts": {
                "record_path": str(record_path),
                "text_path": str(text_path) if text_path and text_path.exists() else None,
                "chunk_path": str(chunk_path) if chunk_path and chunk_path.exists() else None,
                "master_index": str(ingest.MASTER_INDEX),
                "master_bib": str(ingest.MASTER_BIB),
                "text_kind": "pdf_text" if text_content else "",
            },
            "text_extraction": {
                "status": text_status,
                "error": text_error,
                "text_sha256": text_sha256,
                "chunk_count": chunk_count,
                "chunk_target_chars": chunk_chars,
                "chunk_overlap_chars": overlap_chars,
                "converter": str(ingest.PDF2TEXT),
            },
        }
    )
    if parsed_bibtex:
        record_payload["manual_override"] = {
            "raw_bibtex": parsed_bibtex["raw_bibtex"],
            "parsed_bibtex_type": parsed_bibtex["entry_type"],
            "source_note": "pasted into library_add.py pdf flow",
            "bibtex_key": bibtex_key,
        }
    ingest.write_record_json(record_path, record_payload)
    eprint(f"Added PDF-backed record paper_id={paper_id}")
    eprint(f"  metadata_source={metadata_source}")
    if doi_resolution.get("accepted_metadata") is not None:
        eprint(f"  accepted_doi={doi_resolution['accepted_metadata'].get('DOI', '')}")
    if selected_candidate_for_record is not None:
        eprint(
            "  semantic_scholar_paper_id="
            f"{ingest.semantic_candidate_to_metadata(selected_candidate_for_record).get('semantic_scholar_paper_id', '')}"
        )
    return 0


def finalize_reference_addition(
    query: str,
    metadata: dict[str, str],
    semantic_attempts: list[dict[str, Any]],
    semantic_merged: list[dict[str, Any]],
    selected_candidate: dict[str, Any] | None,
    parsed_bibtex: dict[str, Any] | None,
    chunk_chars: int,
    overlap_chars: int,
) -> int:
    ingest.ensure_registry_layout()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    semantic_attempts_json = json_ready_semantic_attempts(semantic_attempts)
    semantic_merged_json = json_ready_semantic_merged(semantic_merged)

    existing_row = ingest.find_existing_reference_match(
        semantic_scholar_paper_id=metadata.get("semantic_scholar_paper_id", ""),
        doi=metadata.get("doi", ""),
        title=metadata.get("title", ""),
        year=metadata.get("year", ""),
    )
    if existing_row:
        paper_id = existing_row["paper_id"]
    else:
        paper_id = ingest.reference_paper_id_from_metadata(
            semantic_scholar_paper_id=metadata.get("semantic_scholar_paper_id", ""),
            doi=metadata.get("doi", ""),
            title=metadata.get("title", ""),
            year=metadata.get("year", ""),
            authors=metadata.get("authors", ""),
            venue=metadata.get("venue", ""),
        )

    record_path = ingest.record_path_for_paper_id(paper_id)
    existing_record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}
    bibtex_type = (existing_row or {}).get("bibtex_type", "") or (parsed_bibtex or {}).get("entry_type", "") or metadata.get("bibtex_type", "") or "article"
    existing_bibtex_key = (existing_row or {}).get("bibtex_key", "")
    bibtex_key = existing_bibtex_key or (parsed_bibtex or {}).get("bibtex_key", "") or ingest.build_bibtex_key_from_fields(
        metadata.get("authors", ""),
        metadata.get("year", ""),
        metadata.get("title", ""),
        fallback_stem="reference",
    )

    bibtex_entry = (
        ingest.build_bibtex_entry_with_preserved_fields(
            bibtex_type,
            bibtex_key,
            (parsed_bibtex or {}).get("fields", {}),
            metadata,
        )
        if parsed_bibtex
        else ingest.build_bibtex_entry_from_fields(bibtex_type, bibtex_key, metadata)
    )
    ingest.upsert_master_bib(paper_id, bibtex_entry)
    if existing_row and existing_row.get("bibtex_key") and existing_row.get("bibtex_key") != bibtex_key:
        ingest.remove_master_bib_entry(existing_row.get("bibtex_key", ""))

    content_kind = (existing_row or {}).get("content_kind", "") or ingest.CONTENT_KIND_REF
    text_path = (existing_row or {}).get("text_path", "")
    chunk_path = (existing_row or {}).get("chunk_path", "")
    text_kind = (existing_record.get("artifacts") or {}).get("text_kind", "")
    if content_kind == ingest.CONTENT_KIND_REF:
        stub_artifacts = ingest.write_reference_stub_artifacts(
            paper_id=paper_id,
            fields=metadata,
            bibtex_key=bibtex_key,
            chunk_chars=chunk_chars,
            overlap_chars=overlap_chars,
        )
        text_path = ingest.relative_to_library(stub_artifacts["text_path"])
        chunk_path = ingest.relative_to_library(stub_artifacts["chunk_path"])
        text_kind = "metadata_stub"

    item_type = (existing_row or {}).get("item_type", "") or ("book" if bibtex_type == "book" else "article")
    row = ingest.master_index_row_defaults(
        {
            "paper_id": paper_id,
            "item_type": item_type,
            "content_kind": content_kind,
            "pdf_path": (existing_row or {}).get("pdf_path", ""),
            "pdf_sha256": (existing_row or {}).get("pdf_sha256", ""),
            "filename": (existing_row or {}).get("filename", ""),
            "title_query": query,
            "resolved_title": metadata.get("title", ""),
            "authors": metadata.get("authors", ""),
            "year": metadata.get("year", ""),
            "venue": metadata.get("venue", ""),
            "doi": metadata.get("doi", ""),
            "semantic_scholar_paper_id": metadata.get("semantic_scholar_paper_id", ""),
            "match_confidence": "",
            "match_status": "matched" if selected_candidate else "manual_verified",
            "bibtex_type": bibtex_type,
            "canonical_url": metadata.get("url", ""),
            "bibtex_key": bibtex_key,
            "text_path": text_path,
            "chunk_path": chunk_path,
            "abstract_source": "semantic_scholar" if selected_candidate and metadata.get("abstract") else ("manual_verified" if metadata.get("abstract") else ""),
            "date_indexed": now,
            "notes": "",
        }
    )
    ingest.upsert_master_index(row)

    record_payload = existing_record
    record_payload.update(
        {
            "paper_id": paper_id,
            "content_kind": content_kind,
            "addition_mode": "ref_manual_bibtex" if parsed_bibtex else "ref_guided",
            "library_root": str(ingest.LIBRARY_ROOT),
            "pdf_path": str((ingest.LIBRARY_ROOT / row["pdf_path"]).resolve()) if row["pdf_path"] else "",
            "pdf_sha256": row["pdf_sha256"],
            "filename": row["filename"],
            "item_type": item_type,
            "title_query": query,
            "date_indexed": now,
            "match_status": row["match_status"],
            "match_confidence": None,
            "metadata_source": "semantic_scholar" if selected_candidate else "manual_bibtex",
            "semantic_scholar": {
                "query": query,
                "limit": len(semantic_merged_json),
                "attempts": semantic_attempts_json,
                "selected_paper_id": metadata.get("semantic_scholar_paper_id", ""),
                "best_candidate": selected_candidate,
                "merged_candidates": semantic_merged_json,
            },
            "artifacts": {
                "record_path": str(record_path),
                "text_path": str((ingest.LIBRARY_ROOT / text_path).resolve()) if text_path else None,
                "chunk_path": str((ingest.LIBRARY_ROOT / chunk_path).resolve()) if chunk_path else None,
                "master_index": str(ingest.MASTER_INDEX),
                "master_bib": str(ingest.MASTER_BIB),
                "text_kind": text_kind,
            },
        }
    )
    if parsed_bibtex:
        record_payload["manual_override"] = {
            "raw_bibtex": parsed_bibtex["raw_bibtex"],
            "parsed_bibtex_type": parsed_bibtex["entry_type"],
            "source_note": "pasted into library_add.py ref flow",
            "bibtex_key": bibtex_key,
        }
    ingest.write_record_json(record_path, record_payload)
    eprint(f"Added reference record paper_id={paper_id} content_kind={content_kind}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Guided library addition for PDF-backed or reference-only records.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--semantic-limit", type=int, default=10, help="Semantic Scholar result limit per query (default: 10)")
    common.add_argument("--chunk-chars", type=int, default=2200, help="Chunk size in characters (default: 2200)")
    common.add_argument("--overlap-chars", type=int, default=250, help="Chunk overlap in characters (default: 250)")
    common.add_argument("--autonomous", action="store_true", help="Skip prompts and use deterministic best-candidate behavior")

    pdf_parser = subparsers.add_parser("pdf", parents=[common], help="Add a PDF-backed paper")
    pdf_parser.add_argument("pdf_path", type=Path, help="Absolute path to a PDF inside the library")

    ref_parser = subparsers.add_parser("ref", parents=[common], help="Add a reference-only paper")
    ref_parser.add_argument("--query", required=True, help="Paper title or keywords")
    return parser.parse_args()


def run_pdf_command(args: argparse.Namespace) -> int:
    pdf_path = args.pdf_path.expanduser().resolve()
    if args.autonomous:
        return ingest.ingest(
            pdf_path=pdf_path,
            force=False,
            chunk_chars=args.chunk_chars,
            overlap_chars=args.overlap_chars,
            semantic_limit=args.semantic_limit,
        )

    pdf_path = ingest.ensure_path_inside_library(pdf_path, "pdf")
    title_query = ingest.derive_title_query(pdf_path)
    eprint(f"PDF add: extracting text via {ingest.PDF2TEXT}")
    text_status, text_error, text_content = ingest.extract_pdf_text(pdf_path)
    if text_status == "ok" and text_content:
        eprint("PDF add: text extraction succeeded.")
    else:
        eprint(f"PDF add: text extraction failed: {text_error or 'no text extracted'}")
    text_title_candidates = ingest.derive_text_title_candidates(text_content) if text_status == "ok" and text_content else []
    initial_queries = [title_query] + text_title_candidates
    filename_hints = ingest.derive_filename_hints(pdf_path)
    doi_resolution = ingest.resolve_pdf_doi_metadata(
        pdf_path=pdf_path,
        extracted_text=text_content if text_status == "ok" else "",
        filename_hints=filename_hints,
        title_targets=initial_queries,
    )
    print_doi_resolution_summary(doi_resolution)

    selection: dict[str, Any]
    accepted_crossref = doi_resolution.get("accepted_metadata")
    if accepted_crossref:
        attempts, merged = ingest.collect_semantic_candidates(
            queries=dedupe_queries(initial_queries),
            filename=pdf_path.name,
            item_type=ingest.detect_item_type(pdf_path),
            filename_hints=filename_hints,
            semantic_limit=args.semantic_limit,
        )
        selected_candidate = ingest.choose_semantic_candidate_for_doi(
            merged,
            str(accepted_crossref.get("DOI") or ""),
            None,
        )
        metadata = metadata_from_crossref_resolution(accepted_crossref, selected_candidate)
        metadata_source = (
            "crossref_doi_plus_semantic_enrichment"
            if selected_candidate is not None
            else "crossref_doi_primary"
        )
        eprint(
            "PDF add: canonical metadata source="
            f"{metadata_source}"
            f" doi={accepted_crossref.get('DOI', '')}"
        )
        if selected_candidate is not None:
            eprint(
                "PDF add: Semantic Scholar enrichment matched paper_id="
                f"{ingest.semantic_candidate_to_metadata(selected_candidate).get('semantic_scholar_paper_id', '')}"
            )
        else:
            eprint("PDF add: no matching Semantic Scholar candidate found for enrichment; proceeding with DOI/Crossref metadata only.")
        selection = {
            "action": "doi_primary",
            "attempts": attempts,
            "merged": merged,
            "candidate": selected_candidate,
            "metadata": metadata,
            "metadata_source": metadata_source,
            "doi_resolution": doi_resolution,
        }
    else:
        eprint("PDF add: no trustworthy DOI/Crossref match accepted; falling back to Semantic Scholar candidate selection.")
        selection = search_loop(
            initial_queries=initial_queries,
            filename=pdf_path.name,
            item_type=ingest.detect_item_type(pdf_path),
            filename_hints=filename_hints,
            semantic_limit=args.semantic_limit,
            autonomous=False,
        )
    if selection["action"] == "skip":
        eprint("Skipped without changes.")
        return 0
    if selection["action"] == "cancel":
        eprint("Cancelled.")
        return 1

    metadata = selection["metadata"]
    parsed_bibtex = selection.get("parsed_bibtex")
    proposed_key = (parsed_bibtex or {}).get("bibtex_key", "")
    proposed_type = (parsed_bibtex or {}).get("entry_type", "") or ("book" if ingest.detect_item_type(pdf_path) == "book" else "article")
    if not confirm_metadata(metadata, proposed_type, proposed_key, ingest.CONTENT_KIND_PDF):
        eprint("Cancelled after metadata review.")
        return 1
    return finalize_pdf_addition(
        pdf_path=pdf_path,
        title_query=title_query,
        metadata=metadata,
        semantic_attempts=selection["attempts"],
        semantic_merged=selection["merged"],
        selected_candidate=selection.get("candidate"),
        parsed_bibtex=parsed_bibtex,
        chunk_chars=args.chunk_chars,
        overlap_chars=args.overlap_chars,
        addition_mode="pdf_guided",
        metadata_source_hint=selection.get("metadata_source", ""),
        doi_resolution_hint=selection.get("doi_resolution"),
    )


def run_ref_command(args: argparse.Namespace) -> int:
    selection = search_loop(
        initial_queries=[args.query],
        filename="reference",
        item_type="article",
        filename_hints={"year_hint": None, "venue_hint": "", "prefix": args.query},
        semantic_limit=args.semantic_limit,
        autonomous=args.autonomous,
    )
    if selection["action"] == "skip":
        eprint("Skipped without changes.")
        return 0
    if selection["action"] == "cancel":
        eprint("Cancelled.")
        return 1

    metadata = selection["metadata"]
    parsed_bibtex = selection.get("parsed_bibtex")
    proposed_key = (parsed_bibtex or {}).get("bibtex_key", "")
    proposed_type = (parsed_bibtex or {}).get("entry_type", "") or metadata.get("bibtex_type", "") or "article"
    if not args.autonomous and not confirm_metadata(metadata, proposed_type, proposed_key, ingest.CONTENT_KIND_REF):
        eprint("Cancelled after metadata review.")
        return 1
    return finalize_reference_addition(
        query=args.query,
        metadata=metadata,
        semantic_attempts=selection["attempts"],
        semantic_merged=selection["merged"],
        selected_candidate=selection.get("candidate"),
        parsed_bibtex=parsed_bibtex,
        chunk_chars=args.chunk_chars,
        overlap_chars=args.overlap_chars,
    )


def main() -> int:
    args = parse_args()
    if args.command == "pdf":
        return run_pdf_command(args)
    if args.command == "ref":
        return run_ref_command(args)
    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
