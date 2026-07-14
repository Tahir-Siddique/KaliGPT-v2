#!/usr/bin/env python3
"""
Isolated Cursor SDK worker.

Runs in a fresh process so the local bridge owns its own sockets. Also applies a
Windows-safe bridge discovery monkeypatch: cursor-sdk uses selectors/select on
the bridge stderr pipe, but Windows select() only accepts sockets (WinError 10038).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any, Mapping


def _patch_windows_bridge_discovery() -> None:
    """Replace cursor_sdk bridge discovery with a Windows-safe pipe reader."""
    if os.name != "nt":
        return

    if not hasattr(os, "get_blocking"):
        os.get_blocking = lambda fd: True  # type: ignore[attr-defined, assignment]
    if not hasattr(os, "set_blocking"):
        os.set_blocking = lambda fd, blocking: None  # type: ignore[attr-defined, assignment]

    try:
        import cursor_sdk._bridge as bridge_mod
        from cursor_sdk.errors import CursorSDKError
    except Exception:
        # Mocked or incomplete install — discovery patch not applicable
        return

    def _read_discovery_windows(process, timeout: float) -> Mapping[str, Any]:
        if process.stderr is None:
            raise CursorSDKError("Bridge process stderr is unavailable")

        box: dict[str, Any] = {
            "discovery": None,
            "error": None,
            "lines": [],
        }

        def reader() -> None:
            try:
                while True:
                    line = process.stderr.readline()
                    if not line:
                        break
                    box["lines"].append(line)
                    discovery = bridge_mod.parse_discovery_line(line)
                    if discovery is not None:
                        box["discovery"] = discovery
                        return
            except Exception as exc:  # pragma: no cover - runtime bridge only
                box["error"] = exc

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if box["discovery"] is not None:
            return box["discovery"]

        exit_code = process.poll()
        if exit_code is not None:
            raise CursorSDKError(
                "Bridge exited before discovery with status "
                f"{exit_code}: {''.join(box['lines'])}"
            )
        if box["error"] is not None:
            raise CursorSDKError(f"Bridge discovery failed: {box['error']}") from box[
                "error"
            ]
        raise CursorSDKError("Timed out waiting for bridge discovery")

    bridge_mod._read_discovery = _read_discovery_windows  # type: ignore[assignment]


def _complete_run(run):
    result = run.wait()
    text = getattr(result, "result", None)
    if text is None:
        text = getattr(run, "result", None)
    status = getattr(result, "status", None)
    run_id = getattr(result, "id", "") or getattr(run, "id", "")
    if status == "error":
        detail = str(text or "").strip()
        msg = f"Run failed: {run_id}"
        if detail:
            msg += f" — {detail}"
        return msg, status
    if status == "cancelled":
        return str(text or "Run cancelled."), status
    return str(text or ""), status


def _run_failed(status: str | None, text: str) -> bool:
    return status == "error" or str(text or "").startswith("Run failed:")


def _prefixed_prompt(prompt: str, *, agent_id: str | None, system_prompt: str) -> str:
    if not agent_id and system_prompt:
        return f"{system_prompt}\n\n---\nUser request:\n{prompt}"
    return prompt


def run_turn(payload: dict) -> dict:
    _patch_windows_bridge_discovery()

    from cursor_sdk import (
        Agent,
        AgentOptions,
        CursorAgentError,
        LocalAgentOptions,
        SendOptions,
    )

    prompt = payload.get("prompt") or ""
    api_key = payload.get("api_key") or ""
    model = payload.get("model") or "composer-2.5"
    cwd = payload.get("cwd") or os.getcwd()
    agent_id = payload.get("agent_id")
    system_prompt = payload.get("system_prompt") or ""

    if not api_key:
        return {"ok": False, "error": "Cursor API key not configured.", "agent_id": agent_id}

    prefixed = _prefixed_prompt(prompt, agent_id=agent_id, system_prompt=system_prompt)
    send_opts = SendOptions(model=model)

    try:
        if agent_id:
            try:
                with Agent.resume(
                    agent_id,
                    AgentOptions(api_key=api_key, model=model),
                ) as agent:
                    text, status = _complete_run(agent.send(prefixed, send_opts))
                    if not _run_failed(status, text):
                        return {"ok": True, "text": text, "agent_id": agent_id}
            except CursorAgentError:
                pass

        with Agent.create(
            model=model,
            api_key=api_key,
            local=LocalAgentOptions(cwd=cwd),
        ) as agent:
            new_id = getattr(agent, "agent_id", None) or getattr(agent, "agentId", None)
            fresh = _prefixed_prompt(prompt, agent_id=None, system_prompt=system_prompt)
            text, status = _complete_run(agent.send(fresh, send_opts))
            if _run_failed(status, text):
                return {"ok": False, "error": text, "agent_id": new_id}
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
        return {"ok": False, "error": f"Cursor error: {exc}", "agent_id": agent_id}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        json.dump({"ok": False, "error": f"Invalid worker input: {exc}"}, sys.stdout)
        return 2

    result = run_turn(payload if isinstance(payload, dict) else {})
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
