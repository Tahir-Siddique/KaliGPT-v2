#!/usr/bin/env python3
"""
Long-lived Cursor SDK daemon.

Keeps Agent handles open so multi-turn resume works. One-shot worker processes
cannot resume local agents after the bridge exits ("agent not found").
Also applies the Windows bridge discovery patch.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any, Dict, Optional

# Reuse Windows select() pipe fix
from agents.cursor_worker import (
    _hatsoff_options,
    _patch_windows_bridge_discovery,
    _prefixed_prompt,
    _run_failed,
    configure_utf8_stdio,
    drive_run,
)

_AGENTS: Dict[str, Any] = {}


def _log(msg: str) -> None:
    try:
        print(msg, file=sys.stderr, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"), file=sys.stderr, flush=True)


def _emit_progress(event: dict) -> None:
    # ASCII JSON so Windows cp1252 pipes never choke on arrows (→) in live logs.
    line = json.dumps(event, ensure_ascii=True, default=str)
    print(line, flush=True)


def _send(agent, prompt: str, model: str):
    from cursor_sdk import SendOptions

    text, status, usage = drive_run(
        agent.send(prompt, SendOptions(model=model)),
        emit=_emit_progress,
    )
    return text, status, usage


def handle_turn(payload: dict) -> dict:
    _patch_windows_bridge_discovery()

    from cursor_sdk import Agent, CursorAgentError

    prompt = payload.get("prompt") or ""
    api_key = payload.get("api_key") or ""
    model = payload.get("model") or "composer-2.5"
    cwd = payload.get("cwd") or os.getcwd()
    agent_id = payload.get("agent_id")
    system_prompt = payload.get("system_prompt") or ""
    opts = _hatsoff_options(api_key, model, cwd) if api_key else None

    if not api_key:
        return {"ok": False, "error": "Cursor API key not configured.", "agent_id": agent_id}

    prefixed = _prefixed_prompt(prompt, agent_id=agent_id, system_prompt=system_prompt)

    def _failed(text: str, keep_id: Any, usage: Optional[dict] = None) -> dict:
        result = {"ok": False, "error": text, "agent_id": keep_id}
        if usage:
            result["usage"] = usage
        return result

    try:
        # Continue an existing agent. Never create a replacement unless agent_id is unset.
        if agent_id:
            if agent_id in _AGENTS:
                text, status, usage = _send(_AGENTS[agent_id], prefixed, model)
                if not _run_failed(status, text):
                    result = {"ok": True, "text": text, "agent_id": agent_id}
                    if usage:
                        result["usage"] = usage
                    return result
                _log(f"run error for cached agent {agent_id}; will try resume")
                try:
                    _AGENTS[agent_id].close()
                except Exception:
                    pass
                _AGENTS.pop(agent_id, None)

            try:
                agent = Agent.resume(agent_id, opts)
                text, status, usage = _send(agent, prefixed, model)
                if not _run_failed(status, text):
                    _AGENTS[agent_id] = agent
                    result = {"ok": True, "text": text, "agent_id": agent_id}
                    if usage:
                        result["usage"] = usage
                    return result
                try:
                    agent.close()
                except Exception:
                    pass
                return _failed(text, agent_id, usage)
            except CursorAgentError as err:
                msg = getattr(err, "message", str(err))
                _log(f"resume failed ({err}); not creating a new agent")
                return _failed(
                    f"Could not continue previous session ({msg}). Type /new to start a fresh agent.",
                    agent_id,
                )

        # Fresh HatsOff Cursor Agent — only when no previous id was requested
        try:
            agent = Agent.create(opts)
        except Exception as exc:
            _log(f"HatsOff tools/create failed ({exc}); retrying without KaliGPT tools")
            agent = Agent.create(_hatsoff_options(api_key, model, cwd, include_tools=False))
        new_id = getattr(agent, "agent_id", None) or getattr(agent, "agentId", None)
        if not new_id:
            agent.close()
            return {"ok": False, "error": "Cursor did not return an agent_id", "agent_id": None}
        fresh = _prefixed_prompt(prompt, agent_id=None, system_prompt=system_prompt)
        text, status, usage = _send(agent, fresh, model)
        if _run_failed(status, text):
            try:
                agent.close()
            except Exception:
                pass
            return {"ok": False, "error": text, "agent_id": new_id}
        _AGENTS[new_id] = agent
        result = {"ok": True, "text": text, "agent_id": new_id}
        if usage:
            result["usage"] = usage
        return result

    except CursorAgentError as err:
        retryable = getattr(err, "is_retryable", False)
        msg = getattr(err, "message", str(err))
        return {
            "ok": False,
            "error": f"Cursor startup failed: {msg} (retryable={retryable})",
            "agent_id": agent_id,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Cursor error: {exc}",
            "agent_id": agent_id,
            "trace": traceback.format_exc()[-800:],
        }


def handle(payload: dict) -> dict:
    op = (payload.get("op") or "turn").strip()
    if op == "ping":
        return {"ok": True, "agents": list(_AGENTS.keys())}
    if op == "shutdown":
        for agent in list(_AGENTS.values()):
            try:
                agent.close()
            except Exception:
                pass
        _AGENTS.clear()
        return {"ok": True, "shutdown": True}
    if op == "turn":
        return handle_turn(payload)
    return {"ok": False, "error": f"Unknown op: {op}"}


def main() -> int:
    configure_utf8_stdio()
    try:
        _patch_windows_bridge_discovery()
        # Unbuffered-friendly ready handshake for parent HatsOff process
        print(json.dumps({"ok": True, "ready": True, "pid": os.getpid()}), flush=True)
        if sys.stdin.isatty():
            print(
                "This process is the HatsOff Cursor SDK daemon (JSON lines), not a chat.\n"
                "Talk to the agent:  python -m agents.cursor\n"
                "Desktop UI:         python -m agents.desktop --browser",
                file=sys.stderr,
            )
    except Exception as exc:
        print(
            json.dumps({"ok": False, "ready": False, "error": str(exc)}),
            flush=True,
        )
        return 1

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                hint = (
                    "Not a chat prompt. This daemon speaks JSON lines. "
                    "Run: python -m agents.cursor"
                )
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "error": hint if not line.startswith("{") else f"Invalid JSON: {exc}",
                        }
                    ),
                    flush=True,
                )
                continue
            try:
                result = handle(payload if isinstance(payload, dict) else {})
            except Exception as exc:
                result = {
                    "ok": False,
                    "error": f"Cursor daemon crash: {exc}",
                    "trace": traceback.format_exc()[-800:],
                }
            print(json.dumps(result), flush=True)
            if result.get("shutdown"):
                return 0
    except Exception as exc:
        _log(f"daemon loop failed: {exc}")
        print(
            json.dumps({"ok": False, "error": f"Daemon loop failed: {exc}"}),
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
