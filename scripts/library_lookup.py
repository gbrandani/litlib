#!/usr/bin/env python3
"""Search the local literature library and render paste-ready citations.

This is a read-only interface over `paper_index/` intended for both humans and
other local tools. It does not mutate canonical bibliography records.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import library_ingest as ingest


SCRIPT_DIR = Path(__file__).resolve().parent
LIBRARY_ROOT = SCRIPT_DIR.parent
INDEX_DIR = LIBRARY_ROOT / "paper_index"
SEARCH_INDEX = INDEX_DIR / "search_index.jsonl"
JOURNAL_ABBREV_FILE = INDEX_DIR / "journal_abbreviations.tsv"
MASTER_BIB = INDEX_DIR / "master.bib"
MASTER_INDEX = INDEX_DIR / "master_index.tsv"
RECORDS_DIR = INDEX_DIR / "records"
CSL_DIR = INDEX_DIR / "csl"

STYLE_DEFINITIONS = {
    "nature": {
        "family": "Nature",
        "csl_path": CSL_DIR / "nature.csl",
    },
    "nar": {
        "family": "Nucleic Acids Research",
        "csl_path": CSL_DIR / "nar.csl",
    },
    "nlm": {
        "family": "NLM Citation Sequence",
        "csl_path": CSL_DIR / "nlm-citation-sequence.csl",
    },
    "ieee": {
        "family": "IEEE",
        "csl_path": CSL_DIR / "ieee.csl",
    },
    "acs": {
        "family": "ACS",
        "csl_path": CSL_DIR / "american-chemical-society.csl",
    },
}

STYLE_NORMALIZATION = {
    "nature": "nature",
    "natcommun": "natcommun",
    "naturecommunications": "natcommun",
    "naturecommunicationsstyle": "natcommun",
    "srep": "srep",
    "scientificreports": "srep",
    "science": "science",
    "scienceadv": "scienceadv",
    "scienceadvances": "scienceadv",
    "scientificadvances": "scienceadv",
    "nar": "nar",
    "nucleicacidsresearch": "nar",
    "pnas": "pnas",
    "proceedingsofthenationalacademyofsciencesoftheunitedstatesofamerica": "pnas",
    "prl": "prl",
    "physicalreviewletters": "prl",
    "ploscompbiol": "ploscompbiol",
    "ploscomputationalbiology": "ploscompbiol",
    "jctc": "jctc",
    "journalofchemicaltheoryandcomputation": "jctc",
    "journalofcomputertheoryandcommunications": "jctc",
    "nlm": "nlm",
    "ieee": "ieee",
    "acs": "acs",
}

STYLE_ALIASES = {
    "nature": "nature",
    "natcommun": "nature",
    "srep": "nature",
    "nlm": "nlm",
    "science": "nlm",
    "scienceadv": "nlm",
    "nar": "nar",
    "pnas": "nlm",
    "ploscompbiol": "nlm",
    "ieee": "ieee",
    "prl": "ieee",
    "acs": "acs",
    "jctc": "acs",
}

STYLE_HELP_LINES = [
    "Citation style to use. Defaults to 'nature'.",
    "Available styles:",
    "  nature: Nature family CSL",
    "  nar: Nucleic Acids Research-style CSL",
    "  nlm: NLM citation sequence",
    "  acs: ACS style",
    "  ieee: IEEE style",
    "Legacy journal aliases:",
    "  science -> nlm",
    "  scienceadv -> nlm",
    "  pnas -> nlm",
    "  ploscompbiol -> nlm",
    "  jctc -> acs",
    "  prl -> ieee",
    "  natcommun -> nature",
    "  srep -> nature",
]
STYLE_HELP_TEXT = "\n".join(STYLE_HELP_LINES)

SEARCH_INDEX_FIELDS = [
    "paper_id",
    "bibtex_key",
    "content_kind",
    "item_type",
    "title",
    "authors",
    "year",
    "venue",
    "doi",
    "canonical_url",
    "filename",
    "pdf_path",
    "text_path",
    "chunk_path",
    "abstract",
    "paper_id_norm",
    "bibtex_key_norm",
    "title_norm",
    "authors_norm",
    "venue_norm",
    "filename_norm",
    "abstract_norm",
    "title_tokens",
    "author_tokens",
    "venue_tokens",
    "filename_tokens",
    "abstract_tokens",
]


def eprint(*parts: object) -> None:
    print(*parts, file=sys.stderr)


def safe_year_int(value: Any) -> int | None:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}", text):
        return int(text)
    return None


def parse_year_filter(value: str | None) -> tuple[int | None, int | None] | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}", text):
        year = int(text)
        return year, year
    match = re.fullmatch(r"(?:(\d{4})?)\s*-\s*(?:(\d{4})?)", text)
    if not match:
        raise SystemExit("Invalid --year value. Use YYYY, YYYY-, -YYYY, or YYYY-YYYY.")
    start_text, end_text = match.groups()
    start = int(start_text) if start_text else None
    end = int(end_text) if end_text else None
    if start is None and end is None:
        raise SystemExit("Invalid --year value. Use YYYY, YYYY-, -YYYY, or YYYY-YYYY.")
    if start is not None and end is not None and start > end:
        raise SystemExit("Invalid --year range: start year is greater than end year.")
    return start, end


def year_matches_filter(row_year: Any, year_filter: tuple[int | None, int | None] | None) -> bool:
    if year_filter is None:
        return True
    row_year_int = safe_year_int(row_year)
    if row_year_int is None:
        return False
    start, end = year_filter
    if start is not None and row_year_int < start:
        return False
    if end is not None and row_year_int > end:
        return False
    return True


def compact_token(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").casefold())


def load_journal_abbreviations() -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not JOURNAL_ABBREV_FILE.exists():
        return mapping
    with JOURNAL_ABBREV_FILE.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            journal = ingest.normalize_venue_name((row.get("journal") or "").strip())
            abbreviation = (row.get("citation_abbreviation") or "").strip()
            if journal and abbreviation:
                mapping[journal] = abbreviation
    return mapping


def load_journal_abbreviations_full() -> dict[str, dict[str, str]]:
    mapping: dict[str, dict[str, str]] = {}
    if not JOURNAL_ABBREV_FILE.exists():
        return mapping
    try:
        with JOURNAL_ABBREV_FILE.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            for row in reader:
                journal = ingest.normalize_venue_name((row.get("journal") or "").strip())
                if journal:
                    mapping[journal] = {
                        "citation_abbreviation": (row.get("citation_abbreviation") or "").strip(),
                        "filename_abbreviation": (row.get("filename_abbreviation") or "").strip(),
                    }
    except Exception as e:
        eprint(f"Error loading journal abbreviations: {e}")
    return mapping


JOURNAL_ABBREVIATIONS = load_journal_abbreviations_full()


def split_bibtex_blocks(text: str) -> list[str]:
    return [block.strip() for block in re.split(r"\n(?=@)", text) if block.strip()]


def load_master_bib_entries() -> dict[str, dict[str, Any]]:
    if not MASTER_BIB.exists():
        return {}
    entries: dict[str, dict[str, Any]] = {}
    for block in split_bibtex_blocks(MASTER_BIB.read_text(encoding="utf-8")):
        parsed = ingest.parse_bibtex_entry(block)
        entries[parsed["bibtex_key"]] = parsed
    return entries


def normalize_abstract_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split()).strip()


def load_record_json(paper_id: str) -> dict[str, Any]:
    record_path = RECORDS_DIR / f"{paper_id}.json"
    if not record_path.exists():
        return {}
    try:
        payload = json.loads(record_path.read_text(encoding="utf-8"))
        return ingest.resolve_record_paths(payload, LIBRARY_ROOT)
    except json.JSONDecodeError:
        return {}


def extract_abstract(record: dict[str, Any]) -> str:
    if str(record.get("match_status") or "").strip() == "matched_supplement":
        return ""
    if isinstance(record.get("supplement_parent"), dict):
        return ""

    resolved = record.get("resolved_metadata")
    if isinstance(resolved, dict):
        abstract = normalize_abstract_text(str(resolved.get("abstract") or ""))
        if abstract:
            return abstract

    manual_override = record.get("manual_override")
    if isinstance(manual_override, dict):
        abstract = normalize_abstract_text(str(manual_override.get("abstract") or ""))
        if abstract:
            return abstract

    semantic = record.get("semantic_scholar")
    if isinstance(semantic, dict):
        best = semantic.get("best_candidate")
        if isinstance(best, dict):
            abstract = normalize_abstract_text(str(best.get("abstract") or ""))
            if abstract:
                return abstract

    crossref = record.get("crossref")
    if isinstance(crossref, dict):
        metadata = crossref.get("metadata")
        if isinstance(metadata, dict):
            abstract = normalize_abstract_text(str(metadata.get("abstract") or ""))
            if abstract:
                return abstract
    return ""


def search_index_record(row: dict[str, str], record: dict[str, Any]) -> dict[str, Any]:
    title = row.get("resolved_title", "") or row.get("title_query", "") or ""
    authors = row.get("authors", "") or ""
    venue = row.get("venue", "") or ""
    filename = row.get("filename", "") or ""
    abstract = extract_abstract(record)
    entry = {
        "paper_id": row.get("paper_id", "") or "",
        "bibtex_key": row.get("bibtex_key", "") or "",
        "content_kind": row.get("content_kind", "") or "",
        "item_type": row.get("item_type", "") or "",
        "title": title,
        "authors": authors,
        "year": row.get("year", "") or "",
        "venue": venue,
        "doi": row.get("doi", "") or "",
        "canonical_url": row.get("canonical_url", "") or "",
        "filename": filename,
        "pdf_path": row.get("pdf_path", "") or "",
        "text_path": row.get("text_path", "") or "",
        "chunk_path": row.get("chunk_path", "") or "",
        "abstract": abstract,
    }
    entry["paper_id_norm"] = compact_token(entry["paper_id"])
    entry["bibtex_key_norm"] = compact_token(entry["bibtex_key"])
    entry["title_norm"] = ingest.normalize_title(entry["title"])
    entry["authors_norm"] = ingest.normalize_title(entry["authors"])
    entry["venue_norm"] = ingest.normalize_title(entry["venue"])
    entry["filename_norm"] = ingest.normalize_title(entry["filename"])
    entry["abstract_norm"] = ingest.normalize_title(entry["abstract"])
    entry["title_tokens"] = sorted(ingest.informative_tokens(entry["title"]))
    entry["author_tokens"] = sorted(ingest.informative_tokens(entry["authors"]))
    
    # Expand venue tokens with citation and filename abbreviations
    venue_normalized = ingest.normalize_venue_name(entry["venue"])
    v_tokens = set(ingest.informative_tokens(entry["venue"]))
    if venue_normalized in JOURNAL_ABBREVIATIONS:
        abbrev_info = JOURNAL_ABBREVIATIONS[venue_normalized]
        cit_abbrev = abbrev_info.get("citation_abbreviation") or ""
        file_abbrev = abbrev_info.get("filename_abbreviation") or ""
        if cit_abbrev:
            v_tokens.update(ingest.informative_tokens(cit_abbrev))
        if file_abbrev:
            v_tokens.update(ingest.informative_tokens(file_abbrev))
    entry["venue_tokens"] = sorted(v_tokens)
    
    entry["filename_tokens"] = sorted(ingest.informative_tokens(entry["filename"]))
    entry["abstract_tokens"] = sorted(ingest.informative_tokens(entry["abstract"]))
    return entry


def search_index_is_stale() -> bool:
    if not SEARCH_INDEX.exists():
        return True
    index_mtime = SEARCH_INDEX.stat().st_mtime
    for path in [MASTER_INDEX, MASTER_BIB, RECORDS_DIR]:
        if path.exists() and path.stat().st_mtime > index_mtime:
            return True
    return False


def rebuild_search_index() -> None:
    lines: list[str] = []
    for row in ingest.load_master_index_rows():
        record = load_record_json(row.get("paper_id", ""))
        entry = search_index_record(row, record)
        lines.append(json.dumps(entry, ensure_ascii=False, sort_keys=True))
    text = ("\n".join(lines) + "\n") if lines else ""
    ingest.atomic_write_text(SEARCH_INDEX, text)


def ensure_search_index() -> bool:
    refreshed = search_index_is_stale()
    if refreshed:
        rebuild_search_index()
    return refreshed


def load_search_index_rows() -> list[dict[str, Any]]:
    ensure_search_index()
    rows: list[dict[str, Any]] = []
    if not SEARCH_INDEX.exists():
        return rows
    with SEARCH_INDEX.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            rows.append(json.loads(text))
    return rows


def resolve_style_alias(name: str) -> tuple[str, dict[str, Any]]:
    alias = STYLE_NORMALIZATION.get(compact_token(name), compact_token(name))
    canonical_name = STYLE_ALIASES.get(alias)
    style = STYLE_DEFINITIONS.get(canonical_name or "")
    if not style:
        supported = ", ".join(sorted(STYLE_DEFINITIONS))
        raise SystemExit(f"Unknown style '{name}'. Supported styles: {supported}")
    if not style["csl_path"].exists():
        raise SystemExit(f"Missing vendored CSL file: {style['csl_path']}")
    return canonical_name or alias, style


def exact_identifier_match(identifier: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    token = compact_token(identifier)
    if not token:
        return None
    for row in rows:
        if token in {
            row.get("paper_id_norm", ""),
            row.get("bibtex_key_norm", ""),
            compact_token(row.get("doi", "")),
        }:
            return row
    return None


def apply_filters(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    author_filter = ingest.normalize_title(args.author or "")
    venue_filter = ingest.normalize_title(args.venue or "")
    doi_filter = ingest.normalize_doi(args.doi or "")
    year_filter = parse_year_filter(args.year)
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if author_filter and author_filter not in row.get("authors_norm", ""):
            continue
        if venue_filter and venue_filter not in row.get("venue_norm", ""):
            continue
        if doi_filter and ingest.normalize_doi(row.get("doi", "")) != doi_filter:
            continue
        if not year_matches_filter(row.get("year", ""), year_filter):
            continue
        if args.has_pdf and row.get("content_kind") != ingest.CONTENT_KIND_PDF:
            continue
        if args.reference_only and row.get("content_kind") != ingest.CONTENT_KIND_REF:
            continue
        filtered.append(row)
    return filtered


def extract_first_author_surname(authors_str: str) -> str:
    if not authors_str:
        return ""
    text = authors_str.strip()
    if " and " in text.lower():
        first_author = text.split(" and ", 1)[0].strip()
        if "," in first_author:
            return ingest.normalize_title(first_author.split(",", 1)[0])
    else:
        first_author = text.split(",", 1)[0].strip()
    parts = first_author.split()
    return ingest.normalize_title(parts[-1] if parts else first_author)


def extract_all_author_surnames(authors_str: str) -> list[str]:
    if not authors_str:
        return []
    text = authors_str.strip()
    if " and " in text.lower():
        authors = [a.strip() for a in re.split(r"\s+and\s+", text, flags=re.I) if a.strip()]
    else:
        authors = [a.strip() for a in text.split(",") if a.strip()]
        
    surnames = []
    for author in authors:
        if "," in author:
            surname = author.split(",", 1)[0].strip()
            surnames.append(ingest.normalize_title(surname))
        else:
            parts = author.split()
            if parts:
                surnames.append(ingest.normalize_title(parts[-1]))
    return [s for s in surnames if s]


def extract_quoted_phrases(query: str) -> list[str]:
    phrases = re.findall(r'"([^"]+)"', query)
    return [ingest.normalize_title(p) for p in phrases if p.strip()]


def metadata_score(row: dict[str, Any], query: str, include_abstract: bool) -> tuple[float, dict[str, float]]:
    if not query:
        return 0.0, {}

    query_norm = ingest.normalize_title(query)
    query_tokens = ingest.informative_tokens(query)
    score = 0.0
    breakdown: dict[str, float] = {}

    title_norm = row.get("title_norm", "")
    if query_norm and title_norm:
        if query_norm == title_norm:
            breakdown["title_exact"] = 120.0
        elif query_norm in title_norm:
            breakdown["title_phrase"] = 90.0
        else:
            similarity = ingest.title_similarity(query, row.get("title", ""))
            if similarity >= 0.45:
                breakdown["title_similarity"] = round(similarity * 55.0, 2)

    title_tokens = set(row.get("title_tokens") or [])
    title_overlap = len(query_tokens & title_tokens)
    if title_overlap:
        coverage = title_overlap / max(len(query_tokens), 1)
        breakdown["title_tokens"] = round(title_overlap * 9.0 + coverage * 18.0, 2)

    if query_norm and compact_token(query) == row.get("paper_id_norm", ""):
        breakdown["paper_id"] = 140.0
    if query_norm and compact_token(query) == row.get("bibtex_key_norm", ""):
        breakdown["bibtex_key"] = 130.0
    query_doi = ingest.normalize_doi(query)
    if query_doi and query_doi == ingest.normalize_doi(row.get("doi", "")):
        breakdown["doi_exact"] = 150.0

    # DOI substring match for individual query tokens (excluding purely numeric tokens)
    doi_val = str(row.get("doi") or "").lower()
    if doi_val:
        for token in query_tokens:
            if len(token) >= 4 and not token.isdigit() and token in doi_val:
                breakdown["doi_token_match"] = 40.0
                break

    author_tokens = set(row.get("author_tokens") or [])
    author_overlap = len(query_tokens & author_tokens)
    if author_overlap:
        breakdown["author_tokens"] = round(author_overlap * 6.0, 2)

    # Multi-author surname match
    author_surnames = extract_all_author_surnames(row.get("authors", ""))
    matched_surnames = [s for s in author_surnames if s in query_tokens]
    if matched_surnames:
        breakdown["any_author_match"] = 15.0
        first_author_surname = author_surnames[0] if author_surnames else ""
        if first_author_surname and first_author_surname in query_tokens:
            breakdown["first_author_match"] = 25.0

    # Year match with proximity scoring
    row_year_int = safe_year_int(row.get("year"))
    if row_year_int is not None:
        query_years = [int(t) for t in query_tokens if t.isdigit() and len(t) == 4 and 1800 <= int(t) <= 2100]
        if query_years:
            min_distance = min(abs(row_year_int - qy) for qy in query_years)
            year_boost = max(0.0, 35.0 - min_distance * 10.0)
            if year_boost > 0:
                if min_distance == 0:
                    breakdown["year_match"] = year_boost
                else:
                    breakdown["year_proximity_match"] = year_boost

    # Venue matching
    venue_tokens = set(row.get("venue_tokens") or [])
    venue_overlap = len(query_tokens & venue_tokens)
    if venue_overlap:
        breakdown["venue_tokens"] = round(venue_overlap * 4.0, 2)

    # Venue abbreviation boost
    venue_name = row.get("venue", "")
    venue_norm = ingest.normalize_venue_name(venue_name)
    if venue_norm in JOURNAL_ABBREVIATIONS:
        abbrev_info = JOURNAL_ABBREVIATIONS[venue_norm]
        file_abbrev = abbrev_info.get("filename_abbreviation") or ""
        if file_abbrev and compact_token(file_abbrev) in query_tokens:
            breakdown["venue_abbreviation_match"] = 25.0

    filename_tokens = set(row.get("filename_tokens") or [])
    filename_overlap = len(query_tokens & filename_tokens)
    if filename_overlap:
        breakdown["filename_tokens"] = round(filename_overlap * 2.5, 2)

    if include_abstract:
        abstract_tokens = set(row.get("abstract_tokens") or [])
        abstract_overlap = len(query_tokens & abstract_tokens)
        if abstract_overlap:
            breakdown["abstract_tokens"] = round(abstract_overlap * 2.0, 2)
        if query_norm and query_norm in row.get("abstract_norm", ""):
            breakdown["abstract_phrase"] = 6.0

    # Quoted phrase matches
    quoted_phrases = extract_quoted_phrases(query)
    for phrase in quoted_phrases:
        if phrase:
            if phrase in title_norm:
                breakdown["title_phrase_match"] = breakdown.get("title_phrase_match", 0.0) + 40.0
            elif include_abstract and phrase in row.get("abstract_norm", ""):
                breakdown["abstract_phrase_match"] = breakdown.get("abstract_phrase_match", 0.0) + 15.0

    # Global query token coverage boost
    all_record_tokens = (
        title_tokens |
        author_tokens |
        venue_tokens |
        filename_tokens |
        (set(row.get("abstract_tokens") or []) if include_abstract else set())
    )
    if row.get("year"):
        all_record_tokens.add(str(row.get("year")).strip())
    for s in author_surnames:
        all_record_tokens.add(s)

    query_overlap = query_tokens & all_record_tokens
    if query_overlap and query_tokens:
        coverage = len(query_overlap) / len(query_tokens)
        breakdown["query_coverage"] = round(coverage * 50.0, 2)

    score = round(sum(breakdown.values()), 2)
    return score, breakdown


def rg_fulltext_matches(query: str, query_tokens: set[str]) -> dict[str, dict[str, Any]]:
    if not shutil.which("rg"):
        return {}
    patterns = sorted(query_tokens)[:12]
    if not patterns:
        patterns = [query]
    cmd = ["rg", "-n", "-i", "-m", "2", "--no-heading", "--color", "never"]
    for pattern in patterns:
        cmd.extend(["-e", pattern])
    cmd.append(str(ingest.TEXT_DIR))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError:
        return {}
    if proc.returncode not in (0, 1):
        return {}

    results: dict[str, dict[str, Any]] = {}
    query_norm = ingest.normalize_title(query)
    for line in proc.stdout.splitlines():
        match = re.match(r"^(.*?):(\d+):(.*)$", line)
        if not match:
            continue
        path_text, _, snippet = match.groups()
        paper_id = Path(path_text).stem
        entry = results.setdefault(paper_id, {"snippets": [], "token_hits": set(), "score": 0.0})
        snippet_clean = " ".join(snippet.split())
        if snippet_clean:
            entry["snippets"].append(snippet_clean)
        snippet_tokens = ingest.informative_tokens(snippet_clean)
        overlap = query_tokens & snippet_tokens
        if overlap:
            entry["token_hits"].update(overlap)
            entry["score"] += len(overlap) * 2.0
        if query_norm and query_norm in ingest.normalize_title(snippet_clean):
            entry["score"] += 4.0

    normalized: dict[str, dict[str, Any]] = {}
    for paper_id, payload in results.items():
        normalized[paper_id] = {
            "score": round(payload["score"], 2),
            "token_hits": sorted(payload["token_hits"]),
            "snippets": payload["snippets"][:2],
        }
    return normalized


def python_fulltext_matches(query: str, query_tokens: set[str], rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    query_norm = ingest.normalize_title(query)
    for row in rows:
        text_path = row.get("text_path", "")
        if not text_path:
            continue
        try:
            resolved = (LIBRARY_ROOT / text_path).resolve()
            text = resolved.read_text(encoding="utf-8")
        except OSError:
            continue
        text_norm = ingest.normalize_title(text)
        snippets: list[str] = []
        token_hits: set[str] = set()
        score = 0.0
        if query_norm and query_norm in text_norm:
            score += 4.0
            idx = text_norm.find(query_norm)
            if idx >= 0:
                start = max(0, idx - 80)
                snippets.append(" ".join(text[start:start + 220].split()))
        text_tokens = ingest.informative_tokens(text)
        overlap = query_tokens & text_tokens
        if overlap:
            score += len(overlap) * 2.0
            token_hits.update(overlap)
        if score > 0:
            results[row["paper_id"]] = {
                "score": round(score, 2),
                "token_hits": sorted(token_hits),
                "snippets": snippets[:2],
            }
    return results


def fulltext_matches(query: str, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    query_tokens = ingest.informative_tokens(query)
    matches = rg_fulltext_matches(query, query_tokens)
    if matches:
        return matches
    return python_fulltext_matches(query, query_tokens, rows)


def combine_search_results(rows: list[dict[str, Any]], args: argparse.Namespace, query: str) -> list[dict[str, Any]]:
    scoped_rows = apply_filters(rows, args)
    include_abstract = not args.no_abstract
    results: list[dict[str, Any]] = []
    for row in scoped_rows:
        score, breakdown = metadata_score(row, query, include_abstract=include_abstract)
        results.append(
            {
                "row": row,
                "score": score,
                "score_breakdown": breakdown,
                "fulltext": {},
            }
        )

    need_fulltext = args.scope == "fulltext" or (args.scope == "auto" and query and not any(item["score"] > 0 for item in results))
    fulltext_by_id = fulltext_matches(query, scoped_rows) if query and need_fulltext else {}

    for result in results:
        paper_id = result["row"]["paper_id"]
        payload = fulltext_by_id.get(paper_id)
        if not payload:
            continue
        result["score"] = round(result["score"] + payload["score"], 2)
        result["score_breakdown"]["fulltext"] = payload["score"]
        result["fulltext"] = payload

    if query:
        results = [item for item in results if item["score"] > 0]

    results.sort(
        key=lambda item: (
            -int(item["score"] / 5),
            -(safe_year_int(item["row"].get("year")) or 0),
            item["row"].get("title", "").casefold(),
        )
    )
    return results[: args.limit]


def match_explanation(result: dict[str, Any]) -> str:
    breakdown = result.get("score_breakdown", {})
    if not breakdown:
        return "filters only"
    ranked = sorted(breakdown.items(), key=lambda item: (-item[1], item[0]))
    labels = []
    for name, value in ranked[:3]:
        labels.append(f"{name}={value:g}")
    return ", ".join(labels)


def relative_or_blank(path_text: str) -> str:
    if not path_text:
        return ""
    path = Path(path_text)
    if path.is_absolute():
        try:
            return str(path.relative_to(LIBRARY_ROOT))
        except ValueError:
            return str(path)
    return path_text


def search_output_record(result: dict[str, Any]) -> dict[str, Any]:
    row = result["row"]
    return {
        "paper_id": row.get("paper_id", ""),
        "bibtex_key": row.get("bibtex_key", ""),
        "title": row.get("title", ""),
        "authors": row.get("authors", ""),
        "year": row.get("year", ""),
        "venue": row.get("venue", ""),
        "doi": row.get("doi", ""),
        "content_kind": row.get("content_kind", ""),
        "canonical_url": row.get("canonical_url", ""),
        "filename": row.get("filename", ""),
        "pdf_path": relative_or_blank(row.get("pdf_path", "")),
        "text_path": relative_or_blank(row.get("text_path", "")),
        "chunk_path": relative_or_blank(row.get("chunk_path", "")),
        "score": result.get("score", 0.0),
        "score_breakdown": result.get("score_breakdown", {}),
        "match_explanation": match_explanation(result),
        "fulltext": result.get("fulltext", {}),
    }


def print_search_results(results: list[dict[str, Any]]) -> None:
    if not results:
        print("No matches.")
        return
    for index, result in enumerate(results, start=1):
        row = result["row"]
        print(
            f"[{index}] score={result['score']:.2f} kind={row.get('content_kind', '')} "
            f"year={row.get('year', '')} match={match_explanation(result)}"
        )
        print(f"    paper_id: {row.get('paper_id', '')}")
        print(f"    bibtex_key: {row.get('bibtex_key', '')}")
        print(f"    title: {row.get('title', '')}")
        print(f"    authors: {row.get('authors', '')}")
        print(f"    venue: {row.get('venue', '')}")
        if row.get("doi"):
            print(f"    doi: {row.get('doi', '')}")
        if result.get("fulltext", {}).get("snippets"):
            print(f"    fulltext: {result['fulltext']['snippets'][0]}")


def show_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "paper_id": row.get("paper_id", ""),
        "bibtex_key": row.get("bibtex_key", ""),
        "content_kind": row.get("content_kind", ""),
        "item_type": row.get("item_type", ""),
        "title": row.get("title", ""),
        "authors": row.get("authors", ""),
        "year": row.get("year", ""),
        "venue": row.get("venue", ""),
        "doi": row.get("doi", ""),
        "canonical_url": row.get("canonical_url", ""),
        "filename": row.get("filename", ""),
        "pdf_path": row.get("pdf_path", ""),
        "text_path": row.get("text_path", ""),
        "chunk_path": row.get("chunk_path", ""),
        "abstract": row.get("abstract", ""),
        "supported_styles": sorted(STYLE_DEFINITIONS),
        "style_aliases": {alias: STYLE_ALIASES[alias] for alias in sorted(STYLE_ALIASES) if alias not in STYLE_DEFINITIONS},
    }


def print_show_record(record: dict[str, Any]) -> None:
    for key in [
        "paper_id",
        "bibtex_key",
        "content_kind",
        "item_type",
        "title",
        "authors",
        "year",
        "venue",
        "doi",
        "canonical_url",
        "filename",
        "pdf_path",
        "text_path",
        "chunk_path",
    ]:
        value = record.get(key, "")
        if value:
            print(f"{key}: {value}")
    abstract = record.get("abstract", "")
    if abstract:
        print("abstract:")
        print(abstract)
    print("supported_styles:", ", ".join(record["supported_styles"]))


def validate_bib_entry(parsed: dict[str, Any]) -> list[str]:
    fields = parsed.get("fields", {})
    missing = []
    if not fields.get("title"):
        missing.append("title")
    if not fields.get("author"):
        missing.append("author")
    if not fields.get("year"):
        missing.append("year")
    venue_field = ingest.venue_field_name_for_bibtex_type(parsed.get("entry_type", ""))
    if venue_field and not fields.get(venue_field):
        missing.append(venue_field)
    return missing


def author_field_for_citeproc(value: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text or " and " in text:
        return text
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) >= 2:
        return " and ".join(parts)
    return text


def split_bibtex_authors(value: str) -> list[str]:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return []
    if " and " in text:
        return [part.strip() for part in text.split(" and ") if part.strip()]
    comma_parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(comma_parts) > 1:
        # Fallback for malformed BibTeX author fields stored as a comma-separated
        # list of full names rather than an `and`-separated list of authors.
        if sum(" " in part for part in comma_parts) >= max(2, len(comma_parts) - 1):
            return comma_parts
    return [text]


def given_name_initials(value: str) -> str:
    parts = re.findall(r"[A-Za-z]+", str(value or ""))
    return "".join(part[0] for part in parts if part)


def format_author_nar(author: str) -> str:
    text = " ".join(str(author or "").split()).strip()
    if not text:
        return ""
    if "," in text:
        surname, given = [part.strip() for part in text.split(",", 1)]
        initials = given_name_initials(given)
        return f"{surname} {initials}".strip()
    tokens = text.split()
    if len(tokens) == 1:
        return tokens[0]
    surname = tokens[-1]
    initials = given_name_initials(" ".join(tokens[:-1]))
    return f"{surname} {initials}".strip()


def compress_page_range(value: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    if "--" not in text:
        return text.replace("-", "–")
    start, end = [part.strip() for part in text.split("--", 1)]
    if not start or not end or not start.isdigit() or not end.isdigit():
        return text.replace("--", "–")
    prefix_len = 0
    for lhs, rhs in zip(start, end):
        if lhs != rhs:
            break
        prefix_len += 1
    end_short = end[prefix_len:] or end
    return f"{start}–{end_short}"


def render_nar_citation(parsed: dict[str, Any], abbreviation_map: dict[str, str]) -> str:
    fields = parsed.get("fields", {})
    normalized = parsed.get("normalized", {})
    title = normalized.get("title", "") or fields.get("title", "")
    authors_raw = fields.get("author", "") or normalized.get("authors", "")
    authors = ", ".join(
        formatted for formatted in (format_author_nar(author) for author in split_bibtex_authors(authors_raw)) if formatted
    )
    journal = ingest.normalize_venue_name(fields.get("shortjournal", "") or fields.get("journal", "") or normalized.get("venue", ""))
    journal = abbreviation_map.get(journal, fields.get("shortjournal", "") or journal)
    journal = " ".join(journal.replace(".", " ").split())
    year = normalized.get("year", "") or fields.get("year", "")
    volume = fields.get("volume", "")
    pages = compress_page_range(fields.get("pages", "") or fields.get("page", ""))
    doi = normalized.get("doi", "") or fields.get("doi", "")

    pieces: list[str] = []
    if authors:
        pieces.append(f"{authors}.")
    if title:
        pieces.append(f"{title}.")
    if journal:
        pieces.append(f"{journal}.")

    serial = ""
    if year:
        serial = year
    if volume:
        serial = f"{serial}; {volume}" if serial else volume
    if pages:
        serial = f"{serial}:{pages}" if serial else pages
    if serial:
        pieces.append(f"{serial}.")
    if doi:
        pieces.append(f"doi:{doi}")
    return " ".join(piece for piece in pieces if piece).strip()


def entry_text_with_short_journal(parsed: dict[str, Any], abbreviation_map: dict[str, str]) -> str:
    fields = dict(parsed.get("fields", {}))
    journal = ingest.normalize_venue_name(fields.get("journal", ""))
    abbreviation = abbreviation_map.get(journal, "")
    if abbreviation:
        fields["journal"] = abbreviation
        fields["shortjournal"] = abbreviation
    normalized_fields = dict(parsed.get("normalized", {}))
    normalized_fields.pop("authors", None)
    normalized_fields.pop("venue", None)
    author_value = fields.get("author") or parsed.get("normalized", {}).get("authors", "")
    if author_value:
        fields["author"] = author_field_for_citeproc(author_value)
    return ingest.build_bibtex_entry_with_preserved_fields(
        parsed.get("entry_type", "article"),
        parsed.get("bibtex_key", ""),
        fields,
        normalized_fields,
    )


def render_citation(parsed: dict[str, Any], style_name: str, style_info: dict[str, Any], abbreviation_map: dict[str, str]) -> str:
    missing = validate_bib_entry(parsed)
    if missing:
        raise SystemExit(f"Cannot render citation for {parsed.get('bibtex_key', '')}: missing {', '.join(missing)}")

    if style_name == "nar":
        return render_nar_citation(parsed, abbreviation_map)

    entry_text = entry_text_with_short_journal(parsed, abbreviation_map)
    with tempfile.TemporaryDirectory(prefix="library_lookup_") as tmpdir_text:
        tmpdir = Path(tmpdir_text)
        bib_path = tmpdir / "entry.bib"
        md_path = tmpdir / "entry.md"
        bib_path.write_text(entry_text, encoding="utf-8")
        md_path.write_text("---\nnocite: |\n  @*\n---\n", encoding="utf-8")
        cmd = [
            "pandoc",
            str(md_path),
            "--from",
            "markdown",
            "--to",
            "plain",
            "--wrap=none",
            "--citeproc",
            "--bibliography",
            str(bib_path),
            "--csl",
            str(style_info["csl_path"]),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except OSError as exc:
            raise SystemExit(f"Could not run pandoc for style '{style_name}': {exc}") from exc
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or "pandoc citeproc failed"
        raise SystemExit(stderr)
    return proc.stdout.strip()


def select_result_interactively(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    print_search_results(results)
    print()
    print("Select a citation by number, or press Enter to cancel.")
    selection = input("> ").strip()
    if not selection:
        return None
    if not selection.isdigit():
        raise SystemExit("Expected a numeric selection.")
    index = int(selection)
    if not 1 <= index <= len(results):
        raise SystemExit("Selection out of range.")
    return results[index - 1]["row"]


def resolve_citation_target(identifier_or_query: str, rows: list[dict[str, Any]], args: argparse.Namespace) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    exact = exact_identifier_match(identifier_or_query, rows)
    if exact:
        return exact, []

    results = combine_search_results(rows, args, identifier_or_query)
    if not results:
        return None, []
    if len(results) == 1:
        return results[0]["row"], results

    top_score = results[0]["score"]
    plausible = [item for item in results if item["score"] >= max(8.0, top_score * 0.8)]
    if len(plausible) == 1:
        return results[0]["row"], results
    return None, results


def add_common_query_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of results to return")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--author", help="Filter by author substring")
    parser.add_argument("--year", help="Filter by year or year range: YYYY, YYYY-, -YYYY, or YYYY-YYYY")
    parser.add_argument("--venue", help="Filter by venue substring")
    parser.add_argument("--doi", help="Filter by exact DOI")
    kind = parser.add_mutually_exclusive_group()
    kind.add_argument("--has-pdf", action="store_true", help="Only return PDF-backed records")
    kind.add_argument("--reference-only", action="store_true", help="Only return reference-only records")
    parser.add_argument(
        "--scope",
        choices=["metadata", "fulltext", "auto"],
        default="auto",
        help="Search scope. 'auto' stays metadata-first and only falls back to full text when metadata has no hits.",
    )
    parser.add_argument("--no-abstract", action="store_true", help="Ignore abstracts during metadata search")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search the local literature library and render citations.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search", help="Search the indexed library")
    search_parser.add_argument("query", nargs="*", help="Query text")
    add_common_query_filters(search_parser)

    show_parser = subparsers.add_parser("show", help="Show one record by paper_id or bibtex_key")
    show_parser.add_argument("identifier", help="paper_id or bibtex_key")
    show_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    cite_parser = subparsers.add_parser(
        "cite",
        help="Render one bibliography entry",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    cite_parser.add_argument("identifier_or_query", nargs="+", help="paper_id, bibtex_key, DOI, or search query")
    cite_parser.add_argument("--style", default="nature", help=STYLE_HELP_TEXT)
    add_common_query_filters(cite_parser)

    return parser


def run_search(args: argparse.Namespace, rows: list[dict[str, Any]], refreshed: bool) -> int:
    query = " ".join(args.query).strip()
    results = combine_search_results(rows, args, query)
    payload = {
        "query": query,
        "scope": args.scope,
        "refreshed_search_index": refreshed,
        "count": len(results),
        "results": [search_output_record(result) for result in results],
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print_search_results(results)
    return 0


def run_show(args: argparse.Namespace, rows: list[dict[str, Any]], refreshed: bool) -> int:
    row = exact_identifier_match(args.identifier, rows)
    if not row:
        raise SystemExit(f"No record found for identifier '{args.identifier}'")
    payload = {
        "refreshed_search_index": refreshed,
        "record": show_record(row),
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print_show_record(payload["record"])
    return 0


def run_cite(args: argparse.Namespace, rows: list[dict[str, Any]], refreshed: bool) -> int:
    identifier_or_query = " ".join(args.identifier_or_query).strip()
    alias, style_info = resolve_style_alias(args.style)
    row, results = resolve_citation_target(identifier_or_query, rows, args)

    if row is None:
        if args.json:
            print(
                json.dumps(
                    {
                        "status": "ambiguous" if results else "no_match",
                        "query": identifier_or_query,
                        "style": alias,
                        "style_family": style_info["family"],
                        "csl_path": str(style_info["csl_path"]),
                        "refreshed_search_index": refreshed,
                        "results": [search_output_record(result) for result in results],
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        if not results:
            raise SystemExit(f"No matches for '{identifier_or_query}'")
        selected = select_result_interactively(results)
        if selected is None:
            return 1
        row = selected

    entries = load_master_bib_entries()
    bibtex_key = row.get("bibtex_key", "")
    parsed = entries.get(bibtex_key)
    if not parsed:
        raise SystemExit(f"Missing BibTeX entry for key '{bibtex_key}' in {MASTER_BIB}")

    citation_text = render_citation(parsed, alias, style_info, load_journal_abbreviations())
    payload = {
        "status": "ok",
        "style": alias,
        "style_family": style_info["family"],
        "csl_path": str(style_info["csl_path"]),
        "refreshed_search_index": refreshed,
        "record": show_record(row),
        "citation": citation_text,
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(citation_text)
    return 0


def main() -> int:
    args = build_parser().parse_args()
    refreshed = ensure_search_index()
    rows = load_search_index_rows()

    if args.command == "search":
        return run_search(args, rows, refreshed)
    if args.command == "show":
        return run_show(args, rows, refreshed)
    if args.command == "cite":
        return run_cite(args, rows, refreshed)
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
