"""Stage 1 scraper for fetching latest papers from arXiv."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List
from collections import defaultdict

import requests
import time
from xml.etree import ElementTree as ET

ARXIV_API_URLS = (
    "https://export.arxiv.org/api/query",
    "http://export.arxiv.org/api/query",
)
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


class ArxivScrapeError(RuntimeError):
    """Raised when the arXiv API call fails."""


def _fetch_feed(params: Dict[str, Any], timeout: int):
    """Request arXiv feed with retries and SSL fallback."""

    last_error: Exception | None = None
    for base_url in ARXIV_API_URLS:
        for attempt in range(1, 4):
            try:
                response = requests.get(base_url, params=params, timeout=timeout)
                response.raise_for_status()
                return response
            except requests.exceptions.SSLError as exc:
                last_error = exc
                break  # switch to next base URL
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 3:
                    print(f"[Stage1] Retry {attempt}/3 failed on {base_url}: {exc}")
                    time.sleep(min(2 ** (attempt - 1), 5))
                    continue
                break
    raise ArxivScrapeError(f"Failed to query arXiv: {last_error}") from last_error


def resolve_target_date(target_date: str | None = None) -> dt.date:
    """Public helper to resolve the effective target date."""

    return _resolve_target_date(target_date)


def fetch_latest_papers(
    categories: Iterable[str],
    *,
    max_results: int | None = None,
    target_date: str | None = None,
    timeout: int = 30,
) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch the latest papers from arXiv grouped per category.

    Args:
        categories: arXiv category identifiers such as "cs.CL".
        max_results: cap the number of results per category; when None fetch all available (default).
        target_date: fetch papers whose arXiv `published` 日期等于该值（YYYY-MM-DD）；默认为当天 UTC 日期。
        timeout: request timeout in seconds.

    Returns:
        A mapping from category to the list of paper dictionaries ready for downstream processing.
    """

    cats = [c.strip() for c in categories if c and c.strip()]
    if not cats:
        raise ValueError("At least one arXiv category must be provided")

    target_date_obj = _resolve_target_date(target_date)

    results: Dict[str, List[Dict[str, Any]]] = {}
    for cat in cats:
        query = f"cat:{cat}"
        results[cat] = _fetch_query(
            query,
            max_results=max_results,
            target_date=target_date_obj,
            allowed_primary={cat},
            timeout=timeout,
        )
    return results



def _resolve_target_date(target_date: str | None) -> dt.date:
    if target_date:
        try:
            return dt.datetime.strptime(target_date.strip(), "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("target_date must be in YYYY-MM-DD format") from exc

    today = dt.datetime.now(dt.timezone.utc).date()
    candidate = today - dt.timedelta(days=1)
    while candidate.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        candidate -= dt.timedelta(days=1)
    return candidate





def _fetch_query(
    query: str,
    *,
    max_results: int | None,
    target_date: dt.date,
    allowed_primary: set[str],
    timeout: int,
) -> List[Dict[str, Any]]:
    per_page = 200
    remaining = None if max_results is None else max(max_results, 0)
    collected: List[Dict[str, Any]] = []
    start = 0

    while True:
        if remaining is not None and remaining <= 0:
            break

        batch_size = per_page if remaining is None else min(per_page, remaining)
        params = {
            "search_query": query,
            "start": start,
            "max_results": batch_size,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }

        response = _fetch_feed(params, timeout)

        root = ET.fromstring(response.text)
        entries = root.findall("atom:entry", ATOM_NS)
        if not entries:
            break

        older_reached = False
        for entry in entries:
            published_raw = entry.findtext("atom:published", default="", namespaces=ATOM_NS)
            try:
                published_dt = dt.datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
            except ValueError:
                published_dt = None

            if not published_dt:
                continue

            published_date = published_dt.date()
            if published_date < target_date:
                older_reached = True
                break
            if published_date > target_date:
                continue

            primary_category = entry.find("arxiv:primary_category", ATOM_NS)
            primary_term = primary_category.attrib.get("term", "") if primary_category is not None else ""
            if allowed_primary and primary_term not in allowed_primary:
                continue

            arxiv_id = entry.findtext("atom:id", default="", namespaces=ATOM_NS).split("/")[-1]
            link = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""

            paper = {
                "arxiv_id": arxiv_id,
                "title": (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").strip(),
                "summary": (entry.findtext("atom:summary", default="", namespaces=ATOM_NS) or "").strip(),
                "authors": [
                    author.findtext("atom:name", default="", namespaces=ATOM_NS).strip()
                    for author in entry.findall("atom:author", ATOM_NS)
                ],
                "published": published_raw,
                "primary_category": primary_term,
                "arxiv_url": link,
            }

            collected.append(paper)

        fetched_count = len(entries)
        start += fetched_count
        if remaining is not None:
            remaining -= fetched_count

        if older_reached or fetched_count < batch_size:
            break

    return collected if max_results is None else collected[:max_results]





def save_raw_papers(
    grouped_papers: Dict[str, List[Dict[str, Any]]],
    *,
    raw_dir: str,
) -> List[Path]:
    """Persist grouped papers to the raw data directory (date/category folders)."""

    raw_path = Path(raw_dir)
    now = dt.datetime.now(dt.timezone.utc)
    fallback_date = now.date().strftime("%Y%m%d")

    created_files: List[Path] = []
    for category, papers in sorted(grouped_papers.items()):
        if not papers:
            continue

        cat_tag = category.replace('.', '')
        papers_by_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for paper in papers:
            published_raw = str(paper.get("published", "")).strip()
            date_tag = fallback_date
            if published_raw:
                try:
                    published_dt = dt.datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
                    date_tag = published_dt.date().strftime("%Y%m%d")
                except ValueError:
                    date_tag = fallback_date
            papers_by_date[date_tag].append(paper)

        for date_tag, date_papers in sorted(papers_by_date.items()):
            target_dir = raw_path / date_tag / cat_tag
            target_dir.mkdir(parents=True, exist_ok=True)
            file_path = target_dir / f"raw_{cat_tag}_{date_tag}.json"

            payload = {
                "generated_at": now.isoformat().replace("+00:00", "Z"),
                "paper_date": date_tag,
                "categories": [category],
                "paper_count": len(date_papers),
                "papers": date_papers,
            }

            file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            created_files.append(file_path)

    return created_files
