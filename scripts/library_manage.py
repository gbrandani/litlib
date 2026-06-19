#!/usr/bin/env python3
"""Safely move, delete, or deduplicate indexed library records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import library_ops as ops


def emit(report: dict, json_output: bool) -> None:
    if json_output:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    print(f"[library_manage] action={report['action']} apply={report['apply']}")
    if report.get("status"):
        print(f"[library_manage] status={report['status']}")
    for line in report.get("summary_lines", []):
        print(line)
    for warning in report.get("warnings", []):
        print(f"[warning] {warning}")
    if report.get("log_path"):
        print(f"[library_manage] log_path={report['log_path']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely move, delete, or deduplicate indexed library records.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--apply", action="store_true", help="Actually perform the requested change. Default is dry-run.")
    common.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    move_parser = subparsers.add_parser("move", parents=[common], help="Move one tracked PDF and update the index")
    move_parser.add_argument("paper_id", help="paper_id to move")
    move_parser.add_argument("destination", help="Destination directory or full PDF path inside the library")

    delete_parser = subparsers.add_parser("delete", parents=[common], help="Delete one indexed record and optionally its PDF")
    delete_parser.add_argument("paper_id", help="paper_id to delete")
    delete_parser.add_argument("--delete-pdf", action="store_true", help="Also delete the tracked PDF from disk")

    dedup_parser = subparsers.add_parser("dedup", parents=[common], help="Consolidate one duplicate entry into another")
    dedup_parser.add_argument("keep_paper_id", help="paper_id to keep")
    dedup_parser.add_argument("drop_paper_id", help="paper_id to remove")
    dedup_parser.add_argument("--delete-drop-pdf", action="store_true", help="Also delete the dropped PDF from disk")
    dedup_parser.add_argument("--force", action="store_true", help="Allow dedup even when the identity signals are weak")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "move":
        row = ops.resolve_target_row(paper_id=args.paper_id)
        filename = ops.clean_text(row.get("filename")) or Path(ops.clean_text(row.get("pdf_path"))).name
        destination = ops.resolve_destination_path(args.destination, filename)
        report = ops.move_record(row, destination, apply=args.apply)
        emit(report, args.json)
        return 0

    if args.command == "delete":
        row = ops.resolve_target_row(paper_id=args.paper_id)
        report = ops.delete_record(row, apply=args.apply, delete_pdf=args.delete_pdf)
        emit(report, args.json)
        return 0

    if args.command == "dedup":
        keep_row = ops.resolve_target_row(paper_id=args.keep_paper_id)
        drop_row = ops.resolve_target_row(paper_id=args.drop_paper_id)
        report = ops.dedup_records(
            keep_row,
            drop_row,
            apply=args.apply,
            delete_drop_pdf=args.delete_drop_pdf,
            force=args.force,
        )
        emit(report, args.json)
        return 0

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
