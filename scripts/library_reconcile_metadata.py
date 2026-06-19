#!/usr/bin/env python3
"""Audit or apply DOI/Crossref metadata upgrades for existing PDF records."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import library_bib_sanity_check as bib_sanity
import library_ingest as ingest
import library_ops as ops


SKIP_METADATA_SOURCES = {
    "manual_bibtex",
    "manual_verified",
    "manual_supplement_link",
    "zotero_verified_match",
}

CROSSREF_REPORT_FIELDS = {
    "DOI",
    "URL",
    "abstract",
    "author",
    "container-title",
    "issue",
    "issued",
    "page",
    "publisher",
    "title",
    "type",
    "volume",
    "ISSN",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply DOI/Crossref metadata upgrades for existing library records."
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--paper-id", help="Reconcile one internal paper_id")
    target.add_argument("--bibtex-key", help="Reconcile one BibTeX key")
    target.add_argument("--pdf-path", help="Reconcile one absolute or library-relative PDF path")
    parser.add_argument("--status", action="append", help="Only process rows with this match_status; may be repeated")
    parser.add_argument("--only-semantic", action="store_true", help="Only process records whose metadata source is Semantic Scholar-like")
    parser.add_argument("--include-manual", action="store_true", help="Allow manual/zotero/supplement records to be considered")
    parser.add_argument("--limit", type=int, help="Maximum number of candidate rows to evaluate")
    parser.add_argument("--min-score", type=float, default=ingest.DOI_ACCEPT_THRESHOLD, help=f"Minimum Crossref title score to accept (default: {ingest.DOI_ACCEPT_THRESHOLD})")
    parser.add_argument("--apply", action="store_true", help="Apply accepted upgrades. Default is dry-run.")
    parser.add_argument(
        "--apply-rewrites",
        action="store_true",
        help="Allow applying identity/version-changing corrections. Default apply only enriches otherwise consistent entries.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--write-report", type=Path, help="Write the full JSON report to this path")
    return parser.parse_args()


def load_record(row: dict[str, str]) -> dict[str, Any]:
    paper_id = str(row.get("paper_id") or "").strip()
    if not paper_id:
        return {}
    path = ingest.record_path_for_paper_id(paper_id)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def record_metadata_source(record: dict[str, Any]) -> str:
    return str(record.get("metadata_source") or "").strip()


def semantic_like_source(source: str) -> bool:
    normalized = source.strip().casefold()
    return normalized in {"", "semantic_scholar", "semantic_scholar_primary"}


def supplement_like_row(row: dict[str, str]) -> bool:
    text = " ".join(
        str(row.get(field) or "")
        for field in ["filename", "pdf_path", "resolved_title", "title_query", "match_status"]
    ).casefold()
    markers = [
        " - si",
        "_si",
        " si.pdf",
        " - sm",
        "_sm",
        " sm.pdf",
        "supplement",
        "supporting information",
        "matched_supplement",
    ]
    return any(marker in text for marker in markers)


def eligible_row(row: dict[str, str], record: dict[str, Any], args: argparse.Namespace) -> tuple[bool, str]:
    if row.get("content_kind") != ingest.CONTENT_KIND_PDF:
        return False, "not_pdf_backed"
    if supplement_like_row(row):
        return False, "supplement"
    source = record_metadata_source(record)
    if source in SKIP_METADATA_SOURCES and not args.include_manual:
        return False, f"protected_source:{source}"
    if args.only_semantic and not semantic_like_source(source):
        return False, f"not_semantic_source:{source or '(blank)'}"
    if args.status and row.get("match_status") not in set(args.status):
        return False, f"status_filtered:{row.get('match_status')}"
    if not row.get("pdf_path"):
        return False, "missing_pdf_path"
    if not row.get("text_path"):
        return False, "missing_text_path"
    record_pdf_path = str(record.get("pdf_path") or "").strip()
    record_filename = str(record.get("filename") or "").strip()
    if record_pdf_path and record_filename and Path(record_pdf_path).name != record_filename:
        return False, f"invalid_record_filename:{record_filename}"
    return True, ""


def selected_rows(args: argparse.Namespace) -> list[dict[str, str]]:
    if args.paper_id or args.bibtex_key or args.pdf_path:
        resolved_row = ops.resolve_target_row(
            paper_id=args.paper_id,
            bibtex_key=args.bibtex_key,
            pdf_path=args.pdf_path,
        )
        paper_id = str(resolved_row.get("paper_id") or "").strip()
        row = ingest.load_master_index_row_by_paper_id(paper_id) if paper_id else None
        return [row or ingest.master_index_row_defaults(resolved_row)]
    rows = ingest.load_master_index_rows()
    output: list[dict[str, str]] = []
    for row in rows:
        record = load_record(row)
        ok, _reason = eligible_row(row, record, args)
        if ok:
            output.append(row)
        if args.limit and len(output) >= args.limit:
            break
    return output


def read_text_for_row(row: dict[str, str]) -> str:
    rel_text = str(row.get("text_path") or "").strip()
    if not rel_text:
        return ""
    text_path = ingest.LIBRARY_ROOT / rel_text
    if not text_path.exists():
        return ""
    return text_path.read_text(encoding="utf-8")


def changed_fields(row: dict[str, str], crossref_meta: dict[str, Any]) -> dict[str, dict[str, str]]:
    crossref_fields = ingest.crossref_to_bibtex_fields(crossref_meta)
    mapping = {
        "resolved_title": crossref_fields.get("title", ""),
        "authors": crossref_fields.get("authors", ""),
        "year": crossref_fields.get("year", ""),
        "venue": crossref_fields.get("venue", ""),
        "doi": crossref_fields.get("doi", ""),
        "canonical_url": crossref_fields.get("url", ""),
    }
    changes: dict[str, dict[str, str]] = {}
    for field, proposed in mapping.items():
        current = str(row.get(field) or "").strip()
        proposed = str(proposed or "").strip()
        if proposed and current != proposed:
            changes[field] = {"current": current, "proposed": proposed}
    for bib_field in ["volume", "number", "pages", "issn", "publisher"]:
        proposed = str(crossref_fields.get(bib_field) or "").strip()
        if proposed:
            changes[f"bibtex.{bib_field}"] = {"current": "", "proposed": proposed}
    return changes


def normalized_value(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def first_author_surname(authors: str) -> str:
    text = normalized_value(authors)
    if not text:
        return ""
    if " and " in text:
        first_author = text.split(" and ", 1)[0].strip()
        if "," in first_author:
            return ingest.normalize_title(first_author.split(",", 1)[0])
    else:
        first_author = text.split(",", 1)[0].strip()
    parts = first_author.split()
    return ingest.normalize_title(parts[-1] if parts else first_author)


def preprint_like(value: str) -> bool:
    text = str(value or "").casefold()
    return any(token in text for token in ["biorxiv", "bio rxiv", "medrxiv", "arxiv", "preprint"])


def classify_reconciliation(row: dict[str, str], crossref_fields: dict[str, str], changes: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Separate additive enrichment from identity/version-changing corrections."""
    reasons: list[str] = []
    current_title = normalized_value(row.get("resolved_title") or row.get("title_query") or "")
    proposed_title = normalized_value(crossref_fields.get("title", ""))
    title_score = ingest.title_similarity(current_title, proposed_title) if current_title and proposed_title else 0.0
    if current_title and proposed_title and title_score < 0.92:
        reasons.append(f"title_changed_score={title_score:.3f}")

    current_doi = ingest.normalize_doi(row.get("doi", ""))
    proposed_doi = ingest.normalize_doi(crossref_fields.get("doi", ""))
    if current_doi and proposed_doi and current_doi.casefold() != proposed_doi.casefold():
        reasons.append("doi_changed")

    current_year = normalized_value(row.get("year", ""))
    proposed_year = normalized_value(crossref_fields.get("year", ""))
    if current_year and proposed_year and current_year != proposed_year:
        reasons.append("year_changed")

    current_authors = normalized_value(row.get("authors", ""))
    proposed_authors = normalized_value(crossref_fields.get("authors", ""))
    current_first = first_author_surname(current_authors)
    proposed_first = first_author_surname(proposed_authors)
    if current_first and proposed_first and current_first != proposed_first:
        reasons.append("first_author_changed")

    current_venue = normalized_value(row.get("venue", ""))
    proposed_venue = normalized_value(crossref_fields.get("venue", ""))
    if preprint_like(current_venue) and proposed_venue and not preprint_like(proposed_venue):
        reasons.append("preprint_to_journal")
    if current_doi.startswith(("10.1101/", "10.48550/")) and proposed_doi and current_doi != proposed_doi:
        reasons.append("preprint_doi_to_journal_doi")

    field_groups = {
        "identity": [field for field in ["resolved_title", "authors", "year", "doi"] if field in changes],
        "venue_url": [field for field in ["venue", "canonical_url"] if field in changes],
        "bibtex_enrichment": sorted(field for field in changes if field.startswith("bibtex.")),
    }
    job_type = "rewrite_entry" if reasons else "enrich_existing_entry"
    apply_policy = (
        "requires --apply-rewrites because current identity/version fields would change"
        if reasons
        else "safe for --apply after dry-run review"
    )
    return {
        "job_type": job_type,
        "safety": "review_required" if reasons else "batch_apply_ok",
        "apply_policy": apply_policy,
        "rewrite_reasons": reasons,
        "title_similarity_current_vs_crossref": round(title_score, 4) if title_score else None,
        "field_groups": field_groups,
    }


