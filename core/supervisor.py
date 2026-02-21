"""Pipeline supervisor — captures stdout noise and produces concise reports."""
from __future__ import annotations

import asyncio
import contextlib
import io
import re
from typing import Any, Callable, Dict


class PipelineSupervisor:
    """Wraps pipeline execution, captures output, produces concise reports."""

    def __init__(self, orchestrator, profile) -> None:
        self._orchestrator = orchestrator
        self._profile = profile
        self._captured_output: str = ""
        self._stages: Dict[str, str] = {}  # stage_name -> "ok"/"skipped"/"failed"
        self._paper_count: int = 0
        self._errors: list[str] = []

    # -- public API ----------------------------------------------------------

    def run(
        self,
        target_date: str | None = None,
        on_stage: Callable[[str, str], None] | None = None,
        on_classify_progress: Callable[[int, int], None] | None = None,
    ) -> Dict[str, Any]:
        """Run pipeline, capture stdout, return structured summary."""
        buf = io.StringIO()

        def _stage_cb(name: str, event: str) -> None:
            if event == "start":
                self._stages[name] = "running"
            elif event == "done":
                self._stages[name] = "ok"
            if on_stage:
                on_stage(name, event)

        try:
            with contextlib.redirect_stdout(buf):
                result = asyncio.run(
                    self._orchestrator.run_full_pipeline(
                        target_date=target_date,
                        on_stage=_stage_cb,
                        on_classify_progress=on_classify_progress,
                    )
                )
        except Exception as exc:
            self._errors.append(str(exc))
            # Mark any running stage as failed
            for name, status in self._stages.items():
                if status == "running":
                    self._stages[name] = "failed"
            self._captured_output = buf.getvalue()
            self._parse_captured_output()
            return self.summarize(status_override="error", date=target_date or "")

        self._captured_output = buf.getvalue()
        self._parse_captured_output()

        # Mark stages that were never started as "skipped"
        for stage in ("scrape", "classify", "send"):
            if stage not in self._stages:
                self._stages[stage] = "skipped"

        return self.summarize(
            status_override=result.get("status", "unknown"),
            date=result.get("date", ""),
        )

    def summarize(self, status_override: str = "", date: str = "") -> Dict[str, Any]:
        """Produce a compact summary dict for the agent."""
        summary_text = self._build_summary_text(status_override)
        return {
            "status": status_override,
            "date": date,
            "stages": dict(self._stages),
            "paper_count": self._paper_count,
            "summary": summary_text,
            "errors": list(self._errors),
        }

    # -- internal helpers ----------------------------------------------------

    def _parse_captured_output(self) -> None:
        """Extract paper counts and other info from captured print() output."""
        text = self._captured_output

        # "Scraped 47 papers across 4 categories."
        m = re.search(r"Scraped\s+(\d+)\s+papers", text)
        if m:
            self._paper_count = int(m.group(1))

        # "Classified 32 papers."
        m = re.search(r"Classified\s+(\d+)\s+papers", text)
        if m and self._paper_count == 0:
            self._paper_count = int(m.group(1))

    def _build_summary_text(self, status: str) -> str:
        """Build a 1-2 sentence human-readable summary."""
        if status == "error":
            failed = [n for n, s in self._stages.items() if s == "failed"]
            stage_str = failed[0] if failed else "unknown"
            err_detail = self._errors[0] if self._errors else "unknown error"
            return f"Pipeline failed at {stage_str} stage: {err_detail}"

        if status == "no_papers":
            return "No papers found for the target date."

        if status == "already_completed":
            return "Pipeline already completed for this date; nothing to do."

        if status == "completed":
            parts = []
            n_cats = len(self._profile.subscriptions.categories)
            parts.append(f"Scraped {self._paper_count} papers across {n_cats} categories")
            parts.append(f"classified {self._paper_count}")

            send_status = self._stages.get("send", "skipped")
            if send_status == "ok":
                ch_types = [ch.type for ch in self._profile.channels] or ["unknown"]
                parts.append(f"sent via {', '.join(ch_types)}")
            elif send_status == "skipped":
                parts.append("no notification channel configured")

            return ", ".join(parts) + "."

        return f"Pipeline finished with status: {status}."
