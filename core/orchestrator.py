"""Async pipeline orchestrator that ties sources, analyzers, notifiers, and storage together."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.config import Profile, ensure_data_directories
from core.storage import StatusStore, combine_papers, store_archive_files, store_daily_file
from sources.arxiv import ArxivSource, resolve_target_date
from analyzers.llm_classifier import LLMClassifier, ClassificationError
from llm.client import AsyncLLMClient, LLMSettings, build_llm_settings
from notifiers.base import BaseNotifier
from notifiers.feishu import FeishuNotifier


NOTIFIER_REGISTRY: Dict[str, type] = {
    "feishu": FeishuNotifier,
}


def _infer_data_root(raw_dir: str) -> str:
    if "/raw" in raw_dir:
        return raw_dir.rsplit("/raw", 1)[0]
    return "./data"


class PipelineOrchestrator:
    """Coordinates the scrape -> classify -> send pipeline with state tracking."""

    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        self.status_store = StatusStore(data_dir=_infer_data_root(profile.data_dirs.raw))
        ensure_data_directories(profile)

    def _build_notifiers(self) -> List[BaseNotifier]:
        notifiers: List[BaseNotifier] = []
        for ch in self.profile.channels:
            cls = NOTIFIER_REGISTRY.get(ch.type)
            if cls:
                notifiers.append(cls.from_channel_config(ch))
        return notifiers

    # -- full pipeline -------------------------------------------------------

    async def run_full_pipeline(
        self,
        *,
        target_date: str | None = None,
        on_stage: Callable[[str, str], None] | None = None,
        on_classify_progress: Callable[[int, int], None] | None = None,
    ) -> Dict[str, Any]:
        resolved_date = resolve_target_date(target_date)
        day_key = resolved_date.isoformat()

        if self.status_store.is_stage_done(day_key, "send"):
            return {"status": "already_completed", "date": day_key}

        # Stage 1
        if on_stage:
            on_stage("scrape", "start")
        raw_files = await self._stage_scrape(day_key, target_date)
        if on_stage:
            on_stage("scrape", "done")
        if not raw_files:
            return {"status": "no_papers", "date": day_key}

        # Stage 2
        if on_stage:
            on_stage("classify", "start")
        daily_file = await self._stage_classify(day_key, raw_files, classify_callback=on_classify_progress)
        if on_stage:
            on_stage("classify", "done")

        # Stage 3
        if on_stage:
            on_stage("send", "start")
        await self._stage_send(day_key, daily_file)
        if on_stage:
            on_stage("send", "done")

        return {"status": "completed", "date": day_key, "daily_file": str(daily_file)}

    # -- individual stages ---------------------------------------------------

    async def _stage_scrape(self, day_key: str, target_date: str | None) -> List[Path]:
        stage_info = self.status_store.get_stage_info(day_key, "scrape")
        if stage_info.get("completed"):
            raw_files = [Path(p) for p in stage_info.get("raw_files", []) if Path(p).exists()]
            if raw_files:
                print("Scrape stage already completed; reusing stored raw files.")
                return raw_files
            self.status_store.clear_stage(day_key, ["scrape", "classify", "send"])

        source = ArxivSource()
        categories = self.profile.subscriptions.categories
        if not categories:
            print("No categories configured; skipping scrape.")
            return []

        paper_groups = source.fetch(categories=categories, target_date=target_date)
        raw_dir = self.profile.data_dirs.raw
        raw_files = source.save_raw(paper_groups, raw_dir)

        total = sum(len(papers) for papers in paper_groups.values())
        print(f"Scraped {total} papers across {len(paper_groups)} categories.")

        if raw_files:
            self.status_store.mark_stage(
                day_key, "scrape", raw_files=[str(p) for p in raw_files]
            )
            self.status_store.clear_stage(day_key, ["classify", "send"])

        return raw_files

    async def _stage_classify(self, day_key: str, raw_files: List[Path], *, classify_callback: Callable[[int, int], None] | None = None) -> Path:
        stage_info = self.status_store.get_stage_info(day_key, "classify")
        if stage_info.get("completed"):
            daily_file = Path(stage_info.get("daily_file", ""))
            if daily_file.exists():
                print("Classification stage already completed; reusing daily file.")
                return daily_file
            self.status_store.clear_stage(day_key, ["classify", "send"])

        papers = combine_papers(raw_files)
        if not papers:
            raise RuntimeError("No papers to classify.")

        if classify_callback:
            classify_callback(0, len(papers))

        analyzer_cfg = self.profile.llm.get("analyzer") or next(iter(self.profile.llm.values()))
        settings = build_llm_settings(analyzer_cfg)
        async_client = AsyncLLMClient(settings)

        interest_tags = [
            {"label": t.label, "description": t.description, "keywords": t.keywords}
            for t in self.profile.subscriptions.interest_tags
        ]
        classifier = LLMClassifier(
            async_client,
            interest_tags=interest_tags,
            max_concurrency=analyzer_cfg.max_concurrency,
            progress_callback=classify_callback,
            language=getattr(self.profile, "language", "en"),
        )

        classified = await classifier.classify(papers)

        archive_dir = Path(self.profile.data_dirs.archive)
        daily_dir = Path(self.profile.data_dirs.daily)
        archive_paths = store_archive_files(classified, archive_dir)
        daily_file = store_daily_file(classified, [str(p) for p in raw_files], daily_dir)

        print(f"Classified {len(classified)} papers.")
        print(f"Daily summary -> {daily_file}")
        print(f"Archive files: {len(archive_paths)}")

        self.status_store.mark_stage(day_key, "classify", daily_file=str(daily_file))
        return daily_file

    async def _stage_send(self, day_key: str, daily_file: Path) -> None:
        if self.status_store.is_stage_done(day_key, "send"):
            print("Send stage already completed; skipping.")
            return

        payload = json.loads(daily_file.read_text(encoding="utf-8"))
        papers = payload.get("papers", [])
        if not papers:
            print("No papers in daily file; skipping send.")
            return

        notifiers = self._build_notifiers()
        if not notifiers:
            print("No notification channels configured; skipping send.")
            return

        for notifier in notifiers:
            notifier.send_digest(papers)

        self.status_store.mark_stage(day_key, "send")
        print("Digest sent successfully.")

    # -- convenience methods for agent tools ---------------------------------

    async def run_scrape_only(self, *, target_date: str | None = None) -> List[Path]:
        resolved_date = resolve_target_date(target_date)
        return await self._stage_scrape(resolved_date.isoformat(), target_date)

    def query_status(self, *, days: int = 7) -> Dict[str, Any]:
        store = self.status_store.load()
        today = dt.datetime.now(dt.timezone.utc).date()
        result: Dict[str, Any] = {}
        for i in range(days):
            day = (today - dt.timedelta(days=i)).isoformat()
            if day in store:
                result[day] = store[day]
        return result

    def query_papers(self, *, keyword: str | None = None, date: str | None = None) -> List[Dict]:
        daily_dir = Path(self.profile.data_dirs.daily)
        papers: List[Dict] = []

        if date:
            date_tag = date.replace("-", "")
            target_dir = daily_dir / date_tag
            if target_dir.exists():
                for f in sorted(target_dir.glob("daily_*.json")):
                    payload = json.loads(f.read_text(encoding="utf-8"))
                    papers.extend(payload.get("papers", []))
        else:
            for date_dir in sorted(daily_dir.iterdir(), reverse=True):
                if not date_dir.is_dir():
                    continue
                for f in sorted(date_dir.glob("daily_*.json")):
                    payload = json.loads(f.read_text(encoding="utf-8"))
                    papers.extend(payload.get("papers", []))
                if papers:
                    break

        if keyword and papers:
            kw = keyword.lower()
            papers = [
                p for p in papers
                if kw in p.get("title", "").lower()
                or kw in p.get("summary", "").lower()
                or kw in p.get("tldr_zh", "").lower()
                or kw in p.get("primary_area", "").lower()
                or kw in p.get("secondary_focus", "").lower()
            ]

        return papers