def metadata_losses(row: dict[str, str], crossref_fields: dict[str, str]) -> list[str]:
    mapping = {
        "resolved_title": "title",
        "authors": "authors",
        "year": "year",
        "venue": "venue",
        "doi": "doi",
    }
    losses: list[str] = []
    for row_field, proposed_field in mapping.items():
        current = normalized_value(row.get(row_field) or (row.get("title_query") if row_field == "resolved_title" else ""))
        proposed = normalized_value(crossref_fields.get(proposed_field, ""))
        if current and not proposed:
            losses.append(row_field)
    return losses


def compact_crossref_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {key: metadata[key] for key in CROSSREF_REPORT_FIELDS if key in metadata}


def compact_crossref_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
    compact = dict(attempt)
    metadata = compact.get("metadata")
    if isinstance(metadata, dict):
        compact["metadata"] = compact_crossref_metadata(metadata)
    return compact


def evaluate_row(row: dict[str, str], args: argparse.Namespace) -> dict[str, Any]:
    record = load_record(row)
    ok, skip_reason = eligible_row(row, record, args)
    result: dict[str, Any] = {
        "paper_id": row.get("paper_id", ""),
        "bibtex_key": row.get("bibtex_key", ""),
        "pdf_path": row.get("pdf_path", ""),
        "current": {
            "metadata_source": record_metadata_source(record),
            "match_status": row.get("match_status", ""),
            "title": row.get("resolved_title") or row.get("title_query") or "",
            "doi": row.get("doi", ""),
            "venue": row.get("venue", ""),
            "year": row.get("year", ""),
        },
        "eligible": ok,
        "skip_reason": skip_reason,
        "proposed_action": "skip",
        "accepted": False,
        "applied": False,
    }
    if not ok:
        return result

    pdf_path = ingest.LIBRARY_ROOT / str(row.get("pdf_path") or "")
    text_content = read_text_for_row(row)
    if not text_content:
        result["skip_reason"] = "missing_text_content"
        result["eligible"] = False
        return result

    filename_hints = ingest.derive_filename_hints(pdf_path)
    title_targets = [
        row.get("resolved_title") or row.get("title_query") or "",
        row.get("title_query") or "",
    ] + ingest.derive_text_title_candidates(text_content)
    resolution = ingest.resolve_pdf_doi_metadata(
        pdf_path=pdf_path,
        extracted_text=text_content,
        filename_hints=filename_hints,
        title_targets=title_targets,
    )
    accepted = resolution.get("accepted_metadata") is not None and float(resolution.get("best_score") or 0.0) >= args.min_score
    result.update(
        {
            "doi_candidates": resolution.get("candidate_dois") or [],
            "best_candidate_doi": resolution.get("best_candidate_doi") or "",
            "best_score": resolution.get("best_score"),
            "crossref_warnings": resolution.get("warnings") or [],
            "crossref_error": resolution.get("error") or "",
            "accepted": accepted,
            "proposed_action": "upgrade_to_crossref" if accepted else "review_or_skip",
        }
    )
    if resolution.get("best_metadata") is not None:
        crossref_fields = ingest.crossref_to_bibtex_fields(resolution["best_metadata"])
        changes = changed_fields(row, resolution["best_metadata"])
        result["changes"] = changes
        result["proposed"] = crossref_fields
        result["reconciliation"] = classify_reconciliation(row, crossref_fields, changes)
        losses = metadata_losses(row, crossref_fields)
        if losses:
            result["accepted"] = False
            result["proposed_action"] = "review_or_skip"
            result["skip_reason"] = "metadata_loss:" + ",".join(losses)
    if result.get("accepted"):
        result["accepted_doi"] = str((resolution["accepted_metadata"] or {}).get("DOI") or "")
        result["resolution"] = {
            "candidate_details": resolution.get("candidate_details") or [],
            "crossref_attempts": [
                compact_crossref_attempt(attempt)
                for attempt in (resolution.get("crossref_attempts") or [])
            ],
            "accepted_source": resolution.get("accepted_source") or "",
            "matched_by": resolution.get("matched_by") or "",
        }
    return result


