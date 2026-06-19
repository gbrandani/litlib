#!/usr/bin/env python3
"""Apply a verified manual metadata override to an indexed paper."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import library_ingest as ingest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply a verified manual metadata override to one indexed paper.")
    parser.add_argument("pdf_path", help="Absolute or library-relative path to the PDF")
    parser.add_argument("--title", required=True, help="Verified article title")
    parser.add_argument("--authors", required=True, help="Comma-separated author list")
    parser.add_argument("--year", required=True, help="Publication year")
    parser.add_argument("--venue", required=True, help="Journal or venue name")
    parser.add_argument("--doi", default="", help="Verified DOI")
    parser.add_argument("--url", default="", help="Verified canonical URL")
    parser.add_argument("--abstract", default="", help="Verified abstract text")
    parser.add_argument("--bibtex-key", default="", help="Explicit BibTeX key to assign")
    parser.add_argument("--source-url", action="append", default=[], help="Source URL used to verify the override; repeatable")
    parser.add_argument("--source-note", default="", help="Short note about the verification source")
    parser.add_argument(
        "--status",
        default="manual_verified",
        choices=["manual_verified", "matched", "matched_via_doi", "needs_manual_review"],
        help="Status to record",
    )
    return parser.parse_args()


def resolve_pdf_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = ingest.LIBRARY_ROOT / raw_path
    return path.resolve()


def build_bibtex_entry(bibtex_key: str, args: argparse.Namespace) -> str:
    return ingest.build_bibtex_entry_from_fields(
        "article",
        bibtex_key,
        {
            "title": args.title,
            "authors": args.authors,
            "year": args.year,
            "venue": args.venue,
            "doi": args.doi,
            "url": args.url,
            "abstract": args.abstract,
        },
    )


def main() -> int:
    args = parse_args()
    pdf_path = resolve_pdf_path(args.pdf_path)
    if not pdf_path.exists():
        raise SystemExit(f"PDF does not exist: {pdf_path}")

    row = ingest.load_master_index_row_by_pdf_path(pdf_path)
    if not row:
        row = ingest.load_master_index_row_by_pdf_sha256(ingest.sha256_path(pdf_path))
    if not row:
        raise SystemExit(f"Paper is not indexed yet: {pdf_path}")
    paper_id = row["paper_id"]

    record_path = ingest.record_path_for_paper_id(paper_id)
    record_payload = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}

    bibtex_key = args.bibtex_key.strip() or row.get("bibtex_key", "") or ingest.build_bibtex_key_from_fields(args.authors, args.year, args.title, fallback_stem=pdf_path.stem)
    bibtex_entry = build_bibtex_entry(bibtex_key, args)
    ingest.upsert_master_bib(paper_id, bibtex_entry)
    old_key = row.get("bibtex_key", "")
    if old_key and old_key != bibtex_key:
        ingest.remove_master_bib_entry(old_key)

    notes = [row.get("notes", "").strip(), args.source_note.strip()]
    row.update(
        {
            "resolved_title": args.title,
            "authors": args.authors,
            "year": args.year,
            "venue": args.venue,
            "doi": args.doi,
            "match_status": args.status,
            "bibtex_type": row.get("bibtex_type", "") or "article",
            "canonical_url": args.url or row.get("canonical_url", ""),
            "bibtex_key": bibtex_key,
            "abstract_source": "manual_verified" if args.abstract else "",
            "notes": " | ".join(note for note in notes if note),
        }
    )
    ingest.upsert_master_index(row)

    record_payload["match_status"] = args.status
    record_payload["metadata_source"] = "manual_verified"
    record_payload["content_kind"] = row.get("content_kind", ingest.CONTENT_KIND_PDF)
    record_payload["manual_override"] = {
        "title": args.title,
        "authors": args.authors,
        "year": args.year,
        "venue": args.venue,
        "doi": args.doi,
        "url": args.url,
        "abstract": args.abstract,
        "source_urls": args.source_url,
        "source_note": args.source_note,
        "bibtex_key": bibtex_key,
        "parsed_bibtex_type": row.get("bibtex_type", "") or "article",
    }
    record_path.write_text(json.dumps(record_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"[library_apply_override] paper_id={paper_id}")
    print(f"[library_apply_override] status={args.status}")
    print(f"[library_apply_override] bibtex_key={bibtex_key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
