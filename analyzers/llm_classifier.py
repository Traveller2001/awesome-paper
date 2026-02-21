"""LLM-based paper classifier (async).

Extracts and refactors the classification logic originally in
``stage2_classifier.py`` into an async :class:`BaseAnalyzer` subclass so that
papers can be classified concurrently.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict, Iterable, List, Sequence

from analyzers.base import BaseAnalyzer
from llm.client import AsyncLLMClient, LLMClientError

# ---------------------------------------------------------------------------
# Taxonomy & prompt constants — bilingual (en / zh)
# ---------------------------------------------------------------------------

_TAXONOMY_ZH = {
    "primary_area": [
        ("text_models", "纯文本生成/理解类模型，例如语言模型、翻译模型"),
        ("multimodal_models", "处理文本+多模态输入输出的模型"),
        ("audio_models", "语音、音频理解或生成模型"),
        ("video_models", "视频理解、生成或编辑模型"),
        ("vla_models", "视觉-语言-动作等多模态智能体/机器人模型"),
        ("diffusion_models", "扩散、流匹配等图像生成模型"),
    ],
    "secondary_focus": [
        ("dialogue_systems", "对话、客服、助手类场景"),
        ("long_context", "长文本/长上下文处理能力"),
        ("reasoning", "推理、逻辑链、数学等能力"),
        ("model_compression", "蒸馏、量化、剪枝等压缩技术"),
        ("model_architecture", "模型结构设计或新框架"),
        ("alignment", "价值观对齐、安全、偏置治理"),
        ("training_optimization", "训练策略、效率、数据配方"),
        ("tech_reports", "官方技术报告或路线图"),
    ],
    "application_domain": [
        ("medical_ai", "医疗、药物、生命科学应用"),
        ("education_ai", "教育、教学、考试场景"),
        ("code_generation", "编程、软件工程相关"),
        ("legal_ai", "法律、合规、司法场景"),
        ("financial_ai", "金融、商业分析场景"),
        ("general_purpose", "通用用途或暂未细分"),
    ],
}

_TAXONOMY_EN = {
    "primary_area": [
        ("text_models", "Text generation/understanding models, e.g. language models, translation"),
        ("multimodal_models", "Models handling text + multimodal input/output"),
        ("audio_models", "Speech and audio understanding or generation models"),
        ("video_models", "Video understanding, generation or editing models"),
        ("vla_models", "Vision-language-action multimodal agent/robot models"),
        ("diffusion_models", "Diffusion, flow-matching and other image generation models"),
    ],
    "secondary_focus": [
        ("dialogue_systems", "Dialogue, customer service, assistant scenarios"),
        ("long_context", "Long text / long context processing"),
        ("reasoning", "Reasoning, chain-of-thought, mathematical abilities"),
        ("model_compression", "Distillation, quantization, pruning techniques"),
        ("model_architecture", "Novel model architecture design or frameworks"),
        ("alignment", "Value alignment, safety, bias governance"),
        ("training_optimization", "Training strategies, efficiency, data recipes"),
        ("tech_reports", "Official technical reports or roadmaps"),
    ],
    "application_domain": [
        ("medical_ai", "Medical, pharmaceutical, life science applications"),
        ("education_ai", "Education, teaching, examination scenarios"),
        ("code_generation", "Programming and software engineering"),
        ("legal_ai", "Legal, compliance, judicial scenarios"),
        ("financial_ai", "Finance, business analytics"),
        ("general_purpose", "General purpose or not yet categorised"),
    ],
}

_SYSTEM_PROMPT = {
    "zh": (
        "You are an expert research analyst. "
        "Classify each arXiv paper using the reference taxonomy "
        "(you may also suggest new labels when needed) and summarise it in Chinese."
    ),
    "en": (
        "You are an expert research analyst. "
        "Classify each arXiv paper using the reference taxonomy "
        "(you may also suggest new labels when needed) and summarise it in English."
    ),
}

_BASE_RESPONSE_INSTRUCTIONS = {
    "zh": (
        "Return a compact JSON object with keys: primary_area, secondary_focus, "
        "application_domain, and tldr_zh. Prefer labels from the reference list, "
        "but you may propose new labels if they better describe the paper. Always provide Chinese TL;DR."
    ),
    "en": (
        "Return a compact JSON object with keys: primary_area, secondary_focus, "
        "application_domain, and tldr_zh. Prefer labels from the reference list, "
        "but you may propose new labels if they better describe the paper. Always provide English TL;DR."
    ),
}

_INTEREST_TAGS_HEADER = {
    "zh": "兴趣标签（仅在论文与描述/关键词高度匹配时，才在 JSON 的 `interest_tags` 中返回对应标签 ID；否则请留空）：",
    "en": (
        "Interest tags (only include a tag ID in the `interest_tags` JSON array "
        "when the paper strongly matches its description/keywords; otherwise leave empty):"
    ),
}

_KEYWORDS_LABEL = {"zh": "关键词: ", "en": "Keywords: "}

_RETRY_HINT = {
    "zh": "\n\nWARNING: 上一次响应解析失败，请仅返回严格的 JSON 对象，不要包含 Markdown 代码块或额外说明。",
    "en": "\n\nWARNING: The previous response failed to parse. Please return ONLY a strict JSON object without Markdown code blocks or extra text.",
}

MAX_CLASSIFY_RETRIES = 3

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class ClassificationError(RuntimeError):
    """Raised when the response from the LLM cannot be parsed."""


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _get_taxonomy(language: str) -> dict:
    return _TAXONOMY_EN if language == "en" else _TAXONOMY_ZH


def _format_taxonomy_reference(language: str = "zh") -> str:
    taxonomy = _get_taxonomy(language)
    lines: List[str] = []
    for dimension, options in taxonomy.items():
        lines.append(f"{dimension}:")
        for value, desc in options:
            lines.append(f"  - {value}: {desc}")
    return "\n".join(lines)


def _format_interest_tags_reference(
    interest_tags: Sequence[Dict[str, Any]],
    language: str = "zh",
) -> str:
    if not interest_tags:
        return ""

    lines: List[str] = [_INTEREST_TAGS_HEADER.get(language, _INTEREST_TAGS_HEADER["en"])]
    kw_label = _KEYWORDS_LABEL.get(language, _KEYWORDS_LABEL["en"])
    for tag in interest_tags:
        label = tag.get("label", "")
        if not label:
            continue
        description = tag.get("description", "")
        keywords = tag.get("keywords") or []
        description_part = f" — {description}" if description else ""
        if keywords:
            keywords_part = f" | {kw_label} " + ", ".join(keywords)
        else:
            keywords_part = ""
        lines.append(f"  - {label}{description_part}{keywords_part}")
    return "\n".join(lines)


def _response_instructions(include_interest_tags: bool, language: str = "zh") -> str:
    instructions = _BASE_RESPONSE_INSTRUCTIONS.get(language, _BASE_RESPONSE_INSTRUCTIONS["en"])
    if include_interest_tags:
        instructions += (
            " Interest tags are optional hints for downstream delivery. Only include a label ID in the "
            "`interest_tags` array when the paper strongly matches its description or keywords; otherwise "
            "return an empty array and rely on your own judgement."
        )
    return instructions


def _build_user_prompt(
    paper: Dict[str, Any],
    interest_tags: Sequence[Dict[str, Any]] | None,
    language: str = "zh",
) -> str:
    title = paper.get("title", "").strip()
    summary = paper.get("summary", "").strip()
    published = paper.get("published", "")
    primary = paper.get("primary_category", "")

    taxonomy_block = _format_taxonomy_reference(language)
    interest_block = _format_interest_tags_reference(interest_tags or [], language)
    instructions = _response_instructions(bool(interest_block), language)
    extra_reference = f"\n\n{interest_block}" if interest_block else ""
    return (
        f"Paper metadata:\n"
        f"- Title: {title}\n"
        f"- arXiv category: {primary}\n"
        f"- Published at: {published}\n\n"
        f"Abstract:\n{summary}\n\n"
        f"Reference taxonomy (IDs with brief descriptions):\n{taxonomy_block}"
        f"{extra_reference}\n\n"
        f"{instructions}"
    )


def _extract_structured_response(raw_text: str) -> Dict[str, Any]:
    cleaned = _strip_code_fences(raw_text)
    candidate = cleaned
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start != -1 and end != -1 and start < end:
        candidate = cleaned[start:end + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ClassificationError(f"LLM response is not valid JSON: {raw_text}") from exc

    missing = [key for key in ("primary_area", "secondary_focus", "application_domain", "tldr_zh") if key not in data]
    if missing:
        raise ClassificationError(f"Missing keys in LLM response: {missing}")

    interest_raw = data.get("interest_tags", [])
    interest_tags: List[str] = []
    if isinstance(interest_raw, str):
        token = interest_raw.strip()
        if token:
            interest_tags.append(token)
    elif isinstance(interest_raw, (list, tuple, set)):
        for raw in interest_raw:
            token = str(raw or "").strip()
            if token:
                interest_tags.append(token)

    return {
        "primary_area": str(data["primary_area"]).strip(),
        "secondary_focus": str(data["secondary_focus"]).strip(),
        "application_domain": str(data["application_domain"]).strip(),
        "tldr_zh": str(data["tldr_zh"]).strip(),
        "interest_tags": interest_tags,
    }


def _strip_code_fences(raw_text: str) -> str:
    text = raw_text.strip()
    lower = text.lower()
    if lower.startswith('```json') or lower.startswith('```javascript') or text.startswith('```'):
        lines = text.splitlines()
        text = '\n'.join(lines[1:]) if len(lines) > 1 else ''
        text = text.strip()
        if text.endswith('```'):
            text = text[:-3].rstrip()
    return text


def _normalise_interest_tags(tags: Iterable[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
    normalised: List[Dict[str, Any]] = []
    if not tags:
        return normalised

    for tag in tags:
        if not isinstance(tag, dict):
            continue
        label = str(tag.get("label") or tag.get("name") or "").strip()
        if not label:
            continue
        description = str(tag.get("description") or "").strip()
        raw_keywords = tag.get("keywords") or []
        keywords: List[str] = []
        if isinstance(raw_keywords, str):
            candidate = raw_keywords.strip()
            if candidate:
                keywords.append(candidate)
        elif isinstance(raw_keywords, (list, tuple, set)):
            for raw_kw in raw_keywords:
                candidate = str(raw_kw or "").strip()
                if candidate:
                    keywords.append(candidate)

        normalised.append(
            {
                "label": label,
                "description": description,
                "keywords": keywords,
            }
        )
    return normalised


def _to_papers_cool(url: str) -> str:
    prefix = "https://arxiv.org/abs/"
    if not url:
        return url
    if url.startswith(prefix):
        return url.replace(prefix, "https://papers.cool/arxiv/")
    return url


# ---------------------------------------------------------------------------
# Async classifier
# ---------------------------------------------------------------------------


class LLMClassifier(BaseAnalyzer):
    """Classifies papers via an async LLM client with bounded concurrency."""

    def __init__(
        self,
        llm_client: AsyncLLMClient,
        *,
        interest_tags: list[dict] | None = None,
        max_concurrency: int = 10,
        progress_callback: Callable[[int, int], None] | None = None,
        language: str = "zh",
    ) -> None:
        self._llm_client = llm_client
        self._interest_tags = _normalise_interest_tags(interest_tags)
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._progress_callback = progress_callback
        self._language = language

    # -- public interface ----------------------------------------------------

    async def classify(self, papers: list[dict]) -> list[dict]:
        """Classify all *papers* concurrently and return enriched dicts.

        Tasks are created for every paper up-front and executed in parallel,
        bounded by the semaphore configured at construction time.  The ``order``
        field is assigned based on the index at task-creation time so that the
        original ordering is preserved regardless of completion order.
        """
        total = len(papers)
        tasks = [
            asyncio.ensure_future(self._classify_one(paper, idx, total))
            for idx, paper in enumerate(papers, start=1)
        ]
        return list(await asyncio.gather(*tasks))

    # -- internals -----------------------------------------------------------

    async def _classify_one(self, paper: dict, idx: int, total: int) -> dict:
        """Classify a single paper with retry logic (mirrors the original)."""
        async with self._semaphore:
            base_prompt = _build_user_prompt(paper, self._interest_tags, self._language)
            system_prompt = _SYSTEM_PROMPT.get(self._language, _SYSTEM_PROMPT["en"])
            retry_hint = _RETRY_HINT.get(self._language, _RETRY_HINT["en"])
            last_error: Exception | None = None
            structured: Dict[str, Any] | None = None

            for attempt in range(1, MAX_CLASSIFY_RETRIES + 1):
                if attempt == 1:
                    user_prompt = base_prompt
                else:
                    user_prompt = base_prompt + retry_hint

                try:
                    raw_response = await self._llm_client.complete(
                        system_prompt=system_prompt, user_prompt=user_prompt,
                    )
                    structured = _extract_structured_response(raw_response)
                    break
                except (LLMClientError, ClassificationError) as exc:
                    print(
                        f"[Stage2] Retry {attempt}/{MAX_CLASSIFY_RETRIES} failed: {exc}",
                        flush=True,
                    )
                    last_error = exc
            else:
                raise ClassificationError(
                    f"Failed to classify paper after {MAX_CLASSIFY_RETRIES} attempts: {last_error}"
                ) from last_error

            papers_cool_url = _to_papers_cool(str(paper.get("arxiv_url", "")))
            enriched = {**paper, **structured, "order": idx, "papers_cool_url": papers_cool_url}
            if self._progress_callback:
                self._progress_callback(idx, total)
            return enriched
