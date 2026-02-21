"""Abstract base class for notification channels."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List


class BaseNotifier(ABC):

    @abstractmethod
    def send_digest(
        self,
        papers: List[Dict[str, Any]],
        *,
        exclude_tags: Iterable[str] | None = None,
    ) -> None:
        """Send a full paper digest via this channel."""
        ...

    @abstractmethod
    def send_text(self, text: str) -> None:
        """Send a simple text message via this channel."""
        ...

    @classmethod
    @abstractmethod
    def from_channel_config(cls, channel_config) -> "BaseNotifier":
        """Factory: construct a notifier from a ChannelConfig dataclass."""
        ...
