"""Feishu notification channel — rich card digest via webhook."""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Set, Tuple

import requests

from notifiers.base import BaseNotifier


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class FeishuSendError(RuntimeError):
    """Raised when Feishu webhook rejects the payload."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMOJI_BY_PRIMARY = {
    "text_models": "\U0001f4dd",
    "multimodal_models": "\U0001f5bc\ufe0f",
    "audio_models": "\U0001f3a7",
    "video_models": "\U0001f3ac",
    "vla_models": "\U0001f916",
    "diffusion_models": "\U0001f32b\ufe0f",
    "uncategorised": "\U0001f4cc",
}

ClusterKey = Tuple[str, str, str, str]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalise(value: str | None, fallback: str) -> str:
    text = (value or "").strip()
    text = " ".join(text.split())
    return text if text else fallback


def _to_papers_cool(url: str) -> str:
    if not url:
        return url
    prefix = "https://arxiv.org/abs/"
    if url.startswith(prefix):
        return url.replace(prefix, "https://papers.cool/arxiv/")
    return url


def _to_alpharxiv(url: str) -> str:
    if not url:
        return url
    prefix = "https://arxiv.org/abs/"
    if url.startswith(prefix):
        return url.replace(prefix, "https://alpharxiv.org/abs/")
    return url


def _emoji_for_primary(primary: str) -> str:
    return EMOJI_BY_PRIMARY.get(primary, "\U0001f4cc")


def _category_key(paper: Dict[str, Any]) -> ClusterKey:
    primary_category = _normalise(paper.get("primary_category"), "unknown_category")
    primary_area = _normalise(paper.get("primary_area"), "uncategorised")
    secondary = _normalise(paper.get("secondary_focus"), "general")
    application = _normalise(paper.get("application_domain"), "general")
    return primary_category, primary_area, secondary, application


def _format_label(key: ClusterKey) -> str:
    primary_category, primary_area, secondary, application = key
    emoji = _emoji_for_primary(primary_area)
    return f"\U0001f4c2 {primary_category} | {emoji} {primary_area} \u00b7 {secondary} \u00b7 {application}"


def _normalise_tag_value(value: Any) -> str | None:
    """Lowercase string representation for tag comparison."""

    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _paper_tags(paper: Dict[str, Any]) -> Set[str]:
    """Collect tag-like fields from a paper for exclusion matching."""

    fields = ("primary_category", "primary_area", "secondary_focus", "application_domain")
    tags: Set[str] = set()
    for field in fields:
        tag = _normalise_tag_value(paper.get(field))
        if tag:
            tags.add(tag)

    extra = paper.get("tags")
    if isinstance(extra, str):
        tag = _normalise_tag_value(extra)
        if tag:
            tags.add(tag)
    elif isinstance(extra, (list, tuple, set)):
        for raw in extra:
            tag = _normalise_tag_value(raw)
            if tag:
                tags.add(tag)

    return tags


def _filter_papers_by_tags(
    papers: Iterable[Dict[str, Any]], excluded_tags: Iterable[str] | None
) -> List[Dict[str, Any]]:
    if not excluded_tags:
        return list(papers)

    tag_set = {_normalise_tag_value(tag) for tag in excluded_tags}
    tag_set = {tag for tag in tag_set if tag}
    if not tag_set:
        return list(papers)

    filtered: List[Dict[str, Any]] = []
    for paper in papers:
        tags = _paper_tags(paper)
        if tags and tags.intersection(tag_set):
            continue
        filtered.append(paper)
    return filtered


def _has_interest_tags(paper: Dict[str, Any]) -> bool:
    raw = paper.get("interest_tags")
    if isinstance(raw, str):
        return bool(raw.strip())
    if isinstance(raw, (list, tuple, set)):
        return any(str(item or "").strip() for item in raw)
    return False


def _format_interest_tags(paper: Dict[str, Any]) -> str | None:
    raw = paper.get("interest_tags")
    tags: List[str] = []
    if isinstance(raw, str):
        token = raw.strip()
        if token:
            tags.append(token)
    elif isinstance(raw, (list, tuple, set)):
        for item in raw:
            token = str(item or "").strip()
            if token:
                tags.append(token)
    if not tags:
        return None
    unique_tags: List[str] = []
    seen: Set[str] = set()
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            unique_tags.append(tag)
    label = "\uff0c".join(unique_tags)
    return f"\u2b50 \u5174\u8da3\u6807\u7b7e: {label}"


# ---------------------------------------------------------------------------
# Post-message builders
# ---------------------------------------------------------------------------

def _build_summary_post(groups: Dict[ClusterKey, List[Dict[str, Any]]], total: int, interest_count: int) -> Dict[str, Any]:
    content: List[List[Dict[str, str]]] = [
        [{"tag": "text", "text": f"\U0001f4da \u603b\u8ba1 {total} \u7bc7 | \u5174\u8da3\u6807\u7b7e {interest_count} \u7bc7 | \u5e38\u89c4\u7c7b\u522b {len(groups)} \u7ec4"}]
    ]
    if interest_count:
        content.append([{"tag": "text", "text": f"\u2b50 \u5174\u8da3\u76f4\u8fbe: {interest_count} \u7bc7"}])

    for key in sorted(groups):
        label = _format_label(key)
        content.append([{ "tag": "text", "text": f"{label}: {len(groups[key])} \u7bc7" }])
    if not content:
        content = [[{"tag": "text", "text": "\U0001f4ed \u6682\u65e0\u8bba\u6587"}]]
    return {"title": "\U0001f4cc \u4eca\u65e5\u8bba\u6587\u6982\u89c8", "content": content, "label": "summary"}


def _build_category_post(key: ClusterKey, papers: List[Dict[str, Any]]) -> Dict[str, Any]:
    label = _format_label(key)
    header = f"{label}\uff08{len(papers)} \u7bc7\uff09"
    content: List[List[Dict[str, str]]] = [[{"tag": "text", "text": header}]]

    for idx, paper in enumerate(papers, start=1):
        title = _normalise(paper.get("title"), "(\u672a\u547d\u540d\u8bba\u6587)")
        link = paper.get("papers_cool_url") or _to_papers_cool(str(paper.get("arxiv_url", "")))
        display_title = f"{idx}. \u2728 {title}"
        if link:
            content.append([{ "tag": "a", "text": display_title, "href": link }])
        else:
            content.append([{ "tag": "text", "text": display_title }])

        authors = paper.get("authors") or []
        authors_text = "\uff0c".join(a for a in authors if a)
        if authors_text:
            content.append([{ "tag": "text", "text": f"\U0001f465 \u4f5c\u8005: {authors_text}" }])

        primary_category, _, secondary, application = key
        content.append([{ "tag": "text", "text": f"\U0001f3f7\ufe0f \u5206\u7c7b: {primary_category} | {secondary} | {application}" }])

        tldr = _normalise(paper.get("tldr_zh"), "\u6682\u65e0 TL;DR")
        content.append([{ "tag": "text", "text": f"\U0001f9e0 TL;DR: {tldr}" }])

        interest_text = _format_interest_tags(paper)
        if interest_text:
            content.append([{ "tag": "text", "text": interest_text }])

        arxiv_url = paper.get("arxiv_url")
        alpharxiv_url = _to_alpharxiv(str(arxiv_url or ""))
        papers_cool = link
        links_row: List[Dict[str, str]] = []
        if alpharxiv_url:
            links_row.append({"tag": "a", "text": "\U0001f517 alphArXiv", "href": alpharxiv_url})
        if papers_cool and papers_cool != alpharxiv_url:
            if links_row:
                links_row.append({"tag": "text", "text": " \uff5c "})
            links_row.append({"tag": "a", "text": "\U0001f4c4 Papers.Cool", "href": papers_cool})
        if links_row:
            content.append(links_row)

        content.append([{ "tag": "text", "text": " " }])

    return {"title": header, "content": content, "label": label}


def _build_interest_post(papers: List[Dict[str, Any]]) -> Dict[str, Any]:
    ordered = sorted(papers, key=lambda item: item.get("order", 0) or 0)
    header = f"\u2b50 \u5174\u8da3\u76f4\u8fbe\uff08{len(ordered)} \u7bc7\uff09"
    content: List[List[Dict[str, str]]] = [[{"tag": "text", "text": header}]]

    for idx, paper in enumerate(ordered, start=1):
        title = _normalise(paper.get("title"), "(\u672a\u547d\u540d\u8bba\u6587)")
        link = paper.get("papers_cool_url") or _to_papers_cool(str(paper.get("arxiv_url", "")))
        display_title = f"{idx}. \u2728 {title}"
        if link:
            content.append([{ "tag": "a", "text": display_title, "href": link }])
        else:
            content.append([{ "tag": "text", "text": display_title }])

        authors = paper.get("authors") or []
        authors_text = "\uff0c".join(a for a in authors if a)
        if authors_text:
            content.append([{ "tag": "text", "text": f"\U0001f465 \u4f5c\u8005: {authors_text}" }])

        primary_category = _normalise(paper.get("primary_category"), "unknown_category")
        primary_area = _normalise(paper.get("primary_area"), "uncategorised")
        secondary = _normalise(paper.get("secondary_focus"), "general")
        application = _normalise(paper.get("application_domain"), "general")
        content.append([{ "tag": "text", "text": f"\U0001f3f7\ufe0f \u5206\u7c7b: {primary_category} | {primary_area} | {secondary} | {application}" }])

        tldr = _normalise(paper.get("tldr_zh"), "\u6682\u65e0 TL;DR")
        content.append([{ "tag": "text", "text": f"\U0001f9e0 TL;DR: {tldr}" }])

        interest_text = _format_interest_tags(paper)
        if interest_text:
            content.append([{ "tag": "text", "text": interest_text }])

        arxiv_url = paper.get("arxiv_url")
        papers_cool = link
        links_row: List[Dict[str, str]] = []
        if arxiv_url:
            links_row.append({"tag": "a", "text": "\U0001f517 ArXiv", "href": arxiv_url})
        if papers_cool and papers_cool != arxiv_url:
            if links_row:
                links_row.append({"tag": "text", "text": " \uff5c "})
            links_row.append({"tag": "a", "text": "\U0001f4c4 Papers.Cool", "href": papers_cool})
        if links_row:
            content.append(links_row)

        content.append([{ "tag": "text", "text": " " }])

    return {"title": header, "content": content, "label": "interest_batch"}


def build_post_messages(papers: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    paper_list = list(papers)
    interest_papers = [paper for paper in paper_list if _has_interest_tags(paper)]
    regular_papers = [paper for paper in paper_list if not _has_interest_tags(paper)]

    grouped: Dict[ClusterKey, List[Dict[str, Any]]] = defaultdict(list)
    ordered_regular = sorted(
        regular_papers,
        key=lambda item: (
            _normalise(item.get("primary_category"), "unknown_category"),
            _normalise(item.get("primary_area"), "uncategorised"),
            _normalise(item.get("secondary_focus"), "general"),
            _normalise(item.get("application_domain"), "general"),
            item.get("order", 0),
        ),
    )
    for paper in ordered_regular:
        grouped[_category_key(paper)].append(paper)

    messages: List[Dict[str, Any]] = []
    messages.append(_build_summary_post(grouped, len(paper_list), len(interest_papers)))
    if interest_papers:
        messages.append(_build_interest_post(interest_papers))

    sorted_keys = sorted(grouped)
    for key in sorted_keys:
        messages.append(_build_category_post(key, grouped[key]))
    return messages


# ---------------------------------------------------------------------------
# Low-level posting helpers
# ---------------------------------------------------------------------------

def _post_json(webhook_url: str, payload: Dict[str, Any]) -> Dict[str, Any] | None:
    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
    except requests.RequestException as exc:
        raise FeishuSendError(f"Failed to call Feishu webhook: {exc}") from exc

    if response.status_code >= 300:
        raise FeishuSendError(f"Feishu webhook error: {response.status_code} {response.text}")

    try:
        data = response.json()
    except ValueError:
        data = None

    if isinstance(data, dict) and data.get("StatusCode", 0) != 0:
        raise FeishuSendError(f"Feishu webhook rejected message: {data}")
    return data if isinstance(data, dict) else None


def _post_post(webhook_url: str, *, title: str, content: List[List[Dict[str, str]]]) -> None:
    payload = {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": content,
                }
            }
        },
    }
    _post_json(webhook_url, payload)


def _post_separator(webhook_url: str, text: str) -> None:
    payload = {
        "msg_type": "text",
        "content": {"text": text},
    }
    _post_json(webhook_url, payload)


# ---------------------------------------------------------------------------
# FeishuNotifier class
# ---------------------------------------------------------------------------

class FeishuNotifier(BaseNotifier):
    """Feishu webhook notifier that sends rich card digests."""

    def __init__(
        self,
        webhook_url: str,
        *,
        delay_seconds: float = 2.0,
        separator_text: str = "",
        exclude_tags: list[str] | None = None,
    ) -> None:
        self._webhook_url = webhook_url
        self._delay_seconds = delay_seconds
        self._separator_text = separator_text
        self._exclude_tags: list[str] = list(exclude_tags) if exclude_tags else []

    # -- public API --------------------------------------------------------

    def send_digest(
        self,
        papers: List[Dict[str, Any]],
        *,
        exclude_tags: Iterable[str] | None = None,
    ) -> None:
        """Send a full paper digest via Feishu webhook.

        Combines the instance-level ``_exclude_tags`` with the per-call
        *exclude_tags* parameter before filtering.
        """
        combined_tags: list[str] = list(self._exclude_tags)
        if exclude_tags:
            combined_tags.extend(exclude_tags)

        filtered_papers = _filter_papers_by_tags(
            list(papers), combined_tags if combined_tags else None,
        )
        messages = build_post_messages(filtered_papers)
        total_messages = len(messages)

        for idx, message in enumerate(messages):
            _post_post(
                self._webhook_url,
                title=message["title"],
                content=message["content"],
            )
            is_last = idx == total_messages - 1
            if not is_last and self._separator_text:
                next_label = messages[idx + 1].get("label", "\u4e0b\u4e00\u7ec4")
                formatted = self._separator_text.format(
                    current=idx + 1,
                    total=total_messages,
                    label=next_label,
                )
                _post_separator(self._webhook_url, formatted)
            if not is_last and self._delay_seconds > 0:
                time.sleep(self._delay_seconds)

    def send_text(self, text: str) -> None:
        """Send a simple text message via Feishu webhook."""
        _post_separator(self._webhook_url, text)

    # -- factory -----------------------------------------------------------

    @classmethod
    def from_channel_config(cls, channel_config) -> "FeishuNotifier":
        """Construct a *FeishuNotifier* from a ``ChannelConfig`` dataclass.

        Expected fields on *channel_config*: ``type``, ``webhook_url``,
        ``delay_seconds``, ``separator_text``, ``exclude_tags``.
        """
        return cls(
            webhook_url=channel_config.webhook_url,
            delay_seconds=channel_config.delay_seconds,
            separator_text=channel_config.separator_text,
            exclude_tags=channel_config.exclude_tags,
        )
