#!/usr/bin/env python3
"""Rebuild paper_index/master.bib from the canonical index and record JSON files."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import library_ingest as ingest


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


def loose_parse_bibtex_chunk(chunk: str) -> dict[str, Any] | None:
    header_match = HEADER_RE.match(chunk.strip())
    if not header_match:
        return None
    try:
        return ingest.parse_bibtex_entry(chunk)
    except Exception:
        return {
            "entry_type": header_match.group("entry_type").strip().lower(),
            "bibtex_key": header_match.group("key").strip(),
            "fields": {},
            "normalized": {},
            "raw_bibtex": chunk.strip(),
        }


def load_existing_entries() -> dict[str, dict[str, Any]]:
    if not ingest.MASTER_BIB.exists():
        return {}
    text = ingest.MASTER_BIB.read_text(encoding="utf-8")
    entries: dict[str, dict[str, Any]] = {}
    for block in split_bibtex_blocks(text):
        parsed = loose_parse_bibtex_chunk(block)
        if parsed is None:
            continue
        key = str(parsed.get("bibtex_key") or "").strip()
        if key and key not in entries:
            entries[key] = parsed
    return entries


def load_rows() -> list[dict[str, str]]:
    if not ingest.MASTER_INDEX.exists():
        return []
    with ingest.MASTER_INDEX.open("r", encoding="utf-8", newline="") as handle:
        return [ingest.master_index_row_defaults(row) for row in csv.DictReader(handle, delimiter="\t")]


def load_record(paper_id: str) -> dict[str, Any]:
    path = ingest.record_path_for_paper_id(paper_id)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def extract_abstract(record: dict[str, Any]) -> str:
    for container_name in ["resolved_metadata", "manual_override"]:
        container = record.get(container_name)
        if isinstance(container, dict):
            abstract = " ".join(str(container.get("abstract") or "").split()).strip()
            if abstract:
                return abstract

    semantic = record.get("semantic_scholar")
    if isinstance(semantic, dict):
        best = semantic.get("best_candidate")
        if isinstance(best, dict):
            abstract = " ".join(str(best.get("abstract") or "").split()).strip()
            if abstract:
                return abstract

    crossref = record.get("crossref")
    if isinstance(crossref, dict):
        metadata = crossref.get("metadata")
        if isinstance(metadata, dict):
            abstract = " ".join(str(metadata.get("abstract") or "").split()).strip()
            if abstract:
                return abstract
    return ""


def supplement_like(row: dict[str, str]) -> bool:
    text = " ".join(
        str(row.get(name) or "")
        for name in ["filename", "pdf_path", "resolved_title", "title_query", "match_status"]
    ).casefold()
    return any(token in text for token in [" - si", "_si", "supplement", "supporting information", "matched_supplement"])


def representative_score(row: dict[str, str], record: dict[str, Any]) -> tuple[int, int, int]:
    metadata_fields = [
        row.get("resolved_title", ""),
        row.get("authors", ""),
        row.get("year", ""),
        row.get("venue", ""),
        row.get("doi", ""),
        row.get("canonical_url", ""),
        extract_abstract(record),
    ]
    richness = sum(1 for value in metadata_fields if str(value or "").strip())
    primary_bonus = 100 if not supplement_like(row) else 0
    pdf_bonus = 20 if (row.get("content_kind") or "") == ingest.CONTENT_KIND_PDF else 0
    return (primary_bonus + pdf_bonus + richness, len(extract_abstract(record)), len(row.get("doi", "")))


def choose_representative(rows_with_records: list[tuple[dict[str, str], dict[str, Any]]]) -> tuple[dict[str, str], dict[str, Any]]:
    return max(rows_with_records, key=lambda item: representative_score(item[0], item[1]))


def normalized_fields_from_row(row: dict[str, str], record: dict[str, Any]) -> dict[str, str]:
    abstract = extract_abstract(record)
    fields = {
        "title": row.get("resolved_title", "") or row.get("title_query", ""),
        "authors": row.get("authors", ""),
        "year": row.get("year", ""),
        "venue": row.get("venue", ""),
        "doi": row.get("doi", ""),
        "url": row.get("canonical_url", ""),
        "abstract": abstract,
        "bibtex_key": row.get("bibtex_key", ""),
        "bibtex_type": row.get("bibtex_type", ""),
    }
    crossref = record.get("crossref")
    if isinstance(crossref, dict) and isinstance(crossref.get("metadata"), dict):
        crossref_fields = ingest.crossref_to_bibtex_fields(crossref["metadata"])
        for field in ["volume", "number", "pages", "publisher", "issn"]:
            value = str(crossref_fields.get(field) or "").strip()
            if value:
                fields[field] = value
    return fields


def original_fields_for_key(existing_entries: dict[str, dict[str, Any]], row: dict[str, str], record: dict[str, Any]) -> tuple[str, dict[str, str]]:
    key = str(row.get("bibtex_key") or "").strip()
    manual = record.get("manual_override")
    if isinstance(manual, dict) and manual.get("raw_bibtex"):
        try:
            parsed = ingest.parse_bibtex_entry(str(manual.get("raw_bibtex")))
            return str(parsed.get("entry_type") or row.get("bibtex_type") or "article"), dict(parsed.get("fields") or {})
        except Exception:
            pass

    existing = existing_entries.get(key)
    if existing is not None:
        return str(existing.get("entry_type") or row.get("bibtex_type") or "article"), dict(existing.get("fields") or {})

    return str(row.get("bibtex_type") or "article"), {}


def main() -> int:
    rows = load_rows()
    existing_entries = load_existing_entries()

    grouped: dict[str, list[tuple[dict[str, str], dict[str, Any]]]] = {}
    for row in rows:
        key = str(row.get("bibtex_key") or "").strip()
        if not key:
            continue
        grouped.setdefault(key, []).append((row, load_record(str(row.get("paper_id") or ""))))

    output_entries: dict[str, str] = {}
    for key, rows_with_records in grouped.items():
        row, record = choose_representative(rows_with_records)
        entry_type, original_fields = original_fields_for_key(existing_entries, row, record)
        normalized_fields = normalized_fields_from_row(row, record)
        output_entries[key] = ingest.build_bibtex_entry_with_preserved_fields(
            entry_type=entry_type,
            bibtex_key=key,
            original_fields=original_fields,
            normalized_fields=normalized_fields,
        )

    output = "".join(output_entries[key].rstrip() + "\n\n" for key in sorted(output_entries))
    ingest.atomic_write_text(ingest.MASTER_BIB, output)

    print(f"[library_rebuild_master_bib] rebuilt_entries={len(output_entries)}")
    print(f"[library_rebuild_master_bib] output={ingest.MASTER_BIB}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
