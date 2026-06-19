#!/usr/bin/env python3
"""One-time migration for guided/reference-aware library records."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import library_ingest as ingest


def load_bibtex_types() -> dict[str, str]:
    if not ingest.MASTER_BIB.exists():
        return {}
    text = ingest.MASTER_BIB.read_text(encoding="utf-8")
    blocks = [block.strip() for block in re.split(r"\n(?=@)", text) if block.strip()]
    types: dict[str, str] = {}
    for block in blocks:
        try:
            parsed = ingest.parse_bibtex_entry(block)
        except Exception:
            continue
        types[parsed["bibtex_key"]] = parsed["entry_type"]
    return types


def canonical_url_from_record(record: dict) -> str:
    manual = record.get("manual_override") or {}
    if manual.get("url"):
        return str(manual.get("url") or "").strip()
    crossref_meta = ((record.get("crossref") or {}).get("metadata") or {})
    if crossref_meta.get("URL"):
        return str(crossref_meta.get("URL") or "").strip()
    semantic = ((record.get("semantic_scholar") or {}).get("best_candidate") or {})
    return str(semantic.get("url") or "").strip()


def migrate_master_index() -> int:
    if not ingest.MASTER_INDEX.exists():
        print(f"[library_migrate_reference_model] missing index: {ingest.MASTER_INDEX}")
        return 0

    bibtex_types = load_bibtex_types()
    with ingest.MASTER_INDEX.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))

    migrated: list[dict[str, str]] = []
    for row in rows:
        paper_id = str(row.get("paper_id") or "").strip()
        record_path = ingest.record_path_for_paper_id(paper_id)
        record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}
        normalized = ingest.master_index_row_defaults(row)
        normalized["content_kind"] = normalized.get("content_kind", "") or ingest.CONTENT_KIND_PDF
        bibtex_key = normalized.get("bibtex_key", "")
        normalized["bibtex_type"] = bibtex_types.get(
            bibtex_key,
            normalized.get("bibtex_type", "") or ("book" if normalized.get("item_type") == "book" else ("article" if normalized.get("item_type") == "article" else "misc")),
        )
        normalized["canonical_url"] = normalized.get("canonical_url", "") or canonical_url_from_record(record)
        migrated.append(normalized)

    ingest.write_master_index_rows(migrated)
    return len(migrated)


def migrate_records() -> int:
    count = 0
    for record_path in sorted(ingest.RECORDS_DIR.glob("*.json")):
        record = json.loads(record_path.read_text(encoding="utf-8"))
        changed = False
        if not record.get("content_kind"):
            record["content_kind"] = ingest.CONTENT_KIND_PDF if record.get("pdf_path") else ingest.CONTENT_KIND_REF
            changed = True
        artifacts = record.setdefault("artifacts", {})
        if not artifacts.get("text_kind"):
            artifacts["text_kind"] = "pdf_text" if record.get("pdf_path") else "metadata_stub"
            changed = True
        if changed:
            ingest.write_record_json(record_path, record)
            count += 1
    return count


def main() -> int:
    row_count = migrate_master_index()
    record_count = migrate_records()
    print(f"[library_migrate_reference_model] migrated_rows={row_count}")
    print(f"[library_migrate_reference_model] updated_records={record_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
