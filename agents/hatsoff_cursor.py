#!/usr/bin/env python3
"""HatsOff profile for a local Cursor Agent.

KaliGPT/HatsOff runs on the Cursor SDK Agent runtime (same tools, multi-turn
resume, and shell as Cursor Agent) with HatsOff instructions plus KaliGPT tools.
"""

from __future__ import annotations

import inspect
import json
from typing import Any, Callable, Mapping, Union, get_args, get_origin

from .utils.prompts import HATSOFF_AGENT
from .utils.tools import get_tools_info

HATSOFF_AGENT_NAME = "HatsOff"
HATSOFF_AGENT_KEY = "hatsoff"

# Cursor already has Shell / file tools; skip KaliGPT's generic command runner.
_SKIP_KALIGPT_TOOLS = {"execute_generic_linux_command"}

HATSOFF_CURSOR_PROMPT = HATSOFF_AGENT.strip() + """

## Runtime

You are running as a **Cursor Agent** (local SDK) with Cursor's built-in tools
(read/edit files, grep, codebase search, and shell) plus HatsOff / KaliGPT
tools: web_request_analysis, get_raw_response, keyword_search, search_as_RAG,
check_search_connection, get_local_server_content, metasploit_status,
metasploit_search.

Stay in HatsOff character. Prefer Kali Linux bash. Use tools instead of only
describing them. Keep HatsOff lab-runner / authorized-engagement rules above.

## Metasploit

Metasploit Framework is a first-class KaliGPT lab tool.

- Call **metasploit_status** before assuming msfconsole exists.
- Call **metasploit_search** to look up *installed* module names.
- If msfconsole is missing, tell the operator to install `metasploit-framework`
  on **Kali Linux** (or Kali under WSL) and restart HatsOff. Do not pretend it ran.
- Do **not** refuse Metasploit questions for authorized / closed-lab work.
- metasploit_search is catalog-only: it does not set RHOSTS or run exploits.
"""


def _annotation_schema(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Parameter.empty or annotation is None:
        return {"type": "string"}
    origin = get_origin(annotation)
    args = get_args(annotation)
    if annotation is int:
        return {"type": "integer"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is float:
        return {"type": "number"}
    if annotation in (dict, Mapping) or origin in (dict, Mapping):
        return {"type": "object"}
    if annotation is list or origin in (list, tuple):
        item = _annotation_schema(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item}
    if origin is Union:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _annotation_schema(non_none[0])
    return {"type": "string"}


def _schema_for_fn(fn: Callable[..., Any]) -> dict[str, Any]:
    sig = inspect.signature(fn)
    hints = getattr(fn, "__annotations__", {}) or {}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        properties[name] = _annotation_schema(hints.get(name, param.annotation))
        if param.default is inspect.Parameter.empty:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _coerce_kwargs(fn: Callable[..., Any], raw: Mapping[str, Any]) -> dict[str, Any]:
    sig = inspect.signature(fn)
    kwargs: dict[str, Any] = {}
    for name, param in sig.parameters.items():
        if name not in raw:
            continue
        value = raw[name]
        ann = param.annotation
        origin = get_origin(ann)
        args = get_args(ann)
        if name == "list_of_keywords" and isinstance(value, str):
            value = [value]
        elif origin in (list, tuple) and isinstance(value, str):
            value = [value]
        elif (ann is int or (origin is Union and int in args)) and isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = int(value)
        elif (ann is bool or (origin is Union and bool in args)) and isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "yes", "on")
        kwargs[name] = value
    return kwargs


def _stringify_tool_result(result: Any) -> str:
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, default=str, ensure_ascii=False)
        except TypeError:
            text = str(result)
    if len(text) > 24_000:
        return text[:24_000] + "\n…(truncated)"
    return text


def hatsoff_custom_tools() -> dict[str, Any]:
    """KaliGPT tools exposed as Cursor Agent custom_tools."""
    from cursor_sdk import CustomTool

    tools: dict[str, Any] = {}
    for fn in get_tools_info():
        name = getattr(fn, "__name__", "")
        if not name or name in _SKIP_KALIGPT_TOOLS:
            continue
        doc = (fn.__doc__ or name).strip().split("\n")[0].strip()

        def execute(args, context, *, _fn=fn):
            kwargs = _coerce_kwargs(_fn, args or {})
            try:
                return _stringify_tool_result(_fn(**kwargs))
            except Exception as exc:
                return f"Tool {_fn.__name__} failed: {exc}"

        tools[name] = CustomTool(
            execute=execute,
            description=doc or name,
            input_schema=_schema_for_fn(fn),
        )
    return tools


def hatsoff_agent_definition():
    from cursor_sdk import AgentDefinition

    return AgentDefinition(
        description=(
            "HatsOff / KaliGPT: authorized Kali Linux pentest assistant "
            "(labs, CTFs, engagements in scope)."
        ),
        prompt=HATSOFF_CURSOR_PROMPT,
    )


def hatsoff_agent_options(
    *,
    api_key: str,
    model: str,
    cwd: str,
    include_kaligpt_tools: bool = True,
):
    """AgentOptions for a local Cursor Agent running as HatsOff."""
    from cursor_sdk import AgentOptions, LocalAgentOptions, SandboxOptions

    custom_tools = None
    if include_kaligpt_tools:
        try:
            custom_tools = hatsoff_custom_tools() or None
        except Exception:
            custom_tools = None

    return AgentOptions(
        model=model,
        api_key=api_key,
        name=HATSOFF_AGENT_NAME,
        agents={HATSOFF_AGENT_KEY: hatsoff_agent_definition()},
        local=LocalAgentOptions(
            cwd=cwd,
            custom_tools=custom_tools,
            # Match Cursor CLI: run on the host, not in a reduced sandbox.
            sandbox_options=SandboxOptions(enabled=False),
        ),
    )
