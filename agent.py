"""Conversational CLI Agent for Awesome Paper configuration and control."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from typing import Any, Dict, List

from core.config import (
    ChannelConfig,
    InterestTag,
    LLMRoleConfig,
    Profile,
    load_profile,
    save_profile,
)
from core.orchestrator import PipelineOrchestrator
from core.supervisor import PipelineSupervisor
from llm.client import LLMClient, LLMClientError, build_llm_settings
from cli.i18n import set_language, t
from cli.ui import (
    PipelineProgressUI,
    console,
    print_assistant,
    print_banner,
    print_config_status,
    print_help,
    print_paper_table,
    print_pipeline_status,
    select_option,
    tool_call_status,
)


# ---------------------------------------------------------------------------
# Tool definitions for LLM function-calling
# ---------------------------------------------------------------------------

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "configure_subscription",
            "description": "Set arXiv categories and interest tags for paper subscription",
            "parameters": {
                "type": "object",
                "properties": {
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "arXiv category IDs, e.g. ['cs.CL', 'cs.AI']",
                    },
                    "interest_tags": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "description": {"type": "string"},
                                "keywords": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["label"],
                        },
                        "description": "Interest tags for paper filtering",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_channel",
            "description": "Set up a notification channel (e.g. Feishu webhook)",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["feishu"],
                        "description": "Channel type",
                    },
                    "webhook_url": {"type": "string", "description": "Webhook URL"},
                    "delay_seconds": {"type": "number"},
                    "exclude_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["type", "webhook_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_llm",
            "description": "Configure LLM backend for a specific role (analyzer or agent)",
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": ["analyzer", "agent"],
                    },
                    "api_base": {"type": "string"},
                    "model": {"type": "string"},
                    "api_key_env": {"type": "string"},
                    "temperature": {"type": "number"},
                    "max_concurrency": {"type": "integer"},
                },
                "required": ["role"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_pipeline",
            "description": "Execute the paper pipeline (scrape -> classify -> send) for a given date",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_date": {
                        "type": "string",
                        "description": "YYYY-MM-DD format, optional",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_status",
            "description": "Check pipeline run history and status for recent days",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of recent days to check (default 7)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_papers",
            "description": "Search paper database by keyword or date",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "date": {
                        "type": "string",
                        "description": "YYYY-MM-DD",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_config",
            "description": "Display the current profile configuration",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


AGENT_SYSTEM_PROMPT = """\
You are the Awesome Paper assistant — an agentic arXiv paper tracker.
Your job is to help users configure and run the paper pipeline through natural conversation.

## Capabilities
- Configure paper subscriptions (arXiv categories, interest tags)
- Set up notification channels (Feishu webhooks)
- Run the paper pipeline (scrape \u2192 classify \u2192 send)
- Query pipeline status and search classified papers
- Configure LLM backends for analyzer and agent roles

## Available arXiv categories
cs.CL, cs.CV, cs.AI, cs.LG, cs.IR, cs.RO, cs.MA, cs.SE, cs.CR, cs.DC, \
stat.ML, eess.AS, eess.IV, math.OC, quant-ph, etc.

