"""Lightweight i18n support for Awesome Paper CLI.

Usage::

    from cli.i18n import set_language, t

    set_language("zh")          # switch to Chinese
    print(t("goodbye"))         # "再见！"
    print(t("pipeline_completed", date="2025-01-15"))
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_current_lang: str = "en"

# ---------------------------------------------------------------------------
# String registry
# ---------------------------------------------------------------------------

_STRINGS: dict[str, dict[str, str]] = {
    # ---- English (default) ------------------------------------------------
    "en": {
        # Banner
        "banner_subtitle": "arXiv Paper Tracker \u2014 type a command or chat to begin",
        "banner_shortcuts": "Shortcuts:",
        # Help panel
        "help_title": "Help",
        "help_table": (
            "| Command | Description |\n"
            "|---------|-------------|\n"
            "| `/help` | Show this help panel |\n"
            "| `/status` | View pipeline status (last 7 days) |\n"
            "| `/config` | Show current configuration |\n"
            "| `/run [date]` | Run pipeline (optionally specify date) |\n"
            "| `/search <keyword>` | Search papers |\n"
            "| `/quit` | Quit |"
        ),
        "help_footer": "You can also chat with the assistant in natural language.",
        # Config tree
        "not_configured": "NOT configured",
        "none_label": "None",
        "no_channels": "No channels (optional)",
        # Pipeline status table
        "pipeline_title": "Pipeline Status (Recent)",
        "col_date": "Date",
        "no_records": "No records yet",
        # Paper table
        "paper_table_title": "Paper Search Results",
        "col_tldr": "TL;DR",
        # Spinner / progress
        "thinking": "Thinking...",
        "initialising": "Initialising...",
        # Pipeline stages
        "stage_scrape": "Scraping arXiv papers",
        "stage_classify": "Classifying papers",
        "stage_send": "Sending notifications",
        # Pipeline results
        "pipeline_completed": "Pipeline completed for {date}",
        "pipeline_already": "Pipeline already completed for {date}",
        "pipeline_no_papers": "No papers found for {date}",
        "pipeline_failed": "Pipeline failed: {error}",
        # Commands
        "search_usage": "Usage: /search <keyword>",
        "search_empty": "No papers found for '{keyword}'",
        "unknown_cmd": "Unknown command: {cmd}. Type /help for available commands.",
        "goodbye": "Goodbye!",
        # Config status (for agent context)
        "cfg_header": "Current configuration status:",
        "cfg_categories": "arXiv categories: {cats}",
        "cfg_categories_missing": "arXiv categories: NOT configured",
        "cfg_tags": "Interest tags: {count} configured",
        "cfg_tags_missing": "Interest tags: none (optional, recommended)",
        "cfg_channel": "Notification channel: {type}",
        "cfg_channel_missing": "Notification channel: NOT configured (optional, results saved locally)",
        "cfg_llm": "Analyzer LLM: {model} @ {api_base}",
        # Agent prompt instructions
        "respond_lang_instruction": "Always respond in English.",
        "greeting_lang_instruction": (
            "Based on the status above, greet the user and briefly tell them what is ready "
            "and what needs to be configured. If everything is ready, ask what they'd like to do. "
            "Keep it concise (3\u20135 sentences). Respond in English."
        ),
    },
    # ---- Chinese ----------------------------------------------------------
    "zh": {
        # Banner
        "banner_subtitle": "arXiv \u8bba\u6587\u8ffd\u8e2a\u52a9\u624b \u2014 \u8f93\u5165\u547d\u4ee4\u6216\u81ea\u7136\u8bed\u8a00\u5f00\u59cb",
        "banner_shortcuts": "\u5feb\u6377\u547d\u4ee4:",
        # Help panel
        "help_title": "\u5e2e\u52a9",
        "help_table": (
            "| \u547d\u4ee4 | \u529f\u80fd |\n"
            "|------|------|\n"
            "| `/help` | \u663e\u793a\u5e2e\u52a9\u9762\u677f |\n"
            "| `/status` | \u67e5\u770b\u8fd1 7 \u5929 pipeline \u72b6\u6001 |\n"
            "| `/config` | \u663e\u793a\u5f53\u524d\u914d\u7f6e |\n"
            "| `/run [date]` | \u6267\u884c pipeline\uff08\u53ef\u9009\u6307\u5b9a\u65e5\u671f\uff09 |\n"
            "| `/search <keyword>` | \u641c\u7d22\u8bba\u6587 |\n"
            "| `/quit` | \u9000\u51fa |"
        ),
        "help_footer": "\u4e5f\u53ef\u4ee5\u76f4\u63a5\u7528\u81ea\u7136\u8bed\u8a00\u4e0e\u52a9\u624b\u5bf9\u8bdd\u3002",
        # Config tree
        "not_configured": "\u672a\u914d\u7f6e",
        "none_label": "\u65e0",
        "no_channels": "\u65e0\u901a\u77e5\u6e20\u9053\uff08\u53ef\u9009\uff09",
        # Pipeline status table
        "pipeline_title": "Pipeline \u72b6\u6001\uff08\u8fd1\u671f\uff09",
        "col_date": "\u65e5\u671f",
        "no_records": "\u6682\u65e0\u8bb0\u5f55",
        # Paper table
        "paper_table_title": "\u8bba\u6587\u641c\u7d22\u7ed3\u679c",
        "col_tldr": "TL;DR\uff08\u4e2d\u6587\uff09",
        # Spinner / progress
        "thinking": "\u6b63\u5728\u601d\u8003...",
        "initialising": "\u521d\u59cb\u5316\u4e2d...",
        # Pipeline stages
        "stage_scrape": "\u6b63\u5728\u6293\u53d6 arXiv \u8bba\u6587",
        "stage_classify": "\u6b63\u5728\u5206\u7c7b\u8bba\u6587",
        "stage_send": "\u6b63\u5728\u53d1\u9001\u901a\u77e5",
        # Pipeline results
        "pipeline_completed": "Pipeline \u5df2\u5b8c\u6210\uff0c\u65e5\u671f {date}",
        "pipeline_already": "Pipeline \u5df2\u5b8c\u6210\u8fc7\uff0c\u65e5\u671f {date}",
        "pipeline_no_papers": "\u672a\u627e\u5230\u8bba\u6587\uff0c\u65e5\u671f {date}",
        "pipeline_failed": "Pipeline \u5931\u8d25\uff1a{error}",
        # Commands
        "search_usage": "\u7528\u6cd5\uff1a/search <\u5173\u952e\u8bcd>",
        "search_empty": "\u672a\u627e\u5230\u5339\u914d '{keyword}' \u7684\u8bba\u6587",
        "unknown_cmd": "\u672a\u77e5\u547d\u4ee4\uff1a{cmd}\u3002\u8f93\u5165 /help \u67e5\u770b\u53ef\u7528\u547d\u4ee4\u3002",
        "goodbye": "\u518d\u89c1\uff01",
        # Config status (for agent context)
        "cfg_header": "\u5f53\u524d\u914d\u7f6e\u72b6\u6001\uff1a",
        "cfg_categories": "arXiv \u5206\u7c7b\uff1a{cats}",
        "cfg_categories_missing": "arXiv \u5206\u7c7b\uff1a\u672a\u914d\u7f6e",
        "cfg_tags": "\u5174\u8da3\u6807\u7b7e\uff1a\u5df2\u914d\u7f6e {count} \u4e2a",
        "cfg_tags_missing": "\u5174\u8da3\u6807\u7b7e\uff1a\u65e0\uff08\u53ef\u9009\uff0c\u5efa\u8bae\u914d\u7f6e\uff09",
        "cfg_channel": "\u901a\u77e5\u6e20\u9053\uff1a{type}",
        "cfg_channel_missing": "\u901a\u77e5\u6e20\u9053\uff1a\u672a\u914d\u7f6e\uff08\u53ef\u9009\uff0c\u7ed3\u679c\u4fdd\u5b58\u5728\u672c\u5730\uff09",
        "cfg_llm": "\u5206\u7c7b LLM\uff1a{model} @ {api_base}",
        # Agent prompt instructions
        "respond_lang_instruction": "\u59cb\u7ec8\u7528\u4e2d\u6587\u56de\u590d\u3002",
        "greeting_lang_instruction": (
            "\u6839\u636e\u4ee5\u4e0a\u72b6\u6001\uff0c\u5411\u7528\u6237\u95ee\u597d\uff0c\u7b80\u8981\u544a\u8bc9\u4ed6\u4eec\u54ea\u4e9b\u5df2\u5c31\u7eea\u3001\u54ea\u4e9b\u8fd8\u9700\u8981\u914d\u7f6e\u3002"
            "\u5982\u679c\u4e00\u5207\u5c31\u7eea\uff0c\u8be2\u95ee\u4ed6\u4eec\u60f3\u505a\u4ec0\u4e48\u3002\u7b80\u6d01\u56de\u590d\uff083\u20135 \u53e5\uff09\u3002\u7528\u4e2d\u6587\u56de\u590d\u3002"
        ),
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def set_language(lang: str) -> None:
    """Set the active language (``'en'`` or ``'zh'``)."""
    global _current_lang
    _current_lang = lang if lang in _STRINGS else "en"


def get_language() -> str:
    """Return the current language code."""
    return _current_lang


def t(key: str, **kwargs: object) -> str:
    """Look up a translated string by *key*, with optional ``str.format`` interpolation."""
    table = _STRINGS.get(_current_lang, _STRINGS["en"])
    text = table.get(key) or _STRINGS["en"].get(key, key)
    if kwargs:
        text = text.format(**kwargs)
    return text
