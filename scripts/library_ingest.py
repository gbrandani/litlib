#!/usr/bin/env python3
"""Ingest a single PDF into the local literature registry.

This script is intentionally deterministic and inspectable:
- it uses the PDF filename as the initial Semantic Scholar query
- it records candidate scores and the exact API response subset
- it extracts plain text via the existing pdf2text.py helper
- it writes stable TSV/JSON/JSONL/BibTeX artifacts under .paper_index/

The first target is article PDFs. Books can still be indexed, but they are
marked for manual review unless explicitly accepted by the user.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
try:
    import fcntl
except ImportError:
    fcntl = None
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
LIBRARY_ROOT = SCRIPT_DIR.parent
REGISTRY_ROOT = LIBRARY_ROOT / "paper_index"
RECORDS_DIR = REGISTRY_ROOT / "records"
TEXT_DIR = REGISTRY_ROOT / "text"
CHUNKS_DIR = REGISTRY_ROOT / "chunks"
LOGS_DIR = REGISTRY_ROOT / "logs"
MASTER_INDEX = REGISTRY_ROOT / "master_index.tsv"
MASTER_BIB = REGISTRY_ROOT / "master.bib"
MASTER_INDEX_LOCK = REGISTRY_ROOT / ".master_index.lock"
MASTER_BIB_LOCK = REGISTRY_ROOT / ".master_bib.lock"
def resolve_pdf2text_path() -> Path:
    env_val = os.environ.get("PDF2TEXT_PATH")
    if env_val:
        p = Path(env_val)
        if p.exists():
            return p
    p_default = Path("/Users/brandani/Dropbox/scripts/pdf2text.py")
    if p_default.exists():
        return p_default
    p_sibling = LIBRARY_ROOT.parent.parent / "scripts" / "pdf2text.py"
    if p_sibling.exists():
        return p_sibling
    p_local = SCRIPT_DIR / "pdf2text.py"
    if p_local.exists():
        return p_local
    return p_default

PDF2TEXT = resolve_pdf2text_path()
SEMANTIC_SCHOLAR_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
CROSSREF_WORKS = "https://api.crossref.org/works/"
MASTER_INDEX_FIELDS = [
    "paper_id",
    "item_type",
    "content_kind",
    "pdf_path",
    "pdf_sha256",
    "filename",
    "title_query",
    "resolved_title",
    "authors",
    "year",
    "venue",
    "doi",
    "semantic_scholar_paper_id",
    "match_confidence",
    "match_status",
    "bibtex_type",
    "canonical_url",
    "bibtex_key",
    "text_path",
    "chunk_path",
    "abstract_source",
    "date_indexed",
    "notes",
]

REFERENCE_ONLY_HEADER = "REFERENCE ONLY - NO PDF ATTACHED"
CONTENT_KIND_PDF = "pdf_backed"
CONTENT_KIND_REF = "reference_only"

CANONICAL_VENUE_BY_CASEFOLD = {
    "annual review of biochemistry": "Annual Review of Biochemistry",
    "annual review of biophysics": "Annual Review of Biophysics",
    "arxiv": "arXiv",
    "biophysical journal": "Biophysical Journal",
    "cell reports": "Cell Reports",
    "chemical reviews": "Chemical Reviews",
    "current opinion in genetics & development": "Current Opinion in Genetics & Development",
    "current opinion in structural biology": "Current Opinion in Structural Biology",
    "genes & development": "Genes & Development",
    "journal of chemical theory and computation": "Journal of Chemical Theory and Computation",
    "journal of molecular biology": "Journal of Molecular Biology",
    "methods in enzymology": "Methods in Enzymology",
    "methods in molecular biology": "Methods in Molecular Biology",
    "molecular cell": "Molecular Cell",
    "nature genetics": "Nature Genetics",
    "nucleic acids research": "Nucleic Acids Research",
    "nature structural & molecular biology": "Nature Structural and Molecular Biology",
    "nature structural &molecular biology": "Nature Structural and Molecular Biology",
    "nature structural &amp; molecular biology": "Nature Structural and Molecular Biology",
    "nature structural and molecular biology": "Nature Structural and Molecular Biology",
    "physical review letters": "Physical Review Letters",
    "plos computational biology": "PLoS Computational Biology",
    "plos one": "PLoS ONE",
    "proceedings of the national academy of sciences": "Proceedings of the National Academy of Sciences of the United States of America",
    "proceedings of the national academy of sciences of the united states of america": "Proceedings of the National Academy of Sciences of the United States of America",
    "progress in biophysics and molecular biology": "Progress in Biophysics and Molecular Biology",
    "the journal of chemical physics": "The Journal of Chemical Physics",
    "the journal of physical chemistry. b": "The Journal of Physical Chemistry. B",
}
STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}
FILENAME_NOISE = re.compile(
    r"""
    (?:
        \b(?:nat(?:ure)?|science|cell|pnas|arxiv|biorxiv|medrxiv|si|supp(?:lement|lementary)?)\b
        |[_\-]+
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
VENUE_ALIASES = {
    "molcell": "molecularcell",
    "molecularcell": "molecularcell",
    "natgenet": "naturegenetics",
    "naturegenetics": "naturegenetics",
    "natcommun": "naturecommunications",
    "naturecommunications": "naturecommunications",
    "natbiotechnol": "naturebiotechnology",
    "naturebiotechnology": "naturebiotechnology",
    "natmethods": "naturemethods",
    "naturemethods": "naturemethods",
    "natstructmolbiol": "naturestructuralmolecularbiology",
    "naturestructuralmolecularbiology": "naturestructuralmolecularbiology",
    "natscience": "nature",
    "nature": "nature",
    "science": "science",
    "cell": "cell",
    "pnas": "proceedingsofthenationalacademyofsciences",
    "proceedingsofthenationalacademyofsciences": "proceedingsofthenationalacademyofsciences",
    "proceedingsofthenationalacademyofsciencesoftheunitedstatesofamerica": "proceedingsofthenationalacademyofsciences",
}


def eprint(*parts: object) -> None:
    print(*parts, file=sys.stderr)


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "untitled"


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_item_type(pdf_path: Path) -> str:
    parts = {part.lower() for part in pdf_path.parts}
    if "articles" in parts:
        return "article"
    if "books" in parts:
        return "book"
    return "unknown"


def detect_known_venue_alias(text: str) -> str:
    compact = compact_alnum(text)
    if not compact:
        return ""
    return VENUE_ALIASES.get(compact, "")


def normalize_titleish_text(text: str) -> str:
    text = text.replace("_", " ")
    text = re.sub(r"[-‐‑–—]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def probable_title_prefix(text: str) -> str:
    text = normalize_titleish_text(text)
    text = re.sub(r"^(?:[A-Z]\s+){2,}[A-Z]?\s*", "", text)
    text = re.sub(r"^(?:research|report|article|review|perspective|resource)\s+", "", text, flags=re.I)
    text = re.sub(r"^(?:molecular biology|dynamic genome|genome biology|biophysics|cell biology)\s+", "", text, flags=re.I)
    text = text.strip()

    author_match = re.search(
        r"\s(?=[A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){0,3}(?:\d|\*|†|‡))",
        text,
    )
    if author_match:
        prefix = text[: author_match.start()].strip(" ,;:-")
        if len(prefix.split()) >= 5:
            return prefix

    sentence_match = re.search(r"([.?!:])\s+[A-Z]", text)
    if sentence_match:
        prefix = text[: sentence_match.start()].strip(" ,;:-")
        if len(prefix.split()) >= 5:
            return prefix

    return text


def derive_title_query(pdf_path: Path) -> str:
    stem = pdf_path.stem
    stem = stem.replace("_", " ")
    stem = re.sub(r"\bSI\b", " ", stem, flags=re.IGNORECASE)
    stem = re.sub(r"\bSupp(?:lement|lementary)?\b", " ", stem, flags=re.IGNORECASE)
    chunks = [chunk.strip() for chunk in stem.split(" - ") if chunk.strip()]
    if len(chunks) >= 3:
        # A common local naming pattern is "Author YEAR Venue - Title - Extra".
        return normalize_titleish_text(chunks[-1])
    if len(chunks) == 2:
        right = chunks[1]
        left = chunks[0]
        normalized_right = normalize_titleish_text(right)
        if len(normalized_right.split()) >= 4:
            return normalized_right
        return normalize_titleish_text(f"{left} {right}".strip())

    year_match = YEAR_RE.search(stem)
    if year_match:
        tail = stem[year_match.end() :].strip(" -_")
        tail_tokens = tail.split()
        if tail_tokens:
            alias = detect_known_venue_alias(tail_tokens[0])
            if alias:
                tail_tokens = tail_tokens[1:]
            elif len(tail_tokens) >= 2:
                alias = detect_known_venue_alias("".join(tail_tokens[:2]))
                if alias:
                    tail_tokens = tail_tokens[2:]
        tail = " ".join(tail_tokens).strip()
        if tail:
            stem = tail

    stem = FILENAME_NOISE.sub(" ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return normalize_titleish_text(stem)


def extract_year(text: str) -> int | None:
    match = YEAR_RE.search(text)
    return int(match.group(0)) if match else None


def compact_alnum(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").casefold())


def normalize_title(text: Any) -> str:
    text = str(text or "").casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: Any) -> list[str]:
    return [token.casefold() for token in TOKEN_RE.findall(str(text or ""))]


def informative_tokens(text: str) -> set[str]:
    return {token for token in tokenize(text) if token not in STOPWORDS and len(token) > 1}


def preferred_title_token(text: str) -> str:
    tokens = sorted(informative_tokens(text))
    return tokens[0] if tokens else "paper"


def title_similarity(query: str, title: str) -> float:
    query_norm = normalize_title(query)
    title_norm = normalize_title(title)
    if not query_norm or not title_norm:
        return 0.0
    if query_norm == title_norm:
        return 1.0
    if query_norm in title_norm or title_norm in query_norm:
        return 0.92

    query_tokens = informative_tokens(query_norm)
    title_tokens = informative_tokens(title_norm)
    if not query_tokens or not title_tokens:
        return 0.0

    overlap = len(query_tokens & title_tokens)
    union = len(query_tokens | title_tokens)
    coverage = overlap / max(len(query_tokens), 1)
    jaccard = overlap / max(union, 1)
    return min(1.0, 0.65 * coverage + 0.35 * jaccard)


def semantic_scholar_fields() -> str:
    return ",".join(
        [
            "paperId",
            "title",
            "abstract",
            "year",
            "venue",
            "publicationDate",
            "publicationTypes",
            "authors",
            "externalIds",
            "url",
            "journal",
            "openAccessPdf",
        ]
    )


def semantic_scholar_search(query: str, limit: int) -> dict[str, Any]:
    params = {"query": query, "limit": str(limit), "fields": semantic_scholar_fields()}
    url = f"{SEMANTIC_SCHOLAR_SEARCH}?{urllib.parse.urlencode(params)}"

    headers = {"Accept": "application/json"}
    api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    if api_key:
        headers["x-api-key"] = api_key

    request = urllib.request.Request(url, headers=headers)
    last_error: urllib.error.HTTPError | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 429 or attempt == 3:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 2.0 * (attempt + 1)
            time.sleep(delay)
        except socket.timeout as exc:
            raise TimeoutError(str(exc)) from exc

    if last_error:
        raise last_error
    raise RuntimeError("Semantic Scholar request failed without response")


def crossref_fetch_by_doi(doi: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(doi, safe="")
    request = urllib.request.Request(
        f"{CROSSREF_WORKS}{encoded}",
        headers={
            "Accept": "application/json",
            "User-Agent": "library_ingest.py (local literature registry)",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    message = payload.get("message")
    if not isinstance(message, dict):
        raise RuntimeError("Crossref response missing message payload")
    return message


def derive_filename_hints(pdf_path: Path) -> dict[str, Any]:
    stem = pdf_path.stem.replace("_", " ")
    chunks = [chunk.strip() for chunk in stem.split(" - ") if chunk.strip()]
    prefix = chunks[0] if chunks else stem
    year_hint = extract_year(prefix)

    venue_hint = ""
    if year_hint:
        suffix = prefix.split(str(year_hint), 1)[-1].strip()
        venue_hint = detect_known_venue_alias(suffix)
        if not venue_hint:
            tokens = suffix.split()
            if tokens:
                venue_hint = detect_known_venue_alias(tokens[0])
            if not venue_hint and len(tokens) >= 2:
                venue_hint = detect_known_venue_alias("".join(tokens[:2]))

    return {
        "year_hint": year_hint,
        "venue_hint": venue_hint,
        "prefix": prefix,
    }


@dataclass
class CandidateScore:
    paper_id: str
    title: str
    score: float
    title_similarity: float
    query_year: int | None
    candidate_year: int | None
    year_bonus: float
    author_bonus: float
    venue_penalty: float


def score_candidate(query: str, candidate: dict[str, Any], filename: str) -> CandidateScore:
    query_year = extract_year(filename)
    candidate_year = candidate.get("year")
    similarity = title_similarity(query, candidate.get("title", ""))

    year_bonus = 0.0
    if query_year and candidate_year:
        if query_year == candidate_year:
            year_bonus = 0.08
        elif abs(query_year - candidate_year) == 1:
            year_bonus = 0.03
        else:
            year_bonus = -0.08

    author_bonus = 0.0
    filename_tokens = informative_tokens(filename)
    authors = candidate.get("authors") or []
    author_names = " ".join(author.get("name", "") for author in authors)
    if informative_tokens(author_names) & filename_tokens:
        author_bonus = 0.04

    venue_penalty = 0.0
    title = candidate.get("title", "")
    if not title:
        venue_penalty = -0.25

    score = max(0.0, min(1.0, similarity + year_bonus + author_bonus + venue_penalty))
    return CandidateScore(
        paper_id=str(candidate.get("paperId", "")),
        title=title,
        score=round(score, 4),
        title_similarity=round(similarity, 4),
        query_year=query_year,
        candidate_year=candidate_year,
        year_bonus=round(year_bonus, 4),
        author_bonus=round(author_bonus, 4),
        venue_penalty=round(venue_penalty, 4),
    )


def match_status(item_type: str, score: float, candidate_count: int) -> str:
    if candidate_count == 0:
        return "not_found"
    if item_type != "article":
        return "needs_manual_review"
    if score >= 0.9:
        return "matched"
    if score >= 0.75:
        return "needs_manual_review"
    return "not_found"


def candidate_venue_key(candidate: dict[str, Any]) -> str:
    journal_name = ""
    if isinstance(candidate.get("journal"), dict):
        journal_name = candidate["journal"].get("name", "")
    venue = candidate.get("venue", "")
    compact = compact_alnum(journal_name or venue)
    return VENUE_ALIASES.get(compact, compact)


def candidate_dois(candidate: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    external_ids = candidate.get("externalIds") or {}
    if isinstance(external_ids, dict):
        doi = external_ids.get("DOI") or external_ids.get("doi")
        if isinstance(doi, str) and doi.strip():
            values.add(doi.strip().lower())

    open_access_pdf = candidate.get("openAccessPdf")
    if isinstance(open_access_pdf, dict):
        url = str(open_access_pdf.get("url") or "")
        for doi in extract_dois_from_text(url):
            values.add(doi.lower())
    return values


def extract_dois_from_text(text: str) -> list[str]:
    seen: list[str] = []
    for raw in DOI_RE.findall(text or ""):
        doi = raw.rstrip(".,);]").strip()
        suffix = doi.split("/", 1)[1] if "/" in doi else ""
        if not doi.lower().startswith("10."):
            continue
        # Reject obviously truncated DOI fragments such as "10.1073/pnas".
        if len(suffix) < 8 or not re.search(r"\d", suffix):
            continue
        if doi not in seen:
            seen.append(doi)
    return seen


def normalize_doi_candidate(doi: str) -> str:
    value = doi.strip().rstrip(".,);]")
    value = re.sub(r"/-/(?:dcsupplemental|supplemental).*", "", value, flags=re.I)
    value = re.sub(r"\.(?:g|t|s|f|e)\d{3}$", "", value, flags=re.I)
    if re.match(r"^10\.7554/elife\.\d+\.\d{3}$", value, flags=re.I):
        value = re.sub(r"\.\d{3}$", "", value)
    return value


def normalize_doi(doi: Any) -> str:
    if not doi:
        return ""
    value = str(doi or "").strip()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/)", "", value, flags=re.I)
    return normalize_doi_candidate(value).lower()


def canonical_url_from_sources(
    manual_override: dict[str, Any] | None = None,
    crossref_meta: dict[str, Any] | None = None,
    best_candidate: dict[str, Any] | None = None,
) -> str:
    manual_override = manual_override or {}
    crossref_meta = crossref_meta or {}
    best_candidate = best_candidate or {}
    if manual_override.get("url"):
        return str(manual_override.get("url") or "").strip()
    if crossref_meta.get("URL"):
        return str(crossref_meta.get("URL") or "").strip()
    return str(best_candidate.get("url") or "").strip()


def reference_paper_id_from_metadata(
    semantic_scholar_paper_id: str = "",
    doi: str = "",
    title: str = "",
    year: str = "",
    authors: str = "",
    venue: str = "",
) -> str:
    semantic_scholar_paper_id = str(semantic_scholar_paper_id or "").strip()
    if semantic_scholar_paper_id:
        return f"s2-{semantic_scholar_paper_id}"
    normalized_doi = normalize_doi(doi)
    if normalized_doi:
        safe = re.sub(r"[^a-z0-9]+", "-", normalized_doi).strip("-")
        return f"doi-{safe}"
    first_author = str(authors or "").split(",")[0].strip() if authors else ""
    token = "|".join(
        [
            normalize_title(title),
            str(year or "").strip(),
            normalize_title(first_author),
            normalize_title(venue),
        ]
    )
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    return f"bib-{digest}"


def master_index_row_defaults(row: dict[str, str]) -> dict[str, str]:
    normalized = {field: row.get(field, "") for field in MASTER_INDEX_FIELDS}
    normalized["content_kind"] = normalized.get("content_kind", "") or (
        CONTENT_KIND_PDF if normalized.get("pdf_path") else CONTENT_KIND_REF
    )
    normalized["bibtex_type"] = normalized.get("bibtex_type", "") or (
        "book" if normalized.get("item_type") == "book" else "article"
    )
    normalized["canonical_url"] = normalized.get("canonical_url", "") or ""
    return normalized


def record_path_for_paper_id(paper_id: str) -> Path:
    return RECORDS_DIR / f"{paper_id}.json"


def text_path_for_paper_id(paper_id: str) -> Path:
    return TEXT_DIR / f"{paper_id}.txt"


def chunk_path_for_paper_id(paper_id: str) -> Path:
    return CHUNKS_DIR / f"{paper_id}.jsonl"


class FileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any | None = None
        self.locked = False

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if fcntl:
            self.handle = self.path.open("a+", encoding="utf-8")
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        else:
            lock_dir = self.path.with_suffix(self.path.suffix + ".dir_lock")
            start_time = time.time()
            while True:
                try:
                    lock_dir.mkdir(exist_ok=False)
                    self.locked = True
                    break
                except FileExistsError:
                    if time.time() - start_time > 10.0:
                        raise TimeoutError(f"Could not acquire lock on {self.path} within 10 seconds.")
                    time.sleep(0.1)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if fcntl:
            if self.handle is not None:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
                self.handle.close()
                self.handle = None
        else:
            if self.locked:
                lock_dir = self.path.with_suffix(self.path.suffix + ".dir_lock")
                try:
                    lock_dir.rmdir()
                except OSError:
                    pass
                self.locked = False


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding=encoding,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(text)
        tmp_path = Path(handle.name)
    os.replace(tmp_path, path)


def ensure_path_inside_library(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(LIBRARY_ROOT)
    except ValueError as exc:
        raise ValueError(f"{label} must live inside the library root: {resolved}") from exc
    return resolved


def candidate_authors(candidate: dict[str, Any]) -> str:
    return ", ".join(author.get("name", "") for author in (candidate.get("authors") or []) if isinstance(author, dict))


def candidate_venue(candidate: dict[str, Any]) -> str:
    if isinstance(candidate.get("journal"), dict):
        return normalize_venue_name(candidate["journal"].get("name", "") or candidate.get("venue", ""))
    return normalize_venue_name(str(candidate.get("venue", "")))


def candidate_doi(candidate: dict[str, Any]) -> str:
    external_ids = candidate.get("externalIds") or {}
    if not isinstance(external_ids, dict):
        return ""
    return str(external_ids.get("DOI") or external_ids.get("doi") or "")


def semantic_candidate_to_metadata(candidate: dict[str, Any]) -> dict[str, str]:
    return normalize_metadata_fields(
        {
            "title": str(candidate.get("title") or ""),
            "authors": candidate_authors(candidate),
            "year": str(candidate.get("year") or ""),
            "venue": candidate_venue(candidate),
            "doi": candidate_doi(candidate),
            "url": str(candidate.get("url") or ""),
            "abstract": str(candidate.get("abstract") or ""),
            "semantic_scholar_paper_id": str(candidate.get("paperId") or ""),
        }
    )


def normalize_metadata_fields(fields: dict[str, Any]) -> dict[str, str]:
    title = " ".join(str(fields.get("title") or "").split()).strip()
    authors = ", ".join(part.strip() for part in str(fields.get("authors") or "").split(",") if part.strip())
    year = str(fields.get("year") or "").strip()
    venue = normalize_venue_name(str(fields.get("venue") or ""))
    doi = normalize_doi(str(fields.get("doi") or ""))
    url = str(fields.get("url") or fields.get("canonical_url") or "").strip()
    abstract = " ".join(str(fields.get("abstract") or "").split()).strip()
    semantic_scholar_paper_id = str(fields.get("semantic_scholar_paper_id") or "").strip()
    bibtex_key = str(fields.get("bibtex_key") or "").strip()
    entry_type = str(fields.get("bibtex_type") or fields.get("entry_type") or "").strip().lower()
    return {
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "url": url,
        "abstract": abstract,
        "semantic_scholar_paper_id": semantic_scholar_paper_id,
        "bibtex_key": bibtex_key,
        "bibtex_type": entry_type,
    }


def bibtex_field_aliases_from_metadata(entry_type: str, fields: dict[str, Any]) -> dict[str, str]:
    bibtex_type = (entry_type or "misc").strip().lower()
    venue_field = venue_field_name_for_bibtex_type(bibtex_type)
    mapped: dict[str, str] = {}
    for name, value in (fields or {}).items():
        field_name = str(name or "").strip()
        field_value = str(value or "").strip()
        if not field_name or not field_value:
            continue
        lower = field_name.lower()
        if lower == "authors":
            mapped["author"] = field_value
        elif lower == "venue":
            mapped[venue_field] = normalize_venue_name(field_value)
        elif lower == "bibtex_type":
            continue
        elif lower == "bibtex_key":
            continue
        else:
            mapped[lower] = field_value
    return mapped


def build_bibtex_entry_from_fields(entry_type: str, bibtex_key: str, fields: dict[str, str]) -> str:
    return build_bibtex_entry_with_preserved_fields(entry_type, bibtex_key, {}, fields)


BIBTEX_ALLOWED_FIELDS_BY_TYPE: dict[str, set[str]] = {
    "article": {"title", "author", "year", "journal", "volume", "number", "pages", "doi", "url", "abstract", "issn", "publisher", "month", "note", "keywords"},
    "book": {"title", "author", "editor", "year", "publisher", "address", "edition", "volume", "series", "isbn", "pages", "doi", "url", "abstract", "month", "note", "keywords"},
    "inproceedings": {"title", "author", "editor", "year", "booktitle", "pages", "publisher", "organization", "series", "volume", "number", "address", "doi", "url", "abstract", "month", "note", "keywords"},
    "conference": {"title", "author", "editor", "year", "booktitle", "pages", "publisher", "organization", "series", "volume", "number", "address", "doi", "url", "abstract", "month", "note", "keywords"},
    "proceedings": {"title", "editor", "year", "booktitle", "publisher", "organization", "series", "volume", "number", "address", "doi", "url", "abstract", "month", "note", "keywords"},
    "incollection": {"title", "author", "editor", "year", "booktitle", "chapter", "pages", "publisher", "series", "volume", "number", "address", "doi", "url", "abstract", "month", "note", "keywords"},
    "inbook": {"title", "author", "editor", "year", "booktitle", "chapter", "pages", "publisher", "series", "volume", "number", "address", "doi", "url", "abstract", "month", "note", "keywords"},
    "phdthesis": {"title", "author", "year", "school", "type", "address", "doi", "url", "abstract", "month", "note", "keywords"},
    "mastersthesis": {"title", "author", "year", "school", "type", "address", "doi", "url", "abstract", "month", "note", "keywords"},
    "thesis": {"title", "author", "year", "school", "type", "address", "doi", "url", "abstract", "month", "note", "keywords"},
    "techreport": {"title", "author", "year", "institution", "number", "type", "address", "doi", "url", "abstract", "month", "note", "keywords"},
    "misc": {"title", "author", "year", "howpublished", "doi", "url", "abstract", "month", "note", "keywords"},
}

BIBTEX_CANONICAL_ORDER_BY_TYPE: dict[str, list[str]] = {
    "article": ["title", "author", "year", "journal", "volume", "number", "pages", "doi", "url", "abstract", "issn", "publisher", "month", "note", "keywords"],
    "book": ["title", "author", "editor", "year", "publisher", "address", "edition", "volume", "series", "isbn", "pages", "doi", "url", "abstract", "month", "note", "keywords"],
    "inproceedings": ["title", "author", "editor", "year", "booktitle", "series", "volume", "number", "pages", "publisher", "organization", "address", "doi", "url", "abstract", "month", "note", "keywords"],
    "conference": ["title", "author", "editor", "year", "booktitle", "series", "volume", "number", "pages", "publisher", "organization", "address", "doi", "url", "abstract", "month", "note", "keywords"],
    "proceedings": ["title", "editor", "year", "booktitle", "series", "volume", "number", "publisher", "organization", "address", "doi", "url", "abstract", "month", "note", "keywords"],
    "incollection": ["title", "author", "editor", "year", "booktitle", "chapter", "pages", "publisher", "series", "volume", "number", "address", "doi", "url", "abstract", "month", "note", "keywords"],
    "inbook": ["title", "author", "editor", "year", "booktitle", "chapter", "pages", "publisher", "series", "volume", "number", "address", "doi", "url", "abstract", "month", "note", "keywords"],
    "phdthesis": ["title", "author", "year", "school", "type", "address", "doi", "url", "abstract", "month", "note", "keywords"],
    "mastersthesis": ["title", "author", "year", "school", "type", "address", "doi", "url", "abstract", "month", "note", "keywords"],
    "thesis": ["title", "author", "year", "school", "type", "address", "doi", "url", "abstract", "month", "note", "keywords"],
    "techreport": ["title", "author", "year", "institution", "number", "type", "address", "doi", "url", "abstract", "month", "note", "keywords"],
    "misc": ["title", "author", "year", "howpublished", "doi", "url", "abstract", "month", "note", "keywords"],
}


def allowed_bibtex_fields(entry_type: str) -> set[str]:
    bibtex_type = (entry_type or "misc").strip().lower()
    return set(BIBTEX_ALLOWED_FIELDS_BY_TYPE.get(bibtex_type, BIBTEX_ALLOWED_FIELDS_BY_TYPE["misc"]))


def canonical_bibtex_field_order(entry_type: str) -> list[str]:
    bibtex_type = (entry_type or "misc").strip().lower()
    return list(BIBTEX_CANONICAL_ORDER_BY_TYPE.get(bibtex_type, BIBTEX_CANONICAL_ORDER_BY_TYPE["misc"]))


def sanitize_bibtex_fields(entry_type: str, fields: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    allowed = allowed_bibtex_fields(entry_type)
    kept: dict[str, str] = {}
    ignored: list[str] = []
    for name, value in (fields or {}).items():
        field_name = str(name or "").strip().lower()
        field_value = str(value or "").strip()
        if not field_name or not field_value:
            continue
        if field_name not in allowed:
            ignored.append(field_name)
            continue
        kept[field_name] = field_value
    return kept, sorted(set(ignored))


def venue_field_name_for_bibtex_type(entry_type: str) -> str:
    bibtex_type = (entry_type or "misc").strip().lower()
    if bibtex_type in {"article"}:
        return "journal"
    if bibtex_type in {"inproceedings", "conference", "proceedings", "incollection", "inbook"}:
        return "booktitle"
    if bibtex_type in {"book"}:
        return "publisher"
    if bibtex_type in {"phdthesis", "mastersthesis", "thesis"}:
        return "school"
    if bibtex_type in {"techreport"}:
        return "institution"
    return "journal"


def build_bibtex_entry_with_preserved_fields(
    entry_type: str,
    bibtex_key: str,
    original_fields: dict[str, str],
    normalized_fields: dict[str, str],
) -> str:
    bibtex_type = (entry_type or "misc").strip().lower()
    merged, _ignored = sanitize_bibtex_fields(bibtex_type, original_fields)
    normalized = normalize_metadata_fields(normalized_fields)
    normalized_aliases = bibtex_field_aliases_from_metadata(bibtex_type, normalized_fields)
    sanitized_normalized, _ignored_normalized = sanitize_bibtex_fields(bibtex_type, normalized_aliases)
    merged.update(sanitized_normalized)
    if normalized["title"]:
        merged["title"] = normalized["title"]
    if normalized["authors"]:
        merged["author"] = normalized["authors"]
    if normalized["year"]:
        merged["year"] = normalized["year"]
    if normalized["doi"]:
        merged["doi"] = normalized["doi"]
    if normalized["url"]:
        merged["url"] = normalized["url"]
    if normalized["abstract"]:
        merged["abstract"] = normalized["abstract"]
    if normalized["venue"]:
        for field_name in ["journal", "booktitle", "publisher", "school", "institution"]:
            if merged.get(field_name):
                merged[field_name] = normalized["venue"]
                break
        else:
            merged[venue_field_name_for_bibtex_type(bibtex_type)] = normalized["venue"]

    merged, _ignored_after_merge = sanitize_bibtex_fields(bibtex_type, merged)
    preferred_order = canonical_bibtex_field_order(bibtex_type)
    populated: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in preferred_order:
        value = merged.get(name, "")
        if value:
            populated.append((name, value))
            seen.add(name)
    for name in sorted(merged):
        if name not in seen and merged[name]:
            populated.append((name, merged[name]))

    body = ",\n".join(
        f"  {name} = {{{sanitize_bibtex_value(value)}}}"
        for name, value in populated
    )
    return f"@{bibtex_type}{{{bibtex_key},\n{body}\n}}\n"


def canonicalize_bibtex_entry(parsed: dict[str, Any]) -> dict[str, Any]:
    entry_type = str(parsed.get("entry_type") or "misc").strip().lower()
    bibtex_key = str(parsed.get("bibtex_key") or "").strip()
    fields = dict(parsed.get("fields") or {})
    normalized = dict(parsed.get("normalized") or {})
    kept_fields, ignored_fields = sanitize_bibtex_fields(entry_type, fields)
    canonical_raw = build_bibtex_entry_with_preserved_fields(
        entry_type=entry_type,
        bibtex_key=bibtex_key,
        original_fields=kept_fields,
        normalized_fields=normalized,
    )
    canonical = parse_bibtex_entry(canonical_raw)
    canonical["ignored_fields"] = ignored_fields
    return canonical


def _bibtex_unwrap_value(raw: str) -> str:
    value = raw.strip().rstrip(",").strip()
    if not value:
        return ""
    if (value.startswith("{") and value.endswith("}")) or (value.startswith('"') and value.endswith('"')):
        value = value[1:-1]
    return " ".join(value.replace("\n", " ").split()).strip()


def parse_bibtex_entry(raw_bibtex: str) -> dict[str, Any]:
    text = raw_bibtex.strip()
    if not text.startswith("@"):
        raise ValueError("BibTeX entry must start with '@'")
    match = re.match(r"@(?P<entry_type>[A-Za-z0-9_:-]+)\s*\{\s*(?P<key>[^,]+)\s*,", text, re.S)
    if not match:
        raise ValueError("Could not parse BibTeX entry header")
    entry_type = match.group("entry_type").strip().lower()
    bibtex_key = match.group("key").strip()
    index = match.end()
    fields: dict[str, str] = {}
    length = len(text)

    while index < length:
        while index < length and text[index] in " \t\r\n,":
            index += 1
        if index >= length or text[index] == "}":
            break

        name_start = index
        while index < length and re.match(r"[A-Za-z0-9_:-]", text[index]):
            index += 1
        field_name = text[name_start:index].strip().lower()
        while index < length and text[index].isspace():
            index += 1
        if index >= length or text[index] != "=":
            raise ValueError(f"Could not parse BibTeX field near: {text[name_start:name_start + 40]!r}")
        index += 1
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            break

        if text[index] == "{":
            depth = 0
            value_start = index
            while index < length:
                char = text[index]
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        index += 1
                        break
                index += 1
            raw_value = text[value_start:index]
        elif text[index] == '"':
            value_start = index
            index += 1
            escaped = False
            while index < length:
                char = text[index]
                if char == '"' and not escaped:
                    index += 1
                    break
                escaped = (char == "\\") and not escaped
                if char != "\\":
                    escaped = False
                index += 1
            raw_value = text[value_start:index]
        else:
            value_start = index
            while index < length and text[index] not in ",}":
                index += 1
            raw_value = text[value_start:index]

        fields[field_name] = _bibtex_unwrap_value(raw_value)
        while index < length and text[index].isspace():
            index += 1
        if index < length and text[index] == ",":
            index += 1

    normalized = normalize_metadata_fields(
        {
            "title": fields.get("title", ""),
            "authors": fields.get("author", ""),
            "year": fields.get("year", ""),
            "venue": fields.get("journal", "") or fields.get("booktitle", "") or fields.get("publisher", ""),
            "doi": fields.get("doi", ""),
            "url": fields.get("url", ""),
            "abstract": fields.get("abstract", ""),
            "bibtex_key": bibtex_key,
            "bibtex_type": entry_type,
        }
    )
    return {
        "entry_type": entry_type,
        "bibtex_key": bibtex_key,
        "fields": fields,
        "normalized": normalized,
        "raw_bibtex": text,
    }


def build_reference_stub_text(fields: dict[str, str], bibtex_key: str) -> str:
    normalized = normalize_metadata_fields(fields)
    lines = [
        REFERENCE_ONLY_HEADER,
        normalized["title"],
        f"Authors: {normalized['authors']}",
        f"Year: {normalized['year']}",
        f"Venue: {normalized['venue']}",
        f"DOI: {normalized['doi']}",
        f"URL: {normalized['url']}",
        f"Abstract: {normalized['abstract']}",
        f"BibTeX key: {bibtex_key}",
    ]
    return "\n".join(lines).rstrip() + "\n"


def write_text_artifacts(
    paper_id: str,
    text_content: str,
    chunk_chars: int,
    overlap_chars: int,
) -> dict[str, Any]:
    text_path = text_path_for_paper_id(paper_id)
    chunk_path = chunk_path_for_paper_id(paper_id)
    text_path.write_text(text_content, encoding="utf-8")
    text_sha256 = hashlib.sha256(text_content.encode("utf-8")).hexdigest()
    chunks = chunk_text(text_content, target_chars=chunk_chars, overlap_chars=overlap_chars)
    write_chunks_jsonl(chunk_path, paper_id, chunks, text_sha256)
    return {
        "text_path": text_path,
        "chunk_path": chunk_path,
        "text_sha256": text_sha256,
        "chunk_count": len(chunks),
    }


def extract_pdf_text(pdf_path: Path) -> tuple[str, str, str]:
    with tempfile.TemporaryDirectory(prefix="library_ingest_") as tmpdir:
        temp_text_path = Path(tmpdir) / "extracted.txt"
        try:
            run_pdf2text(pdf_path, temp_text_path)
        except Exception as exc:
            return "error", str(exc), ""
        return "ok", "", temp_text_path.read_text(encoding="utf-8")


def write_reference_stub_artifacts(
    paper_id: str,
    fields: dict[str, str],
    bibtex_key: str,
    chunk_chars: int,
    overlap_chars: int,
) -> dict[str, Any]:
    stub_text = build_reference_stub_text(fields, bibtex_key)
    return write_text_artifacts(
        paper_id=paper_id,
        text_content=stub_text,
        chunk_chars=chunk_chars,
        overlap_chars=overlap_chars,
    )


def collect_pdf_doi_candidates(pdf_path: Path, extracted_text: str) -> list[str]:
    return [entry["doi"] for entry in collect_pdf_doi_candidate_details(pdf_path, extracted_text)]


def first_page_text(extracted_text: str) -> str:
    if not extracted_text:
        return ""
    marker = "\n[Page 2]"
    if marker in extracted_text:
        return extracted_text.split(marker, 1)[0]
    return extracted_text[:6000]


def doi_candidate_priority_score(source_region: str, position: int, context: str) -> float:
    score = 0.0
    if source_region == "filename":
        score += 0.2
    elif source_region == "first_page":
        score += 0.45
    elif source_region == "front_window":
        score += 0.35
    elif source_region == "broad_window":
        score += 0.15

    if position >= 0:
        if position < 300:
            score += 0.2
        elif position < 1200:
            score += 0.12
        elif position < 4000:
            score += 0.05

    lowered = context.casefold()
    if "doi:" in lowered or "doi.org/" in lowered or "https://doi.org/" in lowered:
        score += 0.18
    if any(marker in lowered for marker in ["references", "bibliography", "cited by", "et al."]):
        score -= 0.2
    return round(score, 4)


def collect_pdf_doi_candidate_details(pdf_path: Path, extracted_text: str) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    windows = [
        ("filename", pdf_path.name),
        ("first_page", first_page_text(extracted_text)),
        ("front_window", extracted_text[:6000]),
        ("broad_window", extracted_text[:20000]),
    ]
    for source_region, source_text in windows:
        if not source_text:
            continue
        for raw_doi in extract_dois_from_text(source_text):
            normalized = normalize_doi_candidate(raw_doi)
            if not normalized:
                continue
            position = source_text.lower().find(raw_doi.lower())
            context_start = max(0, position - 90) if position >= 0 else 0
            context_end = position + len(raw_doi) + 90 if position >= 0 else min(len(source_text), 180)
            context = " ".join(source_text[context_start:context_end].split())
            priority_score = doi_candidate_priority_score(source_region, position, context)
            detail = {
                "doi": normalized,
                "source_region": source_region,
                "position": position,
                "context": context,
                "priority_score": priority_score,
            }
            existing = seen.get(normalized)
            if existing is None or (
                detail["priority_score"],
                -max(detail["position"], -1),
            ) > (
                existing["priority_score"],
                -max(existing["position"], -1),
            ):
                seen[normalized] = detail
    return sorted(
        seen.values(),
        key=lambda entry: (entry["priority_score"], -(entry["position"] if entry["position"] >= 0 else 10**9), entry["doi"]),
        reverse=True,
    )


def is_generic_crossref_title(title: str) -> bool:
    normalized = normalize_title(title)
    return normalized in {
        "",
        "abstract",
        "proceedings of the national academy of sciences",
    }


def score_crossref_metadata(
    message: dict[str, Any],
    filename_hints: dict[str, Any],
    title_targets: list[str],
) -> float:
    title = crossref_title(message)
    if is_generic_crossref_title(title):
        return -1.0

    score = 0.0
    target_scores = [title_similarity(target, title) for target in title_targets if target]
    if target_scores:
        score += max(target_scores)

    year_hint = filename_hints.get("year_hint")
    year_value = crossref_year(message)
    if year_hint and year_value:
        if str(year_hint) == str(year_value):
            score += 0.08
        elif abs(int(year_hint) - int(year_value)) == 1:
            score += 0.03

    venue_hint = filename_hints.get("venue_hint", "")
    venue_value = VENUE_ALIASES.get(compact_alnum(crossref_container(message)), compact_alnum(crossref_container(message)))
    if venue_hint and venue_value:
        if venue_hint == venue_value:
            score += 0.08

    return score


def cleaned_text_lines(text: str, max_chars: int = 5000) -> list[str]:
    snippet = text[:max_chars]
    lines = []
    for raw in snippet.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.fullmatch(r"\[Page \d+\]", line):
            continue
        if re.search(r"\b(doi|received|accepted|published online|open access|check for updates)\b", line, re.I):
            continue
        if re.fullmatch(r"[A-Z\s&,:;.-]{3,}", line) and len(line.split()) <= 8:
            continue
        if re.search(r"@|\bAffiliations?\b|\bCorrespondence\b", line, re.I):
            continue
        lines.append(normalize_titleish_text(line))
    return lines


def derive_text_title_candidates(text: str) -> list[str]:
    lines = cleaned_text_lines(text)
    candidates: list[str] = []

    for line in lines[:25]:
        title_prefix = probable_title_prefix(line)
        if (
            title_prefix != line
            and 5 <= len(title_prefix.split()) <= 25
            and "et al" not in title_prefix.casefold()
        ):
            candidates.append(title_prefix)
        if len(line.split()) < 5:
            continue
        if len(line) < 25 or len(line.split()) > 25:
            continue
        if re.search(r"\b(abstract|summary|introduction|significance|results|supporting information|supplementary)\b", line, re.I):
            continue
        if re.search(r"\b(university|department|institute|laboratory|school|center|centre)\b", line, re.I):
            continue
        if "et al" in line.casefold():
            continue
        candidates.append(line)

    # Also consider short multi-line stitched titles from the top of page 1.
    top_lines = [line for line in lines[:8] if len(line.split()) >= 2]
    for i in range(len(top_lines) - 1):
        joined = normalize_titleish_text(f"{top_lines[i]} {top_lines[i+1]}")
        if 6 <= len(joined.split()) <= 20 and "et al" not in joined.casefold():
            candidates.append(joined)

    deduped: list[str] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    return deduped[:4]


def is_supplement_pdf(pdf_path: Path, text_content: str) -> bool:
    filename = pdf_path.name
    if re.search(r"\b(SI|supp|supplement|supplementary|SM)\b", filename, re.I):
        return True
    head = text_content[:1500]
    if re.search(r"\b(supporting information|supplementary information|supplementary materials)\b", head, re.I):
        return True
    return False


def strip_supplement_markers(text: str) -> str:
    text = re.sub(r"^\s*(supporting information(?: for)?|supplementary (?:information|materials)(?: for)?)\s*", "", text, flags=re.I)
    text = re.sub(r"\b(SI|supplementary materials|supporting information)\b", "", text, flags=re.I)
    return normalize_titleish_text(text)


def find_parent_article_for_supplement(
    pdf_path: Path,
    text_title_candidates: list[str],
) -> dict[str, str] | None:
    rows = load_master_index_rows()
    scoped_rows = [row for row in rows if row.get("match_status") in {"matched", "matched_via_doi", "matched_supplement"}]
    same_dir_prefix = str(pdf_path.parent.resolve().relative_to(LIBRARY_ROOT))
    primary_rows = [row for row in scoped_rows if row.get("pdf_path", "").startswith(same_dir_prefix + "/")]
    if not primary_rows:
        primary_rows = scoped_rows

    best_row: dict[str, str] | None = None
    best_score = 0.0
    for candidate in text_title_candidates:
        stripped = strip_supplement_markers(candidate)
        if len(stripped.split()) < 4:
            continue
        for row in primary_rows:
            row_title = row.get("resolved_title") or row.get("title_query") or ""
            if not row_title:
                continue
            score = title_similarity(stripped, row_title)
            if score > best_score:
                best_score = score
                best_row = row

    if best_row and best_score >= 0.9:
        return best_row
    return None


def consistency_warnings(filename_hints: dict[str, Any], candidate: dict[str, Any] | None) -> list[str]:
    if not candidate:
        return []

    warnings: list[str] = []
    year_hint = filename_hints.get("year_hint")
    candidate_year = candidate.get("year")
    if year_hint and candidate_year and year_hint != candidate_year:
        warnings.append(f"year_hint_mismatch:{year_hint}!={candidate_year}")

    venue_hint = filename_hints.get("venue_hint", "")
    candidate_venue = candidate_venue_key(candidate)
    if venue_hint and candidate_venue and venue_hint != candidate_venue:
        warnings.append(f"venue_hint_mismatch:{venue_hint}!={candidate_venue}")

    return warnings


def crossref_consistency_warnings(filename_hints: dict[str, Any], message: dict[str, Any] | None) -> list[str]:
    if not message:
        return []

    warnings: list[str] = []
    year_hint = filename_hints.get("year_hint")
    crossref_year_value = crossref_year(message)
    if year_hint and crossref_year_value and str(year_hint) != str(crossref_year_value):
        warnings.append(f"crossref_year_hint_mismatch:{year_hint}!={crossref_year_value}")

    venue_hint = filename_hints.get("venue_hint", "")
    crossref_venue_value = VENUE_ALIASES.get(compact_alnum(crossref_container(message)), compact_alnum(crossref_container(message)))
    if venue_hint and crossref_venue_value and venue_hint != crossref_venue_value:
        warnings.append(f"crossref_venue_hint_mismatch:{venue_hint}!={crossref_venue_value}")

    return warnings


def crossref_title(message: dict[str, Any]) -> str:
    title = message.get("title") or []
    if isinstance(title, list) and title:
        return str(title[0])
    if isinstance(title, str):
        return title
    return ""


def crossref_container(message: dict[str, Any]) -> str:
    container = message.get("container-title") or []
    if isinstance(container, list) and container:
        return normalize_venue_name(str(container[0]))
    if isinstance(container, str):
        return normalize_venue_name(container)
    return ""


def normalize_venue_name(value: str) -> str:
    venue = " ".join((value or "").split()).strip()
    venue = venue.replace("\\&", "&").replace("&amp;", "&")
    if not venue:
        return ""
    return CANONICAL_VENUE_BY_CASEFOLD.get(venue.casefold(), venue)


def crossref_year(message: dict[str, Any]) -> str:
    for key in ["published-print", "published-online", "issued"]:
        part = message.get(key) or {}
        date_parts = part.get("date-parts") if isinstance(part, dict) else None
        if isinstance(date_parts, list) and date_parts and date_parts[0]:
            return str(date_parts[0][0])
    return ""


def crossref_authors(message: dict[str, Any]) -> str:
    names: list[str] = []
    for author in message.get("author") or []:
        if not isinstance(author, dict):
            continue
        given = str(author.get("given") or "").strip()
        family = str(author.get("family") or "").strip()
        name = " ".join(part for part in [given, family] if part)
        if name:
            names.append(name)
    return ", ".join(names)


def strip_jats_markup(text: str) -> str:
    cleaned = re.sub(r"</?(?:jats:)?(?:p|i|b|sub|sup|italic|bold|title|sc)>", " ", text or "", flags=re.I)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    return " ".join(cleaned.split()).strip()


def crossref_abstract(message: dict[str, Any]) -> str:
    return strip_jats_markup(str(message.get("abstract") or ""))


def crossref_volume(message: dict[str, Any]) -> str:
    return str(message.get("volume") or "").strip()


def crossref_number(message: dict[str, Any]) -> str:
    return str(message.get("issue") or "").strip()


def crossref_pages(message: dict[str, Any]) -> str:
    return str(message.get("page") or "").strip()


def crossref_publisher(message: dict[str, Any]) -> str:
    return str(message.get("publisher") or "").strip()


def crossref_issn(message: dict[str, Any]) -> str:
    issn = message.get("ISSN") or []
    if isinstance(issn, list) and issn:
        return str(issn[0] or "").strip()
    if isinstance(issn, str):
        return issn.strip()
    return ""


def crossref_to_bibtex_fields(message: dict[str, Any]) -> dict[str, str]:
    return {
        "title": crossref_title(message),
        "authors": crossref_authors(message),
        "year": crossref_year(message),
        "venue": crossref_container(message),
        "doi": str(message.get("DOI") or ""),
        "url": str(message.get("URL") or ""),
        "abstract": crossref_abstract(message),
        "volume": crossref_volume(message),
        "number": crossref_number(message),
        "pages": crossref_pages(message),
        "publisher": crossref_publisher(message),
        "issn": crossref_issn(message),
    }


def build_crossref_bibtex_entry(bibtex_key: str, message: dict[str, Any]) -> str:
    fields = crossref_to_bibtex_fields(message)
    field_mappings = [
        ("title", "title"),
        ("authors", "author"),
        ("year", "year"),
        ("venue", "journal"),
        ("volume", "volume"),
        ("number", "number"),
        ("pages", "pages"),
        ("doi", "doi"),
        ("url", "url"),
        ("publisher", "publisher"),
        ("issn", "issn"),
    ]
    populated = []
    for source_key, target_name in field_mappings:
        val = fields.get(source_key, "")
        if val:
            populated.append((target_name, val))
            
    body = ",\n".join(
        f"  {name} = {{{sanitize_bibtex_value(value)}}}"
        for name, value in populated
    )
    return f"@article{{{bibtex_key},\n{body}\n}}\n"


DOI_ACCEPT_THRESHOLD = 0.55
DOI_REVIEW_THRESHOLD = 0.35


def resolve_pdf_doi_metadata(
    pdf_path: Path,
    extracted_text: str,
    filename_hints: dict[str, Any],
    title_targets: list[str],
) -> dict[str, Any]:
    candidate_details = collect_pdf_doi_candidate_details(pdf_path, extracted_text)
    attempts: list[dict[str, Any]] = []
    best_metadata: dict[str, Any] | None = None
    best_score = -1.0
    best_warnings: list[str] = []
    best_error = ""
    best_doi = ""
    best_priority = -1.0

    for detail in candidate_details:
        doi_candidate = str(detail.get("doi") or "")
        attempt = dict(detail)
        attempt.update(
            {
                "score": None,
                "warnings": [],
                "error": "",
                "accepted": False,
                "metadata": None,
            }
        )
        try:
            metadata = crossref_fetch_by_doi(doi_candidate)
            warnings = crossref_consistency_warnings(filename_hints, metadata)
            score = score_crossref_metadata(metadata, filename_hints, title_targets)
            attempt["score"] = score
            attempt["warnings"] = warnings
            attempt["metadata"] = metadata
            if (score, float(detail.get("priority_score") or 0.0)) > (best_score, best_priority):
                best_metadata = metadata
                best_score = score
                best_warnings = warnings
                best_error = ""
                best_doi = doi_candidate
                best_priority = float(detail.get("priority_score") or 0.0)
        except urllib.error.HTTPError as exc:
            attempt["error"] = f"Crossref HTTP {exc.code}: {exc.reason}"
            best_error = best_error or attempt["error"]
        except urllib.error.URLError as exc:
            attempt["error"] = f"Crossref URL error: {exc.reason}"
            best_error = best_error or attempt["error"]
        except Exception as exc:
            attempt["error"] = f"Crossref error: {exc}"
            best_error = best_error or attempt["error"]
        attempts.append(attempt)

    accepted_metadata = best_metadata if best_score >= DOI_ACCEPT_THRESHOLD else None
    review_metadata = best_metadata if DOI_REVIEW_THRESHOLD <= best_score < DOI_ACCEPT_THRESHOLD else None
    return {
        "candidate_details": candidate_details,
        "candidate_dois": [entry["doi"] for entry in candidate_details],
        "crossref_attempts": attempts,
        "best_candidate_doi": best_doi,
        "best_metadata": best_metadata,
        "best_score": best_score if best_score >= 0 else None,
        "warnings": best_warnings,
        "error": best_error,
        "accepted_metadata": accepted_metadata,
        "review_metadata": review_metadata,
        "accepted_source": "crossref_doi" if accepted_metadata else "",
        "matched_by": "doi_text_validation" if accepted_metadata else "",
    }


def choose_semantic_candidate_for_doi(
    merged_candidates: list[dict[str, Any]],
    accepted_doi: str,
    fallback_candidate: dict[str, Any] | None,
) -> dict[str, Any] | None:
    normalized = normalize_doi(accepted_doi)
    if normalized:
        for entry in merged_candidates:
            candidate = entry.get("candidate") or {}
            if normalized in {normalize_doi(value) for value in candidate_dois(candidate)}:
                return candidate
    return fallback_candidate


def build_bibtex_key_from_fields(authors: str, year: str, title: str, fallback_stem: str) -> str:
    first_author = authors.split(",")[0].strip() if authors else fallback_stem
    surname = first_author.split()[-1] if first_author.split() else fallback_stem
    title_token = preferred_title_token(title)
    return slugify(f"{surname}{year or 'undated'}{title_token}").replace("-", "")


def choose_best_candidate(
    query: str,
    filename: str,
    item_type: str,
    response: dict[str, Any],
    filename_hints: dict[str, Any],
) -> tuple[str, dict[str, Any] | None, list[CandidateScore], list[str]]:
    candidates = response.get("data") or []
    scored = sorted(
        (score_candidate(query, candidate, filename) for candidate in candidates),
        key=lambda item: item.score,
        reverse=True,
    )
    best = scored[0] if scored else None
    best_candidate = None
    if best:
        for candidate in candidates:
            if str(candidate.get("paperId", "")) == best.paper_id:
                best_candidate = candidate
                break
    status = match_status(item_type, best.score if best else 0.0, len(candidates))
    warnings = consistency_warnings(filename_hints, best_candidate)
    if status == "matched" and warnings:
        status = "needs_manual_review"
    return status, best_candidate, scored, warnings


def candidate_preference_tuple(
    status: str,
    warnings: list[str],
    scored_candidates: list[CandidateScore],
) -> tuple[int, float, int]:
    status_rank = {
        "matched": 3,
        "matched_via_doi": 3,
        "matched_supplement": 3,
        "needs_manual_review": 2,
        "not_found": 1,
        "api_error": 0,
        "not_run": 0,
    }.get(status, 0)
    score = scored_candidates[0].score if scored_candidates else 0.0
    return (status_rank, score, -len(warnings))


def run_semantic_attempt(
    query: str,
    filename: str,
    item_type: str,
    filename_hints: dict[str, Any],
    semantic_limit: int,
) -> dict[str, Any]:
    api_response: dict[str, Any] | None = None
    api_error = ""
    status = "not_run"
    best_candidate: dict[str, Any] | None = None
    scored_candidates: list[CandidateScore] = []
    warnings: list[str] = []

    try:
        api_response = semantic_scholar_search(query, semantic_limit)
        status, best_candidate, scored_candidates, warnings = choose_best_candidate(
            query=query,
            filename=filename,
            item_type=item_type,
            response=api_response,
            filename_hints=filename_hints,
        )
    except urllib.error.HTTPError as exc:
        api_error = f"HTTP {exc.code}: {exc.reason}"
        status = "api_error"
    except urllib.error.URLError as exc:
        api_error = f"URL error: {exc.reason}"
        status = "api_error"
    except TimeoutError:
        api_error = "timeout"
        status = "api_error"

    return {
        "query": query,
        "status": status,
        "api_error": api_error,
        "warnings": warnings,
        "best_candidate": best_candidate,
        "scored_candidates": scored_candidates,
        "best_score": scored_candidates[0].score if scored_candidates else None,
        "candidate_count": len((api_response or {}).get("data") or []),
        "raw_response": api_response,
    }


def candidate_identity(candidate: dict[str, Any]) -> str:
    paper_id = str(candidate.get("paperId") or "").strip()
    if paper_id:
        return f"paper:{paper_id}"
    doi = normalize_doi(candidate_doi(candidate))
    if doi:
        return f"doi:{doi}"
    title = normalize_title(str(candidate.get("title") or ""))
    year = str(candidate.get("year") or "").strip()
    if title:
        return f"title:{title}|year:{year}"
    return f"fallback:{hashlib.sha256(json.dumps(candidate, sort_keys=True).encode('utf-8')).hexdigest()[:16]}"


def collect_semantic_candidates(
    queries: list[str],
    filename: str,
    item_type: str,
    filename_hints: dict[str, Any],
    semantic_limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    merged: dict[str, dict[str, Any]] = {}

    for query in queries:
        if not query.strip():
            continue
        attempt = run_semantic_attempt(
            query=query,
            filename=filename,
            item_type=item_type,
            filename_hints=filename_hints,
            semantic_limit=semantic_limit,
        )
        attempts.append(attempt)

        response = attempt.get("raw_response") or {}
        for candidate in response.get("data") or []:
            identity = candidate_identity(candidate)
            score_lookup = {
                score.paper_id: score
                for score in attempt.get("scored_candidates") or []
                if score.paper_id
            }
            score = score_lookup.get(str(candidate.get("paperId") or ""))
            summary = {
                "identity": identity,
                "candidate": candidate,
                "query": query,
                "score": score.score if score else 0.0,
                "title_similarity": score.title_similarity if score else 0.0,
                "candidate_year": score.candidate_year if score else candidate.get("year"),
            }
            existing = merged.get(identity)
            if existing is None or summary["score"] > existing["score"]:
                merged[identity] = summary

    ordered = sorted(
        merged.values(),
        key=lambda item: (item["score"], str(item["candidate"].get("year") or ""), str(item["candidate"].get("title") or "")),
        reverse=True,
    )
    return attempts, ordered


def ensure_registry_layout() -> None:
    for directory in [REGISTRY_ROOT, RECORDS_DIR, TEXT_DIR, CHUNKS_DIR, LOGS_DIR]:
        directory.mkdir(parents=True, exist_ok=True)


def chunk_text(text: str, target_chars: int, overlap_chars: int) -> list[dict[str, Any]]:
    paragraphs = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    chunks: list[dict[str, Any]] = []
    current = ""
    start_offset = 0
    search_start = 0

    for paragraph in paragraphs:
        proposed = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if current and len(proposed) > target_chars:
            chunk_text_value = current.strip()
            idx = text.find(chunk_text_value, search_start)
            idx = search_start if idx < 0 else idx
            end_idx = idx + len(chunk_text_value)
            chunks.append({"text": chunk_text_value, "char_start": idx, "char_end": end_idx})
            overlap_start = max(0, end_idx - overlap_chars)
            search_start = overlap_start
            current = paragraph
            start_offset = overlap_start
        else:
            current = proposed

    if current.strip():
        chunk_text_value = current.strip()
        idx = text.find(chunk_text_value, search_start)
        idx = start_offset if idx < 0 else idx
        end_idx = idx + len(chunk_text_value)
        chunks.append({"text": chunk_text_value, "char_start": idx, "char_end": end_idx})

    return chunks


_PDF2TEXT_PYTHON_CACHE: str | None = None


def resolve_pdf2text_python() -> str:
    global _PDF2TEXT_PYTHON_CACHE
    if _PDF2TEXT_PYTHON_CACHE:
        return _PDF2TEXT_PYTHON_CACHE

    candidates: list[str] = []
    preferred = os.environ.get("PDF2TEXT_PYTHON", "").strip()
    if preferred:
        candidates.append(preferred)
    candidates.extend(
        candidate
        for candidate in [
            sys.executable,
            shutil.which("python3") or "",
            "/Users/brandani/opt/anaconda3/bin/python3",
            "/Users/brandani/opt/anaconda3/bin/python",
            "/Library/Developer/CommandLineTools/usr/bin/python3",
            "/usr/bin/python3",
        ]
        if candidate
    )

    seen: set[str] = set()
    for candidate in candidates:
        resolved = str(Path(candidate).expanduser())
        if resolved in seen or not Path(resolved).exists():
            continue
        seen.add(resolved)
        probe = subprocess.run(
            [resolved, "-c", "import fitz"],
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            _PDF2TEXT_PYTHON_CACHE = resolved
            return resolved

    _PDF2TEXT_PYTHON_CACHE = preferred or sys.executable
    return _PDF2TEXT_PYTHON_CACHE


def run_pdf2text(pdf_path: Path, text_path: Path) -> None:
    if not PDF2TEXT.exists():
        raise FileNotFoundError(f"pdf2text helper not found: {PDF2TEXT}")

    pdf2text_python = resolve_pdf2text_python()
    command = [
        pdf2text_python,
        str(PDF2TEXT),
        str(pdf_path),
        "--output",
        str(text_path),
        "--format",
        "txt",
        "--page-markers",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "pdf2text.py failed"
        raise RuntimeError(
            f"pdf2text.py failed via interpreter '{pdf2text_python}' "
            f"using converter '{PDF2TEXT}': {detail}"
        )


def sanitize_bibtex_value(value: str) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def make_bibtex_key(candidate: dict[str, Any], fallback_stem: str) -> str:
    authors = candidate.get("authors") or []
    first_author = authors[0]["name"] if authors and authors[0].get("name") else fallback_stem
    surname = first_author.split()[-1]
    year = candidate.get("year") or "undated"
    title = candidate.get("title") or fallback_stem
    title_token = preferred_title_token(title)
    return slugify(f"{surname}{year}{title_token}").replace("-", "")


def build_bibtex_entry(entry_type: str, bibtex_key: str, candidate: dict[str, Any]) -> str:
    author_names = " and ".join(author.get("name", "") for author in candidate.get("authors") or [])
    journal = ""
    if isinstance(candidate.get("journal"), dict):
        journal = normalize_venue_name(candidate["journal"].get("name", ""))

    fields: list[tuple[str, str]] = [
        ("title", candidate.get("title", "")),
        ("author", author_names),
        ("year", str(candidate.get("year", ""))),
        ("journal", journal or normalize_venue_name(candidate.get("venue", ""))),
        ("doi", (candidate.get("externalIds") or {}).get("DOI", "")),
        ("url", candidate.get("url", "")),
        ("abstract", candidate.get("abstract", "")),
    ]

    populated = [(name, sanitize_bibtex_value(value)) for name, value in fields if value]
    body = ",\n".join(f"  {name} = {{{value}}}" for name, value in populated)
    return f"@{entry_type}{{{bibtex_key},\n{body}\n}}\n"


def upsert_master_index(row: dict[str, str]) -> None:
    with FileLock(MASTER_INDEX_LOCK):
        rows: list[dict[str, str]] = []
        if MASTER_INDEX.exists():
            with MASTER_INDEX.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle, delimiter="\t")
                rows = [
                    master_index_row_defaults({key: value for key, value in existing.items() if key is not None})
                    for existing in reader
                ]

        row = master_index_row_defaults(row)

        replaced = False
        for idx, existing in enumerate(rows):
            if existing.get("paper_id") == row["paper_id"]:
                rows[idx] = row
                replaced = True
                break
        if not replaced:
            rows.append(row)

        write_master_index_rows(rows)


def load_master_index_row_by_paper_id(paper_id: str) -> dict[str, str] | None:
    if not MASTER_INDEX.exists():
        return None

    with MASTER_INDEX.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            if row.get("paper_id") == paper_id:
                return master_index_row_defaults(row)
    return None


def load_master_index_rows() -> list[dict[str, str]]:
    if not MASTER_INDEX.exists():
        return []
    with MASTER_INDEX.open("r", encoding="utf-8", newline="") as handle:
        return [master_index_row_defaults(row) for row in csv.DictReader(handle, delimiter="\t")]


def write_master_index_rows(rows: list[dict[str, str]]) -> None:
    normalized_rows = [master_index_row_defaults(row) for row in rows]
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        dir=MASTER_INDEX.parent,
        prefix=f".{MASTER_INDEX.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=MASTER_INDEX_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(normalized_rows)
        tmp_path = Path(handle.name)
    os.replace(tmp_path, MASTER_INDEX)


def load_master_index_row(paper_id: str) -> dict[str, str] | None:
    return load_master_index_row_by_paper_id(paper_id)


def load_master_index_row_by_pdf_path(pdf_path: Path | str) -> dict[str, str] | None:
    if not MASTER_INDEX.exists():
        return None
    path = Path(pdf_path) if not isinstance(pdf_path, Path) else pdf_path
    resolved = ensure_path_inside_library(path, "pdf path")
    target = relative_to_library(resolved)
    for row in load_master_index_rows():
        if row.get("pdf_path", "") == target:
            return row
    return None


def load_master_index_row_by_pdf_sha256(pdf_sha256: str) -> dict[str, str] | None:
    if not pdf_sha256:
        return None
    for row in load_master_index_rows():
        if row.get("pdf_sha256", "") == pdf_sha256:
            return row
    return None


def find_existing_reference_match(
    semantic_scholar_paper_id: str = "",
    doi: str = "",
    title: str = "",
    year: str = "",
) -> dict[str, str] | None:
    rows = load_master_index_rows()
    s2_target = str(semantic_scholar_paper_id or "").strip()
    if s2_target:
        matches = [row for row in rows if str(row.get("semantic_scholar_paper_id") or "").strip() == s2_target]
        if len(matches) == 1:
            return matches[0]

    doi_target = normalize_doi(doi)
    if doi_target:
        matches = [row for row in rows if normalize_doi(row.get("doi", "")) == doi_target]
        if len(matches) == 1:
            return matches[0]

    title_target = normalize_title(title)
    year_target = str(year or "").strip()
    if title_target and year_target:
        matches = [
            row
            for row in rows
            if normalize_title((row.get("resolved_title") or row.get("title_query") or "")) == title_target
            and str(row.get("year") or "").strip() == year_target
        ]
        if len(matches) == 1:
            return matches[0]
    return None


def upsert_master_bib(paper_id: str, bibtex_entry: str) -> None:
    with FileLock(MASTER_BIB_LOCK):
        entries: dict[str, str] = {}
        if MASTER_BIB.exists():
            current = MASTER_BIB.read_text(encoding="utf-8")
            blocks = [block.strip() for block in re.split(r"\n(?=@)", current) if block.strip()]
            for block in blocks:
                first_line = block.splitlines()[0]
                match = re.match(r"@\w+\{([^,]+),", first_line)
                if match:
                    entries[match.group(1)] = block + "\n"

        key_match = re.match(r"@\w+\{([^,]+),", bibtex_entry.splitlines()[0])
        if not key_match:
            raise ValueError("Could not parse BibTeX key from generated entry")
        entries[key_match.group(1)] = bibtex_entry

        output = "".join(entries[key].rstrip() + "\n\n" for key in sorted(entries))
        atomic_write_text(MASTER_BIB, output)


def remove_master_bib_entry(bibtex_key: str) -> None:
    if not bibtex_key or not MASTER_BIB.exists():
        return

    with FileLock(MASTER_BIB_LOCK):
        current = MASTER_BIB.read_text(encoding="utf-8") if MASTER_BIB.exists() else ""
        blocks = [block.strip() for block in re.split(r"\n(?=@)", current) if block.strip()]
        kept_blocks: list[str] = []
        for block in blocks:
            first_line = block.splitlines()[0]
            match = re.match(r"@\w+\{([^,]+),", first_line)
            if match and match.group(1) == bibtex_key:
                continue
            kept_blocks.append(block)

        output = ("\n\n".join(kept_blocks).rstrip() + "\n") if kept_blocks else ""
        atomic_write_text(MASTER_BIB, output)


def resolve_record_paths(payload: dict[str, Any], library_root: Path) -> dict[str, Any]:
    if not payload:
        return payload
    library_root_str = str(library_root)
    if "library_root" in payload:
        payload["library_root"] = library_root_str
    
    for key in ["pdf_path"]:
        if payload.get(key):
            val = payload[key]
            if not Path(val).is_absolute() and val != "":
                payload[key] = str((library_root / val).resolve())

    if isinstance(payload.get("artifacts"), dict):
        artifacts = payload["artifacts"]
        for key in ["record_path", "text_path", "chunk_path", "master_index", "master_bib"]:
            if artifacts.get(key):
                val = artifacts[key]
                if not Path(val).is_absolute() and val != "":
                    artifacts[key] = str((library_root / val).resolve())
    return payload


def write_record_json(record_path: Path, payload: dict[str, Any]) -> None:
    payload = json.loads(json.dumps(payload))
    
    def make_relative(path_str: Any) -> Any:
        if not isinstance(path_str, str) or not path_str:
            return path_str
        try:
            p = Path(path_str)
            if p.is_absolute() and p.resolve().is_relative_to(LIBRARY_ROOT):
                return str(p.resolve().relative_to(LIBRARY_ROOT))
        except ValueError:
            pass
        return path_str

    if "pdf_path" in payload:
        payload["pdf_path"] = make_relative(payload["pdf_path"])
    
    if isinstance(payload.get("artifacts"), dict):
        artifacts = payload["artifacts"]
        for key in ["record_path", "text_path", "chunk_path", "master_index", "master_bib"]:
            if key in artifacts:
                artifacts[key] = make_relative(artifacts[key])

    atomic_write_text(record_path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def write_chunks_jsonl(chunk_path: Path, paper_id: str, chunks: list[dict[str, Any]], text_sha256: str) -> None:
    with chunk_path.open("w", encoding="utf-8") as handle:
        for index, chunk in enumerate(chunks, start=1):
            row = {
                "paper_id": paper_id,
                "chunk_id": f"{paper_id}:chunk-{index:04d}",
                "char_start": chunk["char_start"],
                "char_end": chunk["char_end"],
                "text_sha256": text_sha256,
                "text": chunk["text"],
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def relative_to_library(path: Path) -> str:
    return str(path.resolve().relative_to(LIBRARY_ROOT))


def ingest(
    pdf_path: Path,
    force: bool,
    chunk_chars: int,
    overlap_chars: int,
    semantic_limit: int,
) -> int:
    ensure_registry_layout()

    pdf_path = ensure_path_inside_library(pdf_path, "pdf")
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF does not exist: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a PDF file: {pdf_path}")

    rel_pdf_path = relative_to_library(pdf_path)
    item_type = detect_item_type(pdf_path)
    pdf_sha256 = sha256_path(pdf_path)
    sha_paper_id = f"sha256-{pdf_sha256[:16]}"
    existing_by_path = load_master_index_row_by_pdf_path(pdf_path)
    existing_by_sha = load_master_index_row_by_pdf_sha256(pdf_sha256)

    if existing_by_path and existing_by_path.get("content_kind") == CONTENT_KIND_PDF and not force:
        if existing_by_path.get("pdf_sha256", "") == pdf_sha256:
            eprint(f"[library_ingest] already indexed path={pdf_path} paper_id={existing_by_path['paper_id']}")
            return 0
        eprint(f"[library_ingest] pdf changed at indexed path, rerun with --force: {pdf_path}")
        return 1

    title_query = derive_title_query(pdf_path)
    filename_hints = derive_filename_hints(pdf_path)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    text_status, text_error, text_content = extract_pdf_text(pdf_path)
    if text_status != "ok" or not text_content:
        eprint(f"[library_ingest] pdf_text_extraction_failed path={pdf_path}")
        eprint(f"[library_ingest] {text_error or 'No text was extracted from the PDF.'}")
        return 1
    text_title_candidates = derive_text_title_candidates(text_content) if text_content else []
    title_targets = [title_query] + list(text_title_candidates)
    doi_resolution = resolve_pdf_doi_metadata(
        pdf_path=pdf_path,
        extracted_text=text_content,
        filename_hints=filename_hints,
        title_targets=title_targets,
    )
    extracted_dois = doi_resolution["candidate_dois"]
    accepted_crossref_metadata: dict[str, Any] | None = doi_resolution["accepted_metadata"]
    review_crossref_metadata: dict[str, Any] | None = doi_resolution["review_metadata"]
    crossref_metadata: dict[str, Any] | None = doi_resolution["best_metadata"]
    crossref_error = str(doi_resolution["error"] or "")
    crossref_warning_messages: list[str] = list(doi_resolution["warnings"] or [])
    accepted_crossref_score: float | None = doi_resolution["best_score"] if accepted_crossref_metadata else None

    query_candidates = [title_query] + [candidate for candidate in text_title_candidates if normalize_title(candidate) != normalize_title(title_query)]
    attempts, merged_candidates = collect_semantic_candidates(
        queries=query_candidates,
        filename=pdf_path.name,
        item_type=item_type,
        filename_hints=filename_hints,
        semantic_limit=semantic_limit,
    )

    chosen_attempt = attempts[0] if attempts else {
        "query": title_query,
        "status": "not_found",
        "api_error": "",
        "warnings": [],
        "best_candidate": None,
        "scored_candidates": [],
        "best_score": None,
        "candidate_count": 0,
        "raw_response": None,
    }
    for attempt in attempts[1:]:
        if candidate_preference_tuple(
            attempt["status"],
            attempt["warnings"],
            attempt["scored_candidates"],
        ) > candidate_preference_tuple(
            chosen_attempt["status"],
            chosen_attempt["warnings"],
            chosen_attempt["scored_candidates"],
        ):
            chosen_attempt = attempt

    title_query = str(chosen_attempt["query"])
    api_response = chosen_attempt["raw_response"]
    api_error = str(chosen_attempt["api_error"])
    status = str(chosen_attempt["status"])
    best_candidate = chosen_attempt["best_candidate"]
    scored_candidates = chosen_attempt["scored_candidates"]
    warning_messages = chosen_attempt["warnings"]
    semantic_attempts = [
        {
            "query": str(attempt["query"]),
            "status": str(attempt["status"]),
            "api_error": str(attempt["api_error"]),
            "warnings": list(attempt["warnings"]),
            "best_score": attempt["best_score"],
            "candidate_count": attempt["candidate_count"],
        }
        for attempt in attempts
    ]
    supplement_parent_row: dict[str, str] | None = None
    if is_supplement_pdf(pdf_path, text_content) and status in {"not_found", "needs_manual_review", "api_error"} and not accepted_crossref_metadata:
        supplement_parent_row = find_parent_article_for_supplement(pdf_path, text_title_candidates)
        if supplement_parent_row:
            status = "matched_supplement"
    metadata_source = "semantic_scholar_primary"
    s2_candidate_dois = candidate_dois(best_candidate or {})
    matching_extracted_doi = next(
        (doi for doi in extracted_dois if normalize_doi(doi) in {normalize_doi(value) for value in s2_candidate_dois}),
        "",
    )
    if accepted_crossref_metadata:
        status = "matched_via_doi"
        best_candidate = choose_semantic_candidate_for_doi(
            merged_candidates,
            str((accepted_crossref_metadata or {}).get("DOI") or ""),
            best_candidate,
        )
        if best_candidate:
            metadata_source = "crossref_doi_plus_semantic_enrichment"
        else:
            metadata_source = "crossref_doi_primary"
    elif review_crossref_metadata and status in {"not_found", "api_error"}:
        status = "needs_manual_review"

    resolved_title = best_candidate.get("title", "") if best_candidate else ""
    authors = candidate_authors(best_candidate or {})
    year = str((best_candidate or {}).get("year", "") or "")
    venue = candidate_venue(best_candidate or {})
    doi = candidate_doi(best_candidate or {})
    match_confidence = ""
    if scored_candidates:
        match_confidence = f"{scored_candidates[0].score:.4f}"

    if accepted_crossref_metadata:
        crossref_fields = crossref_to_bibtex_fields(accepted_crossref_metadata)
        resolved_title = crossref_fields["title"] or resolved_title
        authors = crossref_fields["authors"] or authors
        year = crossref_fields["year"] or year
        venue = normalize_venue_name(crossref_fields["venue"] or venue)
        doi = crossref_fields["doi"] or doi
    elif supplement_parent_row:
        resolved_title = supplement_parent_row.get("resolved_title", "")
        authors = supplement_parent_row.get("authors", "")
        year = supplement_parent_row.get("year", "")
        venue = normalize_venue_name(supplement_parent_row.get("venue", ""))
        doi = ""

    matched_row = find_existing_reference_match(
        semantic_scholar_paper_id=str((best_candidate or {}).get("paperId", "")),
        doi=doi,
        title=resolved_title or title_query,
        year=year,
    )

    if existing_by_sha and existing_by_sha.get("content_kind") == CONTENT_KIND_PDF:
        matched_row = existing_by_sha

    if matched_row and matched_row.get("content_kind") == CONTENT_KIND_PDF:
        same_pdf = normalize_doi(matched_row.get("doi", "")) == normalize_doi(doi) or matched_row.get("paper_id") == (existing_by_sha or {}).get("paper_id", "")
        if same_pdf:
            if not force and matched_row.get("pdf_path", "") == rel_pdf_path and matched_row.get("pdf_sha256", "") == pdf_sha256:
                eprint(f"[library_ingest] already indexed path={pdf_path} paper_id={matched_row['paper_id']}")
                return 0
        else:
            eprint(
                f"[library_ingest] duplicate warning path={pdf_path} existing_paper_id={matched_row.get('paper_id', '')} status={matched_row.get('match_status', '')}"
            )
            return 0

    existing_row = matched_row or existing_by_sha or existing_by_path or load_master_index_row_by_paper_id(sha_paper_id)
    paper_id = matched_row["paper_id"] if matched_row and matched_row.get("content_kind") == CONTENT_KIND_REF else sha_paper_id
    record_path = record_path_for_paper_id(paper_id)
    existing_record_payload = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}

    text_sha256 = ""
    chunk_count = 0
    text_path: Path | None = None
    chunk_path: Path | None = None
    if text_content:
        text_artifacts = write_text_artifacts(
            paper_id=paper_id,
            text_content=text_content,
            chunk_chars=chunk_chars,
            overlap_chars=overlap_chars,
        )
        text_sha256 = text_artifacts["text_sha256"]
        chunk_count = text_artifacts["chunk_count"]
        text_path = text_artifacts["text_path"]
        chunk_path = text_artifacts["chunk_path"]

    existing_bibtex_key = existing_row.get("bibtex_key", "") if existing_row else ""
    existing_bibtex_type = existing_row.get("bibtex_type", "") if existing_row else ""
    bibtex_type = existing_bibtex_type or ("book" if item_type == "book" else "article")
    bibtex_key = existing_bibtex_key
    abstract_source = ""
    canonical_url = canonical_url_from_sources(
        crossref_meta=accepted_crossref_metadata,
        best_candidate=best_candidate,
    )

    if status == "matched" and best_candidate:
        if not bibtex_key:
            bibtex_key = make_bibtex_key(best_candidate, fallback_stem=pdf_path.stem)
        bibtex_entry = build_bibtex_entry_from_fields(
            bibtex_type,
            bibtex_key,
            semantic_candidate_to_metadata(best_candidate),
        )
        upsert_master_bib(paper_id, bibtex_entry)
        if existing_row and existing_row.get("bibtex_key") and existing_row.get("bibtex_key") != bibtex_key:
            remove_master_bib_entry(existing_row.get("bibtex_key", ""))
        abstract_source = "semantic_scholar" if best_candidate.get("abstract") else ""
    elif accepted_crossref_metadata:
        if not bibtex_key:
            bibtex_key = build_bibtex_key_from_fields(authors, year, resolved_title, fallback_stem=pdf_path.stem)
        crossref_fields = crossref_to_bibtex_fields(accepted_crossref_metadata)
        semantic_abstract = str((best_candidate or {}).get("abstract") or "")
        bibtex_entry = build_bibtex_entry_from_fields(
            bibtex_type,
            bibtex_key,
            {
                **crossref_fields,
                "abstract": crossref_fields.get("abstract") or semantic_abstract,
            },
        )
        upsert_master_bib(paper_id, bibtex_entry)
        if existing_row and existing_row.get("bibtex_key") and existing_row.get("bibtex_key") != bibtex_key:
            remove_master_bib_entry(existing_row.get("bibtex_key", ""))
        if crossref_fields.get("abstract"):
            abstract_source = "crossref"
        elif semantic_abstract:
            abstract_source = "semantic_scholar_linked_record"
    elif supplement_parent_row:
        bibtex_key = bibtex_key or supplement_parent_row.get("bibtex_key", "")

    final_notes: list[str] = [text_error, api_error, crossref_error]
    if status not in {"matched", "matched_via_doi", "matched_supplement"}:
        final_notes.extend(warning_messages)
        final_notes.extend(crossref_warning_messages)

    row = master_index_row_defaults(
        {
        "paper_id": paper_id,
        "item_type": item_type,
        "content_kind": CONTENT_KIND_PDF,
        "pdf_path": rel_pdf_path,
        "pdf_sha256": pdf_sha256,
        "filename": pdf_path.name,
        "title_query": title_query,
        "resolved_title": resolved_title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "semantic_scholar_paper_id": str((best_candidate or {}).get("paperId", "")),
        "match_confidence": match_confidence,
        "match_status": status,
        "bibtex_type": bibtex_type,
        "canonical_url": canonical_url,
        "bibtex_key": bibtex_key,
        "text_path": relative_to_library(text_path) if text_path and text_path.exists() else "",
        "chunk_path": relative_to_library(chunk_path) if chunk_path and chunk_path.exists() else "",
        "abstract_source": abstract_source,
        "date_indexed": now,
        "notes": " | ".join(part for part in final_notes if part),
        }
    )
    upsert_master_index(row)

    record_payload = existing_record_payload
    record_payload.update(
        {
            "paper_id": paper_id,
            "content_kind": CONTENT_KIND_PDF,
            "addition_mode": "pdf_autonomous",
            "library_root": str(LIBRARY_ROOT),
            "pdf_path": str(pdf_path),
            "pdf_sha256": pdf_sha256,
            "filename": pdf_path.name,
            "item_type": item_type,
            "title_query": title_query,
            "filename_hints": filename_hints,
            "doi_candidates": extracted_dois,
            "doi_candidate_details": doi_resolution["candidate_details"],
            "date_indexed": now,
            "match_status": status,
            "match_confidence": scored_candidates[0].score if scored_candidates else None,
            "warnings": warning_messages,
            "crossref_warnings": crossref_warning_messages,
            "metadata_source": metadata_source,
            "semantic_scholar": {
                "query": title_query,
                "limit": semantic_limit,
                "api_error": api_error,
                "attempts": semantic_attempts,
                "selected_paper_id": str((best_candidate or {}).get("paperId", "")),
                "best_candidate": best_candidate,
                "candidate_scores": [asdict(candidate) for candidate in scored_candidates],
                "candidate_dois": sorted(s2_candidate_dois),
                "matching_extracted_doi": matching_extracted_doi,
                "raw_response": api_response,
                "merged_candidates": merged_candidates,
            },
            "crossref": {
                "error": crossref_error,
                "metadata": crossref_metadata,
                "accepted_score": accepted_crossref_score,
                "accepted": accepted_crossref_metadata is not None,
                "accepted_doi": str((accepted_crossref_metadata or {}).get("DOI") or ""),
                "best_candidate_doi": doi_resolution["best_candidate_doi"],
                "crossref_attempts": doi_resolution["crossref_attempts"],
                "accepted_source": doi_resolution["accepted_source"],
                "matched_by": doi_resolution["matched_by"],
            },
            "supplement_parent": supplement_parent_row,
            "artifacts": {
                "record_path": str(record_path),
                "text_path": str(text_path) if text_path and text_path.exists() else None,
                "chunk_path": str(chunk_path) if chunk_path and chunk_path.exists() else None,
                "master_index": str(MASTER_INDEX),
                "master_bib": str(MASTER_BIB),
                "text_kind": "pdf_text" if text_content else "",
            },
            "text_extraction": {
                "status": text_status,
                "error": text_error,
                "text_sha256": text_sha256,
                "chunk_count": chunk_count,
                "chunk_target_chars": chunk_chars,
                "chunk_overlap_chars": overlap_chars,
                "converter": str(PDF2TEXT),
            },
        }
    )
    write_record_json(record_path, record_payload)

    summary = textwrap.dedent(
        f"""\
        [library_ingest] paper_id={paper_id}
        [library_ingest] match_status={status} confidence={match_confidence or 'n/a'}
        [library_ingest] text_status={text_status} chunks={chunk_count}
        [library_ingest] record={record_path}
        """
    ).strip()
    eprint(summary)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest one PDF into the local literature registry.")
    parser.add_argument("pdf", type=Path, help="Path to a PDF inside the library")
    parser.add_argument("--force", action="store_true", help="Re-run ingestion even if a record already exists")
    parser.add_argument(
        "--chunk-chars",
        type=int,
        default=2200,
        help="Target character length for each chunk (default: 2200)",
    )
    parser.add_argument(
        "--overlap-chars",
        type=int,
        default=250,
        help="Character overlap between adjacent chunks (default: 250)",
    )
    parser.add_argument(
        "--semantic-limit",
        type=int,
        default=10,
        help="Number of Semantic Scholar candidates to request (default: 10)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pdf_path = args.pdf.expanduser().resolve()
    return ingest(
        pdf_path=pdf_path,
        force=args.force,
        chunk_chars=args.chunk_chars,
        overlap_chars=args.overlap_chars,
        semantic_limit=args.semantic_limit,
    )


if __name__ == "__main__":
    raise SystemExit(main())