def apply_upgrade(row: dict[str, str], evaluation: dict[str, Any]) -> dict[str, Any]:
    paper_id = str(row.get("paper_id") or "")
    record = load_record(row)
    ops.validate_record_payload(record, paper_id)
    accepted_metadata = None
    for attempt in ((evaluation.get("resolution") or {}).get("crossref_attempts") or []):
        metadata = attempt.get("metadata")
        if metadata and str(metadata.get("DOI") or "").casefold() == str(evaluation.get("accepted_doi") or "").casefold():
            accepted_metadata = metadata
            break
    if accepted_metadata is None:
        raise SystemExit(f"Cannot apply {paper_id}: accepted Crossref metadata not available in evaluation report.")

    now_note = dt.datetime.now(dt.timezone.utc).isoformat()
    record["match_status"] = "matched_via_doi"
    record["metadata_source"] = "crossref_doi_plus_semantic_enrichment" if ((record.get("semantic_scholar") or {}).get("best_candidate")) else "crossref_doi_primary"
    record["crossref"] = {
        "error": evaluation.get("crossref_error") or "",
        "metadata": accepted_metadata,
        "accepted_score": evaluation.get("best_score"),
        "accepted": True,
        "accepted_doi": evaluation.get("accepted_doi") or "",
        "best_candidate_doi": evaluation.get("best_candidate_doi") or "",
        "crossref_attempts": (evaluation.get("resolution") or {}).get("crossref_attempts") or [],
        "accepted_source": (evaluation.get("resolution") or {}).get("accepted_source") or "crossref_doi",
        "matched_by": (evaluation.get("resolution") or {}).get("matched_by") or "doi_text_validation",
    }
    record["doi_candidates"] = evaluation.get("doi_candidates") or record.get("doi_candidates") or []
    record["doi_candidate_details"] = (evaluation.get("resolution") or {}).get("candidate_details") or record.get("doi_candidate_details") or []
    notes = list(record.get("crossref_warnings") or [])
    for warning in evaluation.get("crossref_warnings") or []:
        if warning not in notes:
            notes.append(warning)
    record["crossref_warnings"] = notes
    record["metadata_reconciliation"] = {
        "last_applied": now_note,
        "tool": "library_reconcile_metadata.py",
        "accepted_doi": evaluation.get("accepted_doi") or "",
        "score": evaluation.get("best_score"),
    }
    ingest.write_record_json(ingest.record_path_for_paper_id(paper_id), record)
    refreshed = ops.refresh_record_from_row(row)
    return refreshed


