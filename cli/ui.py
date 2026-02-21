"""Rich UI components for the Awesome Paper CLI."""
from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Sequence

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.theme import Theme
from rich.tree import Tree

from cli.i18n import t

# ---------------------------------------------------------------------------
# Console singleton
# ---------------------------------------------------------------------------

_THEME = Theme(
    {
        "info": "cyan",
        "success": "bold green",
        "warning": "bold yellow",
        "error": "bold red",
        "title": "bold magenta",
    }
)

console = Console(theme=_THEME)

# ---------------------------------------------------------------------------
# Banner & help
# ---------------------------------------------------------------------------


def print_banner() -> None:
    """Print a startup banner with available commands."""
    content = (
        f"[title]Awesome Paper[/title]\n"
        f"{t('banner_subtitle')}\n\n"
        f"[info]{t('banner_shortcuts')}[/info]\n"
        f"  /help   /status   /config   /run   /search   /quit"
    )
    console.print(Panel(content, border_style="cyan", expand=False))


def print_help() -> None:
    """Print the help panel with all available commands."""
    body = t("help_table") + "\n\n" + t("help_footer")
    console.print(Panel(Markdown(body), title=t("help_title"), border_style="cyan", expand=False))


def print_assistant(text: str) -> None:
    """Render assistant reply as Markdown inside a panel."""
    console.print(Panel(Markdown(text), title="Assistant", border_style="green", expand=True))


# ---------------------------------------------------------------------------
# Config & status display
# ---------------------------------------------------------------------------


def print_config_status(profile_dict: Dict[str, Any]) -> None:
    """Render configuration as a Rich Tree."""
    tree = Tree("[title]Profile Configuration[/title]")

    # Subscriptions
    subs = profile_dict.get("subscriptions", {})
    sub_branch = tree.add("[info]Subscriptions[/info]")
    cats = subs.get("categories", [])
    sub_branch.add(
        f"Categories: {', '.join(cats) if cats else '[warning]' + t('not_configured') + '[/warning]'}"
    )
    tags = subs.get("interest_tags", [])
    if tags:
        tag_branch = sub_branch.add(f"Interest Tags ({len(tags)})")
        for tag in tags:
            tag_branch.add(tag.get("label", "?"))
    else:
        sub_branch.add(f"Interest Tags: [dim]{t('none_label')}[/dim]")

    # Channels
    channels = profile_dict.get("channels", [])
    ch_branch = tree.add("[info]Channels[/info]")
    if channels:
        for ch in channels:
            ch_branch.add(f"{ch.get('type', '?')} [success]\u2713[/success]")
    else:
        ch_branch.add(f"[dim]{t('no_channels')}[/dim]")

    # LLM
    llm_cfg = profile_dict.get("llm", {})
    llm_branch = tree.add("[info]LLM Backends[/info]")
    for role, cfg in llm_cfg.items():
        model = cfg.get("model", "?") if isinstance(cfg, dict) else "?"
        base = cfg.get("api_base", "") if isinstance(cfg, dict) else ""
        llm_branch.add(f"{role}: {model} @ {base}")

    console.print(Panel(tree, border_style="cyan", expand=False))


def print_pipeline_status(status_dict: Dict[str, Any]) -> None:
    """Render pipeline run history as a Rich Table."""
    table = Table(title=t("pipeline_title"), border_style="cyan")
    table.add_column(t("col_date"), style="bold")
    table.add_column("Scrape", justify="center")
    table.add_column("Classify", justify="center")
    table.add_column("Send", justify="center")

    def _status_icon(day_data: dict, stage: str) -> str:
        info = day_data.get(stage, {})
        if isinstance(info, dict) and info.get("completed"):
            return "[success]\u2713[/success]"
        return "[dim]-[/dim]"

    if not status_dict:
        table.add_row(f"[dim]{t('no_records')}[/dim]", "", "", "")
    else:
        for day in sorted(status_dict.keys(), reverse=True):
            day_data = status_dict[day]
            table.add_row(
                day,
                _status_icon(day_data, "scrape"),
                _status_icon(day_data, "classify"),
                _status_icon(day_data, "send"),
            )

    console.print(table)


