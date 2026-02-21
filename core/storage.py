"""Centralised status tracking and data persistence for the Awesome Paper pipeline.

StatusStore  -- pipeline state tracking (extracted from automation_runner.py)
Module-level helpers -- data persistence (extracted from awesome_paper_manager.py)
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List


# ---------------------------------------------------------------------------
# StatusStore  (from automation_runner.py)
# ---------------------------------------------------------------------------

class StatusStore:
    """Persistent pipeline state tracker backed by a JSON file."""

    def __init__(self, data_dir: str = "./data") -> None:
        self._path = Path(data_dir) / "automation_status.json"

    # -- low-level I/O -----------------------------------------------------

    def load(self) -> Dict:
        """Read the status file and return the full store dictionary."""
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    def save(self, store: Dict) -> None:
        """Write *store* back to the status file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # -- convenience mutations ---------------------------------------------

    def mark_stage(self, day: str, stage: str, **info: object) -> None:
        """Record *stage* as completed for *day* and persist immediately."""
        store = self.load()
        day_status = store.setdefault(day, {})
        stage_info: Dict[str, object] = {
            "completed": True,
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        stage_info.update(info)
        day_status[stage] = stage_info
        self.save(store)

    def clear_stage(self, day: str, stages: list[str]) -> None:
        """Remove one or more stage entries for *day* and persist."""
        store = self.load()
        day_status = store.setdefault(day, {})
        for stage in stages:
            day_status.pop(stage, None)
        self.save(store)

    # -- query helpers -----------------------------------------------------

    def is_stage_done(self, day: str, stage: str) -> bool:
        """Return whether *stage* is marked completed for *day*."""
        store = self.load()
        return bool(store.get(day, {}).get(stage, {}).get("completed"))

    def get_stage_info(self, day: str, stage: str) -> Dict[str, Any]:
        """Return the full info dict stored for *stage* on *day*."""
        store = self.load()
        return dict(store.get(day, {}).get(stage, {}))


# ---------------------------------------------------------------------------
# Data-persistence helpers  (from awesome_paper_manager.py)
# ---------------------------------------------------------------------------

def _safe_segment(value: str | None, fallback: str) -> str:
    """Sanitise *value* into a filesystem-safe path segment."""
    token = (value or '').strip().lower()
    if not token:
        return fallback
    token = re.sub(r'[^a-z0-9]+', '-', token)
    token = token.strip('-')
    return token or fallback


def _paper_filename(paper: Dict[str, Any], index: int) -> str:
    """Derive a filename for a single paper JSON."""
    arxiv_id = str(paper.get('arxiv_id', '')).strip()
    if arxiv_id:
        slug = re.sub(r'[^a-z0-9]+', '-', arxiv_id.lower())
    else:
        slug = _safe_segment(paper.get('title', ''), f'paper-{index}')
    return f"{slug or f'paper-{index}'}.json"


def store_archive_files(papers: List[Dict], archive_root: Path) -> List[Path]:
    """Store each paper as an individual JSON file under a taxonomy hierarchy.

    Directory structure: ``archive_root / primary_area / secondary_focus / application_domain``.
    """
    stored_paths: List[Path] = []
    for idx, paper in enumerate(papers, start=1):
        primary = _safe_segment(paper.get('primary_area'), 'uncategorised')
        secondary = _safe_segment(paper.get('secondary_focus'), 'general')
        application = _safe_segment(paper.get('application_domain'), 'general')

        dest_dir = archive_root / primary / secondary / application
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / _paper_filename(paper, idx)
        dest_path.write_text(
            json.dumps(paper, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        stored_paths.append(dest_path)

    return stored_paths


def store_daily_file(
    papers: List[Dict],
    raw_sources: List[str],
    daily_dir: Path,
) -> Path:
    """Create the daily summary JSON inside *daily_dir* and return its path.

    The payload mirrors the format previously produced inline by
    ``_classify_and_store`` in *awesome_paper_manager.py*.
    """
    from datetime import datetime  # local import to match original scope

    run_timestamp = datetime.utcnow()
    date_tag = run_timestamp.strftime("%Y%m%d")
    daily_subdir = daily_dir / date_tag
    daily_subdir.mkdir(parents=True, exist_ok=True)
    daily_filename = f"daily_{date_tag}_{run_timestamp.strftime('%H%M%S')}.json"
    daily_path = daily_subdir / daily_filename

    output_payload: Dict[str, Any] = {
        "generated_at": run_timestamp.isoformat() + "Z",
        "source_raw_files": raw_sources,
        "paper_count": len(papers),
        "papers": papers,
    }
    if len(raw_sources) == 1:
        output_payload["source_raw_file"] = raw_sources[0]

    daily_path.write_text(
        json.dumps(output_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return daily_path


# ---------------------------------------------------------------------------
# Combining raw files  (from automation_runner.py)
# ---------------------------------------------------------------------------

def combine_papers(raw_files: Iterable[Path]) -> List[Dict]:
    """Merge papers from multiple raw-scrape JSON files into a single list."""
    combined: List[Dict[str, object]] = []
    for raw_path in raw_files:
        try:
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        combined.extend(payload.get("papers", []))
    return combined
