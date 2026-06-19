#!/usr/bin/env python3
"""Rename resolved library PDFs to a canonical filename convention.

Convention:
    FirstAuthor YEAR JournalAbbrev - Title.pdf

Supplements:
    FirstAuthor YEAR JournalAbbrev - Title - SI.pdf
    FirstAuthor YEAR JournalAbbrev - Title - SM.pdf

This script is conservative:
- only renames rows with resolved statuses
- defaults to dry-run
- updates master_index.tsv and per-paper record JSON paths when applied
- does not touch text/chunk artifacts because they are keyed by paper_id
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import unicodedata
from pathlib import Path


LIBRARY_ROOT = Path("/Users/brandani/Dropbox/documents/library")
INDEX_DIR = LIBRARY_ROOT / "paper_index"
MASTER_INDEX = INDEX_DIR / "master_index.tsv"
RECORDS_DIR = INDEX_DIR / "records"
JOURNAL_ABBREV_FILE = INDEX_DIR / "journal_abbreviations.tsv"
RESOLVED_STATUSES = {"matched", "matched_via_doi", "matched_supplement", "manual_verified"}
FILENAME_ABBREV_OVERRIDES = {
    "Proc. Natl. Acad. Sci. U.S.A.": "PNAS",
    "Nucleic Acids Res": "NAR",
    "Nucleic Acids Res.": "NAR",
    "Phys. Rev. Lett.": "PRL",
    "Phys. Rev. E": "PRE",
    "Phys. Rev. X": "PRX",
}

JOURNAL_ABBREV = {
    "bioRxiv": "bioRxiv",
    "Cell": "Cell",
    "Cell Reports": "CellRep",
    "Cell reports": "CellRep",
    "Current Biology": "CurrBiol",
    "Current Opinion in Cell Biology": "CurrOpinCellBiol",
    "eLife": "eLife",
    "EMBO Journal": "EMBOJ",
    "Genome Research": "GenomeRes",
    "Journal of Biological Chemistry": "JBiolChem",
    "Journal of Chemical Physics": "JChemPhys",
    "Journal of Chemical Theory and Computation": "JCTC",
    "Journal of Molecular Biology": "JMolBiol",
    "Journal of Molecular Graphics and Modelling": "JMolGraphModel",
    "Machine Learning: Science and Technology": "MLST",
    "Molecular Cell": "MolCell",
    "Nature": "Nature",
    "Nature Biotechnology": "NatBiotechnol",
    "Nature Cell Biology": "NatCellBiol",
    "Nature Communications": "NatCommun",
    "Nature Genetics": "NatGenet",
    "Nature Machine Intelligence": "NatMachIntell",
    "Nature Methods": "NatMethods",
    "Nature Reviews Molecular Cell Biology": "NatRevMolCellBiol",
    "Nature Structural & Molecular Biology": "NatStructMolBiol",
    "Nature Structural and Molecular Biology": "NatStructMolBiol",
    "Nucleic Acids Research": "NAR",
    "NAR Genomics and Bioinformatics": "NARGAB",
    "Physical Review Letters": "PRL",
    "Physical Review Research": "PRR",
    "Physical Review B": "PRB",
    "PLOS Computational Biology": "PlosComputBiol",
    "PLoS Computational Biology": "PlosComputBiol",
    "PLoS Biology": "PlosBiol",
    "PLoS ONE": "PLOSOne",
    "Proceedings of the National Academy of Sciences": "PNAS",
    "Proceedings of the National Academy of Sciences of the United States of America": "PNAS",
    "Protein Science": "ProteinSci",
    "Quarterly Reviews of Biophysics": "QRevBiophys",
    "Science": "Science",
    "Science Advances": "SciAdv",
    "Science China Life Sciences": "SciChinaLifeSci",
}


def load_curated_journal_abbrev() -> dict[str, str]:
    mapping = dict(JOURNAL_ABBREV)
    if not JOURNAL_ABBREV_FILE.exists():
        return mapping

    with JOURNAL_ABBREV_FILE.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            journal = (row.get("journal") or "").strip()
            abbr = (
                (row.get("filename_abbreviation") or "").strip()
                or FILENAME_ABBREV_OVERRIDES.get((row.get("citation_abbreviation") or "").strip(), "")
                or safe_filename_fragment((row.get("citation_abbreviation") or "").strip()).replace(" ", "").replace(".", "")
                or (row.get("abbreviation") or "").strip()
            )
            if journal and abbr:
                mapping[journal] = abbr
    return mapping


def normalize_ascii(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode("ascii")


def safe_filename_fragment(text: str) -> str:
    text = re.sub(r"[‐‑‒–—−]", "-", text)
    text = normalize_ascii(text)
    text = text.replace("&", "and")
    text = re.sub(r"[/:?*\"<>|]+", " ", text)
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip(" .-_")
    return text


def first_author_surname(authors: str) -> str:
    first = (authors.split(",")[0] if authors else "").strip()
    if not first:
        return "Unknown"
    parts = first.split()
    if len(parts) >= 2 and parts[-2].lower() in {
        "da",
        "de",
        "del",
        "della",
        "di",
        "du",
        "la",
        "le",
        "st.",
        "van",
        "von",
    }:
        return safe_filename_fragment(" ".join(parts[-2:])) or "Unknown"
    return safe_filename_fragment(parts[-1]) or "Unknown"


def infer_journal_abbrev(row: dict[str, str]) -> str:
    journal_abbrev = load_curated_journal_abbrev()
    venue = row.get("venue", "").strip()
    if venue in journal_abbrev:
        return journal_abbrev[venue]

    filename = row.get("filename", "")
    year = row.get("year", "").strip()
    if year and year in filename:
        after = filename.split(year, 1)[-1]
        if " - " in after:
            token = after.split(" - ", 1)[0].strip(" _-")
            token = safe_filename_fragment(token).replace(" ", "")
            if 2 <= len(token) <= 18:
                return token

    compact = safe_filename_fragment(venue).replace(" ", "")
    return compact[:24] if compact else "UnknownJournal"


def filename_looks_bad(row: dict[str, str], min_stem_chars: int) -> bool:
    filename = row.get("filename", "")
    stem = filename[:-4] if filename.lower().endswith(".pdf") else filename
    if len(stem.strip()) < min_stem_chars:
        return True
    if " - " not in stem:
        return True
    if re.fullmatch(r"(?:[0-9A-Za-z]+[-_.]?)+", stem):
        return True
    return False


def filename_looks_downloaded(row: dict[str, str]) -> bool:
    filename = row.get("filename", "")
    stem = filename[:-4] if filename.lower().endswith(".pdf") else filename
    lowered = stem.lower()

    explicit_patterns = (
        r"^1-s2\.0-",
        r"^elife-\d+",
        r"^bio[a-z]*rxiv[-_]",
        r"^supplement",
        r"^full[-_ ]?text",
        r"^main$",
        r"^s\d+(?:[-_.]\d+)*$",
        r"^article$",
    )
    if any(re.search(pattern, lowered) for pattern in explicit_patterns):
        return True
    return False


def filename_needs_touchup(row: dict[str, str], min_stem_chars: int) -> bool:
    filename = row.get("filename", "")
    stem = filename[:-4] if filename.lower().endswith(".pdf") else filename
    if len(stem.strip()) < min_stem_chars:
        return True
    if " -" in stem and " - " not in stem:
        return True
    if "- " in stem and " - " not in stem:
        return True
    return False


def supplement_suffix(filename: str) -> str:
    if re.search(r"\bSM\b", filename, re.I):
        return " - SM"
    if re.search(r"\bSI\b|\bsupp\b|\bsupplement\b", filename, re.I):
        return " - SI"
    return ""


def desired_filename(row: dict[str, str]) -> str | None:
    if row.get("match_status") not in RESOLVED_STATUSES:
        return None

    authors = row.get("authors", "")
    year = row.get("year", "").strip()
    title = row.get("resolved_title", "").strip() or row.get("title_query", "").strip()
    if not year or not title:
        return None

    author = first_author_surname(authors)
    journal = infer_journal_abbrev(row)
    suffix = supplement_suffix(row.get("filename", ""))
    title_part = safe_filename_fragment(title)
    return f"{author} {year} {journal} - {title_part}{suffix}.pdf"


def load_rows() -> list[dict[str, str]]:
    with MASTER_INDEX.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_rows(rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    ingest.write_master_index_rows(rows)


def update_record_pdf_path(paper_id: str, new_path: Path) -> None:
    record_path = RECORDS_DIR / f"{paper_id}.json"
    if not record_path.exists():
        return
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    payload["pdf_path"] = str(new_path)
    ingest.write_record_json(record_path, payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rename resolved library PDFs from the canonical index.")
    parser.add_argument("--apply", action="store_true", help="Apply the renames instead of printing a dry run")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of rename operations")
    parser.add_argument(
        "--bad-filenames-only",
        action="store_true",
        help="Only rename files whose current names look like poor downloaded/default names",
    )
    parser.add_argument(
        "--downloaded-only",
        action="store_true",
        help="Only rename files whose current names look like original downloaded/default filenames",
    )
    parser.add_argument(
        "--min-stem-chars",
        type=int,
        default=20,
        help="Minimum stem length used by --bad-filenames-only heuristic (default: 20)",
    )
    parser.add_argument(
        "--touchup-only",
        action="store_true",
        help="Only rename genuinely short filenames or near-canonical names with malformed delimiter spacing",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = load_rows()
    fieldnames = list(rows[0].keys()) if rows else []

    planned: list[tuple[dict[str, str], Path, Path]] = []
    for row in rows:
        rel = row.get("pdf_path", "")
        if not rel.startswith("articles/"):
            continue
        current = LIBRARY_ROOT / rel
        target_name = desired_filename(row)
        if not target_name or not current.exists():
            continue
        target = current.with_name(target_name)
        if current.name == target.name:
            continue
        if args.bad_filenames_only and not filename_looks_bad(row, args.min_stem_chars):
            continue
        if args.downloaded_only and not filename_looks_downloaded(row):
            continue
        if args.touchup_only and not filename_needs_touchup(row, args.min_stem_chars):
            continue
        planned.append((row, current, target))

    if args.limit > 0:
        planned = planned[: args.limit]

    for row, current, target in planned:
        print(f"{current} -> {target}")

    if not args.apply:
        print(f"\nDry run only. Planned renames: {len(planned)}")
        return 0

    for row, current, target in planned:
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            candidate = target
            idx = 2
            while candidate.exists():
                candidate = target.with_name(f"{stem} ({idx}){suffix}")
                idx += 1
            target = candidate

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(current), str(target))
        row["pdf_path"] = str(target.resolve().relative_to(LIBRARY_ROOT))
        row["filename"] = target.name
        update_record_pdf_path(row["paper_id"], target.resolve())

    write_rows(rows, fieldnames)
    print(f"\nApplied renames: {len(planned)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
