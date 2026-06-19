#!/usr/bin/env python3
"""Read-only Semantic Scholar fetch tool for metadata, BibTeX, and optional PDF/text artifacts."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import library_ingest as ingest


def eprint(*parts: object) -> None:
    print(*parts, file=sys.stderr)


def dedupe_queries(queries: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for query in queries:
        normalized = ingest.normalize_title(query)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(" ".join(query.split()).strip())
    return ordered


def print_candidates(candidates: list[dict[str, Any]], limit: int) -> None:
    if not candidates:
        eprint("No Semantic Scholar candidates found.")
        return
    for index, entry in enumerate(candidates[:limit], start=1):
        metadata = ingest.semantic_candidate_to_metadata(entry["candidate"])
        authors = metadata["authors"]
        author_preview = authors if len(authors) <= 90 else authors[:87] + "..."
        eprint(
            f"[{index}] score={entry['score']:.4f} year={metadata['year'] or 'n/a'} "
            f"venue={metadata['venue'] or 'n/a'} doi={metadata['doi'] or 'n/a'}"
        )
        eprint(f"    title: {metadata['title'] or 'n/a'}")
        eprint(f"    authors: {author_preview or 'n/a'}")


def parse_args(mode: str = "full") -> argparse.Namespace:
    description = (
        "Search Semantic Scholar and optionally download a PDF or extract plain text "
        "without modifying the local library."
        if mode == "full"
        else "Search Semantic Scholar and output one canonical BibTeX entry without modifying the local library."
    )
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--query", required=True, help="Paper title or keywords")
    parser.add_argument("--semantic-limit", type=int, default=10, help="Semantic Scholar result limit per query (default: 10)")
    parser.add_argument("--best", action="store_true", help="Select the top-ranked candidate automatically")
    parser.add_argument("--select", type=int, help="Select a 1-based candidate rank automatically")
    parser.add_argument("--list", action="store_true", help="Only list candidates, do not output a selected BibTeX entry")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of raw BibTeX")
    if mode == "full":
        parser.add_argument("--download-pdf", action="store_true", help="Download the selected open-access PDF when available")
        parser.add_argument("--extract-text", action="store_true", help="Extract plain text with pdf2text.py after downloading the PDF")
        parser.add_argument("--output-dir", type=Path, help="Output directory for downloaded PDFs and extracted text (default: current directory)")
    return parser.parse_args()


def candidate_to_bibtex(candidate: dict[str, Any], query: str) -> dict[str, Any]:
    metadata = ingest.semantic_candidate_to_metadata(candidate)
    bibtex_type = "article"
    bibtex_key = ingest.build_bibtex_key_from_fields(
        metadata.get("authors", ""),
        metadata.get("year", ""),
        metadata.get("title", ""),
        fallback_stem=query,
    )
    raw = ingest.build_bibtex_entry_from_fields(
        bibtex_type,
        bibtex_key,
        {
            "title": metadata.get("title", ""),
            "authors": metadata.get("authors", ""),
            "year": metadata.get("year", ""),
            "venue": metadata.get("venue", ""),
            "doi": metadata.get("doi", ""),
            "url": metadata.get("url", ""),
            "abstract": metadata.get("abstract", ""),
        },
    )
    canonical = ingest.canonicalize_bibtex_entry(ingest.parse_bibtex_entry(raw))
    return {
        "bibtex_key": canonical.get("bibtex_key", ""),
        "bibtex_type": canonical.get("entry_type", ""),
        "metadata": metadata,
        "bibtex_entry": canonical.get("raw_bibtex", ""),
        "candidate": candidate,
        "ignored_fields": canonical.get("ignored_fields", []),
    }


def crossref_payload_to_bibtex(
    crossref_message: dict[str, Any],
    *,
    query: str,
    semantic_candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    crossref_fields = ingest.crossref_to_bibtex_fields(crossref_message)
    semantic_meta = ingest.semantic_candidate_to_metadata(semantic_candidate or {})
    merged_metadata = dict(crossref_fields)
    merged_metadata["abstract"] = crossref_fields.get("abstract") or semantic_meta.get("abstract", "")
    merged_metadata["semantic_scholar_paper_id"] = semantic_meta.get("semantic_scholar_paper_id", "")
    bibtex_type = "article"
    bibtex_key = ingest.build_bibtex_key_from_fields(
        merged_metadata.get("authors", ""),
        merged_metadata.get("year", ""),
        merged_metadata.get("title", ""),
        fallback_stem=query,
    )
    raw = ingest.build_bibtex_entry_from_fields(bibtex_type, bibtex_key, merged_metadata)
    canonical = ingest.canonicalize_bibtex_entry(ingest.parse_bibtex_entry(raw))
    merged_metadata["bibtex_key"] = canonical.get("bibtex_key", "")
    merged_metadata["bibtex_type"] = canonical.get("entry_type", "")
    return {
        "bibtex_key": canonical.get("bibtex_key", ""),
        "bibtex_type": canonical.get("entry_type", ""),
        "metadata": merged_metadata,
        "bibtex_entry": canonical.get("raw_bibtex", ""),
        "candidate": semantic_candidate,
        "ignored_fields": canonical.get("ignored_fields", []),
    }


def select_candidate_interactively(candidates: list[dict[str, Any]], limit: int) -> dict[str, Any] | None:
    eprint("")
    eprint("Actions: number = select candidate, r = retry with new query, q = cancel")
    selection = input("> ").strip()
    if not selection:
        return {"action": "cancel"}
    if selection.lower() == "q":
        return {"action": "cancel"}
    if selection.lower() == "r":
        return {"action": "retry", "query": input("New search query: ").strip()}
    if selection.isdigit():
        index = int(selection)
        if 1 <= index <= min(limit, len(candidates)):
            return {"action": "select", "candidate": candidates[index - 1]["candidate"]}
    raise SystemExit("Unrecognized action.")


def search_loop(args: argparse.Namespace) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    queries = dedupe_queries([args.query])
    while True:
        attempts, merged = ingest.collect_semantic_candidates(
            queries=queries,
            filename=args.query,
            item_type="article",
            filename_hints={"year_hint": ingest.extract_year(args.query), "venue_hint": "", "prefix": args.query},
            semantic_limit=args.semantic_limit,
        )
        if args.list:
            return queries[-1], None, attempts, merged
        if args.best:
            candidate = merged[0]["candidate"] if merged else None
            return queries[-1], candidate, attempts, merged
        if args.select is not None:
            if not 1 <= args.select <= min(args.semantic_limit, len(merged)):
                raise SystemExit("Requested --select rank is out of range for the current candidate list.")
            return queries[-1], merged[args.select - 1]["candidate"], attempts, merged

        print_candidates(merged, args.semantic_limit)
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return queries[-1], None, attempts, merged
        action = select_candidate_interactively(merged, args.semantic_limit)
        if action is None or action.get("action") == "cancel":
            return queries[-1], None, attempts, merged
        if action.get("action") == "retry":
            retry_query = str(action.get("query") or "").strip()
            if retry_query:
                queries = dedupe_queries(queries + [retry_query])
            continue
        if action.get("action") == "select":
            return queries[-1], action.get("candidate"), attempts, merged


def attempts_payload(attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "query": attempt.get("query", ""),
            "status": attempt.get("status", ""),
            "candidate_count": attempt.get("candidate_count"),
            "best_score": attempt.get("best_score"),
            "warnings": attempt.get("warnings") or [],
        }
        for attempt in attempts
    ]


def candidates_payload(merged: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return [
        {
            "rank": index,
            "score": entry.get("score"),
            "metadata": ingest.semantic_candidate_to_metadata(entry["candidate"]),
            "candidate": entry["candidate"],
        }
        for index, entry in enumerate(merged[:limit], start=1)
    ]


def pdf_url_for_candidate(candidate: dict[str, Any]) -> str:
    open_access = candidate.get("openAccessPdf")
    direct_url = ""
    if isinstance(open_access, dict):
        direct_url = str(open_access.get("url") or "").strip()
        if direct_url and ".pdf" in direct_url.lower():
            return direct_url
    external_ids = candidate.get("externalIds") or {}
    if isinstance(external_ids, dict):
        arxiv_id = str(external_ids.get("ArXiv") or "").strip()
        if arxiv_id:
            return f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        pmc_id = str(external_ids.get("PubMedCentral") or "").strip()
        if pmc_id:
            pmc_id = pmc_id if pmc_id.upper().startswith("PMC") else f"PMC{pmc_id}"
            return f"https://pmc.ncbi.nlm.nih.gov/articles/{pmc_id}/pdf"
    if direct_url:
        return direct_url
    return ""


def resolve_output_dir(raw: Path | None) -> Path:
    return (raw or Path.cwd()).expanduser().resolve()


def abbreviate_venue_name(venue: str) -> str:
    cleaned = re.sub(r"[^A-Za-z]+", " ", venue or "")
    parts = []
    for word in cleaned.split():
        if len(word) < 3:
            continue
        parts.append(word[:3].capitalize())
    return "".join(parts)


def canonical_fetch_stem(bibtex_key: str, metadata: dict[str, str], query_used: str) -> str:
    key = str(bibtex_key or "").strip()
    if not key:
        key = ingest.build_bibtex_key_from_fields(
            metadata.get("authors", ""),
            metadata.get("year", ""),
            metadata.get("title", "") or query_used,
            fallback_stem=query_used,
        )
    venue_suffix = abbreviate_venue_name(metadata.get("venue", ""))
    if venue_suffix:
        return f"{key}_{venue_suffix}"
    return key


def choose_pdf_filename(bibtex_key: str, metadata: dict[str, str], query_used: str) -> str:
    return f"{canonical_fetch_stem(bibtex_key, metadata, query_used)}.pdf"


def download_pdf(pdf_url: str, output_dir: Path, filename: str) -> tuple[Path | None, bool, str]:
    if not pdf_url:
        return None, False, "No open-access PDF URL is available for the selected record."
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / filename
    if target.exists():
        return target, True, ""
    request = urllib.request.Request(pdf_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            content_type = response.headers.get_content_type()
            data = response.read()
    except Exception as exc:
        return None, False, f"PDF download failed for {pdf_url}: {exc}"
    if content_type != "application/pdf" and not data.startswith(b"%PDF-"):
        return None, False, f"Open-access URL did not resolve to a PDF: {pdf_url}"
    target.write_bytes(data)
    return target, False, ""


def extract_text(pdf_path: Path) -> tuple[str, str, Path | None]:
    text_path = pdf_path.with_suffix(".txt")
    try:
        ingest.run_pdf2text(pdf_path, text_path)
    except Exception as exc:
        return "error", str(exc), None
    return "ok", "", text_path


def extract_text_for_metadata(pdf_path: Path) -> tuple[str, str, str]:
    with tempfile.TemporaryDirectory(prefix="library_fetch_") as tmpdir:
        temp_text_path = Path(tmpdir) / "extracted.txt"
        try:
            ingest.run_pdf2text(pdf_path, temp_text_path)
        except Exception as exc:
            return "error", str(exc), ""
        return "ok", "", temp_text_path.read_text(encoding="utf-8")


def build_full_payload(
    args: argparse.Namespace,
    query_used: str,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    payload = candidate_to_bibtex(candidate, query_used)
    metadata = payload["metadata"]
    pdf_url = pdf_url_for_candidate(candidate)
    result: dict[str, Any] = {
        "query": args.query,
        "query_used": query_used,
        "bibtex_key": payload["bibtex_key"],
        "bibtex_type": payload["bibtex_type"],
        "metadata": metadata,
        "bibtex_entry": payload["bibtex_entry"],
        "candidate": payload["candidate"],
        "ignored_fields": payload["ignored_fields"],
        "metadata_source": "semantic_scholar_primary",
        "doi_candidates": [],
        "doi_candidate_details": [],
        "accepted_doi": "",
        "crossref": {
            "best_candidate_doi": "",
            "accepted": False,
            "accepted_source": "",
            "matched_by": "",
            "score": None,
            "warnings": [],
            "error": "",
            "attempts": [],
        },
        "pdf_url": pdf_url,
        "download_requested": bool(getattr(args, "download_pdf", False)),
        "extract_requested": bool(getattr(args, "extract_text", False)),
        "downloaded_pdf_path": None,
        "download_reused_existing": False,
        "download_error": "",
        "extraction": {
            "status": "not_requested",
            "error": "",
            "text_path": None,
            "converter": str(ingest.PDF2TEXT),
            "python": ingest.resolve_pdf2text_python(),
        },
    }

    if not getattr(args, "download_pdf", False) and not getattr(args, "extract_text", False):
        return result

    output_dir = resolve_output_dir(getattr(args, "output_dir", None))
    filename = choose_pdf_filename(payload["bibtex_key"], metadata, query_used)
    pdf_path, reused, download_error = download_pdf(pdf_url, output_dir, filename)
    result["download_reused_existing"] = reused
    result["download_error"] = download_error
    result["downloaded_pdf_path"] = str(pdf_path) if pdf_path else None
    if not pdf_path:
        return result

    metadata_text_status = "not_run"
    metadata_text_error = ""
    metadata_text_content = ""
    if getattr(args, "extract_text", False):
        status, error, text_path = extract_text(pdf_path)
        result["extraction"] = {
            "status": status,
            "error": error,
            "text_path": str(text_path) if text_path else None,
            "converter": str(ingest.PDF2TEXT),
            "python": ingest.resolve_pdf2text_python(),
        }
        if status == "ok" and text_path:
            metadata_text_status = status
            metadata_text_content = text_path.read_text(encoding="utf-8")
        else:
            metadata_text_status = status
            metadata_text_error = error
    else:
        metadata_text_status, metadata_text_error, metadata_text_content = extract_text_for_metadata(pdf_path)

    if metadata_text_status == "ok" and metadata_text_content:
        filename_hints = ingest.derive_filename_hints(pdf_path)
        title_targets = [metadata.get("title", "")] + ingest.derive_text_title_candidates(metadata_text_content)
        doi_resolution = ingest.resolve_pdf_doi_metadata(
            pdf_path=pdf_path,
            extracted_text=metadata_text_content,
            filename_hints=filename_hints,
            title_targets=title_targets,
        )
        result["doi_candidates"] = doi_resolution["candidate_dois"]
        result["doi_candidate_details"] = doi_resolution["candidate_details"]
        result["accepted_doi"] = str((doi_resolution["accepted_metadata"] or {}).get("DOI") or "")
        result["crossref"] = {
            "best_candidate_doi": doi_resolution["best_candidate_doi"],
            "accepted": doi_resolution["accepted_metadata"] is not None,
            "accepted_source": doi_resolution["accepted_source"],
            "matched_by": doi_resolution["matched_by"],
            "score": doi_resolution["best_score"],
            "warnings": doi_resolution["warnings"],
            "error": doi_resolution["error"],
            "attempts": doi_resolution["crossref_attempts"],
        }
        if doi_resolution["accepted_metadata"] is not None:
            enriched_candidate = ingest.choose_semantic_candidate_for_doi(
                [{"candidate": candidate}],
                str((doi_resolution["accepted_metadata"] or {}).get("DOI") or ""),
                None,
            )
            payload = crossref_payload_to_bibtex(
                doi_resolution["accepted_metadata"],
                query=query_used,
                semantic_candidate=enriched_candidate,
            )
            metadata = payload["metadata"]
            result["bibtex_key"] = payload["bibtex_key"]
            result["bibtex_type"] = payload["bibtex_type"]
            result["metadata"] = metadata
            result["bibtex_entry"] = payload["bibtex_entry"]
            result["candidate"] = payload["candidate"]
            result["ignored_fields"] = payload["ignored_fields"]
            result["metadata_source"] = (
                "crossref_doi_plus_semantic_enrichment"
                if enriched_candidate is not None
                else "crossref_doi_primary"
            )
    elif metadata_text_status == "error" and not getattr(args, "extract_text", False):
        result["crossref"]["error"] = metadata_text_error

    return result


def emit_full_payload(result: dict[str, Any], args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    print(result["bibtex_entry"].rstrip())
    if result.get("pdf_url"):
        eprint(f"PDF URL: {result['pdf_url']}")
    if result.get("downloaded_pdf_path"):
        eprint(f"Downloaded PDF: {result['downloaded_pdf_path']}")
        if result.get("download_reused_existing"):
            eprint("Download reused existing file.")
    elif result.get("download_requested"):
        eprint(result.get("download_error") or "No PDF was downloaded.")
    extraction = result.get("extraction") or {}
    if extraction.get("status") == "ok":
        eprint(f"Extracted text: {extraction.get('text_path')}")
    elif extraction.get("status") == "error":
        eprint(f"Text extraction failed: {extraction.get('error')}")
    return 0


def main(mode: str = "full") -> int:
    args = parse_args(mode=mode)
    if getattr(args, "extract_text", False):
        args.download_pdf = True

    query_used, candidate, attempts, merged = search_loop(args)

    if args.list:
        if args.json:
            print(
                json.dumps(
                    {
                        "query": args.query,
                        "query_used": query_used,
                        "candidate_count": len(merged),
                        "candidates": candidates_payload(merged, args.semantic_limit),
                        "mode": mode,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print_candidates(merged, args.semantic_limit)
        return 0

    if candidate is None:
        if args.json:
            print(
                json.dumps(
                    {
                        "query": args.query,
                        "query_used": query_used,
                        "selected": None,
                        "candidate_count": len(merged),
                        "candidates": candidates_payload(merged, args.semantic_limit),
                        "attempts": attempts_payload(attempts),
                        "mode": mode,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        raise SystemExit("No candidate selected.")

    if mode == "bib_only":
        payload = candidate_to_bibtex(candidate, query_used)
        if args.json:
            print(
                json.dumps(
                    {
                        "query": args.query,
                        "query_used": query_used,
                        "bibtex_key": payload["bibtex_key"],
                        "bibtex_type": payload["bibtex_type"],
                        "metadata": payload["metadata"],
                        "bibtex_entry": payload["bibtex_entry"],
                        "candidate": payload["candidate"],
                        "ignored_fields": payload["ignored_fields"],
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(payload["bibtex_entry"].rstrip())
        return 0

    result = build_full_payload(args, query_used, candidate)
    return emit_full_payload(result, args)


if __name__ == "__main__":
    raise SystemExit(main())
