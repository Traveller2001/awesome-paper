"""Abstract base class for paper analyzers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class BaseAnalyzer(ABC):

    @abstractmethod
    async def classify(self, papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Classify papers and return enriched dicts. Async interface."""
        ...
