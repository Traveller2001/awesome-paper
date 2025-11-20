
"""Stage 3 sender that posts rich Feishu cards."""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Set, Tuple

import requests


class FeishuSendError(RuntimeError):
    """Raised when Feishu webhook rejects the payload."""


EMOJI_BY_PRIMARY = {
    "text_models": "📝",
    "multimodal_models": "🖼️",
    "audio_models": "🎧",
    "video_models": "🎬",
    "vla_models": "🤖",
    "diffusion_models": "🌫️",
    "uncategorised": "📌",
}


ClusterKey = Tuple[str, str, str, str]


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
    return EMOJI_BY_PRIMARY.get(primary, "📌")


def _category_key(paper: Dict[str, Any]) -> ClusterKey:
    primary_category = _normalise(paper.get("primary_category"), "unknown_category")
    primary_area = _normalise(paper.get("primary_area"), "uncategorised")
    secondary = _normalise(paper.get("secondary_focus"), "general")
    application = _normalise(paper.get("application_domain"), "general")
    return primary_category, primary_area, secondary, application


def _format_label(key: ClusterKey) -> str:
    primary_category, primary_area, secondary, application = key
    emoji = _emoji_for_primary(primary_area)
    return f"📂 {primary_category} | {emoji} {primary_area} · {secondary} · {application}"


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
    label = "，".join(unique_tags)
    return f"⭐ 兴趣标签: {label}"


def _build_summary_post(groups: Dict[ClusterKey, List[Dict[str, Any]]], total: int, interest_count: int) -> Dict[str, Any]:
    content: List[List[Dict[str, str]]] = [
        [{"tag": "text", "text": f"📚 总计 {total} 篇 | 兴趣标签 {interest_count} 篇 | 常规类别 {len(groups)} 组"}]
    ]
    if interest_count:
        content.append([{"tag": "text", "text": f"⭐ 兴趣直达: {interest_count} 篇"}])

    for key in sorted(groups):
        label = _format_label(key)
        content.append([{ "tag": "text", "text": f"{label}: {len(groups[key])} 篇" }])
    if not content:
        content = [[{"tag": "text", "text": "📭 暂无论文"}]]
    return {"title": "📌 今日论文概览", "content": content, "label": "summary"}


def _build_category_post(key: ClusterKey, papers: List[Dict[str, Any]]) -> Dict[str, Any]:
    label = _format_label(key)
    header = f"{label}（{len(papers)} 篇）"
    content: List[List[Dict[str, str]]] = [[{"tag": "text", "text": header}]]

    for idx, paper in enumerate(papers, start=1):
        title = _normalise(paper.get("title"), "(未命名论文)")
        link = paper.get("papers_cool_url") or _to_papers_cool(str(paper.get("arxiv_url", "")))
        display_title = f"{idx}. ✨ {title}"
        if link:
            content.append([{ "tag": "a", "text": display_title, "href": link }])
        else:
            content.append([{ "tag": "text", "text": display_title }])

        authors = paper.get("authors") or []
        authors_text = "，".join(a for a in authors if a)
        if authors_text:
            content.append([{ "tag": "text", "text": f"👥 作者: {authors_text}" }])

        primary_category, _, secondary, application = key
        content.append([{ "tag": "text", "text": f"🏷️ 分类: {primary_category} | {secondary} | {application}" }])

        tldr = _normalise(paper.get("tldr_zh"), "暂无 TL;DR")
        content.append([{ "tag": "text", "text": f"🧠 TL;DR: {tldr}" }])

        interest_text = _format_interest_tags(paper)
        if interest_text:
            content.append([{ "tag": "text", "text": interest_text }])

        arxiv_url = paper.get("arxiv_url")
        alpharxiv_url = _to_alpharxiv(str(arxiv_url or ""))
        papers_cool = link
        links_row: List[Dict[str, str]] = []
        if alpharxiv_url:
            links_row.append({"tag": "a", "text": "🔗 alphArXiv", "href": alpharxiv_url})
        if papers_cool and papers_cool != alpharxiv_url:
            if links_row:
                links_row.append({"tag": "text", "text": " ｜ "})
            links_row.append({"tag": "a", "text": "📄 Papers.Cool", "href": papers_cool})
        if links_row:
            content.append(links_row)

        content.append([{ "tag": "text", "text": " " }])

    return {"title": header, "content": content, "label": label}


def _build_interest_post(papers: List[Dict[str, Any]]) -> Dict[str, Any]:
    ordered = sorted(papers, key=lambda item: item.get("order", 0) or 0)
    header = f"⭐ 兴趣直达（{len(ordered)} 篇）"
    content: List[List[Dict[str, str]]] = [[{"tag": "text", "text": header}]]

    for idx, paper in enumerate(ordered, start=1):
        title = _normalise(paper.get("title"), "(未命名论文)")
        link = paper.get("papers_cool_url") or _to_papers_cool(str(paper.get("arxiv_url", "")))
        display_title = f"{idx}. ✨ {title}"
        if link:
            content.append([{ "tag": "a", "text": display_title, "href": link }])
        else:
            content.append([{ "tag": "text", "text": display_title }])

        authors = paper.get("authors") or []
        authors_text = "，".join(a for a in authors if a)
        if authors_text:
            content.append([{ "tag": "text", "text": f"👥 作者: {authors_text}" }])

        primary_category = _normalise(paper.get("primary_category"), "unknown_category")
        primary_area = _normalise(paper.get("primary_area"), "uncategorised")
        secondary = _normalise(paper.get("secondary_focus"), "general")
        application = _normalise(paper.get("application_domain"), "general")
        content.append([{ "tag": "text", "text": f"🏷️ 分类: {primary_category} | {primary_area} | {secondary} | {application}" }])

        tldr = _normalise(paper.get("tldr_zh"), "暂无 TL;DR")
        content.append([{ "tag": "text", "text": f"🧠 TL;DR: {tldr}" }])

        interest_text = _format_interest_tags(paper)
        if interest_text:
            content.append([{ "tag": "text", "text": interest_text }])

        arxiv_url = paper.get("arxiv_url")
        papers_cool = link
        links_row: List[Dict[str, str]] = []
        if arxiv_url:
            links_row.append({"tag": "a", "text": "🔗 ArXiv", "href": arxiv_url})
        if papers_cool and papers_cool != arxiv_url:
            if links_row:
                links_row.append({"tag": "text", "text": " ｜ "})
            links_row.append({"tag": "a", "text": "📄 Papers.Cool", "href": papers_cool})
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




def send_plain_text(webhook_url: str, text: str) -> None:
    """Send a simple text message via Feishu webhook."""

    _post_separator(webhook_url, text)


def send_digest(
    webhook_url: str,
    papers: Iterable[Dict[str, Any]],
    *,
    delay_seconds: float = 0.0,
    separator_text: str | None = None,
    exclude_tags: Iterable[str] | None = None,
) -> None:
    filtered_papers = _filter_papers_by_tags(list(papers), exclude_tags)
    messages = build_post_messages(filtered_papers)
    total_messages = len(messages)

    for idx, message in enumerate(messages):
        _post_post(webhook_url, title=message["title"], content=message["content"])
        is_last = idx == total_messages - 1
        if not is_last and separator_text:
            next_label = messages[idx + 1].get("label", "下一组")
            formatted = separator_text.format(
                current=idx + 1,
                total=total_messages,
                label=next_label,
            )
            _post_separator(webhook_url, formatted)
        if not is_last and delay_seconds > 0:
            time.sleep(delay_seconds)
