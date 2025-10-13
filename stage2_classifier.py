"""Stage 2 classifier that delegates semantic understanding to the LLM API."""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List

from llm_api import LLMClient, LLMClientError

TAXONOMY_REFERENCE = {
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
        ("general_purpose", "通用用途或暂未细分")
    ],
}

SYSTEM_PROMPT = (
    "You are an expert research analyst. "
    "Classify each arXiv paper using the reference taxonomy (you may also suggest new labels when needed) and summarise it in Chinese."
)

RESPONSE_INSTRUCTIONS = (
    "Return a compact JSON object with keys: primary_area, secondary_focus, "
    "application_domain, and tldr_zh. Prefer labels from the reference list, "
    "but you may propose new labels if they better describe the paper. Always provide Chinese TL;DR."
)




def _format_taxonomy_reference() -> str:
    lines: List[str] = []
    for dimension, options in TAXONOMY_REFERENCE.items():
        lines.append(f"{dimension}:")
        for value, desc in options:
            lines.append(f"  - {value}: {desc}")
    return "\n".join(lines)


def _reference_ids() -> Dict[str, List[str]]:
    return {key: [value for value, _ in options] for key, options in TAXONOMY_REFERENCE.items()}

REFERENCE_IDS = _reference_ids()

MAX_CLASSIFY_RETRIES = 3

class ClassificationError(RuntimeError):
    """Raised when the response from the LLM cannot be parsed."""


def _build_user_prompt(paper: Dict[str, Any]) -> str:
    title = paper.get("title", "").strip()
    summary = paper.get("summary", "").strip()
    published = paper.get("published", "")
    primary = paper.get("primary_category", "")

    taxonomy_block = _format_taxonomy_reference()
    return (
        f"Paper metadata:\n"
        f"- Title: {title}\n"
        f"- arXiv category: {primary}\n"
        f"- Published at: {published}\n\n"
        f"Abstract:\n{summary}\n\n"
        f"Reference taxonomy (IDs with brief descriptions):\n{taxonomy_block}\n\n"
        f"{RESPONSE_INSTRUCTIONS}"
    )


def _to_papers_cool(url: str) -> str:
    prefix = "https://arxiv.org/abs/"
    if not url:
        return url
    if url.startswith(prefix):
        return url.replace(prefix, "https://papers.cool/arxiv/")
    return url



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

    return {
        "primary_area": str(data["primary_area"]).strip(),
        "secondary_focus": str(data["secondary_focus"]).strip(),
        "application_domain": str(data["application_domain"]).strip(),
        "tldr_zh": str(data["tldr_zh"]).strip(),
    }


def classify_with_llm(papers: Iterable[Dict[str, Any]], llm_client: LLMClient) -> List[Dict[str, Any]]:
    """Classify papers using the provided LLM client."""

    paper_list = list(papers)
    total = len(paper_list)

    results: List[Dict[str, Any]] = []
    for idx, paper in enumerate(paper_list, start=1):
        print(f"[Stage2] Classifying paper {idx}/{total}", flush=True)
        base_prompt = _build_user_prompt(paper)
        last_error: Exception | None = None
        structured: Dict[str, Any] | None = None

        for attempt in range(1, MAX_CLASSIFY_RETRIES + 1):
            if attempt == 1:
                user_prompt = base_prompt
            else:
                hint = "\n\nWARNING: 上一次响应解析失败，请仅返回严格的 JSON 对象，不要包含 Markdown 代码块或额外说明。"
                user_prompt = base_prompt + hint

            try:
                raw_response = llm_client.complete(system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt)
                structured = _extract_structured_response(raw_response)
                break
            except (LLMClientError, ClassificationError) as exc:
                print(f"[Stage2] Retry {attempt}/{MAX_CLASSIFY_RETRIES} failed: {exc}", flush=True)
                last_error = exc
        else:
            raise ClassificationError(
                f"Failed to classify paper after {MAX_CLASSIFY_RETRIES} attempts: {last_error}"
            ) from last_error

        papers_cool_url = _to_papers_cool(str(paper.get("arxiv_url", "")))
        enriched = {**paper, **structured, "order": idx, "papers_cool_url": papers_cool_url}
        results.append(enriched)

    return results
