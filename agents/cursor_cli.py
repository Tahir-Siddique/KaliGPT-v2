#!/usr/bin/env python3
"""HatsOff Cursor Agent CLI chrome — chat-style terminal UI."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Optional

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .utils.parse_n_print_response import get_console_width

SLASH_HELP = "/help"
SLASH_STATUS = "/status"
SLASH_NEW = "/new"
SLASH_CLEAR = "/clear"
SLASH_MSF = "/msf"
SLASH_EXIT = frozenset({"/exit", "/quit", "/bye"})
CLI_COMMANDS = frozenset(
    {SLASH_HELP, SLASH_STATUS, SLASH_NEW, SLASH_CLEAR, SLASH_MSF}
) | SLASH_EXIT


def _metasploit_row() -> str:
    try:
        from .utils.tools.metasploit import metasploit_status

        info = metasploit_status()
    except Exception as exc:
        return f"[red]error[/red]  {exc}"
    if info.get("installed"):
        ver = info.get("version") or "msfconsole"
        return f"[green]ready[/green]  {ver}"
    return "[yellow]not installed[/yellow]  install metasploit-framework on Kali"


def console() -> Console:
    return Console(width=get_console_width(), highlight=False)


def _short_path(path: str, keep: int = 42) -> str:
    text = os.path.expanduser(path)
    home = str(Path.home())
    if text.startswith(home):
        text = "~" + text[len(home) :]
    if len(text) <= keep:
        return text
    return "…" + text[-(keep - 1) :]


def _short_id(agent_id: Optional[str]) -> str:
    if not agent_id:
        return "not started"
    if len(agent_id) <= 28:
        return agent_id
    return agent_id[:12] + "…" + agent_id[-8:]


def is_cli_command(line: str) -> bool:
    token = (line or "").strip().lower().replace("-", " ")
    if token in CLI_COMMANDS:
        return True
    # allow "/new chat" style
    first = token.split()[0] if token else ""
    return first in CLI_COMMANDS or first in SLASH_EXIT


def is_error_reply(text: str) -> bool:
    low = (text or "").strip().lower()
    return low.startswith(
        (
            "error:",
            "cursor error",
            "cursor startup",
            "run failed:",
            "not a chat",
        )
    )


def print_startup(
    *,
    model: str,
    cwd: str,
    inprocess: bool,
    daemon_pid: Optional[int] = None,
    daemon_error: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> None:
    c = console()
    c.print()
    title = Text()
    title.append("HatsOff", style="bold white")
    title.append("  ·  ", style="grey50")
    title.append("Cursor Agent", style="bold cyan")

    table = Table.grid(padding=(0, 2))
    table.add_column(style="grey50", no_wrap=True)
    table.add_column(overflow="fold")
    table.add_row("runtime", "[white]Cursor SDK local agent[/white]  (Agent.create / resume)")
    table.add_row("model", f"[white]{model}[/white]")
    table.add_row("workspace", f"[white]{_short_path(cwd, 56)}[/white]")

    if inprocess:
        table.add_row(
            "bridge",
            "[yellow]in-process[/yellow]  unset KALIGPT_CURSOR_INPROCESS for the daemon",
        )
    elif daemon_error:
        table.add_row("bridge", f"[red]daemon failed[/red]  {daemon_error}")
    elif daemon_pid:
        table.add_row("bridge", f"[green]daemon ready[/green]  pid {daemon_pid}")
    else:
        table.add_row("bridge", "[yellow]starting…[/yellow]")

    table.add_row("agent id", f"[white]{_short_id(agent_id)}[/white]")
    table.add_row("metasploit", _metasploit_row())
    table.add_row(
        "tools",
        "Cursor shell / read / grep / edit  +  KaliGPT web, search, Metasploit",
    )
    table.add_row(
        "commands",
        "[cyan]/help[/cyan]  [cyan]/status[/cyan]  [cyan]/msf[/cyan]  "
        "[cyan]/new[/cyan]  [cyan]/clear[/cyan]  [cyan]/exit[/cyan]",
    )

    c.print(
        Panel(
            table,
            title=title,
            title_align="left",
            border_style="cyan",
            padding=(1, 2),
            subtitle="[grey50]Enter send  ·  Ctrl+C exit[/grey50]",
            subtitle_align="right",
        )
    )
    c.print()


def print_help() -> None:
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("cmd", style="cyan", no_wrap=True)
    table.add_column("what", style="white")
    table.add_row("/help", "This list")
    table.add_row("/status", "Daemon, model, workspace, Cursor agent id")
    table.add_row("/new", "Start a fresh Cursor Agent (new agent id)")
    table.add_row("/clear", "Clear the screen and redraw the header")
    table.add_row("/msf", "Show whether Metasploit (msfconsole) is installed")
    table.add_row("/list tools", "KaliGPT tool names")
    table.add_row("/change model", "Switch default provider / model")
    table.add_row("/exit", "Quit (/quit, /bye)")
    console().print(
        Panel(table, title="[bold]Cursor Agent commands[/bold]", border_style="cyan", padding=(1, 2))
    )


def print_msf() -> None:
    from .utils.tools.metasploit import metasploit_status

    info = metasploit_status()
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("k", style="grey50", no_wrap=True)
    table.add_column("v", style="white")
    table.add_row("installed", "yes" if info.get("installed") else "no")
    table.add_row("msfconsole", str(info.get("msfconsole") or "—"))
    table.add_row("msfvenom", str(info.get("msfvenom") or "—"))
    table.add_row("version", str(info.get("version") or "—"))
    if info.get("hint"):
        table.add_row("hint", str(info["hint"]))
    console().print(
        Panel(
            table,
            title="[bold]Metasploit[/bold]",
            border_style="green" if info.get("installed") else "yellow",
            padding=(1, 2),
        )
    )


def print_status(
    *,
    model: str,
    cwd: str,
    inprocess: bool,
    daemon_pid: Optional[int],
    agent_id: Optional[str],
) -> None:
    print_startup(
        model=model,
        cwd=cwd,
        inprocess=inprocess,
        daemon_pid=daemon_pid,
        agent_id=agent_id,
    )


def print_user(text: str) -> None:
    console().print(
        Panel(
            Text(text.strip(), style="white"),
            title="[bold]You[/bold]",
            title_align="left",
            border_style="#424242",
            padding=(0, 1),
        )
    )


def print_agent(text: str, *, agent_id: Optional[str] = None) -> None:
    from .utils.parse_n_print_response import parse_n_print_response

    err = is_error_reply(text)
    title = "Cursor Agent error" if err else "Cursor Agent"
    if agent_id and not err:
        title = f"Cursor Agent  ·  {_short_id(agent_id)}"
    parse_n_print_response(
        text or "",
        title=f"( {title} )",
        border_style="red" if err else "cyan",
    )


def print_notice(message: str, *, style: str = "cyan") -> None:
    console().print(f"[{style}]{message}[/{style}]")


def _merge_assistant(collected: str, incoming: str, *, delta: bool) -> str:
    if not incoming:
        return collected
    if delta or not collected:
        return collected + incoming
    if incoming.startswith(collected):
        return incoming
    if collected.endswith(incoming):
        return collected
    return collected + incoming


def _fmt_tokens(value: int) -> str:
    return f"{int(value):,}"


class TurnLive:
    """Live token usage + tool/thinking/shell log while a Cursor run is in flight."""

    def __init__(self, con: Optional[Console] = None):
        self.con = con or console()
        self.logs: list[str] = []
        self._tool_seen: dict[str, str] = {}
        self.thinking = ""
        self.assistant = ""
        self.shell = ""
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.live_tokens = 0
        self.saw_assistant = False
        self._live: Optional[Live] = None

    def __enter__(self) -> "TurnLive":
        self._live = Live(
            self.view(),
            console=self.con,
            refresh_per_second=16,
            vertical_overflow="visible",
        )
        self._live.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._live is not None:
            self._live.update(self.view())
            self._live.stop()
            self._live = None
            if self.assistant and not self.assistant.endswith("\n"):
                self.con.print()

    def handle(self, event: dict[str, Any]) -> None:
        kind = str(event.get("kind") or "")
        if kind == "thinking":
            text = str(event.get("text") or "")
            if event.get("delta"):
                self.thinking += text
            else:
                self.thinking = text or self.thinking
        elif kind == "assistant":
            incoming = str(event.get("text") or "")
            if incoming:
                self.assistant = _merge_assistant(
                    self.assistant, incoming, delta=bool(event.get("delta"))
                )
                self.saw_assistant = True
                self.thinking = ""
        elif kind == "tool":
            name = str(event.get("name") or "Tool")
            detail = str(event.get("detail") or "")
            status = str(event.get("status") or "running")
            call_id = str(event.get("call_id") or name)
            mark = "x" if status == "error" else ("+" if status == "completed" else ">")
            line = f"{mark} {name}" + (f"  {detail}" if detail else "")
            if self._tool_seen.get(call_id) != line:
                self._tool_seen[call_id] = line
                self.logs.append(line)
        elif kind == "shell":
            text = str(event.get("text") or "")
            if text:
                self.shell = (self.shell + text)[-1200:]
        elif kind == "status":
            text = str(event.get("text") or "").strip()
            if text:
                self.logs.append(f"· {text}")
        elif kind == "token":
            self.live_tokens += int(event.get("delta") or 0)
        elif kind == "usage":
            self.input_tokens = int(event.get("input_tokens") or self.input_tokens)
            self.output_tokens = int(event.get("output_tokens") or self.output_tokens)
            self.total_tokens = int(
                event.get("total_tokens")
                or (self.input_tokens + self.output_tokens)
                or self.total_tokens
            )
            if self.total_tokens:
                self.live_tokens = max(self.live_tokens, self.total_tokens)
        self.logs = self.logs[-16:]
        if self._live is not None:
            self._live.update(self.view())

    def view(self) -> Any:
        parts: list[Any] = [self._usage_line()]
        if self.logs:
            log = Text()
            for line in self.logs:
                style = "red" if line.startswith("x ") else (
                    "green" if line.startswith("+ ") else "cyan"
                )
                if line.startswith("· "):
                    style = "grey50"
                log.append(line + "\n", style=style)
            parts.append(log)
        if self.shell:
            parts.append(Text(self.shell[-400:], style="grey50"))
        if self.thinking and not self.assistant:
            snippet = self.thinking[-280:].replace("\n", " ")
            parts.append(Text(snippet, style="italic grey50"))
        if self.assistant:
            parts.append(Markdown(self.assistant))
        return Group(*parts)

    def _usage_line(self) -> Text:
        inn = self.input_tokens
        out = self.output_tokens
        total = self.total_tokens or self.live_tokens
        line = Text()
        if inn or out or total:
            line.append("tokens  ", style="grey50")
            if inn or out:
                line.append(_fmt_tokens(inn), style="bold white")
                line.append(" in  ", style="grey50")
                line.append(_fmt_tokens(out), style="bold white")
                line.append(" out", style="grey50")
                if total:
                    line.append("  ·  ", style="grey50")
                    line.append(_fmt_tokens(total), style="bold cyan")
                    line.append(" total", style="grey50")
            else:
                line.append(_fmt_tokens(total), style="bold cyan")
                line.append(" streamed", style="grey50")
        else:
            line.append("working…  ", style="bold cyan")
            line.append("waiting for tools / tokens", style="grey50")
        return line


def history_path() -> Path:
    folder = Path.home() / ".kaligpt"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "cli_history"


def make_prompt_session(toolbar: Callable[[], str]):
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.styles import Style

    style = Style.from_dict(
        {
            "prompt": "bold #ececec",
            "mark": "#8f8f8f",
            "bottom-toolbar": "bg:#2f2f2f #b4b4b4",
        }
    )

    def bottom_toolbar():
        return HTML(f'<style bg="#2f2f2f" fg="#b4b4b4"> {toolbar()} </style>')

    return PromptSession(
        history=FileHistory(str(history_path())),
        style=style,
        bottom_toolbar=bottom_toolbar,
        enable_history_search=True,
    )


def read_line(session, *, initial: Optional[str] = None) -> str:
    if initial is not None:
        return initial
    from prompt_toolkit.formatted_text import HTML

    if not os.isatty(0):
        return input("You › ")
    return session.prompt(
        HTML('<b fg="#ececec">You </b><b fg="#8f8f8f">› </b>'),
    )
