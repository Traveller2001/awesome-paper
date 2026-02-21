"""Profile-based configuration system with backward-compatible migration."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG_PATH = Path("config.json")
PROFILES_DIR = Path("profiles")
DEFAULT_PROFILE_NAME = "default"


# ---------------------------------------------------------------------------
# .env loader (no extra dependency)
# ---------------------------------------------------------------------------

def _load_dotenv(path: Path | str = ".env") -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (skip if missing)."""
    env_path = Path(path)
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


@dataclass
class InterestTag:
    label: str
    description: str = ""
    keywords: list[str] = field(default_factory=list)


@dataclass
class LLMRoleConfig:
    api_base: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    api_key_env: str = "LLM_API_KEY"
    api_key: str = ""
    temperature: float = 0.2
    max_concurrency: int = 10

    def resolve_api_key(self) -> str:
        key = os.getenv(self.api_key_env, "").strip()
        if key:
            return key
        if self.api_key:
            return self.api_key
        raise ValueError(f"Environment variable {self.api_key_env} is not set and no api_key configured")


@dataclass
class ChannelConfig:
    type: str = "feishu"
    webhook_url: str = ""
    delay_seconds: float = 2.0
    separator_text: str = "\U0001f6a7 \u4e0b\u4e00\u7c7b\u522b\uff1a{label} \uff08\u8fdb\u5ea6 {current}/{total}\uff09\U0001f6a7"
    exclude_tags: list[str] = field(default_factory=list)


@dataclass
class ScheduleConfig:
    mode: str = "workday"
    max_attempts: int = 6
    interval_seconds: int = 3600


@dataclass
class DataDirsConfig:
    raw: str = "./data/raw"
    archive: str = "./data/paper_database"
    daily: str = "./data/daily"


@dataclass
class SubscriptionsConfig:
    categories: list[str] = field(default_factory=lambda: ["cs.CL", "cs.AI", "cs.LG", "cs.CV"])
    interest_tags: list[InterestTag] = field(default_factory=list)


@dataclass
class Profile:
    subscriptions: SubscriptionsConfig = field(default_factory=SubscriptionsConfig)
    channels: list[ChannelConfig] = field(default_factory=list)
    llm: dict[str, LLMRoleConfig] = field(default_factory=lambda: {
        "analyzer": LLMRoleConfig(),
        "agent": LLMRoleConfig(),
    })
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    data_dirs: DataDirsConfig = field(default_factory=DataDirsConfig)
    language: str = "en"


def _profile_path(profile_name: str) -> Path:
    return PROFILES_DIR / f"{profile_name}.json"


def _profile_to_dict(profile: Profile) -> Dict[str, Any]:
    data = asdict(profile)
    # Never persist api_key to disk — use .env / env vars instead
    for role_cfg in data.get("llm", {}).values():
        if isinstance(role_cfg, dict):
            role_cfg.pop("api_key", None)
    return data


def _dict_to_profile(data: Dict[str, Any]) -> Profile:
    subs_raw = data.get("subscriptions", {})
    interest_raw = subs_raw.get("interest_tags", [])
    interest_tags = [
        InterestTag(**tag) if isinstance(tag, dict) else InterestTag(label=str(tag))
        for tag in interest_raw
    ]
    subscriptions = SubscriptionsConfig(
        categories=subs_raw.get("categories", ["cs.CL", "cs.AI", "cs.LG", "cs.CV"]),
        interest_tags=interest_tags,
    )

    channels_raw = data.get("channels", [])
    channels = [ChannelConfig(**ch) for ch in channels_raw]

    llm_raw = data.get("llm", {})
    llm: dict[str, LLMRoleConfig] = {}
    for role, cfg in llm_raw.items():
        if isinstance(cfg, dict):
            llm[role] = LLMRoleConfig(**{k: v for k, v in cfg.items() if k in LLMRoleConfig.__dataclass_fields__})
        else:
            llm[role] = LLMRoleConfig()
    if not llm:
        llm = {"analyzer": LLMRoleConfig(), "agent": LLMRoleConfig()}

    schedule_raw = data.get("schedule", {})
    schedule = ScheduleConfig(**{k: v for k, v in schedule_raw.items() if k in ScheduleConfig.__dataclass_fields__})

    dirs_raw = data.get("data_dirs", {})
    data_dirs = DataDirsConfig(**{k: v for k, v in dirs_raw.items() if k in DataDirsConfig.__dataclass_fields__})

    language = data.get("language", "en")

    return Profile(
        subscriptions=subscriptions,
        channels=channels,
        llm=llm,
        schedule=schedule,
        data_dirs=data_dirs,
        language=language,
    )


