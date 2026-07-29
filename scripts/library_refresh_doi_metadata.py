#!/usr/bin/env python3
"""Safely update existing library entries using canonical DOI/Crossref metadata."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
LIBRARY_ROOT = SCRIPT_DIR.parent
sys.path.append(str(SCRIPT_DIR))

import library_bib_sanity_check as bib_sanity
import library_ingest as ingest
import library_ops as ops
import library_rebuild_master_bib as rebuild_bib


DEFAULT_MIN_SIMILARITY = 0.70


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely update library entries using canonical DOI/Crossref metadata."
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--paper-id", help="Refresh metadata for one paper_id")
    target.add_argument("--bibtex-key", help="Refresh metadata for one BibTeX key")
    target.add_argument("--pdf-path", help="Refresh metadata for one PDF path")
    parser.add_argument("--only-semantic", action="store_true", help="Only process records originating from Semantic Scholar")
    parser.add_argument("--limit", type=int, help="Maximum number of candidate records to process")
    parser.add_argument("--min-similarity", type=float, default=DEFAULT_MIN_SIMILARITY, help=f"Minimum title similarity threshold to accept (default: {DEFAULT_MIN_SIMILARITY})")
    parser.add_argument("--apply", action="store_true", help="Apply accepted updates to master index and records (default: dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Explicitly specify dry-run mode (default)")
    parser.add_argument("--force", action="store_true", help="Bypass title similarity safety check")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output")
    parser.add_argument("--verbose", action="store_true", help="Print detailed field-by-field diffs")
    return parser.parse_args()


def load_record(paper_id: str) -> dict[str, Any]:
    if not paper_id:
        return {}
    path = ingest.record_path_for_paper_id(paper_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def is_semantic_source(row: dict[str, str], record: dict[str, Any]) -> bool:
    source = str(record.get("metadata_source") or "").strip().casefold()
    paper_id = str(row.get("paper_id") or "").strip()
    s2_id = str(row.get("semantic_scholar_paper_id") or "").strip()
    if paper_id.startswith("s2-") or s2_id != "":
        return True
    if source in {"semantic_scholar", "semantic_scholar_primary", "crossref_doi_plus_semantic_enrichment"}:
        return True
    if isinstance(record.get("semantic_scholar"), dict):
        return True
    return False


def evaluate_record(row: dict[str, str], args: argparse.Namespace) -> dict[str, Any]:
    paper_id = row.get("paper_id", "").strip()
    bibtex_key = row.get("bibtex_key", "").strip()
    doi = ingest.normalize_doi(row.get("doi", ""))
    
    current_title = (row.get("resolved_title") or row.get("title_query") or "").strip()
    current_authors = (row.get("authors") or "").strip()
    current_year = (row.get("year") or "").strip()
    current_venue = (row.get("venue") or "").strip()
    
    record = load_record(paper_id)
    source = str(record.get("metadata_source") or "").strip()

    result: dict[str, Any] = {
        "paper_id": paper_id,
        "bibtex_key": bibtex_key,
        "doi": doi,
        "current": {
            "title": current_title,
            "authors": current_authors,
            "year": current_year,
            "venue": current_venue,
            "metadata_source": source,
        },
        "eligible": True,
        "skip_reason": "",
        "action": "skip",
        "updated": False,
    }

    if not doi:
        result["eligible"] = False
        result["skip_reason"] = "no_doi"
        return result

    if args.only_semantic and not is_semantic_source(row, record):
        result["eligible"] = False
        result["skip_reason"] = f"not_semantic_source:{source}"
        return result

    try:
        crossref_meta = ingest.crossref_fetch_by_doi(doi)
    except Exception as err:
        result["eligible"] = False
        result["skip_reason"] = f"crossref_fetch_error:{err}"
        return result

    if not crossref_meta:
        result["eligible"] = False
        result["skip_reason"] = "crossref_not_found"
        return result

    crossref_fields = ingest.crossref_to_bibtex_fields(crossref_meta)
    proposed_title = (crossref_fields.get("title") or "").strip()
    proposed_authors = (crossref_fields.get("authors") or "").strip()
    proposed_year = (crossref_fields.get("year") or "").strip()
    proposed_venue = (crossref_fields.get("venue") or "").strip()
    proposed_url = (crossref_fields.get("url") or "").strip()

    if not proposed_title:
        result["eligible"] = False
        result["skip_reason"] = "crossref_missing_title"
        return result

    title_sim = ingest.title_similarity(current_title, proposed_title) if (current_title and proposed_title) else 1.0
    result["title_similarity"] = round(title_sim, 4)
    result["proposed"] = {
        "title": proposed_title,
        "authors": proposed_authors,
        "year": proposed_year,
        "venue": proposed_venue,
        "url": proposed_url,
        "volume": crossref_fields.get("volume", ""),
        "number": crossref_fields.get("number", ""),
        "pages": crossref_fields.get("pages", ""),
        "publisher": crossref_fields.get("publisher", ""),
    }

    if title_sim < args.min_similarity and not args.force:
        result["eligible"] = False
        result["skip_reason"] = f"low_title_similarity:{title_sim:.3f}<{args.min_similarity}"
        return result

    changes: dict[str, dict[str, str]] = {}
    field_map = {
        "resolved_title": ("title", current_title, proposed_title),
        "authors": ("authors", current_authors, proposed_authors),
        "year": ("year", current_year, proposed_year),
        "venue": ("venue", current_venue, proposed_venue),
    }
    for key, (label, curr, prop) in field_map.items():
        if prop and curr != prop:
            changes[label] = {"current": curr, "proposed": prop}

    for bfield in ["volume", "number", "pages", "publisher"]:
        prop = (crossref_fields.get(bfield) or "").strip()
        if prop:
            changes[f"bibtex.{bfield}"] = {"current": "", "proposed": prop}

    result["changes"] = changes
    if changes:
        result["action"] = "update"
    else:
        result["action"] = "already_up_to_date"
        result["eligible"] = False
        result["skip_reason"] = "no_changes_needed"

    result["crossref_meta"] = crossref_meta
    return result


def apply_record_update(row: dict[str, str], eval_res: dict[str, Any]) -> None:
    paper_id = eval_res["paper_id"]
    proposed = eval_res["proposed"]

    # 1. Update Master Index row fields
    row["resolved_title"] = proposed["title"]
    if proposed["authors"]:
        row["authors"] = proposed["authors"]
    if proposed["year"]:
        row["year"] = proposed["year"]
    if proposed["venue"]:
        row["venue"] = proposed["venue"]
    if proposed["url"]:
        row["canonical_url"] = proposed["url"]
    row["match_status"] = "matched_via_doi"

    # 2. Update Record JSON
    record = load_record(paper_id)
    record["resolved_title"] = proposed["title"]
    if proposed["authors"]:
        record["authors"] = proposed["authors"]
    if proposed["year"]:
        record["year"] = proposed["year"]
    if proposed["venue"]:
        record["venue"] = proposed["venue"]
    if proposed["url"]:
        record["canonical_url"] = proposed["url"]
    
    record["match_status"] = "matched_via_doi"
    record["metadata_source"] = "crossref_doi_refresh"
    record["crossref"] = {
        "metadata": eval_res["crossref_meta"],
        "accepted": True,
        "accepted_doi": eval_res["doi"],
        "matched_by": "doi_refresh_script",
    }
    
    # Save record JSON
    rec_path = ingest.record_path_for_paper_id(paper_id)
    ingest.write_record_json(rec_path, record)


def main() -> int:
    args = parse_args()
    all_rows = ingest.load_master_index_rows()

    if args.paper_id or args.bibtex_key or args.pdf_path:
        target_row = ops.resolve_target_row(
            paper_id=args.paper_id,
            bibtex_key=args.bibtex_key,
            pdf_path=args.pdf_path,
        )
        pid = (target_row.get("paper_id") or "").strip()
        rows = [r for r in all_rows if r.get("paper_id") == pid] if pid else []
    else:
        rows = all_rows

    if args.limit:
        rows = rows[:args.limit]

    results: list[dict[str, Any]] = []
    updates_applied = 0

    print(f"Evaluating {len(rows)} records for DOI Crossref metadata refresh (apply={args.apply})...\n")

    for row in rows:
        eval_res = evaluate_record(row, args)
        if eval_res["action"] == "update" and args.apply:
            try:
                apply_record_update(row, eval_res)
                eval_res["updated"] = True
                updates_applied += 1
            except Exception as e:
                eval_res["updated"] = False
                eval_res["update_error"] = str(e)

        results.append(eval_res)

        if not args.json:
            action_label = f"[{eval_res['action'].upper()}]"
            if eval_res.get("updated"):
                action_label = "[UPDATED]"
            elif eval_res.get("skip_reason"):
                action_label = f"[SKIP: {eval_res['skip_reason']}]"

            print(f"{action_label} {eval_res['paper_id']} ({eval_res['bibtex_key']})")
            print(f"  Title: {eval_res['current']['title']}")
            if eval_res.get("title_similarity") is not None:
                print(f"  Title Similarity: {eval_res['title_similarity']}")
            
            if args.verbose and eval_res.get("changes"):
                print("  Changes:")
                for field, chg in eval_res["changes"].items():
                    print(f"    - {field}: '{chg['current']}' -> '{chg['proposed']}'")
            print()

    if args.apply and updates_applied > 0:
        print(f"Saving updated master index ({len(all_rows)} rows)...")
        ingest.write_master_index_rows(all_rows)

        print("Rebuilding master.bib from updated records...")
        rebuild_bib.main()

        print("Running BibTeX sanity check...")
        sys.argv = [str(Path(bib_sanity.__file__).name)]
        sanity_res = bib_sanity.main()
        if sanity_res == 0:
            print("✓ BibTeX sanity check passed cleanly!")
        else:
            print("⚠️ BibTeX sanity check returned warnings/errors.")

    if args.json:
        output_data = {
            "apply": args.apply,
            "total_evaluated": len(results),
            "updates_count": sum(1 for r in results if r["action"] == "update"),
            "updates_applied": updates_applied,
            "results": [
                {k: v for k, v in r.items() if k != "crossref_meta"}
                for r in results
            ],
        }
        print(json.dumps(output_data, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
