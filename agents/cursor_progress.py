"""Turn Cursor SDK run events into HatsOff CLI progress records."""

from __future__ import annotations

from typing import Any, Iterable, Iterator, Mapping, Optional


def usage_as_dict(usage: Any) -> Optional[dict[str, Any]]:
    if usage is None:
        return None
    if isinstance(usage, Mapping):
        inn = int(usage.get("input_tokens") or usage.get("inputTokens") or 0)
        out = int(usage.get("output_tokens") or usage.get("outputTokens") or 0)
        total = int(
            usage.get("total_tokens")
            or usage.get("totalTokens")
            or (inn + out)
        )
        cache = int(
            usage.get("cache_read_tokens") or usage.get("cacheReadTokens") or 0
        )
        reason = usage.get("reasoning_tokens") or usage.get("reasoningTokens")
        if inn == 0 and out == 0 and total == 0 and cache == 0 and not reason:
            return None
        payload = {
            "input_tokens": inn,
            "output_tokens": out,
            "total_tokens": total,
            "cache_read_tokens": cache,
        }
        if reason is not None:
            payload["reasoning_tokens"] = int(reason)
        return payload
    inn = int(getattr(usage, "input_tokens", 0) or 0)
    out = int(getattr(usage, "output_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", 0) or (inn + out))
    cache = int(getattr(usage, "cache_read_tokens", 0) or 0)
    reason = getattr(usage, "reasoning_tokens", None)
    if inn == 0 and out == 0 and total == 0 and cache == 0 and reason is None:
        return None
    payload = {
        "input_tokens": inn,
        "output_tokens": out,
        "total_tokens": total,
        "cache_read_tokens": cache,
    }
    if reason is not None:
        payload["reasoning_tokens"] = int(reason)
    return payload


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        for name in names:
            if obj.get(name) is not None:
                return obj[name]
        return default
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def _short(text: Any, width: int = 88) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= width:
        return value
    return value[: width - 1] + "…"


def _tool_label(name: str) -> str:
    key = (name or "").lower().replace("-", "_")
    mapping = {
        "shell": "Shell",
        "bash": "Shell",
        "read_file": "Read",
        "readfile": "Read",
        "read": "Read",
        "write": "Write",
        "edit": "Edit",
        "strreplace": "Edit",
        "apply_patch": "Edit",
        "grep": "Grep",
        "glob": "Glob",
        "web_search": "Search",
        "websearch": "Search",
        "delete": "Delete",
    }
    return mapping.get(key, name or "Tool")


def _tool_fields(tool_call: Any) -> tuple[str, str]:
    data = tool_call if isinstance(tool_call, Mapping) else {}
    raw_name = (
        data.get("name")
        or data.get("toolName")
        or data.get("tool")
        or data.get("function")
        or "Tool"
    )
    if isinstance(raw_name, Mapping):
        raw_name = raw_name.get("name") or "Tool"
    args = (
        data.get("args")
        or data.get("arguments")
        or data.get("params")
        or data.get("input")
        or data
    )
    detail = ""
    if isinstance(args, Mapping):
        for key in (
            "command",
            "path",
            "target_file",
            "file_path",
            "query",
            "pattern",
            "glob_pattern",
        ):
            value = args.get(key)
            if value:
                detail = _short(value, 72)
                break
    elif args not in (None, data) and not isinstance(args, Mapping):
        detail = _short(args, 72)
    return _tool_label(str(raw_name)), detail


def _assistant_text(message: Any) -> str:
    content = _get(_get(message, "message"), "content", default=()) or ()
    parts: list[str] = []
    for block in content:
        text = _get(block, "text")
        if text:
            parts.append(str(text))
    return "".join(parts)


def _shell_text(event: Any) -> str:
    if not isinstance(event, Mapping):
        return _short(event, 160) if event else ""
    for key in ("data", "chunk", "text", "stdout", "output", "delta"):
        value = event.get(key)
        if value:
            return str(value)
    inner = event.get("event")
    if isinstance(inner, Mapping):
        return _shell_text(inner)
    return ""


def _progress(kind: str, **fields: Any) -> dict[str, Any]:
    payload = {"type": "progress", "kind": kind}
    payload.update({key: value for key, value in fields.items() if value is not None})
    return payload


