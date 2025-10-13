"""Automation runner for Awesome Paper pipeline."""
from __future__ import annotations

import os

import argparse
import datetime as dt
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Iterable, List

from awesome_paper_manager import (
    _classify_and_store,
    command_scrape,
    command_send,
    load_config,
)
from stage1_scraper import resolve_target_date
from stage3_sender import send_plain_text

CONFIG_MAX_ATTEMPTS_KEY = "max_attempts"
CONFIG_INTERVAL_KEY = "interval_seconds"
DEFAULT_MAX_ATTEMPTS = 6
DEFAULT_INTERVAL_SECONDS = 3600
MIN_INTERVAL_SECONDS = 30
STATUS_PATH = Path("data/automation_status.json")


def _read_paper_count(raw_path: Path) -> int:
    try:
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return 0
    return int(payload.get("paper_count") or 0)


def _combine_papers(raw_files: Iterable[Path]) -> List[Dict[str, object]]:
    combined: List[Dict[str, object]] = []
    for raw_path in raw_files:
        try:
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        combined.extend(payload.get("papers", []))
    return combined


def _load_status_store() -> Dict[str, Dict[str, Dict[str, object]]]:
    if STATUS_PATH.exists():
        try:
            return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_status_store(store: Dict[str, Dict[str, Dict[str, object]]]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def _mark_stage(store: Dict[str, Dict[str, Dict[str, object]]], day: str, stage: str, **info: object) -> None:
    day_status = store.setdefault(day, {})
    stage_info = {
        "completed": True,
        "timestamp": dt.datetime.utcnow().isoformat() + "Z",
    }
    stage_info.update(info)
    day_status[stage] = stage_info
    _save_status_store(store)


def _clear_stage(store: Dict[str, Dict[str, Dict[str, object]]], day: str, stages: Iterable[str]) -> None:
    day_status = store.setdefault(day, {})
    for stage in stages:
        day_status.pop(stage, None)
    _save_status_store(store)


def _send_weekend_message(config: Dict[str, object]) -> None:
    webhook = config.get("webhook_url") or os.getenv("FEISHU_WEBHOOK_URL", "")
    if not webhook:
        print("Weekend message skipped: webhook_url missing in config.json or FEISHU_WEBHOOK_URL environment variable")
        return
    today_str = dt.datetime.now().strftime("%Y-%m-%d")
    text = f"📅 {today_str} 周末无新论文更新，请好好休息！"
    try:
        send_plain_text(str(webhook), text)
        print("Weekend reminder sent.")
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to send weekend reminder: {exc}")


def _prepare_raw_files(store: Dict[str, Dict[str, Dict[str, object]]], day_key: str) -> List[Path]:
    day_status = store.get(day_key, {})
    scrape_info = day_status.get("scrape", {})
    if not scrape_info.get("completed"):
        return []
    raw_files = [Path(p) for p in scrape_info.get("raw_files", []) if Path(p).exists()]
    if raw_files:
        print("Scrape stage already completed; reusing stored raw files.")
        return raw_files
    print("Stored raw files missing; will rescrape.")
    _clear_stage(store, day_key, ["scrape", "classify", "send"])
    return []


def _prepare_daily_file(store: Dict[str, Dict[str, Dict[str, object]]], day_key: str) -> Path | None:
    day_status = store.get(day_key, {})
    classify_info = day_status.get("classify", {})
    if not classify_info.get("completed"):
        return None
    daily_file = Path(classify_info.get("daily_file", ""))
    if daily_file.exists():
        print("Classification stage already completed; reusing daily file.")
        return daily_file
    print("Stored daily file missing; will reclassify.")
    _clear_stage(store, day_key, ["classify", "send"])
    return None


def _run_once(config: Dict[str, object], *, target_date: str | None = None) -> bool:
    resolved_date = resolve_target_date(target_date)
    day_key = resolved_date.isoformat()
    print(f"Running pipeline for target date: {day_key}")

    status_store = _load_status_store()
    day_status = status_store.get(day_key, {})

    if day_status.get("send", {}).get("completed"):
        print("Send stage already completed; skipping entire pipeline.")
        return True

    raw_files = _prepare_raw_files(status_store, day_key)

    if not raw_files:
        args = SimpleNamespace(categories=None, max_results=None, target_date=target_date, webhook=None)
        try:
            raw_files_str = command_scrape(args, config)
        except SystemExit as exc:
            print(f"command_scrape exited early: {exc}")
            return False
        raw_files = [Path(p) for p in raw_files_str]
        if not raw_files:
            return False
        total_papers = sum(_read_paper_count(path) for path in raw_files)
        if total_papers == 0:
            print("Scrape returned zero papers; will retry if attempts remain.")
            return False
        _mark_stage(status_store, day_key, "scrape", raw_files=[str(path) for path in raw_files])
        _clear_stage(status_store, day_key, ["classify", "send"])

    daily_file = _prepare_daily_file(status_store, day_key)
    if daily_file is None:
        combined = _combine_papers(raw_files)
        daily_file = _classify_and_store(combined, [str(path) for path in raw_files], config)
        _mark_stage(status_store, day_key, "classify", daily_file=str(daily_file))
        _clear_stage(status_store, day_key, ["send"])

    day_status = status_store.get(day_key, {})
    if day_status.get("send", {}).get("completed"):
        print("Send stage already completed; skipping.")
        return True

    send_args = SimpleNamespace(classified_file=str(daily_file), webhook=None)
    command_send(send_args, config)
    _mark_stage(status_store, day_key, "send")
    return True


def main(argv: List[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Automate Awesome Paper pipeline")
    parser.add_argument("--target-date", help="Override target date (YYYY-MM-DD)")
    parser.add_argument("--max-attempts", type=int, default=None, help="Max retry attempts per day")
    parser.add_argument("--interval", type=int, default=None, help="Seconds between retries")
    args = parser.parse_args(argv)

    config = load_config()
    automation_cfg = config.get("automation", {})

    max_attempts_cfg = int(automation_cfg.get(CONFIG_MAX_ATTEMPTS_KEY, DEFAULT_MAX_ATTEMPTS) or DEFAULT_MAX_ATTEMPTS)
    interval_cfg = int(automation_cfg.get(CONFIG_INTERVAL_KEY, DEFAULT_INTERVAL_SECONDS) or DEFAULT_INTERVAL_SECONDS)

    max_attempts = max(1, args.max_attempts if args.max_attempts is not None else max_attempts_cfg)
    interval = max(MIN_INTERVAL_SECONDS, args.interval if args.interval is not None else interval_cfg)

    today = dt.datetime.now(dt.timezone.utc).date()
    is_weekend = today.weekday() >= 5

    if is_weekend and not args.target_date:
        print("Weekend detected (UTC). Skipping scrape and sending reminder.")
        _send_weekend_message(config)
        return

    for attempt in range(1, max_attempts + 1):
        print(f"Attempt {attempt}/{max_attempts} started at {dt.datetime.now():%Y-%m-%d %H:%M:%S}.")
        success = _run_once(config, target_date=args.target_date)
        if success:
            print("Pipeline completed successfully.")
            return
        if attempt < max_attempts:
            print(f"Sleeping {interval} seconds before next attempt...")
            time.sleep(interval)

    print("All attempts exhausted without successful run.")


if __name__ == "__main__":
    main()
