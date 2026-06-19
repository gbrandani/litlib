#!/usr/bin/env python3
"""Export a project-facing BibTeX file from the canonical master bibliography.

By default this copies `paper_index/master.bib`. With `--abbreviate-journals`,
it rewrites `journal = {...}` fields using the curated table in
`paper_index/journal_abbreviations.tsv`.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


LIBRARY_ROOT = Path("/Users/brandani/Dropbox/documents/library")
INDEX_DIR = LIBRARY_ROOT / "paper_index"
MASTER_BIB = INDEX_DIR / "master.bib"
JOURNAL_ABBREV_FILE = INDEX_DIR / "journal_abbreviations.tsv"


def load_journal_abbrev() -> dict[str, str]:
    mapping: dict[str, str] = {}
    with JOURNAL_ABBREV_FILE.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            journal = (row.get("journal") or "").strip()
            abbr = (row.get("citation_abbreviation") or "").strip()
            if journal and abbr:
                mapping[journal] = abbr
    return mapping


def abbreviate_bibtext(text: str, mapping: dict[str, str], strip_dots: bool = False) -> str:
    journal_re = re.compile(r"^(\s*journal\s*=\s*\{)(.*?)(\},\s*)$", re.MULTILINE)

    def repl(match: re.Match[str]) -> str:
        prefix, journal, suffix = match.groups()
        journal = journal.strip()
        abbr = mapping.get(journal)
        if not abbr:
            return match.group(0)
        if strip_dots:
            abbr = abbr.replace(".", "")
        return f"{prefix}{abbr}{suffix}"

    return journal_re.sub(repl, text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a project-facing BibTeX file from the library.")
    parser.add_argument("output", help="Output .bib path")
    parser.add_argument(
        "--abbreviate-journals",
        action="store_true",
        help="Replace full journal names with curated abbreviations",
    )
    parser.add_argument(
        "--strip-abbrev-dots",
        action="store_true",
        help="When abbreviating journals, remove periods from the stored citation abbreviations",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not MASTER_BIB.exists():
        raise SystemExit(f"Missing canonical bibliography: {MASTER_BIB}")
    if args.abbreviate_journals and not JOURNAL_ABBREV_FILE.exists():
        raise SystemExit(f"Missing journal abbreviation table: {JOURNAL_ABBREV_FILE}")

    text = MASTER_BIB.read_text(encoding="utf-8")
    if args.abbreviate_journals:
        text = abbreviate_bibtext(text, load_journal_abbrev(), strip_dots=args.strip_abbrev_dots)

    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    print(f"[library_export_bib] output={output}")
    print(f"[library_export_bib] abbreviate_journals={args.abbreviate_journals}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
