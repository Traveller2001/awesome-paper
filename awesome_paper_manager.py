"""Command line entrypoint orchestrating the three stages."""
import os

import argparse
import json
import re
from datetime import datetime
import datetime as dt
from pathlib import Path
from typing import Any, Dict, Iterable, List

from llm_api import LLMClient, LLMClientError
from stage1_scraper import fetch_latest_papers, save_raw_papers, resolve_target_date
from stage2_classifier import ClassificationError, classify_with_llm
from stage3_sender import FeishuSendError, send_digest

DEFAULT_CONFIG_PATH = Path("config.json")


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise SystemExit(f"Config file not found: {path}") from exc


def ensure_directories(dirs: Iterable[str]) -> None:
    for directory in dirs:
        Path(directory).mkdir(parents=True, exist_ok=True)


def command_scrape(args: argparse.Namespace, config: Dict[str, Any]) -> List[Path]:
    categories = args.categories or config.get("categories", [])
    if not categories:
        raise SystemExit("No categories provided. Use --categories or set them in config.json")

    stage1_cfg = config.get("stage1", {})
    max_results = args.max_results if args.max_results is not None else stage1_cfg.get("max_results")
    target_date = args.target_date or stage1_cfg.get("target_date")

    user_explicit_date = bool(target_date)
    if not user_explicit_date:
        today = dt.datetime.now(dt.timezone.utc).date()
        if today.weekday() >= 5:
            print("Today is weekend (UTC). No new arXiv submissions to fetch.")
            return []

    resolved_target_date = resolve_target_date(target_date)
    print(f"Target date resolved to: {resolved_target_date.isoformat()}")

    paper_groups = fetch_latest_papers(categories, max_results=max_results, target_date=target_date)
    raw_dir = config.get("data_dirs", {}).get("raw", "./data/raw")
    raw_files = save_raw_papers(paper_groups, raw_dir=raw_dir)

    counts = {cat: len(items) for cat, items in paper_groups.items()}
    total = sum(counts.values())
    print(f"Scraped {total} papers across {len(counts)} categories.")
    for cat in sorted(counts):
        print(f"  - {cat}: {counts[cat]} papers")

    if raw_files:
        print("Raw data saved to:")
        for file_path in raw_files:
            print(f"  - {file_path}")
    else:
        print("No raw files were created (no papers found).")

    return raw_files


def _safe_segment(value: str | None, fallback: str) -> str:
    token = (value or '').strip().lower()
    if not token:
        return fallback
    token = re.sub(r'[^a-z0-9]+', '-', token)
    token = token.strip('-')
    return token or fallback


def _paper_filename(paper: Dict[str, Any], index: int) -> str:
    arxiv_id = str(paper.get('arxiv_id', '')).strip()
    if arxiv_id:
        slug = re.sub(r'[^a-z0-9]+', '-', arxiv_id.lower())
    else:
        slug = _safe_segment(paper.get('title', ''), f'paper-{index}')
    return f"{slug or f'paper-{index}'}.json"


def _store_archive_files(papers: List[Dict[str, Any]], archive_root: Path) -> List[Path]:
    stored_paths: List[Path] = []
    for idx, paper in enumerate(papers, start=1):
        primary = _safe_segment(paper.get('primary_area'), 'uncategorised')
        secondary = _safe_segment(paper.get('secondary_focus'), 'general')
        application = _safe_segment(paper.get('application_domain'), 'general')

        dest_dir = archive_root / primary / secondary / application
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / _paper_filename(paper, idx)
        dest_path.write_text(json.dumps(paper, ensure_ascii=False, indent=2), encoding='utf-8')
        stored_paths.append(dest_path)

    return stored_paths




