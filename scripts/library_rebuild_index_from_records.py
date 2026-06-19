#!/usr/bin/env python3
"""Rebuild master_index.tsv from per-paper record JSON files."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import library_ingest as ingest


def relative_to_library(raw_path: str | None) -> str:
    if not raw_path:
        return ""
    path = Path(raw_path)
    if path.is_absolute():
        try:
            return str(path.resolve().relative_to(ingest.LIBRARY_ROOT))
        except Exception:
            return str(path)
    return str(path)


def pick_semantic_field(record: dict, name: str) -> str:
    candidate = ((record.get("semantic_scholar") or {}).get("best_candidate") or {})
    if name == "title":
        return str(candidate.get("title") or "")
    if name == "authors":
        authors = candidate.get("authors") or []
        return ", ".join(author.get("name", "") for author in authors if isinstance(author, dict))
    if name == "year":
        return str(candidate.get("year") or "")
    if name == "venue":
        if isinstance(candidate.get("journal"), dict):
            return ingest.normalize_venue_name(str(candidate["journal"].get("name") or candidate.get("venue") or ""))
        return ingest.normalize_venue_name(str(candidate.get("venue") or ""))
    if name == "doi":
        external_ids = candidate.get("externalIds") or {}
        return str(external_ids.get("DOI") or external_ids.get("doi") or "")
    if name == "paper_id":
        return str(candidate.get("paperId") or "")
    return ""


def manual_override_metadata(record: dict) -> dict[str, str]:
    manual = record.get("manual_override") or {}
    if not isinstance(manual, dict):
        return {}

    metadata = {
        "title": str(manual.get("title") or ""),
        "authors": str(manual.get("authors") or ""),
        "year": str(manual.get("year") or ""),
        "venue": ingest.normalize_venue_name(str(manual.get("venue") or "")),
        "doi": str(manual.get("doi") or ""),
        "url": str(manual.get("url") or ""),
        "abstract": str(manual.get("abstract") or ""),
        "bibtex_key": str(manual.get("bibtex_key") or ""),
        "bibtex_type": str(manual.get("parsed_bibtex_type") or ""),
    }

    raw_bibtex = str(manual.get("raw_bibtex") or "").strip()
    if raw_bibtex:
        try:
            parsed = ingest.parse_bibtex_entry(raw_bibtex)
            normalized = parsed.get("normalized") or {}
            metadata["title"] = metadata["title"] or str(normalized.get("title") or "")
            metadata["authors"] = metadata["authors"] or str(normalized.get("authors") or "")
            metadata["year"] = metadata["year"] or str(normalized.get("year") or "")
            metadata["venue"] = metadata["venue"] or ingest.normalize_venue_name(str(normalized.get("venue") or ""))
            metadata["doi"] = metadata["doi"] or str(normalized.get("doi") or "")
            metadata["url"] = metadata["url"] or str(normalized.get("url") or "")
            metadata["abstract"] = metadata["abstract"] or str(normalized.get("abstract") or "")
            metadata["bibtex_key"] = metadata["bibtex_key"] or str(parsed.get("bibtex_key") or "")
            metadata["bibtex_type"] = metadata["bibtex_type"] or str(parsed.get("entry_type") or "")
        except Exception:
            pass

    return metadata


HEADER_RE = re.compile(r"@(?P<entry_type>[A-Za-z0-9_:-]+)\s*\{\s*(?P<key>[^,\s]+)\s*,")

def split_bibtex_blocks(text: str) -> list[str]:
    matches = list(HEADER_RE.finditer(text))
    blocks: list[str] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        if block:
            blocks.append(block)
    return blocks


def loose_parse_bibtex_chunk(chunk: str) -> dict | None:
    header_match = HEADER_RE.match(chunk.strip())
    if not header_match:
        return None
    try:
        return ingest.parse_bibtex_entry(chunk)
    except Exception:
        def extract(name: str) -> str:
            match = re.search(
                rf"^\s*{re.escape(name)}\s*=\s*(\{{.*?\}}|\".*?\")\s*,?\s*$",
                chunk,
                re.IGNORECASE | re.MULTILINE | re.DOTALL,
            )
            if not match:
                return ""
            return ingest._bibtex_unwrap_value(match.group(1))

        entry_type = header_match.group("entry_type").strip().lower()
        bibtex_key = header_match.group("key").strip()
        fields = {
            "title": extract("title"),
            "author": extract("author"),
            "year": extract("year"),
            "journal": extract("journal"),
            "booktitle": extract("booktitle"),
            "publisher": extract("publisher"),
            "school": extract("school"),
            "institution": extract("institution"),
            "doi": extract("doi"),
            "url": extract("url"),
            "abstract": extract("abstract"),
        }
        normalized = ingest.normalize_metadata_fields(
            {
                "title": fields.get("title", ""),
                "authors": fields.get("author", ""),
                "year": fields.get("year", ""),
                "venue": fields.get("journal", "") or fields.get("booktitle", "") or fields.get("publisher", "") or fields.get("school", "") or fields.get("institution", ""),
                "doi": fields.get("doi", ""),
                "url": fields.get("url", ""),
                "abstract": fields.get("abstract", ""),
                "bibtex_key": bibtex_key,
                "bibtex_type": entry_type,
            }
        )
        return {
            "entry_type": entry_type,
            "bibtex_key": bibtex_key,
            "fields": {name: value for name, value in fields.items() if value},
            "normalized": normalized,
            "raw_bibtex": chunk.strip(),
        }


def load_master_bib_entries() -> list[dict]:
    if not ingest.MASTER_BIB.exists():
        return []
    entries: list[dict] = []
    text = ingest.MASTER_BIB.read_text(encoding="utf-8")
    for block in split_bibtex_blocks(text):
        parsed = loose_parse_bibtex_chunk(block)
        if parsed is not None:
            entries.append(parsed)
    return entries


def build_master_bib_lookups(entries: list[dict]) -> dict[str, dict]:
    doi_lookup: dict[str, list[dict]] = {}
    title_year_lookup: dict[tuple[str, str], list[dict]] = {}
    title_lookup: dict[str, list[dict]] = {}
    key_lookup: dict[str, dict] = {}

    for entry in entries:
        key = str(entry.get("bibtex_key") or "").strip()
        if key:
            key_lookup[key] = entry

        normalized = entry.get("normalized") or {}
        doi = ingest.normalize_doi(normalized.get("doi", ""))
        if doi:
            doi_lookup.setdefault(doi, []).append(entry)

        title_norm = ingest.normalize_title(normalized.get("title", ""))
        year = str(normalized.get("year", "") or "").strip()
        if title_norm:
            title_lookup.setdefault(title_norm, []).append(entry)
        if title_norm and year:
            title_year_lookup.setdefault((title_norm, year), []).append(entry)

    return {
        "doi": doi_lookup,
        "title_year": title_year_lookup,
        "title": title_lookup,
        "key": key_lookup,
    }


def authors_overlap(lhs: str, rhs: str) -> bool:
    lhs_tokens = {token for token in ingest.informative_tokens(lhs) if len(token) >= 3}
    rhs_tokens = {token for token in ingest.informative_tokens(rhs) if len(token) >= 3}
    if not lhs_tokens or not rhs_tokens:
        return False
    return bool(lhs_tokens & rhs_tokens)


def choose_bibtex_entry(
    *,
    bib_lookups: dict[str, dict],
    existing_key: str,
    title: str,
    authors: str,
    year: str,
    doi: str,
) -> dict | None:
    existing_key = str(existing_key or "").strip()
    if existing_key:
        existing = bib_lookups["key"].get(existing_key)
        if existing is not None:
            return existing

    normalized_doi = ingest.normalize_doi(doi)
    if normalized_doi:
        matches = bib_lookups["doi"].get(normalized_doi, [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            title_norm = ingest.normalize_title(title)
            year_text = str(year or "").strip()
            filtered = [
                entry
                for entry in matches
                if ingest.normalize_title((entry.get("normalized") or {}).get("title", "")) == title_norm
                and str((entry.get("normalized") or {}).get("year", "") or "").strip() == year_text
            ]
            if len(filtered) == 1:
                return filtered[0]

    title_norm = ingest.normalize_title(title)
    year_text = str(year or "").strip()
    if title_norm and year_text:
        matches = bib_lookups["title_year"].get((title_norm, year_text), [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            filtered = [
                entry
                for entry in matches
                if authors_overlap(authors, (entry.get("normalized") or {}).get("authors", ""))
            ]
            if len(filtered) == 1:
                return filtered[0]

    if title_norm:
        matches = bib_lookups["title"].get(title_norm, [])
        if len(matches) == 1:
            return matches[0]
    return None


def build_row(record: dict, bib_lookups: dict[str, dict], existing_rows_by_paper_id: dict[str, dict[str, str]]) -> dict[str, str]:
    manual = record.get("manual_override") or {}
    manual_meta = manual_override_metadata(record)
    crossref_meta = ((record.get("crossref") or {}).get("metadata") or {}) if ((record.get("crossref") or {}).get("accepted") or record.get("match_status") == "matched_via_doi") else {}
    supplement_parent = record.get("supplement_parent") or {}
    content_kind = str(record.get("content_kind") or "").strip() or (
        ingest.CONTENT_KIND_PDF if record.get("pdf_path") else ingest.CONTENT_KIND_REF
    )

    resolved_title = ""
    authors = ""
    year = ""
    venue = ""
    doi = ""
    semantic_scholar_paper_id = pick_semantic_field(record, "paper_id")

    if manual:
        resolved_title = manual_meta.get("title", "")
        authors = manual_meta.get("authors", "")
        year = manual_meta.get("year", "")
        venue = manual_meta.get("venue", "")
        doi = manual_meta.get("doi", "")
    elif crossref_meta:
        resolved_title = ingest.crossref_title(crossref_meta)
        authors = ingest.crossref_authors(crossref_meta)
        year = ingest.crossref_year(crossref_meta)
        venue = ingest.crossref_container(crossref_meta)
        doi = str(crossref_meta.get("DOI") or "")
    elif supplement_parent:
        resolved_title = str(supplement_parent.get("resolved_title") or "")
        authors = str(supplement_parent.get("authors") or "")
        year = str(supplement_parent.get("year") or "")
        venue = ingest.normalize_venue_name(str(supplement_parent.get("venue") or ""))
        doi = ""
    else:
        resolved_title = pick_semantic_field(record, "title")
        authors = pick_semantic_field(record, "authors")
        year = pick_semantic_field(record, "year")
        venue = pick_semantic_field(record, "venue")
        doi = pick_semantic_field(record, "doi")

    artifacts = record.get("artifacts") or {}
    notes_parts = []
    text_error = ((record.get("text_extraction") or {}).get("error") or "").strip()
    api_error = ((record.get("semantic_scholar") or {}).get("api_error") or "").strip()
    crossref_error = ((record.get("crossref") or {}).get("error") or "").strip()
    for value in [text_error, api_error, crossref_error]:
        if value:
            notes_parts.append(value)
    notes_parts.extend(record.get("warnings") or [])
    notes_parts.extend(record.get("crossref_warnings") or [])
    source_note = str(manual.get("source_note") or "").strip()
    if source_note:
        notes_parts.append(source_note)

    bibtex_key = ""
    bibtex_type = ""
    existing_row = existing_rows_by_paper_id.get(str(record.get("paper_id") or ""))
    existing_key = str((existing_row or {}).get("bibtex_key") or "")
    if manual:
        bibtex_key = manual_meta.get("bibtex_key", "")
        bibtex_type = manual_meta.get("bibtex_type", "")
    elif supplement_parent:
        bibtex_key = str(supplement_parent.get("bibtex_key") or "")
    else:
        matched_entry = choose_bibtex_entry(
            bib_lookups=bib_lookups,
            existing_key=existing_key,
            title=resolved_title,
            authors=authors,
            year=year,
            doi=doi,
        )
        if matched_entry is not None:
            bibtex_key = str(matched_entry.get("bibtex_key") or "")
            bibtex_type = str(matched_entry.get("entry_type") or "")
        else:
            bibtex_key = ingest.build_bibtex_key_from_fields(authors, year, resolved_title, Path(str(record.get("filename") or "paper")).stem)
    if not bibtex_type:
        matched_entry = choose_bibtex_entry(
            bib_lookups=bib_lookups,
            existing_key=bibtex_key,
            title=resolved_title,
            authors=authors,
            year=year,
            doi=doi,
        )
        if matched_entry is not None:
            bibtex_type = str(matched_entry.get("entry_type") or "")
    if not bibtex_type:
        bibtex_type = "book" if str(record.get("item_type") or "") == "book" else "article"

    abstract_source = ""
    if manual and manual.get("abstract"):
        abstract_source = "manual_verified"
    elif ((record.get("semantic_scholar") or {}).get("best_candidate") or {}).get("abstract"):
        abstract_source = "semantic_scholar"

    canonical_url = str(manual_meta.get("url") or "").strip()
    if not canonical_url:
        canonical_url = str(crossref_meta.get("URL") or "").strip()
    if not canonical_url:
        canonical_url = str(((record.get("semantic_scholar") or {}).get("best_candidate") or {}).get("url") or "").strip()

    return {
        "paper_id": str(record.get("paper_id") or ""),
        "item_type": str(record.get("item_type") or ""),
        "content_kind": content_kind,
        "pdf_path": relative_to_library(record.get("pdf_path")),
        "pdf_sha256": str(record.get("pdf_sha256") or ""),
        "filename": str(record.get("filename") or ""),
        "title_query": str(record.get("title_query") or ""),
        "resolved_title": resolved_title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "semantic_scholar_paper_id": semantic_scholar_paper_id,
        "match_confidence": str(record.get("match_confidence") or ""),
        "match_status": str(record.get("match_status") or ""),
        "bibtex_type": bibtex_type,
        "canonical_url": canonical_url,
        "bibtex_key": bibtex_key,
        "text_path": relative_to_library(artifacts.get("text_path")),
        "chunk_path": relative_to_library(artifacts.get("chunk_path")),
        "abstract_source": abstract_source,
        "date_indexed": str(record.get("date_indexed") or ""),
        "notes": " | ".join(part for part in notes_parts if part),
    }


def main() -> int:
    existing_rows_by_paper_id: dict[str, dict[str, str]] = {}
    if ingest.MASTER_INDEX.exists():
        with ingest.MASTER_INDEX.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                paper_id = str(row.get("paper_id") or "")
                if paper_id:
                    existing_rows_by_paper_id[paper_id] = row

    bib_entries = load_master_bib_entries()
    bib_lookups = build_master_bib_lookups(bib_entries)

    rows = []
    for record_path in sorted(ingest.RECORDS_DIR.glob("*.json")):
        payload = json.loads(record_path.read_text(encoding="utf-8"))
        record = ingest.resolve_record_paths(payload, ingest.LIBRARY_ROOT)
        rows.append(build_row(record, bib_lookups, existing_rows_by_paper_id))

    rows.sort(key=lambda row: row["pdf_path"])
    ingest.write_master_index_rows(rows)

    print(f"[library_rebuild_index_from_records] rebuilt_rows={len(rows)}")
    print(f"[library_rebuild_index_from_records] output={ingest.MASTER_INDEX}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