def print_human_result(result: dict[str, Any]) -> None:
    label = result.get("proposed_action", "skip")
    print(f"[{label}] {result.get('paper_id')} {result.get('bibtex_key') or '(no-key)'}")
    print(f"  title: {result.get('current', {}).get('title') or 'n/a'}")
    print(f"  current_source: {result.get('current', {}).get('metadata_source') or 'n/a'} status={result.get('current', {}).get('match_status') or 'n/a'}")
    if result.get("skip_reason"):
        print(f"  skip_reason: {result.get('skip_reason')}")
    if result.get("doi_candidates"):
        print(f"  doi_candidates: {', '.join(result.get('doi_candidates') or [])}")
    if result.get("best_candidate_doi"):
        print(f"  best_crossref: doi={result.get('best_candidate_doi')} score={result.get('best_score')}")
    if result.get("reconciliation"):
        recon = result["reconciliation"]
        print(f"  job_type: {recon.get('job_type')} safety={recon.get('safety')}")
        if recon.get("rewrite_reasons"):
            print(f"  rewrite_reasons: {', '.join(recon.get('rewrite_reasons') or [])}")
    if result.get("crossref_error"):
        print(f"  crossref_error: {result.get('crossref_error')}")
    if result.get("changes"):
        adds = []
        for field, change in result["changes"].items():
            if field.startswith("bibtex."):
                adds.append(f"{field}={change.get('proposed')}")
        if adds:
            print(f"  adds: {', '.join(adds)}")
    if result.get("applied"):
        print("  applied: yes")
    if result.get("apply_blocked"):
        print(f"  apply_blocked: {result.get('apply_blocked')}")


def main() -> int:
    args = parse_args()
    rows = selected_rows(args)
    results: list[dict[str, Any]] = []
    for row in rows:
        result = evaluate_row(row, args)
        if args.apply and result.get("accepted"):
            reconciliation = result.get("reconciliation") or {}
            if reconciliation.get("job_type") == "rewrite_entry" and not args.apply_rewrites:
                result["applied"] = False
                result["apply_blocked"] = "rewrite_entry requires --apply-rewrites"
            else:
                refreshed = apply_upgrade(row, result)
                result["applied"] = True
                result["refreshed_row"] = refreshed.get("row")
        results.append(result)
        if not args.json:
            print_human_result(result)

    report = {
        "apply": bool(args.apply),
        "count": len(results),
        "accepted_count": sum(1 for result in results if result.get("accepted")),
        "applied_count": sum(1 for result in results if result.get("applied")),
        "results": results,
    }
    if args.write_report:
        ingest.atomic_write_text(args.write_report.expanduser().resolve(), json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        if not args.json:
            print(f"[library_reconcile_metadata] wrote_report={args.write_report}")
    if args.apply:
        original_argv = sys.argv
        try:
            sys.argv = [str(Path(bib_sanity.__file__).name)]
            sanity_status = bib_sanity.main()
        finally:
            sys.argv = original_argv
        if sanity_status:
            return int(sanity_status)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
