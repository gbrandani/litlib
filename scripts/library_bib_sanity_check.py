#!/usr/bin/env python3
"""Sanity checks for paper_index/master.bib."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import library_ingest as ingest


HEADER_RE = re.compile(r"@(?P<entry_type>[A-Za-z0-9_:-]+)\s*\{")


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate paper_index/master.bib for malformed or duplicate entries.")
    parser.add_argument(
        "--path",
        type=Path,
        default=ingest.MASTER_BIB,
        help="BibTeX file to validate (default: paper_index/master.bib)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = args.path.expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"BibTeX file not found: {path}")

    text = path.read_text(encoding="utf-8")
    blocks = split_bibtex_blocks(text)
    errors: list[tuple[int, str, str]] = []
    seen_keys: dict[str, int] = {}
    duplicate_keys: list[tuple[str, int, int]] = []
    embedded_headers: list[tuple[int, str]] = []
    stale_reference_rows: list[tuple[str, str]] = []

    for index, block in enumerate(blocks, start=1):
        raw = block if block.startswith("@") else "@" + block
        first_line = raw.splitlines()[0] if raw.splitlines() else "<empty>"
        for match in HEADER_RE.finditer(raw[1:]):
            context = raw[max(0, match.start() - 40): match.start() + 80].replace("\n", "\\n")
            embedded_headers.append((index, context))
            break
        try:
            parsed = ingest.parse_bibtex_entry(raw)
            key = parsed["bibtex_key"]
            if key in seen_keys:
                duplicate_keys.append((key, seen_keys[key], index))
            else:
                seen_keys[key] = index
        except Exception as exc:
            errors.append((index, first_line, repr(exc)))

    if path.resolve() == ingest.MASTER_BIB.resolve() and ingest.MASTER_INDEX.exists():
        with ingest.MASTER_INDEX.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                if row.get("content_kind", "") != ingest.CONTENT_KIND_REF:
                    continue
                paper_id = str(row.get("paper_id") or "").strip()
                if not paper_id:
                    continue
                record_path = ingest.record_path_for_paper_id(paper_id)
                if not record_path.exists():
                    continue
                try:
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                manual = record.get("manual_override") or {}
                raw_bibtex = str(manual.get("raw_bibtex") or "").strip() if isinstance(manual, dict) else ""
                if not raw_bibtex:
                    continue
                core_fields = [
                    str(row.get("resolved_title") or "").strip(),
                    str(row.get("authors") or "").strip(),
                    str(row.get("year") or "").strip(),
                    str(row.get("venue") or "").strip(),
                    str(row.get("bibtex_key") or "").strip(),
                ]
                if not any(core_fields):
                    stale_reference_rows.append((paper_id, record_path.name))

    print(f"[library_bib_sanity_check] path={path}")
    print(f"[library_bib_sanity_check] entries={len(blocks)}")
    print(f"[library_bib_sanity_check] parse_errors={len(errors)}")
    print(f"[library_bib_sanity_check] duplicate_keys={len(duplicate_keys)}")
    print(f"[library_bib_sanity_check] embedded_headers={len(embedded_headers)}")
    print(f"[library_bib_sanity_check] stale_reference_rows={len(stale_reference_rows)}")

    for index, first_line, message in errors:
        print(f"ERROR entry={index} {first_line} {message}")
    for key, first_index, second_index in duplicate_keys:
        print(f"DUPLICATE key={key} first_entry={first_index} second_entry={second_index}")
    for index, context in embedded_headers:
        print(f"EMBEDDED_HEADER entry={index} context={context}")
    for paper_id, record_name in stale_reference_rows:
        print(f"STALE_REFERENCE_ROW paper_id={paper_id} record={record_name}")

    trailing = text.rstrip("\n")
    if trailing and not trailing.endswith("}"):
        print("[library_bib_sanity_check] trailing_text_warning=final non-newline character is not '}'")

    return 0 if not errors and not duplicate_keys and not embedded_headers and not stale_reference_rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
