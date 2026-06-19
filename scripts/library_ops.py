#!/usr/bin/env python3
"""Shared record resolution and mutation operations for the literature library."""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import library_bib_sanity_check as bib_sanity
import library_ingest as ingest
import library_lookup as lookup
import library_rebuild_index_from_records as rebuild_index
import library_rebuild_master_bib as rebuild_bib


SCRIPT_DIR = Path(__file__).resolve().parent
LIBRARY_ROOT = SCRIPT_DIR.parent
INDEX_DIR = LIBRARY_ROOT / "paper_index"
LOGS_DIR = INDEX_DIR / "logs"
PROTECTED_ROOT_NAMES = {"paper_index", "scripts"}
NULLISH = {"", "none", "n/a", "na", "null"}


def clean_text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.casefold() in NULLISH else text


def resolve_pdf_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = LIBRARY_ROOT / raw_path
    return path.resolve()


def load_search_rows() -> list[dict[str, Any]]:
    return lookup.load_search_index_rows()


def build_query_args(
    *,
    limit: int = 10,
    author: str = "",
    year: str = "",
    venue: str = "",
    doi: str = "",
    has_pdf: bool = False,
    reference_only: bool = False,
    scope: str = "auto",
    no_abstract: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        author=author,
        year=year,
        venue=venue,
        doi=doi,
        has_pdf=has_pdf,
        reference_only=reference_only,
        scope=scope,
        no_abstract=no_abstract,
        limit=limit,
    )