def load_profile(profile_name: str = DEFAULT_PROFILE_NAME) -> Profile:
    path = _profile_path(profile_name)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return _dict_to_profile(data)

    if DEFAULT_CONFIG_PATH.exists():
        profile = migrate_from_legacy_config(DEFAULT_CONFIG_PATH)
        save_profile(profile, profile_name)
        return profile

    profile = Profile()
    save_profile(profile, profile_name)
    return profile


def save_profile(profile: Profile, profile_name: str = DEFAULT_PROFILE_NAME) -> Path:
    path = _profile_path(profile_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _profile_to_dict(profile)
    content = json.dumps(data, ensure_ascii=False, indent=2)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        Path(tmp_path).replace(path)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise
    return path


def migrate_from_legacy_config(config_path: Path = DEFAULT_CONFIG_PATH) -> Profile:
    raw = json.loads(config_path.read_text(encoding="utf-8-sig"))

    categories = raw.get("categories", ["cs.CL", "cs.AI", "cs.LG", "cs.CV"])

    raw_interest = raw.get("stage2", {}).get("interest_tags", [])
    interest_tags: list[InterestTag] = []
    if isinstance(raw_interest, dict):
        raw_interest = [raw_interest]
    if isinstance(raw_interest, list):
        for item in raw_interest:
            if isinstance(item, str):
                interest_tags.append(InterestTag(label=item))
            elif isinstance(item, dict):
                label = str(item.get("label") or item.get("name") or "").strip()
                if label:
                    interest_tags.append(InterestTag(
                        label=label,
                        description=str(item.get("description", "")),
                        keywords=item.get("keywords", []),
                    ))

    webhook_url = raw.get("webhook_url", "") or os.getenv("FEISHU_WEBHOOK_URL", "")
    stage3 = raw.get("stage3", {})
    channels = []
    if webhook_url:
        channels.append(ChannelConfig(
            type="feishu",
            webhook_url=webhook_url,
            delay_seconds=stage3.get("delay_seconds", 2.0),
            separator_text=stage3.get("separator_text", ChannelConfig.separator_text),
            exclude_tags=stage3.get("exclude_tags", []),
        ))

    api_base = os.getenv("LLM_API_BASE", "https://api.deepseek.com").strip()
    model = os.getenv("LLM_MODEL", "deepseek-chat").strip()
    llm_cfg = LLMRoleConfig(api_base=api_base, model=model, api_key_env="LLM_API_KEY")
    llm = {"analyzer": llm_cfg, "agent": LLMRoleConfig(api_base=api_base, model=model, api_key_env="LLM_API_KEY")}

    automation = raw.get("automation", {})
    schedule = ScheduleConfig(
        max_attempts=automation.get("max_attempts", 6),
        interval_seconds=automation.get("interval_seconds", 3600),
    )

    data_dirs_raw = raw.get("data_dirs", {})
    data_dirs = DataDirsConfig(
        raw=data_dirs_raw.get("raw", "./data/raw"),
        archive=data_dirs_raw.get("archive", "./data/paper_database"),
        daily=data_dirs_raw.get("daily", "./data/daily"),
    )

    return Profile(
        subscriptions=SubscriptionsConfig(categories=categories, interest_tags=interest_tags),
        channels=channels,
        llm=llm,
        schedule=schedule,
        data_dirs=data_dirs,
    )


def ensure_data_directories(profile: Profile) -> None:
    for d in (profile.data_dirs.raw, profile.data_dirs.archive, profile.data_dirs.daily):
        Path(d).mkdir(parents=True, exist_ok=True)
