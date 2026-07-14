#!/usr/bin/env python3

# /agents/cursor.py
# KaliGPT Cursor Agent — talks to a long-lived daemon so local multi-turn
# resume works (one-shot workers cannot resume after the bridge exits).

from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
import threading
import time
from typing import IO, Deque, Optional, Tuple

from .utils.agent_configs import get_api_key, get_ai_specific_default_model
from .utils.prompts import HATSOFF_AGENT as SYSTEM_PROMPT


CURSOR_API_KEY: Optional[str] = None
CURSOR_MODEL: Optional[str] = None
CURSOR_CWD: str = os.getcwd()
_CURSOR_LOCK = threading.Lock()

_daemon_proc: Optional[subprocess.Popen] = None
_daemon_lock = threading.Lock()
_daemon_stderr: Deque[str] = collections.deque(maxlen=120)
_daemon_stderr_thread: Optional[threading.Thread] = None


def _resolve_api_key() -> Optional[str]:
    key = get_api_key("cursor")
    if key and key not in ("CURSOR_API_KEY", "YOUR_CURSOR_API_KEY", ""):
        return key.strip()
    env = os.environ.get("CURSOR_API_KEY", "").strip()
    return env or None


def initialize_configs() -> None:
    global CURSOR_API_KEY, CURSOR_MODEL, CURSOR_CWD
    try:
        CURSOR_API_KEY = _resolve_api_key()
        CURSOR_MODEL = get_ai_specific_default_model("cursor") or "composer-2.5"
        CURSOR_CWD = os.getcwd()

        if not CURSOR_API_KEY:
            print("[!] Cursor API Key not found. Configure it in desktop Settings.")
            sys.exit(0)
    except Exception as e:
        print(f"Failed to initialize Cursor Agent: {e}")
        sys.exit(1)


from .cursor_worker import _complete_run, _run_failed


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _daemon_env() -> dict:
    env = os.environ.copy()
    root = _repo_root()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root if not existing else os.pathsep.join([root, existing])
    return env


def _daemon_stderr_tail(n: int = 40) -> str:
    if not _daemon_stderr:
        return ""
    return "\n".join(list(_daemon_stderr)[-n:])


def _pump_daemon_stderr(proc: subprocess.Popen) -> None:
    """Drain stderr so the daemon never blocks on a full PIPE buffer."""
    try:
        if proc.stderr is None:
            return
        for line in proc.stderr:
            text = line.rstrip()
            if text:
                _daemon_stderr.append(text)
    except Exception:
        pass


def _daemon_exit_hint(code: Optional[int]) -> str:
    tail = _daemon_stderr_tail()
    parts = [f"Cursor daemon exited unexpectedly (code={code})."]
    if tail:
        parts.append("Daemon log:\n" + tail)
    else:
        parts.append(
            "No daemon log captured. On Kali, install deps then retry:\n"
            "  pip install -U cursor-sdk\n"
            "  # Cursor local agents also need the Cursor CLI / agent bridge available.\n"
            "Or set KALIGPT_CURSOR_INPROCESS=1, or switch provider away from Cursor."
        )
    low = tail.lower()
    if "modulenotfounderror" in low or "no module named 'cursor_sdk'" in low:
        parts.append("Fix: pip install cursor-sdk  (inside the same venv you use to launch HatsOff)")
    if "cursoragent" in low or "bridge" in low or "not found" in low:
        parts.append(
            "Fix: install/update Cursor CLI so the local agent bridge can start "
            "(https://cursor.com/docs — CLI / agent)."
        )
    return "\n".join(parts)


def _read_json_line(stream: IO[str], timeout: float = 900.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = stream.readline()
        if line:
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                # ignore non-JSON banner lines if any leak to stdout
                continue
        if _daemon_proc and _daemon_proc.poll() is not None:
            raise RuntimeError(_daemon_exit_hint(_daemon_proc.poll()))
        time.sleep(0.02)
    raise TimeoutError(
        "Timed out waiting for Cursor daemon response.\n" + (_daemon_stderr_tail() or "")
    )


def _ensure_daemon() -> subprocess.Popen:
    global _daemon_proc, _daemon_stderr_thread
    with _daemon_lock:
        if _daemon_proc is not None and _daemon_proc.poll() is None:
            return _daemon_proc

        # Reset log buffer for a fresh process
        _daemon_stderr.clear()
        creationflags = 0
        if os.name == "nt":
            # Avoid console flicker; keep pipes
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        _daemon_proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "agents.cursor_daemon"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=_daemon_env(),
            cwd=_repo_root(),
            creationflags=creationflags,
        )
        assert _daemon_proc.stdout is not None
        _daemon_stderr_thread = threading.Thread(
            target=_pump_daemon_stderr,
            args=(_daemon_proc,),
            daemon=True,
            name="cursor-daemon-stderr",
        )
        _daemon_stderr_thread.start()

        try:
            ready = _read_json_line(_daemon_proc.stdout, timeout=60.0)
        except Exception as exc:
            code = _daemon_proc.poll()
            raise RuntimeError(
                f"Cursor daemon failed during startup: {exc}\n"
                + _daemon_exit_hint(code)
            ) from exc

        if not ready.get("ok") or not ready.get("ready"):
            raise RuntimeError(
                f"Cursor daemon failed to start: {ready}\n" + _daemon_stderr_tail()
            )
        return _daemon_proc


