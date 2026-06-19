#!/usr/bin/env python3
"""Normalize capitalization variants of canonical journal names across the library index."""

from __future__ import annotations

import csv
import re
from pathlib import Path

import library_ingest as ingest


LIBRARY_ROOT = Path("/Users/brandani/Dropbox/documents/library")
INDEX_DIR = LIBRARY_ROOT / "paper_index"
MASTER_INDEX = INDEX_DIR / "master_index.tsv"
MASTER_BIB = INDEX_DIR / "master.bib"
JOURNAL_ABBREV = INDEX_DIR / "journal_abbreviations.tsv"


def normalize_master_index() -> int:
    with MASTER_INDEX.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
        fieldnames = rows[0].keys() if rows else []

    changes = 0
    for row in rows:
        venue = row.get("venue", "")
        normalized = ingest.normalize_venue_name(venue)
        if venue != normalized:
            row["venue"] = normalized
            changes += 1

    ingest.write_master_index_rows(rows)
    return changes


def normalize_master_bib() -> int:
    text = MASTER_BIB.read_text(encoding="utf-8")
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        prefix, journal, suffix = match.groups()
        normalized = ingest.normalize_venue_name(" ".join(journal.split()))
        if normalized != journal:
            count += 1
        return f"{prefix}{normalized}{suffix}"

    journal_re = re.compile(r"^(\s*journal\s*=\s*\{)(.*?)(\},\s*)$", re.MULTILINE)
    updated = journal_re.sub(repl, text)
    ingest.atomic_write_text(MASTER_BIB, updated)
    return count


def normalize_journal_abbrev() -> int:
    with JOURNAL_ABBREV.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))

    deduped: dict[str, dict[str, str]] = {}
    changes = 0
    for row in rows:
        original = row["journal"]
        normalized = ingest.normalize_venue_name(original)
        if original != normalized:
            changes += 1
        row["journal"] = normalized
        key = normalized.casefold()
        if key not in deduped:
            deduped[key] = row
            continue

        existing = deduped[key]
        existing_filename = (existing.get("filename_abbreviation") or "").strip()
        row_filename = (row.get("filename_abbreviation") or "").strip()
        if not existing_filename and row_filename:
            deduped[key] = row
        elif existing["journal"] != normalized and row["journal"] == normalized:
            deduped[key] = row

    fieldnames = ["journal", "filename_abbreviation", "citation_abbreviation", "citation_source"]
    normalized_rows = sorted(deduped.values(), key=lambda row: row["journal"].casefold())
    with JOURNAL_ABBREV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(normalized_rows)
    return changes


def main() -> int:
    master_index_changes = normalize_master_index()
    master_bib_changes = normalize_master_bib()
    journal_abbrev_changes = normalize_journal_abbrev()
    print(f"[library_normalize_journals] master_index_changes={master_index_changes}")
    print(f"[library_normalize_journals] master_bib_changes={master_bib_changes}")
    print(f"[library_normalize_journals] journal_abbreviations_changes={journal_abbrev_changes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