def progress_from_sdk_message(message: Any) -> Iterator[dict[str, Any]]:
    kind = str(_get(message, "type", default="") or "")
    if kind == "thinking":
        text = str(_get(message, "text", default="") or "")
        if text:
            yield _progress("thinking", text=text, delta=False)
        else:
            yield _progress("status", text="thinking")
        return
    if kind == "tool_call":
        name = _tool_label(str(_get(message, "name", default="") or "Tool"))
        detail = _short(_get(message, "args", default="") or "", 72)
        if not detail:
            _, detail = _tool_fields(_get(message, "args"))
        yield _progress(
            "tool",
            name=name,
            detail=detail,
            status=str(_get(message, "status", default="running") or "running"),
            call_id=str(_get(message, "call_id", "callId", default=name) or name),
        )
        return
    if kind == "assistant":
        text = _assistant_text(message)
        if text:
            yield _progress("assistant", text=text, delta=False)
        return
    if kind == "usage":
        usage = usage_as_dict(_get(message, "usage"))
        if usage:
            yield _progress("usage", **usage)
        return
    if kind == "status":
        text = str(
            _get(message, "message", default="")
            or _get(message, "status", default="")
            or ""
        )
        if text:
            yield _progress("status", text=text)
        return
    if kind == "task":
        text = str(_get(message, "text", default="") or "")
        if text:
            yield _progress("status", text=text)


def progress_from_update(update: Any) -> Iterator[dict[str, Any]]:
    kind = str(_get(update, "type", default="") or "")
    if kind == "thinking-delta":
        text = str(_get(update, "text", default="") or "")
        if text:
            yield _progress("thinking", text=text, delta=True)
        return
    if kind == "thinking-completed":
        ms = _get(update, "thinking_duration_ms", "thinkingDurationMs")
        text = f"thought {int(ms)}ms" if ms else "thought"
        yield _progress("status", text=text)
        return
    if kind == "text-delta":
        text = str(_get(update, "text", default="") or "")
        if text:
            yield _progress("assistant", text=text, delta=True)
        return
    if kind == "token-delta":
        tokens = int(_get(update, "tokens", default=0) or 0)
        if tokens:
            yield _progress("token", delta=tokens)
        return
    if kind in ("tool-call-started", "partial-tool-call", "tool-call-completed"):
        name, detail = _tool_fields(_get(update, "tool_call", "toolCall"))
        status = "completed" if kind == "tool-call-completed" else "running"
        yield _progress(
            "tool",
            name=name,
            detail=detail,
            status=status,
            call_id=str(_get(update, "call_id", "callId", default=name) or name),
        )
        return
    if kind == "shell-output-delta":
        text = _shell_text(_get(update, "event", default={}))
        if text:
            yield _progress("shell", text=text, delta=True)
        return
    if kind == "turn-ended":
        usage = usage_as_dict(_get(update, "usage"))
        if usage:
            yield _progress("usage", **usage)
        return
    if kind == "step-started":
        step_id = _get(update, "step_id", "stepId")
        yield _progress("status", text=f"step {step_id}" if step_id is not None else "step")
        return
    if kind == "step-completed":
        step_id = _get(update, "step_id", "stepId")
        ms = _get(update, "step_duration_ms", "stepDurationMs")
        label = f"step {step_id} done" if step_id is not None else "step done"
        if ms:
            label += f" ({int(ms)}ms)"
        yield _progress("status", text=label)


def progress_from_stream_event(event: Any) -> Iterator[dict[str, Any]]:
    message = _get(event, "sdk_message", "sdkMessage")
    if message is not None:
        yield from progress_from_sdk_message(message)
    update = _get(event, "interaction_update", "interactionUpdate")
    if update is not None:
        yield from progress_from_update(update)


def iter_progress(run: Any) -> Iterable[dict[str, Any]]:
    events_fn = getattr(run, "events", None)
    if callable(events_fn):
        try:
            for event in events_fn():
                yield from progress_from_stream_event(event)
            return
        except Exception:
            return
    stream_fn = getattr(run, "stream", None) or getattr(run, "messages", None)
    if callable(stream_fn):
        try:
            for message in stream_fn():
                yield from progress_from_sdk_message(message)
        except Exception:
            return


