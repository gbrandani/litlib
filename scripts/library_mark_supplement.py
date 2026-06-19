#!/usr/bin/env python3
"""Mark an indexed PDF as a supplement linked to a parent article."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import library_ingest as ingest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mark a PDF as a supplement linked to a parent article.")
    parser.add_argument("pdf_path", help="Supplement PDF path, absolute or library-relative")
    parser.add_argument("parent_pdf_path", help="Parent article PDF path, absolute or library-relative")
    parser.add_argument("--source-note", default="", help="Optional note explaining the supplement linkage")
    return parser.parse_args()


def resolve_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = ingest.LIBRARY_ROOT / raw
    return path.resolve()

def main() -> int:
    args = parse_args()
    supplement_pdf = resolve_path(args.pdf_path)
    parent_pdf = resolve_path(args.parent_pdf_path)
    if not supplement_pdf.exists():
        raise SystemExit(f"Supplement PDF not found: {supplement_pdf}")
    if not parent_pdf.exists():
        raise SystemExit(f"Parent PDF not found: {parent_pdf}")

    supplement_row = ingest.load_master_index_row_by_pdf_path(supplement_pdf)
    if not supplement_row:
        supplement_row = ingest.load_master_index_row_by_pdf_sha256(ingest.sha256_path(supplement_pdf))
    parent_row = ingest.load_master_index_row_by_pdf_path(parent_pdf)
    if not parent_row:
        parent_row = ingest.load_master_index_row_by_pdf_sha256(ingest.sha256_path(parent_pdf))
    if not supplement_row:
        raise SystemExit(f"Supplement not indexed: {supplement_pdf}")
    if not parent_row:
        raise SystemExit(f"Parent not indexed: {parent_pdf}")
    supplement_id = supplement_row["paper_id"]
    parent_id = parent_row["paper_id"]

    supplement_row.update(
        {
            "resolved_title": parent_row.get("resolved_title", ""),
            "authors": parent_row.get("authors", ""),
            "year": parent_row.get("year", ""),
            "venue": parent_row.get("venue", ""),
            "doi": "",
            "match_status": "matched_supplement",
            "canonical_url": parent_row.get("canonical_url", ""),
            "bibtex_key": parent_row.get("bibtex_key", ""),
            "abstract_source": "",
        }
    )
    note_parts = [supplement_row.get("notes", "").strip(), args.source_note.strip()]
    supplement_row["notes"] = " | ".join(part for part in note_parts if part)
    ingest.upsert_master_index(supplement_row)

    record_path = ingest.record_path_for_paper_id(supplement_id)
    payload = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}
    payload["match_status"] = "matched_supplement"
    payload["metadata_source"] = "manual_supplement_link"
    payload["supplement_parent"] = parent_row
    if args.source_note:
        payload["manual_supplement_note"] = args.source_note
    record_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"[library_mark_supplement] paper_id={supplement_id}")
    print(f"[library_mark_supplement] parent_id={parent_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