def _ask_via_daemon(
    prompt: str,
    *,
    model: Optional[str],
    agent_id: Optional[str],
    cwd: Optional[str],
    api_key: str,
) -> Tuple[str, Optional[str]]:
    proc = _ensure_daemon()
    assert proc.stdin is not None and proc.stdout is not None

    payload = {
        "op": "turn",
        "prompt": prompt,
        "api_key": api_key,
        "model": model or get_ai_specific_default_model("cursor") or "composer-2.5",
        "cwd": cwd or CURSOR_CWD or os.getcwd(),
        "agent_id": agent_id,
        "system_prompt": SYSTEM_PROMPT,
    }
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()
    data = _read_json_line(proc.stdout, timeout=900.0)

    if data.get("ok"):
        return (str(data.get("text") or ""), data.get("agent_id") or agent_id)
    err = str(data.get("error") or "Cursor error")
    if err.startswith("Run failed:"):
        # Stale session — retry once without agent_id so a fresh agent is created.
        if agent_id:
            return _ask_via_daemon(
                prompt,
                model=model,
                agent_id=None,
                cwd=cwd,
                api_key=api_key,
            )
    return (err, data.get("agent_id") or agent_id)


def ask_inprocess(
    prompt: str,
    *,
    model: Optional[str] = None,
    agent_id: Optional[str] = None,
    cwd: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """Direct in-process SDK call (tests / explicit override)."""
    from .cursor_worker import run_turn

    key = api_key or _resolve_api_key()
    if not key:
        return ("Error: Cursor API key not configured.", None)

    payload = {
        "prompt": prompt,
        "api_key": key,
        "model": model or get_ai_specific_default_model("cursor") or "composer-2.5",
        "cwd": cwd or CURSOR_CWD or os.getcwd(),
        "agent_id": agent_id,
        "system_prompt": SYSTEM_PROMPT,
    }
    result = run_turn(payload)
    if result.get("ok"):
        return (str(result.get("text") or ""), result.get("agent_id"))
    err = str(result.get("error") or "Cursor error")
    if err.startswith("Run failed:") and agent_id:
        result = run_turn({**payload, "agent_id": None})
        if result.get("ok"):
            return (str(result.get("text") or ""), result.get("agent_id"))
    return (err, result.get("agent_id") or agent_id)


def ask(
    prompt: str,
    *,
    model: Optional[str] = None,
    agent_id: Optional[str] = None,
    cwd: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """
    Send one turn to a local Cursor agent via the long-lived daemon.

    Set KALIGPT_CURSOR_INPROCESS=1 to skip the daemon (single-shot only).
    """
    key = api_key or _resolve_api_key()
    if not key:
        return ("Error: Cursor API key not configured.", None)

    use_inprocess = os.environ.get("KALIGPT_CURSOR_INPROCESS", "").strip() in (
        "1",
        "true",
        "True",
    )

    with _CURSOR_LOCK:
        try:
            if use_inprocess:
                return ask_inprocess(
                    prompt,
                    model=model,
                    agent_id=agent_id,
                    cwd=cwd,
                    api_key=key,
                )
            return _ask_via_daemon(
                prompt,
                model=model,
                agent_id=agent_id,
                cwd=cwd,
                api_key=key,
            )
        except Exception as exc:
            # One retry after resetting a dead daemon
            msg = str(exc)
            if "daemon exited" in msg.lower() or "failed during startup" in msg.lower():
                global _daemon_proc
                with _daemon_lock:
                    if _daemon_proc is not None:
                        try:
                            _daemon_proc.kill()
                        except Exception:
                            pass
                        _daemon_proc = None
                try:
                    return _ask_via_daemon(
                        prompt,
                        model=model,
                        agent_id=None,
                        cwd=cwd,
                        api_key=key,
                    )
                except Exception as exc2:
                    return (f"Cursor error: {exc2}", None)
            return (f"Cursor error: {exc}", agent_id)


def main(prompt=None):
    from .utils.agent_management import AI_MANAGEMENT_OPTIONS, agent_management
    from .utils.parse_n_print_response import parse_n_print_response

    initialize_configs()
    print(f"㉿ HatsOff ( cursor/{CURSOR_MODEL} )")
    agent_id = None

    while True:
        try:
            if prompt is None:
                prompt = input("\nYou ➤ ")

            if prompt.lower().replace("-", " ").strip() in AI_MANAGEMENT_OPTIONS:
                agent_management(prompt.lower().replace("-", " ").strip())
                initialize_configs()
                prompt = None
                continue

            response, agent_id = ask(
                prompt,
                model=CURSOR_MODEL,
                agent_id=agent_id,
                cwd=CURSOR_CWD,
                api_key=CURSOR_API_KEY,
            )
            parse_n_print_response(response)
            prompt = None

        except KeyboardInterrupt:
            print("\n   Exiting HatsOff. See you later!")
            break
        except Exception as err:
            print(f"\n[!] An error occurred: {err}")
            break


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(" ".join(sys.argv[1:]))
    else:
        main()
