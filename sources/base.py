"""Abstract base class for paper sources."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List


class BaseSource(ABC):

    @abstractmethod
    def fetch(
        self,
        *,
        categories: List[str],
        target_date: str | None = None,
        max_results: int | None = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch papers grouped by category."""
        ...

    @abstractmethod
    def save_raw(
        self,
        grouped_papers: Dict[str, List[Dict[str, Any]]],
        raw_dir: str,
    ) -> List[Path]:
        """Persist raw papers. Returns created file paths."""
        ...