def search_results(
    query: str,
    *,
    limit: int = 10,
    author: str = "",
    year: str = "",
    venue: str = "",
    doi: str = "",
    has_pdf: bool = False,
    reference_only: bool = False,
    scope: str = "auto",
    no_abstract: bool = False,
    exclude_paper_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    rows = load_search_rows()
    args = build_query_args(
        limit=limit,
        author=author,
        year=year,
        venue=venue,
        doi=doi,
        has_pdf=has_pdf,
        reference_only=reference_only,
        scope=scope,
        no_abstract=no_abstract,
    )
    results = lookup.combine_search_results(rows, args, query)
    if exclude_paper_ids:
        results = [result for result in results if str(result.get("row", {}).get("paper_id") or "") not in exclude_paper_ids]
    return results


def render_search_result_lines(result: dict[str, Any], index: int) -> list[str]:
    row = result.get("row", {})
    title = str(row.get("title") or row.get("resolved_title") or "").strip() or "n/a"
    venue = str(row.get("venue") or "").strip() or "n/a"
    year = str(row.get("year") or "").strip() or "n/a"
    bibtex_key = str(row.get("bibtex_key") or "").strip() or "(none)"
    pdf_path = str(row.get("pdf_path") or "").strip() or "(no pdf)"
    explanation = lookup.match_explanation(result)
    return [
        f"[{index}] {title}",
        f"    paper_id={row.get('paper_id', '')} bibtex_key={bibtex_key} year={year} venue={venue}",
        f"    pdf_path={pdf_path}",
        f"    match={explanation}",
    ]


def resolve_target_row(
    *,
    paper_id: str | None = None,
    bibtex_key: str | None = None,
    pdf_path: str | None = None,
    query: str | None = None,
    limit: int = 10,
    select_result: Callable[[list[dict[str, Any]]], dict[str, Any] | None] | None = None,
    exclude_paper_ids: set[str] | None = None,
) -> dict[str, Any]:
    rows = load_search_rows()
    if paper_id:
        row = lookup.exact_identifier_match(paper_id, rows)
        if row is None:
            row = ingest.load_master_index_row_by_paper_id(str(paper_id).strip())
        if row is None:
            raise SystemExit(f"No record found for paper_id '{paper_id}'")
        return row
    if bibtex_key:
        row = lookup.exact_identifier_match(bibtex_key, rows)
        if row is None:
            matches = [
                candidate
                for candidate in ingest.load_master_index_rows()
                if str(candidate.get("bibtex_key") or "").strip() == str(bibtex_key).strip()
            ]
            if len(matches) == 1:
                row = matches[0]
        if row is None:
            raise SystemExit(f"No record found for BibTeX key '{bibtex_key}'")
        return row
    if pdf_path:
        resolved = resolve_pdf_path(pdf_path)
        row = ingest.load_master_index_row_by_pdf_path(resolved)
        if row is None and resolved.exists():
            row = ingest.load_master_index_row_by_pdf_sha256(ingest.sha256_path(resolved))
        if row is None:
            raise SystemExit(f"No record found for PDF path '{pdf_path}'")
        search_row = lookup.exact_identifier_match(str(row.get("paper_id") or ""), rows)
        return search_row or row
    query_text = str(query or "").strip()
    if not query_text:
        raise SystemExit("One of paper_id, bibtex_key, pdf_path, or query is required.")
    results = search_results(query_text, limit=limit, exclude_paper_ids=exclude_paper_ids)
    if not results:
        raise SystemExit(f"No matches for '{query_text}'")
    if len(results) == 1:
        return results[0]["row"]
    if select_result is None:
        raise SystemExit(f"Query '{query_text}' matched multiple records. Refine the query or select interactively.")
    selected = select_result(results)
    if selected is None:
        raise SystemExit(1)
    return selected.get("row", selected)


def record_path_for_row(row: dict[str, Any]) -> Path:
    paper_id = str(row.get("paper_id") or "").strip()
    if not paper_id:
        raise SystemExit("Selected row is missing paper_id.")
    return ingest.record_path_for_paper_id(paper_id)


def load_record_payload(paper_id: str) -> dict[str, Any]:
    path = ingest.record_path_for_paper_id(paper_id)
    if not path.exists():
        raise SystemExit(f"Record JSON does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ingest.resolve_record_paths(payload, LIBRARY_ROOT)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Could not parse record JSON: {path}: {exc}") from exc


def load_optional_record_json(paper_id: str) -> dict[str, Any]:
    path = ingest.record_path_for_paper_id(paper_id)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ingest.resolve_record_paths(payload, LIBRARY_ROOT)
    except json.JSONDecodeError:
        return {}


def current_bibtex_text(row: dict[str, Any], record: dict[str, Any]) -> str:
    key = str(row.get("bibtex_key") or "").strip()
    if key:
        entries = lookup.load_master_bib_entries()
        parsed = entries.get(key)
        if parsed is not None:
            return str(parsed.get("raw_bibtex") or "").strip()
    manual = record.get("manual_override") or {}
    if isinstance(manual, dict):
        raw = str(manual.get("raw_bibtex") or "").strip()
        if raw:
            return raw
    return ""


def summary_lines_for_row(row: dict[str, Any]) -> list[str]:
    title = str(row.get("title") or row.get("resolved_title") or row.get("title_query") or "").strip() or "n/a"
    return [
        f"paper_id: {row.get('paper_id', '')}",
        f"bibtex_key: {row.get('bibtex_key', '') or '(none)'}",
        f"title: {title}",
        f"year: {row.get('year', '') or 'n/a'}",
        f"venue: {row.get('venue', '') or 'n/a'}",
        f"content_kind: {row.get('content_kind', '') or 'n/a'}",
        f"pdf_path: {row.get('pdf_path', '') or '(no pdf)'}",
    ]


def update_record_manual_override_from_parsed(
    record: dict[str, Any],
    parsed: dict[str, Any],
    *,
    source_note: str,
) -> dict[str, Any]:
    normalized = parsed.get("normalized") or {}
    manual = record.get("manual_override") or {}
    if not isinstance(manual, dict):
        manual = {}
    previous_note = str(manual.get("source_note") or "").strip()
    combined_note = source_note if not previous_note else previous_note + " | " + source_note
    updated = dict(record)
    updated["manual_override"] = {
        "raw_bibtex": parsed.get("raw_bibtex", ""),
        "parsed_bibtex_type": parsed.get("entry_type", ""),
        "bibtex_key": normalized.get("bibtex_key", "") or parsed.get("bibtex_key", ""),
        "title": normalized.get("title", ""),
        "authors": normalized.get("authors", ""),
        "year": normalized.get("year", ""),
        "venue": normalized.get("venue", ""),
        "doi": normalized.get("doi", ""),
        "url": normalized.get("url", ""),
        "abstract": normalized.get("abstract", ""),
        "source_note": combined_note,
        "source_urls": list(manual.get("source_urls") or []),
    }
    updated["metadata_source"] = "manual_bibtex"
    return updated


def canonicalize_parsed_bibtex(parsed: dict[str, Any]) -> dict[str, Any]:
    return ingest.canonicalize_bibtex_entry(parsed)


def validate_raw_bibtex(record: dict[str, Any]) -> None:
    manual = record.get("manual_override") or {}
    if not isinstance(manual, dict):
        return
    raw_bibtex = str(manual.get("raw_bibtex") or "").strip()
    if not raw_bibtex:
        return
    try:
        ingest.parse_bibtex_entry(raw_bibtex)
    except Exception as exc:
        raise SystemExit(f"manual_override.raw_bibtex is not parseable: {exc}") from exc


def rebuild_single_index_row(record: dict[str, Any], existing_rows_by_paper_id: dict[str, dict[str, str]]) -> dict[str, str]:
    bib_entries = rebuild_index.load_master_bib_entries()
    bib_lookups = rebuild_index.build_master_bib_lookups(bib_entries)
    return rebuild_index.build_row(record, bib_lookups, existing_rows_by_paper_id)


def validate_record_payload(record: dict[str, Any], paper_id: str) -> None:
    record_paper_id = str(record.get("paper_id") or "").strip()
    if record_paper_id != paper_id:
        raise SystemExit(f"Record JSON paper_id mismatch: expected '{paper_id}', found '{record_paper_id or '(blank)'}'")

    content_kind = str(record.get("content_kind") or "").strip()
    if content_kind and content_kind not in {ingest.CONTENT_KIND_PDF, ingest.CONTENT_KIND_REF}:
        raise SystemExit(f"Invalid content_kind '{content_kind}' in record JSON")

    validate_raw_bibtex(record)

    if content_kind == ingest.CONTENT_KIND_PDF or record.get("pdf_path"):
        pdf_path_value = str(record.get("pdf_path") or "").strip()
        if not pdf_path_value:
            raise SystemExit("PDF-backed record is missing pdf_path in record JSON")
        pdf_path = Path(pdf_path_value)
        if not pdf_path.exists():
            raise SystemExit(f"PDF referenced by record JSON does not exist: {pdf_path}")
        filename = str(record.get("filename") or "").strip()
        if filename and pdf_path.name != filename:
            raise SystemExit(
                f"Record JSON filename mismatch: filename='{filename}' but pdf_path basename='{pdf_path.name}'"
            )
        expected_sha = str(record.get("pdf_sha256") or "").strip()
        if expected_sha:
            actual_sha = ingest.sha256_path(pdf_path)
            if actual_sha != expected_sha:
                raise SystemExit(
                    f"PDF sha256 mismatch for {pdf_path}: record has {expected_sha}, actual file is {actual_sha}"
                )


def validate_refreshed_row(refreshed_row: dict[str, str], old_row: dict[str, str], allow_lossy: bool) -> None:
    content_kind = str(refreshed_row.get("content_kind") or "").strip()
    if content_kind not in {ingest.CONTENT_KIND_PDF, ingest.CONTENT_KIND_REF}:
        raise SystemExit(f"Refreshed row has invalid content_kind '{content_kind or '(blank)'}'")
    if not str(refreshed_row.get("paper_id") or "").strip():
        raise SystemExit("Refreshed row is missing paper_id")
    if content_kind == ingest.CONTENT_KIND_PDF:
        for field in ["pdf_path", "filename", "text_path", "chunk_path"]:
            if not str(refreshed_row.get(field) or "").strip():
                raise SystemExit(f"Refreshed PDF-backed row is missing required field '{field}'")
        pdf_path = LIBRARY_ROOT / str(refreshed_row.get("pdf_path") or "")
        if not pdf_path.exists():
            raise SystemExit(f"Refreshed row points to missing PDF: {pdf_path}")
    if not allow_lossy:
        protected_fields = [
            "resolved_title",
            "authors",
            "year",
            "venue",
            "doi",
            "bibtex_key",
            "canonical_url",
        ]
        losses = [
            field
            for field in protected_fields
            if str(old_row.get(field) or "").strip() and not str(refreshed_row.get(field) or "").strip()
        ]
        if losses:
            raise SystemExit(
                "Refresh would clear existing metadata fields: "
                + ", ".join(losses)
                + ". Fix the JSON record or rerun with --allow-lossy if intentional."
            )


def refresh_master_bib_entry(bibtex_key: str) -> None:
    key = str(bibtex_key or "").strip()
    if not key:
        return
    existing_entries = rebuild_bib.load_existing_entries()
    rows = [row for row in ingest.load_master_index_rows() if str(row.get("bibtex_key") or "").strip() == key]
    if not rows:
        ingest.remove_master_bib_entry(key)
        return
    rows_with_records: list[tuple[dict[str, str], dict[str, Any]]] = []
    for row in rows:
        record = load_record_payload(str(row.get("paper_id") or ""))
        rows_with_records.append((row, record))
    representative_row, representative_record = rebuild_bib.choose_representative(rows_with_records)
    entry_type, original_fields = rebuild_bib.original_fields_for_key(
        existing_entries,
        representative_row,
        representative_record,
    )
    normalized_fields = rebuild_bib.normalized_fields_from_row(representative_row, representative_record)
    bibtex_entry = ingest.build_bibtex_entry_with_preserved_fields(
        entry_type=entry_type,
        bibtex_key=key,
        original_fields=original_fields,
        normalized_fields=normalized_fields,
    )
    try:
        ingest.parse_bibtex_entry(bibtex_entry)
    except Exception as exc:
        raise SystemExit(f"Generated BibTeX entry for '{key}' is invalid: {exc}") from exc
    ingest.upsert_master_bib(str(representative_row.get("paper_id") or ""), bibtex_entry)


def validate_master_bib_integrity() -> None:
    if not ingest.MASTER_BIB.exists():
        return
    text = ingest.MASTER_BIB.read_text(encoding="utf-8")
    seen: set[str] = set()
    for index, block in enumerate(bib_sanity.split_bibtex_blocks(text), start=1):
        raw = block if block.startswith("@") else "@" + block
        for match in bib_sanity.HEADER_RE.finditer(raw[1:]):
            context = raw[max(0, match.start() - 40): match.start() + 80].replace("\n", "\\n")
            raise SystemExit(f"master.bib contains an embedded BibTeX header in entry {index}: {context}")
        try:
            parsed = ingest.parse_bibtex_entry(raw)
        except Exception as exc:
            raise SystemExit(f"master.bib contains an invalid entry at position {index}: {exc}") from exc
        key = parsed["bibtex_key"]
        if key in seen:
            raise SystemExit(f"master.bib contains duplicate BibTeX key '{key}'")
        seen.add(key)


def refresh_record_from_row(row: dict[str, Any], *, allow_lossy: bool = False) -> dict[str, Any]:
    paper_id = str(row.get("paper_id") or "").strip()
    if not paper_id:
        raise SystemExit("Resolved row does not contain a paper_id.")
    record = load_record_payload(paper_id)
    validate_record_payload(record, paper_id)
    existing_rows_by_paper_id = {item.get("paper_id", ""): item for item in ingest.load_master_index_rows()}
    old_key = str(row.get("bibtex_key") or "").strip()
    refreshed_row = rebuild_single_index_row(record, existing_rows_by_paper_id)
    validate_refreshed_row(refreshed_row, row, allow_lossy=allow_lossy)
    ingest.upsert_master_index(refreshed_row)
    persisted = ingest.load_master_index_row_by_paper_id(paper_id)
    if persisted is None:
        raise SystemExit(f"Post-refresh validation failed: row for paper_id '{paper_id}' is missing from master_index.tsv")
    new_key = str(refreshed_row.get("bibtex_key") or "").strip()
    if old_key and old_key != new_key:
        if not any(
            str(item.get("bibtex_key") or "").strip() == old_key
            for item in ingest.load_master_index_rows()
            if item.get("paper_id") != paper_id
        ):
            ingest.remove_master_bib_entry(old_key)
    if new_key:
        refresh_master_bib_entry(new_key)
    validate_master_bib_integrity()
    return {
        "action": "refresh-record",
        "paper_id": paper_id,
        "bibtex_key": new_key,
        "record_path": str(ingest.record_path_for_paper_id(paper_id)),
        "master_index_path": str(ingest.MASTER_INDEX),
        "master_bib_path": str(ingest.MASTER_BIB),
        "row": refreshed_row,
        "summary_lines": [
            f"paper_id={paper_id}",
            f"bibtex_key={new_key or '(none)'}",
            f"record={ingest.record_path_for_paper_id(paper_id)}",
            f"master_index={ingest.MASTER_INDEX}",
            f"master_bib={ingest.MASTER_BIB}",
        ],
    }


def replace_bibtex_for_row(
    row: dict[str, Any],
    raw_bibtex: str,
    *,
    allow_lossy: bool = False,
    source_note: str = "replaced via library_edit.py",
) -> dict[str, Any]:
    try:
        parsed = ingest.parse_bibtex_entry(raw_bibtex)
    except Exception as exc:
        raise SystemExit(f"Could not parse replacement BibTeX entry: {exc}") from exc
    canonical = canonicalize_parsed_bibtex(parsed)
    paper_id = str(row.get("paper_id") or "").strip()
    record = load_record_payload(paper_id)
    updated_record = update_record_manual_override_from_parsed(record, canonical, source_note=source_note)
    ingest.write_record_json(ingest.record_path_for_paper_id(paper_id), updated_record)
    refreshed = refresh_record_from_row(
        ingest.load_master_index_row_by_paper_id(paper_id) or row,
        allow_lossy=allow_lossy,
    )
    refreshed["parsed_bibtex"] = canonical
    return refreshed


def write_log(action: str, payload: dict[str, Any]) -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    path = LOGS_DIR / f"{action}_{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def master_index_rows() -> list[dict[str, str]]:
    return ingest.load_master_index_rows()


def write_master_index_rows(rows: list[dict[str, str]]) -> None:
    with ingest.FileLock(ingest.MASTER_INDEX_LOCK):
        ingest.write_master_index_rows(rows)


def remove_master_index_row(paper_id: str) -> None:
    rows = [row for row in master_index_rows() if clean_text(row.get("paper_id")) != paper_id]
    write_master_index_rows(rows)


def replace_master_index_row(updated_row: dict[str, str]) -> None:
    rows = master_index_rows()
    replaced = False
    for index, row in enumerate(rows):
        if clean_text(row.get("paper_id")) == clean_text(updated_row.get("paper_id")):
            rows[index] = ingest.master_index_row_defaults(updated_row)
            replaced = True
            break
    if not replaced:
        rows.append(ingest.master_index_row_defaults(updated_row))
    write_master_index_rows(rows)


def remaining_bibtex_key_usage(excluding_paper_ids: set[str] | None = None) -> dict[str, int]:
    excluding_paper_ids = excluding_paper_ids or set()
    counts: dict[str, int] = {}
    for row in master_index_rows():
        paper_id = clean_text(row.get("paper_id"))
        if paper_id in excluding_paper_ids:
            continue
        key = clean_text(row.get("bibtex_key"))
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


def is_allowed_pdf_destination(path: Path) -> bool:
    try:
        rel = path.resolve().relative_to(LIBRARY_ROOT)
    except ValueError:
        return False
    if not rel.parts:
        return False
    return rel.parts[0] not in PROTECTED_ROOT_NAMES


def resolve_destination_path(raw_destination: str, current_filename: str) -> Path:
    destination = Path(raw_destination).expanduser()
    if not destination.is_absolute():
        destination = (LIBRARY_ROOT / destination).resolve()
    else:
        destination = destination.resolve()
    if destination.exists() and destination.is_dir():
        final_path = destination / current_filename
    elif destination.suffix.lower() == ".pdf":
        final_path = destination
    else:
        final_path = destination / current_filename
    if not is_allowed_pdf_destination(final_path):
        raise SystemExit(f"Destination must stay inside the library but outside protected roots: {final_path}")
    return final_path


def identity_signals(keep_row: dict[str, str], drop_row: dict[str, str]) -> list[str]:
    labels: list[str] = []
    keep_doi = ingest.normalize_doi(keep_row.get("doi", ""))
    drop_doi = ingest.normalize_doi(drop_row.get("doi", ""))
    if keep_doi and keep_doi == drop_doi:
        labels.append("same_doi")
    keep_bib = clean_text(keep_row.get("bibtex_key")).casefold()
    drop_bib = clean_text(drop_row.get("bibtex_key")).casefold()
    if keep_bib and keep_bib == drop_bib:
        labels.append("same_bibtex_key")
    keep_title_year = (
        ingest.normalize_title(keep_row.get("resolved_title") or keep_row.get("title_query") or ""),
        clean_text(keep_row.get("year")),
    )
    drop_title_year = (
        ingest.normalize_title(drop_row.get("resolved_title") or drop_row.get("title_query") or ""),
        clean_text(drop_row.get("year")),
    )
    if keep_title_year[0] and keep_title_year == drop_title_year:
        labels.append("same_title_year")
    keep_sha = clean_text(keep_row.get("pdf_sha256"))
    drop_sha = clean_text(drop_row.get("pdf_sha256"))
    if keep_sha and keep_sha == drop_sha:
        labels.append("same_pdf_sha256")
    return labels


def supplement_like(row: dict[str, str]) -> bool:
    text = " ".join(
        clean_text(value)
        for value in [
            row.get("filename"),
            row.get("pdf_path"),
            row.get("resolved_title"),
            row.get("title_query"),
            row.get("notes"),
            row.get("match_status"),
        ]
        if clean_text(value)
    ).casefold()
    return any(
        token in text
        for token in [
            " - si",
            "_si",
            " - sm",
            "_sm",
            "supplement",
            "supporting information",
            "supporting material",
            "matched_supplement",
        ]
    )


def merge_row_fields(keep_row: dict[str, str], drop_row: dict[str, str]) -> dict[str, str]:
    merged = ingest.master_index_row_defaults(dict(keep_row))
    for field in [
        "resolved_title",
        "authors",
        "year",
        "venue",
        "doi",
        "semantic_scholar_paper_id",
        "match_confidence",
        "match_status",
        "bibtex_type",
        "canonical_url",
        "bibtex_key",
        "abstract_source",
        "notes",
    ]:
        if not clean_text(merged.get(field)) and clean_text(drop_row.get(field)):
            merged[field] = clean_text(drop_row.get(field))
    return merged


def append_maintenance_event(record: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    maintenance = record.get("maintenance")
    if not isinstance(maintenance, list):
        maintenance = []
    maintenance.append(event)
    record["maintenance"] = maintenance
    return record


def merge_record_payloads(keep_record: dict[str, Any], drop_record: dict[str, Any]) -> dict[str, Any]:
    merged = dict(keep_record)
    for field in ["title_query", "match_status", "match_confidence", "metadata_source", "item_type"]:
        if not clean_text(merged.get(field)) and clean_text(drop_record.get(field)):
            merged[field] = drop_record.get(field)
    if (not isinstance(merged.get("manual_override"), dict) or not clean_text((merged.get("manual_override") or {}).get("raw_bibtex"))) and isinstance(drop_record.get("manual_override"), dict):
        merged["manual_override"] = dict(drop_record.get("manual_override") or {})
    if (not isinstance(merged.get("semantic_scholar"), dict) or not merged.get("semantic_scholar")) and isinstance(drop_record.get("semantic_scholar"), dict):
        merged["semantic_scholar"] = dict(drop_record.get("semantic_scholar") or {})
    if (not isinstance(merged.get("crossref"), dict) or not merged.get("crossref")) and isinstance(drop_record.get("crossref"), dict):
        merged["crossref"] = dict(drop_record.get("crossref") or {})
    if not isinstance(merged.get("warnings"), list) and isinstance(drop_record.get("warnings"), list):
        merged["warnings"] = list(drop_record.get("warnings") or [])
    if not isinstance(merged.get("crossref_warnings"), list) and isinstance(drop_record.get("crossref_warnings"), list):
        merged["crossref_warnings"] = list(drop_record.get("crossref_warnings") or [])
    return merged


def move_record(row: dict[str, str], destination: Path, *, apply: bool) -> dict[str, Any]:
    current_pdf_rel = clean_text(row.get("pdf_path"))
    if clean_text(row.get("content_kind")) != ingest.CONTENT_KIND_PDF or not current_pdf_rel:
        raise SystemExit("Move is only supported for pdf_backed records with a tracked pdf_path.")
    current_path = (LIBRARY_ROOT / current_pdf_rel).resolve()
    if not current_path.exists():
        raise SystemExit(f"Current PDF does not exist on disk: {current_path}")
    if destination.exists():
        raise SystemExit(f"Destination already exists: {destination}")
    new_rel = ingest.relative_to_library(destination)
    updated_row = ingest.master_index_row_defaults(dict(row))
    updated_row["pdf_path"] = new_rel
    updated_row["filename"] = destination.name
    record = load_optional_record_json(clean_text(row.get("paper_id")))
    updated_record = dict(record)
    updated_record["pdf_path"] = str(destination)
    updated_record["filename"] = destination.name
    append_maintenance_event(
        updated_record,
        {
            "action": "move",
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "from_pdf_path": str(current_path),
            "to_pdf_path": str(destination),
        },
    )
    report = {
        "action": "move",
        "apply": apply,
        "paper_id": clean_text(row.get("paper_id")),
        "status": "planned",
        "summary_lines": [
            f"paper_id={clean_text(row.get('paper_id'))}",
            f"from={current_path}",
            f"to={destination}",
            f"updates={ingest.record_path_for_paper_id(clean_text(row.get('paper_id')))}, {ingest.MASTER_INDEX}",
        ],
        "warnings": [],
    }
    if apply:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(current_path), str(destination))
        ingest.write_record_json(ingest.record_path_for_paper_id(clean_text(row.get("paper_id"))), updated_record)
        refreshed = refresh_record_from_row(updated_row)
        persisted = refreshed["row"]
        if clean_text(persisted.get("pdf_path")) != new_rel:
            raise SystemExit("Post-move validation failed: master_index.tsv was not updated with the new pdf_path")
        log_path = write_log("move_record", report | {"before_row": row, "after_row": persisted})
        report["status"] = "applied"
        report["log_path"] = str(log_path)
        report["summary_lines"] = refreshed["summary_lines"]
    return report


def delete_record(row: dict[str, str], *, apply: bool, delete_pdf: bool) -> dict[str, Any]:
    paper_id = clean_text(row.get("paper_id"))
    bibtex_key = clean_text(row.get("bibtex_key"))
    pdf_rel = clean_text(row.get("pdf_path"))
    pdf_path = (LIBRARY_ROOT / pdf_rel).resolve() if pdf_rel else None
    record_path = ingest.record_path_for_paper_id(paper_id)
    text_path = ingest.text_path_for_paper_id(paper_id)
    chunk_path = ingest.chunk_path_for_paper_id(paper_id)
    warnings: list[str] = []
    if pdf_path and pdf_path.exists() and not delete_pdf:
        warnings.append("PDF file will remain in the library as an unindexed file unless you also pass --delete-pdf.")
    report = {
        "action": "delete",
        "apply": apply,
        "paper_id": paper_id,
        "status": "planned",
        "summary_lines": [
            f"paper_id={paper_id}",
            f"content_kind={clean_text(row.get('content_kind'))}",
            f"delete_pdf={delete_pdf}",
            f"updates={record_path}, {text_path}, {chunk_path}, {ingest.MASTER_INDEX}, {ingest.MASTER_BIB}",
        ],
        "warnings": warnings,
    }
    if apply:
        pdf_deleted = False
        remove_master_index_row(paper_id)
        remaining_usage = remaining_bibtex_key_usage(excluding_paper_ids={paper_id})
        if bibtex_key and remaining_usage.get(bibtex_key, 0) == 0:
            ingest.remove_master_bib_entry(bibtex_key)
        if record_path.exists():
            record_path.unlink()
        if text_path.exists():
            text_path.unlink()
        if chunk_path.exists():
            chunk_path.unlink()
        if delete_pdf and pdf_path and pdf_path.exists():
            pdf_path.unlink()
            pdf_deleted = True
        if ingest.load_master_index_row_by_paper_id(paper_id) is not None:
            raise SystemExit(f"Post-delete validation failed: paper_id '{paper_id}' is still present in master_index.tsv")
        validate_master_bib_integrity()
        log_path = write_log(
            "delete_record",
            {
                "action": "delete",
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                "paper_id": paper_id,
                "deleted_pdf": pdf_deleted,
                "row": row,
            },
        )
        report["status"] = "applied"
        report["log_path"] = str(log_path)
    return report


def dedup_records(
    keep_row: dict[str, str],
    drop_row: dict[str, str],
    *,
    apply: bool,
    delete_drop_pdf: bool,
    force: bool,
) -> dict[str, Any]:
    keep_id = clean_text(keep_row.get("paper_id"))
    drop_id = clean_text(drop_row.get("paper_id"))
    if keep_id == drop_id:
        raise SystemExit("Keep and drop paper_id must be different.")
    signals = identity_signals(keep_row, drop_row)
    warnings: list[str] = []
    if not signals and not force:
        raise SystemExit(
            "Refusing dedup: the two rows do not share DOI, BibTeX key, title/year, or PDF hash. "
            "Use --force only after manual review."
        )
    if supplement_like(drop_row) and not supplement_like(keep_row) and not force:
        raise SystemExit("Refusing dedup: drop candidate appears supplement-like. Use --force after manual review.")
    if clean_text(drop_row.get("content_kind")) == ingest.CONTENT_KIND_PDF and clean_text(drop_row.get("pdf_path")) and not delete_drop_pdf:
        warnings.append("Drop PDF will remain on disk as an unindexed file unless you pass --delete-drop-pdf.")
    merged_keep_row = merge_row_fields(keep_row, drop_row)
    keep_record = load_optional_record_json(keep_id)
    drop_record = load_optional_record_json(drop_id)
    updated_keep_record = merge_record_payloads(keep_record, drop_record)
    append_maintenance_event(
        updated_keep_record,
        {
            "action": "dedup_keep",
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "merged_from_paper_id": drop_id,
            "identity_signals": signals,
            "deleted_drop_pdf": bool(delete_drop_pdf),
        },
    )
    report = {
        "action": "dedup",
        "apply": apply,
        "paper_id_keep": keep_id,
        "paper_id_drop": drop_id,
        "status": "planned",
        "summary_lines": [
            f"keep={keep_id}",
            f"drop={drop_id}",
            f"signals={', '.join(signals) or 'none'}",
            f"delete_drop_pdf={delete_drop_pdf}",
            f"updates={ingest.record_path_for_paper_id(keep_id)}, {ingest.MASTER_INDEX}, {ingest.MASTER_BIB}",
        ],
        "warnings": warnings,
    }
    if apply:
        remove_master_index_row(drop_id)
        keep_bib = clean_text(merged_keep_row.get("bibtex_key"))
        drop_bib = clean_text(drop_row.get("bibtex_key"))
        remaining_usage = remaining_bibtex_key_usage(excluding_paper_ids={drop_id})
        if drop_bib and drop_bib != keep_bib and remaining_usage.get(drop_bib, 0) == 0:
            ingest.remove_master_bib_entry(drop_bib)
        drop_record_path = ingest.record_path_for_paper_id(drop_id)
        drop_text_path = ingest.text_path_for_paper_id(drop_id)
        drop_chunk_path = ingest.chunk_path_for_paper_id(drop_id)
        drop_pdf_path = None
        if clean_text(drop_row.get("pdf_path")):
            drop_pdf_path = (LIBRARY_ROOT / clean_text(drop_row.get("pdf_path"))).resolve()
        ingest.write_record_json(ingest.record_path_for_paper_id(keep_id), updated_keep_record)
        if drop_record_path.exists():
            drop_record_path.unlink()
        if drop_text_path.exists():
            drop_text_path.unlink()
        if drop_chunk_path.exists():
            drop_chunk_path.unlink()
        if delete_drop_pdf and drop_pdf_path and drop_pdf_path.exists():
            drop_pdf_path.unlink()
        refreshed = refresh_record_from_row(merged_keep_row)
        if ingest.load_master_index_row_by_paper_id(drop_id) is not None:
            raise SystemExit(f"Post-dedup validation failed: dropped paper_id '{drop_id}' is still present")
        if ingest.load_master_index_row_by_paper_id(keep_id) is None:
            raise SystemExit(f"Post-dedup validation failed: keep paper_id '{keep_id}' is missing")
        log_path = write_log(
            "dedup_record",
            {
                "action": "dedup",
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                "keep_before": keep_row,
                "keep_after": refreshed["row"],
                "drop_row": drop_row,
                "identity_signals": signals,
                "deleted_drop_pdf": bool(delete_drop_pdf),
                "drop_record": drop_record,
            },
        )
        report["status"] = "applied"
        report["log_path"] = str(log_path)
        report["summary_lines"] = refreshed["summary_lines"]
    return report
