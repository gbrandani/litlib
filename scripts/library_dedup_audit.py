#!/usr/bin/env python3
"""Audit likely duplicate or malformed records in the local literature index.

This tool is intentionally read-only. It does not delete files or rewrite the
index. It groups records that appear to describe the same paper and emits a
classification plus a recommended next action.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import library_ingest as ingest


SCRIPT_DIR = Path(__file__).resolve().parent
LIBRARY_ROOT = SCRIPT_DIR.parent
INDEX_DIR = LIBRARY_ROOT / "paper_index"
MASTER_INDEX = INDEX_DIR / "master_index.tsv"
LOGS_DIR = INDEX_DIR / "logs"

NULLISH = {"", "none", "n/a", "na", "null"}
SUPPLEMENT_RE = re.compile(
    r"(?<![a-z0-9])(si|sm|supp|supplement|supplementary|supporting information|supporting material|supporting materials|appendix)(?![a-z0-9])",
    re.I,
)


def eprint(*parts: object) -> None:
    print(*parts, file=sys.stderr)


def is_meaningful(value: Any) -> bool:
    text = str(value or "").strip()
    return text.casefold() not in NULLISH


def clean_text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.casefold() in NULLISH else text


def normalize_bibtex_key(value: Any) -> str:
    text = clean_text(value)
    return text.casefold()


def normalize_title_year_key(row: dict[str, str]) -> str:
    title = ingest.normalize_title(row.get("resolved_title") or row.get("title_query") or "")
    year = clean_text(row.get("year"))
    if title and year:
        return f"{title}|{year}"
    return ""


def row_title(row: dict[str, str]) -> str:
    return clean_text(row.get("resolved_title") or row.get("title_query") or "")


def row_is_malformed(row: dict[str, str]) -> bool:
    if not clean_text(row.get("paper_id")):
        return True
    if clean_text(row.get("content_kind")) not in {ingest.CONTENT_KIND_PDF, ingest.CONTENT_KIND_REF}:
        return True
    if not row_title(row):
        return True
    return False


def row_is_supplement_like(row: dict[str, str]) -> bool:
    if clean_text(row.get("match_status")) == "matched_supplement":
        return True
    haystack = " ".join(
        clean_text(value)
        for value in [
            row.get("filename"),
            row.get("pdf_path"),
            row.get("resolved_title"),
            row.get("title_query"),
            row.get("notes"),
        ]
        if is_meaningful(value)
    )
    return bool(SUPPLEMENT_RE.search(haystack))


def row_score_for_preferred_keep(row: dict[str, str]) -> tuple[int, int, int, int, str]:
    score = 0
    if clean_text(row.get("content_kind")) == ingest.CONTENT_KIND_PDF:
        score += 100
    if not row_is_supplement_like(row):
        score += 50
    if clean_text(row.get("doi")):
        score += 20
    if clean_text(row.get("canonical_url")):
        score += 10
    if clean_text(row.get("match_status")) in {"matched", "matched_via_doi", "manual_verified"}:
        score += 5
    filename = clean_text(row.get("filename"))
    if " - " in filename:
        score += 3
    return (
        score,
        1 if clean_text(row.get("content_kind")) == ingest.CONTENT_KIND_PDF else 0,
        0 if row_is_supplement_like(row) else 1,
        1 if clean_text(row.get("doi")) else 0,
        clean_text(row.get("paper_id")),
    )


def shared_identity_labels(rows: list[dict[str, str]]) -> list[str]:
    labels: list[str] = []
    dois = {ingest.normalize_doi(row.get("doi", "")) for row in rows if clean_text(row.get("doi"))}
    bibs = {normalize_bibtex_key(row.get("bibtex_key")) for row in rows if clean_text(row.get("bibtex_key"))}
    title_years = {normalize_title_year_key(row) for row in rows if normalize_title_year_key(row)}
    shas = {clean_text(row.get("pdf_sha256")) for row in rows if clean_text(row.get("pdf_sha256"))}
    if len(dois) == 1 and dois:
        labels.append("same_doi")
    if len(bibs) == 1 and bibs:
        labels.append("same_bibtex_key")
    if len(title_years) == 1 and title_years:
        labels.append("same_title_year")
    if len(shas) == 1 and shas:
        labels.append("same_pdf_sha256")
    return labels


def classify_component(rows: list[dict[str, str]]) -> tuple[str, str]:
    malformed = any(row_is_malformed(row) for row in rows)
    all_pdf = all(clean_text(row.get("content_kind")) == ingest.CONTENT_KIND_PDF for row in rows)
    any_supp = any(row_is_supplement_like(row) for row in rows)
    all_supp = all(row_is_supplement_like(row) for row in rows)
    labels = set(shared_identity_labels(rows))

    if malformed:
        return (
            "malformed_rows",
            "Repair malformed rows before deduplicating. Some records are missing paper_id, title, or valid content_kind.",
        )
    if all_supp:
        return (
            "supplement_cluster",
            "Likely supplement/supporting-information cluster. Do not auto-merge with a main paper without manual review.",
        )
    if any_supp and all_pdf:
        return (
            "main_plus_supplement",
            "Likely main-paper plus supplement set. Keep separate unless you explicitly want to remove the supplement copy.",
        )
    if "same_pdf_sha256" in labels and all_pdf:
        return (
            "exact_pdf_content_duplicate",
            "Exact duplicate PDF content. Safe candidate for manual consolidation after choosing the preferred path.",
        )
    if {"same_doi", "same_title_year"} <= labels and all_pdf:
        return (
            "likely_duplicate_pdf_copies",
            "Likely duplicate copies of the same paper stored in multiple places. Safe candidate for manual consolidation after checking filenames and folders.",
        )
    if "same_bibtex_key" in labels and "same_title_year" in labels:
        return (
            "likely_duplicate_records",
            "Likely duplicate records for the same citation identity. Review before deleting because there may be alternate local copies.",
        )
    return (
        "manual_review",
        "Shared identity signals exist, but the cluster is not safe to consolidate automatically.",
    )


def row_summary(row: dict[str, str]) -> dict[str, Any]:
    return {
        "paper_id": clean_text(row.get("paper_id")),
        "content_kind": clean_text(row.get("content_kind")),
        "item_type": clean_text(row.get("item_type")),
        "bibtex_key": clean_text(row.get("bibtex_key")),
        "title": row_title(row),
        "year": clean_text(row.get("year")),
        "venue": clean_text(row.get("venue")),
        "doi": clean_text(row.get("doi")),
        "match_status": clean_text(row.get("match_status")),
        "pdf_sha256": clean_text(row.get("pdf_sha256")),
        "filename": clean_text(row.get("filename")),
        "pdf_path": clean_text(row.get("pdf_path")),
        "canonical_url": clean_text(row.get("canonical_url")),
        "supplement_like": row_is_supplement_like(row),
        "malformed": row_is_malformed(row),
    }


class DSU:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, value: int) -> int:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: int, right: int) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left != root_right:
            self.parent[root_right] = root_left


def load_rows() -> list[dict[str, str]]:
    all_rows = ingest.load_master_index_rows()
    return [row for row in all_rows if clean_text(row.get("match_status")) != "matched_supplement"]


def build_relation_keys(rows: list[dict[str, str]]) -> dict[tuple[str, str], list[int]]:
    relation_keys: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        doi = ingest.normalize_doi(row.get("doi", ""))
        if doi:
            relation_keys[("doi", doi)].append(index)
        bibtex_key = normalize_bibtex_key(row.get("bibtex_key"))
        if bibtex_key:
            relation_keys[("bibtex_key", bibtex_key)].append(index)
        title_year = normalize_title_year_key(row)
        if title_year:
            relation_keys[("title_year", title_year)].append(index)
        pdf_sha = clean_text(row.get("pdf_sha256"))
        if pdf_sha:
            relation_keys[("pdf_sha256", pdf_sha)].append(index)
    return relation_keys


def build_duplicate_components(rows: list[dict[str, str]]) -> list[list[int]]:
    relation_keys = build_relation_keys(rows)
    dsu = DSU(len(rows))
    for indices in relation_keys.values():
        if len(indices) < 2:
            continue
        first = indices[0]
        for index in indices[1:]:
            dsu.union(first, index)

    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(rows)):
        root = dsu.find(index)
        components[root].append(index)

    duplicate_components: list[list[int]] = []
    for indices in components.values():
        if len(indices) < 2:
            continue
        duplicate_components.append(sorted(indices))
    duplicate_components.sort(key=lambda indices: (-len(indices), indices[0]))
    return duplicate_components


def component_report(rows: list[dict[str, str]], indices: list[int]) -> dict[str, Any]:
    component_rows = [rows[index] for index in indices]
    classification, recommendation = classify_component(component_rows)
    sorted_rows = sorted(component_rows, key=row_score_for_preferred_keep, reverse=True)
    preferred = sorted_rows[0]
    relation_counts = Counter()
    relation_keys = build_relation_keys(component_rows)
    for relation_type, _ in relation_keys:
        relation_counts[relation_type] += 1

    return {
        "classification": classification,
        "recommendation": recommendation,
        "size": len(component_rows),
        "preferred_keep_paper_id": clean_text(preferred.get("paper_id")),
        "shared_identity_labels": shared_identity_labels(component_rows),
        "rows": [row_summary(row) for row in sorted_rows],
    }


def malformed_singletons(rows: list[dict[str, str]], component_indices: set[int]) -> list[dict[str, Any]]:
    malformed: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if index in component_indices:
            continue
        if row_is_malformed(row):
            malformed.append(row_summary(row))
    return malformed


def build_report(rows: list[dict[str, str]]) -> dict[str, Any]:
    components = build_duplicate_components(rows)
    component_reports = [component_report(rows, indices) for indices in components]
    component_indices = {index for component in components for index in component}
    malformed = malformed_singletons(rows, component_indices)
    summary = Counter(report["classification"] for report in component_reports)
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "library_root": str(LIBRARY_ROOT),
        "master_index": str(MASTER_INDEX),
        "total_rows": len(rows),
        "duplicate_group_count": len(component_reports),
        "malformed_singleton_count": len(malformed),
        "classification_counts": dict(sorted(summary.items())),
        "duplicate_groups": component_reports,
        "malformed_singletons": malformed,
    }


def print_human_summary(report: dict[str, Any], limit: int) -> None:
    print(f"[library_dedup_audit] total_rows={report['total_rows']}")
    print(f"[library_dedup_audit] duplicate_groups={report['duplicate_group_count']}")
    print(f"[library_dedup_audit] malformed_singletons={report['malformed_singleton_count']}")
    for classification, count in sorted(report["classification_counts"].items()):
        print(f"[library_dedup_audit] classification={classification} count={count}")

    for index, group in enumerate(report["duplicate_groups"][:limit], start=1):
        print("")
        print(
            f"[{index}] classification={group['classification']} size={group['size']} "
            f"preferred_keep={group['preferred_keep_paper_id'] or 'n/a'}"
        )
        print(f"    shared_identity={', '.join(group['shared_identity_labels']) or 'n/a'}")
        print(f"    recommendation: {group['recommendation']}")
        for row in group["rows"][:5]:
            location = row["pdf_path"] or row["filename"] or row["title"]
            print(
                f"    - {row['paper_id'] or 'n/a'} "
                f"{row['content_kind'] or 'n/a'} "
                f"{row['bibtex_key'] or 'n/a'} "
                f"{location}"
            )

    if report["malformed_singletons"][:limit]:
        print("")
        print("[malformed_singletons]")
        for row in report["malformed_singletons"][:limit]:
            location = row["pdf_path"] or row["filename"] or row["title"]
            print(f"    - {row['paper_id'] or 'n/a'} {row['bibtex_key'] or 'n/a'} {location}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit likely duplicate or malformed library records without changing the index.")
    parser.add_argument("--json", action="store_true", help="Emit the full report as JSON to stdout")
    parser.add_argument("--limit", type=int, default=20, help="Maximum number of groups to show in human-readable mode")
    parser.add_argument(
        "--write-json",
        type=Path,
        help="Optional path for writing the full JSON report",
    )
    parser.add_argument(
        "--write-default-json",
        action="store_true",
        help="Write the JSON report to paper_index/logs/dedup_audit_<timestamp>.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = load_rows()
    report = build_report(rows)

    if args.write_default_json:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        args.write_json = LOGS_DIR / f"dedup_audit_{stamp}.json"

    if args.write_json:
        output = args.write_json.expanduser()
        if not output.is_absolute():
            output = (LIBRARY_ROOT / output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        eprint(f"[library_dedup_audit] wrote_json={output}")

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_human_summary(report, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
