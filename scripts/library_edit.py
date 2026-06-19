#!/usr/bin/env python3
"""Unified interactive maintenance tool for existing library records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from library_cli import choose_action, choose_numbered_item, prompt_multiline_text, prompt_yes_no
import library_ops as ops


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactively edit, move, delete, or deduplicate one existing library record.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--query", help="Search query used to select one record interactively")
    target.add_argument("--bibtex-key", help="Target one record directly by BibTeX key")
    target.add_argument("--paper-id", help="Target one record directly by internal paper_id")
    target.add_argument("--pdf-path", help="Target one record directly by absolute or library-relative PDF path")
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of search results to show")
    parser.add_argument(
        "--allow-lossy",
        action="store_true",
        help="Allow refresh even if it would clear metadata currently present in master_index.tsv.",
    )
    return parser.parse_args()


def select_result_interactively(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    print("Select a record by number.")
    return choose_numbered_item(
        results,
        ops.render_search_result_lines,
        prompt_text="> ",
        cancel_hint="Press Enter to cancel.",
    )


def print_record_view(row: dict[str, Any]) -> None:
    record = ops.load_record_payload(str(row.get("paper_id") or ""))
    print("")
    print("Record summary:")
    for line in ops.summary_lines_for_row(row):
        print(f"  {line}")
    print(f"  record_path: {ops.record_path_for_row(row)}")
    manual = record.get("manual_override") or {}
    if isinstance(manual, dict):
        source_note = str(manual.get("source_note") or "").strip()
        if source_note:
            print(f"  manual_override.source_note: {source_note}")
    current = ops.current_bibtex_text(row, record)
    print("")
    if current:
        print("Current BibTeX entry:")
        print(current)
    else:
        print("Current BibTeX entry: (none)")
    print("")


def confirm_parsed_entry(parsed: dict[str, Any]) -> bool:
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
    return prompt_yes_no("Apply this BibTeX replacement?", default=False)


def print_report_preview(report: dict[str, Any]) -> None:
    print("")
    print("Planned changes:")
    for line in report.get("summary_lines", []):
        print(f"  {line}")
    for warning in report.get("warnings", []):
        print(f"  warning: {warning}")
    print("")


def run_edit_bib(row: dict[str, Any], *, allow_lossy: bool) -> dict[str, Any]:
    record = ops.load_record_payload(str(row.get("paper_id") or ""))
    current = ops.current_bibtex_text(row, record)
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
    return ops.replace_bibtex_for_row(
        row,
        raw_bibtex,
        allow_lossy=allow_lossy,
        source_note="replaced via library_edit.py",
    )


def run_refresh(row: dict[str, Any], *, allow_lossy: bool) -> dict[str, Any] | None:
    print("")
    print("This will rebuild the master index row and BibTeX entry from the record JSON.")
    print(f"  record_json: {ops.record_path_for_row(row)}")
    print(f"  master_index: {ops.ingest.MASTER_INDEX}")
    print(f"  master_bib: {ops.ingest.MASTER_BIB}")
    if not prompt_yes_no("Proceed with refresh-record?", default=False):
        return None
    return ops.refresh_record_from_row(row, allow_lossy=allow_lossy)


def run_move(row: dict[str, Any]) -> dict[str, Any] | None:
    destination_raw = input("Destination directory or PDF path inside the library: ").strip()
    if not destination_raw:
        return None
    filename = ops.clean_text(row.get("filename")) or Path(ops.clean_text(row.get("pdf_path"))).name
    destination = ops.resolve_destination_path(destination_raw, filename)
    preview = ops.move_record(row, destination, apply=False)
    print_report_preview(preview)
    if not prompt_yes_no("Apply move-pdf?", default=False):
        return None
    return ops.move_record(row, destination, apply=True)


def run_delete(row: dict[str, Any]) -> dict[str, Any] | None:
    delete_pdf = prompt_yes_no("Also delete the tracked PDF from disk?", default=False)
    preview = ops.delete_record(row, apply=False, delete_pdf=delete_pdf)
    print_report_preview(preview)
    if not prompt_yes_no("Apply delete-record?", default=False):
        return None
    return ops.delete_record(row, apply=True, delete_pdf=delete_pdf)


def select_dedup_partner(current_row: dict[str, Any], limit: int) -> dict[str, Any]:
    query = input("Search query for the other record to merge: ").strip()
    if not query:
        raise SystemExit("No dedup-merge query provided.")
    return ops.resolve_target_row(
        query=query,
        limit=limit,
        select_result=select_result_interactively,
        exclude_paper_ids={str(current_row.get("paper_id") or "")},
    )


def run_dedup(current_row: dict[str, Any], *, limit: int) -> tuple[dict[str, Any], bool] | None:
    other_row = select_dedup_partner(current_row, limit)
    print("")
    print("Choose which record to keep:")
    keep_choice = choose_action(
        "Dedup orientation:",
        [
            ("current", f"Keep current record ({current_row.get('paper_id', '')})"),
            ("other", f"Keep matched record ({other_row.get('paper_id', '')})"),
        ],
    )
    if keep_choice is None:
        return None
    if keep_choice == "current":
        keep_row, drop_row = current_row, other_row
        current_survives = True
    else:
        keep_row, drop_row = other_row, current_row
        current_survives = False
    delete_drop_pdf = prompt_yes_no("Also delete the dropped PDF from disk?", default=False)
    force = prompt_yes_no("Force dedup even if the identity signals are weak?", default=False)
    preview = ops.dedup_records(
        keep_row,
        drop_row,
        apply=False,
        delete_drop_pdf=delete_drop_pdf,
        force=force,
    )
    print_report_preview(preview)
    if not prompt_yes_no("Apply dedup-merge?", default=False):
        return None
    return ops.dedup_records(
        keep_row,
        drop_row,
        apply=True,
        delete_drop_pdf=delete_drop_pdf,
        force=force,
    ), current_survives


def print_action_result(report: dict[str, Any] | None) -> None:
    if not report:
        return
    print("")
    print(f"Action completed: {report.get('action', 'unknown')}")
    for line in report.get("summary_lines", []):
        print(f"  {line}")
    if report.get("log_path"):
        print(f"  log_path={report['log_path']}")
    print("")


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
    while True:
        print_record_view(row)
        action = choose_action(
            "Choose an action:",
            [
                ("edit-bib", "Edit BibTeX entry"),
                ("refresh-record", "Refresh derived index and bibliography"),
                ("move-pdf", "Move tracked PDF"),
                ("delete-record", "Delete indexed record"),
                ("dedup-merge", "Merge with another record"),
                ("show-record", "Show raw record JSON"),
                ("cancel", "Cancel"),
            ],
        )
        if action in {None, "cancel"}:
            return 0
        if action == "show-record":
            record = ops.load_record_payload(str(row.get("paper_id") or ""))
            print(json.dumps(record, indent=2, ensure_ascii=False))
            continue
        if action == "edit-bib":
            report = run_edit_bib(row, allow_lossy=args.allow_lossy)
            print_action_result(report)
            row = ops.resolve_target_row(paper_id=str(row.get("paper_id") or ""))
            continue
        if action == "refresh-record":
            report = run_refresh(row, allow_lossy=args.allow_lossy)
            print_action_result(report)
            if report:
                row = ops.resolve_target_row(paper_id=str(row.get("paper_id") or ""))
            continue
        if action == "move-pdf":
            report = run_move(row)
            print_action_result(report)
            if report:
                row = ops.resolve_target_row(paper_id=str(row.get("paper_id") or ""))
            continue
        if action == "delete-record":
            report = run_delete(row)
            print_action_result(report)
            return 0 if report else 0
        if action == "dedup-merge":
            result = run_dedup(row, limit=args.limit)
            if result is None:
                continue
            report, current_survives = result
            print_action_result(report)
            if not current_survives:
                return 0
            row = ops.resolve_target_row(paper_id=str(row.get("paper_id") or ""))
            continue
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
