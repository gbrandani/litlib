#!/usr/bin/env python3
"""Refresh one indexed record from its canonical JSON payload."""

from __future__ import annotations

import argparse

import library_ops as ops


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh one record from paper_index/records/<paper_id>.json into master_index.tsv and master.bib."
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--paper-id", help="Internal canonical paper_id")
    target.add_argument("--bibtex-key", help="Human-facing BibTeX key")
    target.add_argument("--pdf-path", help="Absolute or library-relative PDF path")
    parser.add_argument(
        "--allow-lossy",
        action="store_true",
        help="Allow refresh even if it would clear metadata currently present in master_index.tsv.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    row = ops.resolve_target_row(
        paper_id=args.paper_id,
        bibtex_key=args.bibtex_key,
        pdf_path=args.pdf_path,
    )
    report = ops.refresh_record_from_row(row, allow_lossy=args.allow_lossy)
    for line in report.get("summary_lines", []):
        print(f"[library_refresh_record] {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
