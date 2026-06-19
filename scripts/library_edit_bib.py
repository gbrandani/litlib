#!/usr/bin/env python3
"""Interactively replace one record's BibTeX entry and refresh derived outputs."""

from __future__ import annotations

import argparse

from library_cli import choose_numbered_item, prompt_multiline_text, prompt_yes_no
import library_ops as ops


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search for one record, show its current BibTeX entry, paste a replacement entry, and refresh the library."
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--paper-id", help="Target one record directly by internal paper_id")
    target.add_argument("--bibtex-key", help="Target one record directly by BibTeX key")
    target.add_argument("--pdf-path", help="Target one record directly by absolute or library-relative PDF path")
    target.add_argument("--query", help="Search query used to find one record interactively")
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of search results to show")
    parser.add_argument(
        "--allow-lossy",
        action="store_true",
        help="Allow refresh even if it would clear metadata currently present in master_index.tsv.",
    )
    return parser.parse_args()


def select_result_interactively(results: list[dict]) -> dict | None:
    print("Select the record to modify by number.")
    return choose_numbered_item(
        results,
        ops.render_search_result_lines,
        prompt_text="> ",
        cancel_hint="Press Enter to cancel.",
    )


def confirm_parsed_entry(parsed: dict) -> bool:
    normalized = parsed.get("normalized") or {}
    print("Replacement BibTeX summary:")
    print(f"  bibtex_type: {parsed.get('entry_type', '') or 'n/a'}")
    print(f"  bibtex_key: {parsed.get('bibtex_key', '') or 'n/a'}")
    print(f"  title: {normalized.get('title', '') or 'n/a'}")
    print(f"  authors: {normalized.get('authors', '') or 'n/a'}")
    print(f"  year: {normalized.get('year', '') or 'n/a'}")
    print(f"  venue: {normalized.get('venue', '') or 'n/a'}")
    print(f"  doi: {normalized.get('doi', '') or 'n/a'}")
    print(f"  url: {normalized.get('url', '') or 'n/a'}")
    ignored_fields = list(parsed.get("ignored_fields") or [])
    if ignored_fields:
        print(f"  ignored_fields: {', '.join(ignored_fields)}")
    print("")
    print("Canonical stored BibTeX entry:")
    print(str(parsed.get("raw_bibtex") or "").strip())
    return prompt_yes_no("Replace the current BibTeX entry with this one?", default=False)


def main() -> int:
    args = parse_args()
    row = ops.resolve_target_row(
        paper_id=args.paper_id,
        bibtex_key=args.bibtex_key,
        pdf_path=args.pdf_path,
        query=args.query,
        limit=args.limit,
        select_result=select_result_interactively if args.query else None,
    )
    record = ops.load_record_payload(str(row.get("paper_id") or ""))
    print("Selected record:")
    for line in ops.summary_lines_for_row(row):
        print(f"  {line}")
    print("")
    current = ops.current_bibtex_text(row, record)
    if current:
        print("Current BibTeX entry:")
        print(current)
    else:
        print("Current BibTeX entry: (none)")
    print("")
    raw_bibtex = prompt_multiline_text(
        "Edit or replace the BibTeX entry below.",
        initial_text=current,
        end_marker="END",
    )
    if not raw_bibtex:
        raise SystemExit("No BibTeX entry provided.")
    try:
        parsed = ops.ingest.parse_bibtex_entry(raw_bibtex)
    except Exception as exc:
        raise SystemExit(f"Could not parse replacement BibTeX entry: {exc}") from exc
    canonical = ops.canonicalize_parsed_bibtex(parsed)
    if not confirm_parsed_entry(canonical):
        raise SystemExit(1)
    report = ops.replace_bibtex_for_row(
        row,
        raw_bibtex,
        allow_lossy=args.allow_lossy,
        source_note="replaced via library_edit_bib.py",
    )
    for line in report.get("summary_lines", []):
        print(f"[library_edit_bib] {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