## Behaviour rules
1. On startup you will receive the current config status. Proactively tell the user what is ready \
and what is missing. Guide them to fill in missing pieces before running the pipeline.
2. If categories are not configured, ask the user which arXiv categories they want to track.
3. If no notification channel is configured, remind the user — but it is optional (pipeline can \
still run, results are saved locally).
4. Interest tags are optional but recommended — ask the user about their research interests and \
create tags automatically based on their description.
5. When the user asks to run the pipeline, execute it directly. If it's a weekend and no target \
date is specified, automatically pick the most recent weekday.
6. Use the provided tools to execute operations. Never fabricate results.
7. {respond_lang_instruction}
8. Be concise — no long paragraphs unless the user asks for details.\
"""


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------


class Agent:
    def __init__(self, profile_name: str = "default") -> None:
        self.profile_name = profile_name
        self.profile = load_profile(profile_name)
        set_language(self.profile.language)
        self._llm = self._build_agent_llm()
        self._orchestrator = PipelineOrchestrator(self.profile)
        system_prompt = AGENT_SYSTEM_PROMPT.format(
            respond_lang_instruction=t("respond_lang_instruction"),
        )
        self._messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]

    def _build_agent_llm(self) -> LLMClient:
        agent_cfg = self.profile.llm.get("agent") or next(iter(self.profile.llm.values()))
        settings = build_llm_settings(agent_cfg)
        return LLMClient(settings)

    def _build_config_status(self) -> str:
        """Generate a status summary of the current profile for the agent."""
        p = self.profile
        lines: List[str] = [t("cfg_header")]

        # Categories
        cats = p.subscriptions.categories
        if cats:
            lines.append(f"- {t('cfg_categories', cats=', '.join(cats))} \u2713")
        else:
            lines.append(f"- {t('cfg_categories_missing')} \u2717")

        # Interest tags — show count only to avoid language mismatch
        tags = p.subscriptions.interest_tags
        if tags:
            lines.append(f"- {t('cfg_tags', count=len(tags))} \u2713")
        else:
            lines.append(f"- {t('cfg_tags_missing')}")

        # Channels
        if p.channels:
            for ch in p.channels:
                lines.append(f"- {t('cfg_channel', type=ch.type)} \u2713")
        else:
            lines.append(f"- {t('cfg_channel_missing')}")

        # LLM
        analyzer = p.llm.get("analyzer")
        if analyzer:
            lines.append(f"- {t('cfg_llm', model=analyzer.model, api_base=analyzer.api_base)} \u2713")

        return "\n".join(lines)

    # -- slash command handling ----------------------------------------------

    def handle_command(self, raw_input: str) -> bool:
        """Handle ``/`` commands. Returns True if a command was handled."""
        parts = raw_input.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/help":
            print_help()
            return True

        if cmd == "/config":
            print_config_status(asdict(self.profile))
            return True

        if cmd == "/status":
            status = self._orchestrator.query_status(days=7)
            print_pipeline_status(status)
            return True

        if cmd == "/run":
            target_date = arg or None
            self._run_pipeline_with_progress(target_date)
            return True

        if cmd == "/search":
            if not arg:
                console.print(f"[warning]{t('search_usage')}[/warning]")
                return True
            papers = self._orchestrator.query_papers(keyword=arg)
            if papers:
                print_paper_table(papers[:20], title=f"Search: {arg}")
            else:
                console.print(f"[dim]{t('search_empty', keyword=arg)}[/dim]")
            return True

        if cmd == "/quit":
            console.print(f"[info]{t('goodbye')}[/info]")
            raise SystemExit(0)

        console.print(f"[warning]{t('unknown_cmd', cmd=cmd)}[/warning]")
        return True

    # -- pipeline with progress UI -------------------------------------------

    def _run_pipeline_with_progress(self, target_date: str | None = None) -> Dict[str, Any]:
        """Execute the pipeline with a Rich progress display. Returns a concise summary dict."""
        self._orchestrator = PipelineOrchestrator(self.profile)
        supervisor = PipelineSupervisor(self._orchestrator, self.profile)
        progress_ui = PipelineProgressUI()
        progress_ui.add_stage("scrape", t("stage_scrape"))
        progress_ui.add_stage("classify", t("stage_classify"))
        progress_ui.add_stage("send", t("stage_send"))

        def on_stage(stage_name: str, event: str) -> None:
            if event == "done":
                progress_ui.complete_stage(stage_name)

        classify_cb = progress_ui.make_classify_callback()

        progress_ui.start()
        result = supervisor.run(
            target_date=target_date,
            on_stage=on_stage,
            on_classify_progress=classify_cb,
        )
        progress_ui.stop()

        # Display a short status line in the terminal
        status = result.get("status", "unknown")
        date = result.get("date", "")
        if status == "completed":
            console.print(f"[success]\u2713 {t('pipeline_completed', date=date)}[/success]")
        elif status == "already_completed":
            console.print(f"[info]{t('pipeline_already', date=date)}[/info]")
        elif status == "no_papers":
            console.print(f"[warning]{t('pipeline_no_papers', date=date)}[/warning]")
        elif status == "error":
            err = result.get("errors", [""])[0] if result.get("errors") else ""
            console.print(f"[error]{t('pipeline_failed', error=err)}[/error]")
        else:
            console.print(f"[info]Pipeline result: {status} ({date})[/info]")

        return result

    # -- tool dispatch -------------------------------------------------------

    def _execute_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        handlers = {
            "configure_subscription": self._tool_configure_subscription,
            "configure_channel": self._tool_configure_channel,
            "configure_llm": self._tool_configure_llm,
            "run_pipeline": self._tool_run_pipeline,
            "query_status": self._tool_query_status,
            "query_papers": self._tool_query_papers,
            "show_config": self._tool_show_config,
        }
        handler = handlers.get(name)
        if not handler:
            return json.dumps({"error": f"Unknown tool: {name}"})
        try:
            result = handler(arguments)
            summarized = self._summarize_tool_result(name, result)
            return json.dumps(summarized, ensure_ascii=False, default=str)
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    def _summarize_tool_result(self, fn_name: str, result: Any) -> Any:
        """Compress tool output to keep the agent's message history lean."""
        if not isinstance(result, dict):
            return result

        if fn_name == "query_papers":
            papers = result.get("papers", [])
            slim_papers = [
                {"title": p.get("title", ""), "primary_area": p.get("primary_area", "")}
                for p in papers[:10]
            ]
            return {
                "count": result.get("count", 0),
                "showing": len(slim_papers),
                "papers": slim_papers,
            }

        if fn_name == "show_config":
            subs = result.get("subscriptions", {})
            tags = subs.get("interest_tags", [])
            slim_tags = [{"label": t.get("label", "")} for t in tags]
            channels = result.get("channels", [])
            slim_channels = [
                {"type": ch.get("type", ""), "configured": bool(ch.get("webhook_url"))}
                for ch in channels
            ]
            llm = result.get("llm", {})
            slim_llm = {
                role: {"model": cfg.get("model", ""), "api_base": cfg.get("api_base", "")}
                for role, cfg in llm.items()
            }
            return {
                "categories": subs.get("categories", []),
                "interest_tags": slim_tags,
                "channels": slim_channels,
                "llm": slim_llm,
                "language": result.get("language", "en"),
            }

        # run_pipeline, query_status, configure_* — already concise
        return result

    # -- tool implementations ------------------------------------------------

    def _tool_configure_subscription(self, args: Dict) -> Dict:
        if "categories" in args:
            self.profile.subscriptions.categories = args["categories"]
        if "interest_tags" in args:
            self.profile.subscriptions.interest_tags = [
                InterestTag(
                    label=tag.get("label", ""),
                    description=tag.get("description", ""),
                    keywords=tag.get("keywords", []),
                )
                for tag in args["interest_tags"]
            ]
        save_profile(self.profile, self.profile_name)
        return {
            "status": "ok",
            "categories": self.profile.subscriptions.categories,
            "interest_tags_count": len(self.profile.subscriptions.interest_tags),
        }

    def _tool_configure_channel(self, args: Dict) -> Dict:
        new_channel = ChannelConfig(
            type=args.get("type", "feishu"),
            webhook_url=args.get("webhook_url", ""),
            delay_seconds=args.get("delay_seconds", 2.0),
            separator_text=args.get("separator_text", ChannelConfig.separator_text),
            exclude_tags=args.get("exclude_tags", []),
        )
        self.profile.channels = [
            ch for ch in self.profile.channels if ch.type != new_channel.type
        ]
        self.profile.channels.append(new_channel)
        save_profile(self.profile, self.profile_name)
        return {"status": "ok", "channel_type": new_channel.type, "total_channels": len(self.profile.channels)}

    def _tool_configure_llm(self, args: Dict) -> Dict:
        role = args.pop("role")
        existing = self.profile.llm.get(role, LLMRoleConfig())
        for key, val in args.items():
            if hasattr(existing, key):
                setattr(existing, key, val)
        self.profile.llm[role] = existing
        save_profile(self.profile, self.profile_name)
        return {"status": "ok", "role": role, "model": existing.model, "api_base": existing.api_base}

    def _tool_run_pipeline(self, args: Dict) -> Dict:
        target_date = args.get("target_date")
        return self._run_pipeline_with_progress(target_date)

    def _tool_query_status(self, args: Dict) -> Dict:
        days = args.get("days", 7)
        status = self._orchestrator.query_status(days=days)
        print_pipeline_status(status)
        return status

    def _tool_query_papers(self, args: Dict) -> Dict:
        keyword = args.get("keyword")
        date = args.get("date")
        papers = self._orchestrator.query_papers(keyword=keyword, date=date)
        summary = [
            {
                "title": p.get("title", ""),
                "primary_area": p.get("primary_area", ""),
                "tldr_zh": p.get("tldr_zh", ""),
                "arxiv_url": p.get("arxiv_url", ""),
            }
            for p in papers[:20]
        ]
        if summary:
            print_paper_table(summary, title=f"Papers{f' ({keyword})' if keyword else ''}")
        return {"count": len(papers), "showing": len(summary), "papers": summary}

    def _tool_show_config(self, _args: Dict) -> Dict:
        config = asdict(self.profile)
        print_config_status(config)
        return config

    # -- conversation loop ---------------------------------------------------

    def chat_turn(self, user_input: str) -> str:
        self._messages.append({"role": "user", "content": user_input})

        with console.status(f"[info]{t('thinking')}[/info]", spinner="dots"):
            try:
                response = self._llm.chat(messages=self._messages, tools=AGENT_TOOLS)
            except LLMClientError as exc:
                error_msg = f"LLM call failed: {exc}"
                self._messages.append({"role": "assistant", "content": error_msg})
                return error_msg

        message = response.choices[0].message

        while message.tool_calls:
            msg_dict = message.model_dump()
            if msg_dict.get("content") is None:
                msg_dict["content"] = ""
            self._messages.append(msg_dict)
            for tool_call in message.tool_calls:
                fn_name = tool_call.function.name
                try:
                    fn_args = json.loads(tool_call.function.arguments or "{}")
                except (json.JSONDecodeError, TypeError):
                    fn_args = {}
                args_str = json.dumps(fn_args, ensure_ascii=False)
                with tool_call_status(fn_name, args_str):
                    result = self._execute_tool(fn_name, fn_args)
                console.print(f"  [success]\u2713 {fn_name} completed[/success]")
                self._messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })

            with console.status(f"[info]{t('thinking')}[/info]", spinner="dots"):
                try:
                    response = self._llm.chat(messages=self._messages, tools=AGENT_TOOLS)
                except LLMClientError as exc:
                    error_msg = f"LLM call failed: {exc}"
                    self._messages.append({"role": "assistant", "content": error_msg})
                    return error_msg

            message = response.choices[0].message

        assistant_text = message.content or ""
        self._messages.append({"role": "assistant", "content": assistant_text})
        return assistant_text


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _prompt_language(profile: Profile, profile_name: str) -> None:
    """Ask the user to pick a display language and persist the choice."""
    languages = ["English", "中文"]
    idx = select_option("Language / 语言选择", languages, default=0)
    lang = "zh" if idx == 1 else "en"
    set_language(lang)
    profile.language = lang
    save_profile(profile, profile_name)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Awesome Paper")
    parser.add_argument("--profile", default="default", help="Profile name")
    args = parser.parse_args()

    # Load profile and prompt for language on first run (or if not yet set)
    try:
        pre_profile = load_profile(args.profile)
    except Exception:
        pre_profile = Profile()

    _prompt_language(pre_profile, args.profile)

    print_banner()

    try:
        agent = Agent(profile_name=args.profile)
    except Exception as exc:
        console.print(f"[error]Failed to initialise agent: {exc}[/error]")
        console.print("[dim]Tip: check profiles/default.json has a valid LLM api_key or set LLM_API_KEY env var.[/dim]")
        sys.exit(1)

    # Build config status and let the agent greet with awareness
    config_status = agent._build_config_status()
    with console.status(f"[info]{t('initialising')}[/info]", spinner="dots"):
        greeting = agent.chat_turn(
            f"{config_status}\n\n"
            f"{t('greeting_lang_instruction')}"
        )
    print_assistant(greeting)

    while True:
        try:
            user_input = console.input("\n[bold cyan]You:[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print(f"\n[info]{t('goodbye')}[/info]")
            break
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            console.print(f"[info]{t('goodbye')}[/info]")
            break

        # Check for slash commands first
        if user_input.startswith("/"):
            try:
                agent.handle_command(user_input)
            except SystemExit:
                break
            continue

        response = agent.chat_turn(user_input)
        print_assistant(response)


if __name__ == "__main__":
    main()