def print_paper_table(papers: Sequence[Dict[str, Any]], title: str | None = None) -> None:
    """Render a list of papers as a Rich Table."""
    table = Table(title=title or t("paper_table_title"), border_style="cyan", show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Title", max_width=50)
    table.add_column("Area", max_width=16)
    table.add_column(t("col_tldr"), max_width=40)

    for i, p in enumerate(papers, 1):
        table.add_row(
            str(i),
            p.get("title", "")[:80],
            p.get("primary_area", ""),
            p.get("tldr_zh", "")[:60],
        )

    console.print(table)


# ---------------------------------------------------------------------------
# Arrow-key selector
# ---------------------------------------------------------------------------


def select_option(title: str, options: Sequence[str], default: int = 0) -> int:
    """Display an interactive arrow-key menu and return the selected index.

    Falls back to simple numeric input when the terminal doesn't support
    raw mode (e.g. piped stdin).
    """
    try:
        import tty
        import termios

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
    except (ImportError, OSError, ValueError):
        return _select_fallback(title, options, default)

    cursor = default
    count = len(options)
    # total lines rendered: 1 (blank) + 1 (title) + count (options) + 1 (blank) + 1 (hint) = count + 4
    total_lines = count + 4

    CLEAR_LINE = "\x1b[2K"  # erase entire line
    CURSOR_UP = "\x1b[A"
    HIDE_CURSOR = "\x1b[?25l"
    SHOW_CURSOR = "\x1b[?25h"

    def _render(first: bool = False) -> None:
        out = sys.stdout
        if not first:
            # move cursor up to the start of the menu
            out.write(CURSOR_UP * total_lines)
        lines = [""]  # blank line
        lines.append(f"  \x1b[1m{title}\x1b[0m")
        for i, opt in enumerate(options):
            if i == cursor:
                lines.append(f"    \x1b[36m\x1b[1m> {opt}\x1b[0m")
            else:
                lines.append(f"      \x1b[2m{opt}\x1b[0m")
        lines.append("")
        lines.append("    \x1b[2m\u2191/\u2193 navigate  Enter confirm\x1b[0m")
        for line in lines:
            out.write(f"{CLEAR_LINE}\r{line}\n")
        out.flush()

    sys.stdout.write(HIDE_CURSOR)
    sys.stdout.flush()
    _render(first=True)

    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                break
            if ch == "\x1b":
                seq = sys.stdin.read(2)
                if seq == "[A":
                    cursor = (cursor - 1) % count
                elif seq == "[B":
                    cursor = (cursor + 1) % count
                # temporarily restore cooked mode to render cleanly
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                _render(first=False)
                tty.setraw(fd)
            elif ch == "\x03":
                raise KeyboardInterrupt
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sys.stdout.write(SHOW_CURSOR)
        sys.stdout.flush()

    return cursor


def _select_fallback(title: str, options: Sequence[str], default: int) -> int:
    """Simple numbered fallback for non-TTY environments."""
    console.print(f"\n[bold]{title}[/bold]")
    for i, opt in enumerate(options):
        console.print(f"  [cyan]{i + 1}[/cyan]  {opt}")
    raw = console.input(f"\n[bold]Choose (1-{len(options)}) [{default + 1}]: [/bold]").strip()
    if not raw:
        return default
    try:
        idx = int(raw) - 1
        return idx if 0 <= idx < len(options) else default
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Tool-call spinner
# ---------------------------------------------------------------------------


@contextmanager
def tool_call_status(fn_name: str, args_str: str = ""):
    """Context manager that shows a spinner while a tool runs."""
    label = f"[info]{fn_name}[/info]"
    if args_str:
        label += f" {args_str}"
    with console.status(f"Calling {label} ...", spinner="dots"):
        yield


# ---------------------------------------------------------------------------
# PipelineProgressUI
# ---------------------------------------------------------------------------


class PipelineProgressUI:
    """Manages a multi-stage progress display for the pipeline."""

    def __init__(self) -> None:
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
        )
        self._tasks: Dict[str, Any] = {}
        self._classify_task_id: Any | None = None

    def start(self) -> None:
        self._progress.start()

    def stop(self) -> None:
        self._progress.stop()

    def add_stage(self, name: str, description: str, total: int = 1) -> None:
        task_id = self._progress.add_task(description, total=total)
        self._tasks[name] = task_id

    def complete_stage(self, name: str) -> None:
        task_id = self._tasks.get(name)
        if task_id is not None:
            task = self._progress.tasks[task_id]
            self._progress.update(task_id, completed=task.total, description=f"\u2713 {task.description}")

    def set_classify_total(self, total: int) -> None:
        task_id = self._tasks.get("classify")
        if task_id is not None:
            self._progress.update(task_id, total=total)
            self._classify_task_id = task_id

    def advance_classify(self) -> None:
        if self._classify_task_id is not None:
            self._progress.advance(self._classify_task_id)

    def make_classify_callback(self) -> Callable[[int, int], None]:
        """Return a ``(current, total) -> None`` callback for classify progress."""

        def _cb(current: int, total: int) -> None:
            if current == 0:
                self.set_classify_total(total)
            else:
                self.advance_classify()

        return _cb