def _classify_and_store(papers: List[Dict[str, Any]], raw_sources: List[str], config: Dict[str, Any]) -> Path:
    if not papers:
        raise SystemExit("No papers to classify.")

    stage2_cfg = config.get("stage2", {})
    raw_interest = stage2_cfg.get("interest_tags", [])
    interest_tags: List[Dict[str, Any]] = []
    if isinstance(raw_interest, dict):
        interest_tags = [raw_interest]
    elif isinstance(raw_interest, list):
        for item in raw_interest:
            if isinstance(item, str):
                interest_tags.append({"label": item})
            elif isinstance(item, dict):
                interest_tags.append(item)
    elif isinstance(raw_interest, str):
        interest_tags = [{"label": raw_interest}]

    try:
        llm_client = LLMClient()
        classified = classify_with_llm(papers, llm_client, interest_tags=interest_tags)
    except (LLMClientError, ClassificationError) as exc:
        raise SystemExit(f"Classification failed: {exc}") from exc

    # Archive storage: taxonomy-based hierarchy with one JSON per paper
    data_dirs = config.get("data_dirs", {})
    archive_dir = Path(data_dirs.get("archive", "./data/paper_database"))
    daily_dir = Path(data_dirs.get("daily", "./data/daily"))
    ensure_directories([archive_dir, daily_dir])

    stored_archive_files = _store_archive_files(classified, archive_dir)

    # Daily storage: date-based folder with full run payload
    run_timestamp = datetime.utcnow()
    date_tag = run_timestamp.strftime("%Y%m%d")
    daily_subdir = daily_dir / date_tag
    daily_subdir.mkdir(parents=True, exist_ok=True)
    daily_filename = f"daily_{date_tag}_{run_timestamp.strftime('%H%M%S')}.json"
    daily_path = daily_subdir / daily_filename

    output_payload = {
        "generated_at": run_timestamp.isoformat() + "Z",
        "source_raw_files": raw_sources,
        "paper_count": len(classified),
        "papers": classified,
        "archive_files": [str(path) for path in stored_archive_files],
    }
    if len(raw_sources) == 1:
        output_payload["source_raw_file"] = raw_sources[0]

    daily_path.write_text(json.dumps(output_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Classified {len(classified)} papers.")
    print(f"Daily summary -> {daily_path}")
    if stored_archive_files:
        print("Archive files created:")
        for archive_path in stored_archive_files:
            print(f"  - {archive_path}")

    return daily_path



def command_classify(args: argparse.Namespace, config: Dict[str, Any]) -> Path:
    raw_file = Path(args.raw_file)
    if not raw_file.exists():
        raise SystemExit(f"Raw data file does not exist: {raw_file}")

    payload = json.loads(raw_file.read_text(encoding="utf-8"))
    papers = payload.get("papers", [])
    if not papers:
        raise SystemExit("Raw file contains no papers to classify.")

    return _classify_and_store(papers, [str(raw_file)], config)


def command_send(args: argparse.Namespace, config: Dict[str, Any]) -> None:
    classified_file = Path(args.classified_file)
    if not classified_file.exists():
        raise SystemExit(f"Classified data file does not exist: {classified_file}")

    webhook_url = args.webhook or config.get("webhook_url") or os.getenv("FEISHU_WEBHOOK_URL", "")
    if not webhook_url:
        raise SystemExit("Feishu webhook URL is missing. Provide --webhook, set it in config.json, or use FEISHU_WEBHOOK_URL environment variable")

    payload = json.loads(classified_file.read_text(encoding="utf-8"))
    papers = payload.get("papers", [])
    if not papers:
        raise SystemExit("Classified file contains no papers to send.")

    try:
        stage3_cfg = config.get("stage3", {})
        delay = stage3_cfg.get("delay_seconds", 0)
        separator_text = stage3_cfg.get("separator_text", "🚧 下一类别：{label} （进度 {current}/{total}）🚧")
        exclude_tags_cfg = stage3_cfg.get("exclude_tags")
        if isinstance(exclude_tags_cfg, str):
            exclude_tags = [exclude_tags_cfg]
        elif isinstance(exclude_tags_cfg, list):
            exclude_tags = [str(tag) for tag in exclude_tags_cfg]
        else:
            exclude_tags = []
        send_digest(
            webhook_url,
            papers,
            delay_seconds=delay,
            separator_text=separator_text,
            exclude_tags=exclude_tags,
        )
    except FeishuSendError as exc:
        raise SystemExit(f"Failed to send Feishu digest: {exc}") from exc

    print("Feishu digest sent successfully.")


def command_full(args: argparse.Namespace, config: Dict[str, Any]) -> None:
    raw_files = command_scrape(args, config)
    if not raw_files:
        print("No papers scraped; classification and sending skipped.")
        return

    combined_papers = []
    for raw_path in raw_files:
        payload = json.loads(Path(raw_path).read_text(encoding="utf-8"))
        combined_papers.extend(payload.get("papers", []))

    if not combined_papers:
        print("No papers available after scraping; classification and sending skipped.")
        return

    daily_file = _classify_and_store(combined_papers, [str(path) for path in raw_files], config)
    send_args = argparse.Namespace(classified_file=str(daily_file), webhook=args.webhook)
    command_send(send_args, config)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Awesome Paper manager")
    sub = parser.add_subparsers(dest="command", required=True)

    scrape = sub.add_parser("scrape", help="Stage 1: fetch latest papers from arXiv")
    scrape.add_argument("--categories", nargs="*", help="Override categories from config")
    scrape.add_argument("--max-results", type=int, dest="max_results", help="Limit results per request")
    scrape.add_argument("--target-date", dest="target_date", help="Fetch papers published on the given YYYY-MM-DD date (defaults to today)")

    classify = sub.add_parser("classify", help="Stage 2: delegate semantic classification to the LLM")
    classify.add_argument("--raw-file", required=True, dest="raw_file", help="Path to the raw data JSON file")

    send = sub.add_parser("send", help="Stage 3: send Feishu digest")
    send.add_argument("--classified-file", required=True, dest="classified_file", help="Path to the classified JSON file")
    send.add_argument("--webhook", help="Override the Feishu webhook URL")

    full = sub.add_parser("full", help="Run scrape -> classify -> send in order")
    full.add_argument("--categories", nargs="*", help="Override categories from config")
    full.add_argument("--max-results", type=int, dest="max_results", help="Limit results per request")
    full.add_argument("--target-date", dest="target_date", help="Fetch papers published on the given YYYY-MM-DD date (defaults to today)")
    full.add_argument("--webhook", help="Override the Feishu webhook URL")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config()

    data_dirs = config.get("data_dirs", {})
    ensure_directories(data_dirs.values())

    if args.command == "scrape":
        command_scrape(args, config)
    elif args.command == "classify":
        command_classify(args, config)
    elif args.command == "send":
        command_send(args, config)
    elif args.command == "full":
        command_full(args, config)
    else:  # pragma: no cover - argparse already validates
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
