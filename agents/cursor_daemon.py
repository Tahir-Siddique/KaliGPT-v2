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
    _complete_run,
    _patch_windows_bridge_discovery,
    _prefixed_prompt,
    _run_failed,
)

_AGENTS: Dict[str, Any] = {}


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _send(agent, prompt: str, model: str):
    from cursor_sdk import SendOptions

    return _complete_run(agent.send(prompt, SendOptions(model=model)))


def handle_turn(payload: dict) -> dict:
    _patch_windows_bridge_discovery()

    from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions

    prompt = payload.get("prompt") or ""
    api_key = payload.get("api_key") or ""
    model = payload.get("model") or "composer-2.5"
    cwd = payload.get("cwd") or os.getcwd()
    agent_id = payload.get("agent_id")
    system_prompt = payload.get("system_prompt") or ""

    if not api_key:
        return {"ok": False, "error": "Cursor API key not configured.", "agent_id": agent_id}

    prefixed = _prefixed_prompt(prompt, agent_id=agent_id, system_prompt=system_prompt)

    try:
        # Hot path: agent already open in this daemon
        if agent_id and agent_id in _AGENTS:
            text, status = _send(_AGENTS[agent_id], prefixed, model)
            if not _run_failed(status, text):
                return {"ok": True, "text": text, "agent_id": agent_id}
            _log(f"run error for cached agent {agent_id}; evicting and retrying fresh")
            try:
                _AGENTS[agent_id].close()
            except Exception:
                pass
            _AGENTS.pop(agent_id, None)
            agent_id = None

        # Resume from local store if possible
        if agent_id:
            try:
                agent = Agent.resume(
                    agent_id,
                    AgentOptions(api_key=api_key, model=model),
                )
                text, status = _send(agent, prefixed, model)
                if not _run_failed(status, text):
                    _AGENTS[agent_id] = agent
                    return {"ok": True, "text": text, "agent_id": agent_id}
                _log(f"run error after resume for {agent_id}; creating a new agent")
                try:
                    agent.close()
                except Exception:
                    pass
            except CursorAgentError as err:
                _log(f"resume failed ({err}); creating a new agent")

        # Fresh agent
        agent = Agent.create(
            model=model,
            api_key=api_key,
            local=LocalAgentOptions(cwd=cwd),
        )
        new_id = getattr(agent, "agent_id", None) or getattr(agent, "agentId", None)
        if not new_id:
            agent.close()
            return {"ok": False, "error": "Cursor did not return an agent_id", "agent_id": None}
        fresh = _prefixed_prompt(prompt, agent_id=None, system_prompt=system_prompt)
        text, status = _send(agent, fresh, model)
        if _run_failed(status, text):
            try:
                agent.close()
            except Exception:
                pass
            return {"ok": False, "error": text, "agent_id": new_id}
        _AGENTS[new_id] = agent
        return {"ok": True, "text": text, "agent_id": new_id}

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
    try:
        _patch_windows_bridge_discovery()
        # Unbuffered-friendly ready handshake for parent HatsOff process
        print(json.dumps({"ok": True, "ready": True, "pid": os.getpid()}), flush=True)
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
                print(
                    json.dumps({"ok": False, "error": f"Invalid JSON: {exc}"}),
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
