#!/usr/bin/env python3
"""Batch backfill the local literature registry.

This walks a subtree, finds PDFs, and invokes library_ingest.py for files that
are not yet indexed. It is intentionally conservative:
- defaults to articles/ only
- skips already-indexed files unless --force is used
- records a JSON summary under .paper_index/logs/
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
LIBRARY_ROOT = SCRIPT_DIR.parent
REGISTRY_ROOT = LIBRARY_ROOT / "paper_index"
MASTER_INDEX = REGISTRY_ROOT / "master_index.tsv"
LOGS_DIR = REGISTRY_ROOT / "logs"
INGEST_SCRIPT = SCRIPT_DIR / "library_ingest.py"


def eprint(*parts: object) -> None:
    print(*parts, file=sys.stderr)


def load_master_index() -> dict[str, dict[str, str]]:
    if not MASTER_INDEX.exists():
        return {}

    rows: dict[str, dict[str, str]] = {}
    with MASTER_INDEX.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            pdf_path = row.get("pdf_path", "")
            if pdf_path:
                rows[pdf_path] = row
    return rows


def find_pdfs(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.pdf") if path.is_file())


def should_process(
    pdf_path: Path,
    master_index: dict[str, dict[str, str]],
    force: bool,
) -> tuple[bool, str]:
    rel = str(pdf_path.resolve().relative_to(LIBRARY_ROOT))
    row = master_index.get(rel)
    if not row:
        return True, "unindexed"
    if force:
        return True, "force"
    return False, row.get("match_status", "indexed")


def run_ingest(pdf_path: Path, args: argparse.Namespace, force_reingest: bool = False) -> tuple[int, str, str]:
    command = [
        sys.executable,
        str(INGEST_SCRIPT),
        str(pdf_path),
        "--chunk-chars",
        str(args.chunk_chars),
        "--overlap-chars",
        str(args.overlap_chars),
        "--semantic-limit",
        str(args.semantic_limit),
    ]
    if args.force or force_reingest:
        command.append("--force")

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=args.ingest_timeout if args.ingest_timeout > 0 else None,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        stderr = (stderr + "\n" if stderr else "") + f"[library_backfill] ingest timed out after {args.ingest_timeout}s"
        return 124, stdout, stderr


def write_log(payload: dict[str, Any]) -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = LOGS_DIR / f"backfill_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill the local literature registry for a folder of PDFs.")
    parser.add_argument(
        "root",
        nargs="?",
        default=str(LIBRARY_ROOT / "articles"),
        help="Root folder to scan (default: library/articles)",
    )
    parser.add_argument("--force", action="store_true", help="Re-run ingest even for already indexed PDFs")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of PDFs to process (0 = no limit)")
    parser.add_argument(
        "--match-status",
        nargs="*",
        default=[],
        help="When not forcing, only reprocess already-indexed rows with one of these statuses",
    )
    parser.add_argument("--chunk-chars", type=int, default=2200, help="Chunk size passed to library_ingest.py")
    parser.add_argument("--overlap-chars", type=int, default=250, help="Chunk overlap passed to library_ingest.py")
    parser.add_argument("--semantic-limit", type=int, default=10, help="Candidate count passed to library_ingest.py")
    parser.add_argument(
        "--ingest-timeout",
        type=int,
        default=600,
        help="Per-file timeout in seconds for library_ingest.py (0 = no timeout, default: 600)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"Root path does not exist: {root}")

    if not INGEST_SCRIPT.exists():
        raise SystemExit(f"Ingest script not found: {INGEST_SCRIPT}")

    master_index = load_master_index()
    pdfs = find_pdfs(root)
    selected: list[tuple[Path, str]] = []
    requested_statuses = set(args.match_status)

    for pdf_path in pdfs:
        process, reason = should_process(pdf_path, master_index, args.force)
        if not process and requested_statuses:
            rel = str(pdf_path.resolve().relative_to(LIBRARY_ROOT))
            row = master_index.get(rel, {})
            if row.get("match_status", "") in requested_statuses:
                process, reason = True, f"status:{row.get('match_status', '')}"
        if process:
            selected.append((pdf_path, reason))

    if args.limit > 0:
        selected = selected[: args.limit]

    eprint(f"[library_backfill] scan_root={root}")
    eprint(f"[library_backfill] discovered_pdfs={len(pdfs)} selected={len(selected)}")

    processed: list[dict[str, Any]] = []
    counts = {"ok": 0, "failed": 0}
    for index, (pdf_path, reason) in enumerate(selected, start=1):
        eprint(f"[library_backfill] {index}/{len(selected)} ingest reason={reason} path={pdf_path}")
        rc, stdout, stderr = run_ingest(pdf_path, args, force_reingest=(reason.startswith("status:") or reason == "force"))
        outcome = "ok" if rc == 0 else "failed"
        counts[outcome] += 1
        processed.append(
            {
                "pdf_path": str(pdf_path),
                "reason": reason,
                "returncode": rc,
                "stdout": stdout.strip(),
                "stderr": stderr.strip(),
            }
        )

    payload = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "scan_root": str(root),
        "discovered_pdfs": len(pdfs),
        "selected_pdfs": len(selected),
        "counts": counts,
        "match_status_filter": sorted(requested_statuses),
        "force": args.force,
        "processed": processed,
    }
    log_path = write_log(payload)

    eprint(f"[library_backfill] ok={counts['ok']} failed={counts['failed']}")
    eprint(f"[library_backfill] log={log_path}")
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
