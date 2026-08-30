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
from typing import IO, Callable, Deque, Optional, Tuple

from .utils.agent_configs import get_api_key, get_ai_specific_default_model
from .hatsoff_cursor import HATSOFF_CURSOR_PROMPT as SYSTEM_PROMPT


CURSOR_API_KEY: Optional[str] = None
CURSOR_MODEL: Optional[str] = None
CURSOR_CWD: str = os.getcwd()
_CURSOR_LOCK = threading.Lock()

_daemon_proc: Optional[subprocess.Popen] = None
_daemon_lock = threading.Lock()
_daemon_stderr: Deque[str] = collections.deque(maxlen=120)
_daemon_stderr_thread: Optional[threading.Thread] = None


def _inprocess_enabled() -> bool:
    return os.environ.get("KALIGPT_CURSOR_INPROCESS", "").strip() in (
        "1",
        "true",
        "True",
    )


def _daemon_pid() -> Optional[int]:
    if _daemon_proc is not None and _daemon_proc.poll() is None:
        return _daemon_proc.pid
    return None


def _startup_bridge() -> tuple[Optional[int], Optional[str]]:
    if _inprocess_enabled():
        return None, None
    try:
        proc = _ensure_daemon()
        return proc.pid, None
    except Exception as exc:
        return None, str(exc)


def _resolve_api_key() -> Optional[str]:
    key = get_api_key("cursor")
    if key and key not in ("CURSOR_API_KEY", "YOUR_CURSOR_API_KEY", ""):
        return key.strip()
    env = os.environ.get("CURSOR_API_KEY", "").strip()
    return env or None


def _workspace_cwd() -> str:
    """Directory the operator launched from — not the HatsOff install path."""
    env = (
        os.environ.get("HATSOFF_WORKSPACE")
        or os.environ.get("HATSOFF_CWD")
        or ""
    ).strip()
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.getcwd()


def initialize_configs() -> None:
    global CURSOR_API_KEY, CURSOR_MODEL, CURSOR_CWD
    try:
        CURSOR_API_KEY = _resolve_api_key()
        CURSOR_MODEL = get_ai_specific_default_model("cursor") or "composer-2.5"
        CURSOR_CWD = _workspace_cwd()

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
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
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
            encoding="utf-8",
            errors="replace",
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


def _read_turn_response(
    stream: IO[str],
    *,
    timeout: float = 900.0,
    on_progress: Optional[Callable[[dict], None]] = None,
) -> dict:
    deadline = time.monotonic() + timeout
    remaining = timeout
    while remaining > 0:
        data = _read_json_line(stream, timeout=remaining)
        remaining = deadline - time.monotonic()
        if data.get("type") == "progress":
            if on_progress:
                on_progress(data)
            continue
        return data
    raise TimeoutError(
        "Timed out waiting for Cursor daemon response.\n" + (_daemon_stderr_tail() or "")
    )


def _finish_turn(
    data: dict,
    *,
    agent_id: Optional[str],
    on_progress: Optional[Callable[[dict], None]] = None,
) -> Tuple[str, Optional[str]]:
    usage = data.get("usage")
    if on_progress and isinstance(usage, dict):
        on_progress({"type": "progress", "kind": "usage", **usage})
    if data.get("ok"):
        return (str(data.get("text") or ""), data.get("agent_id") or agent_id)
    return (str(data.get("error") or "Cursor error"), data.get("agent_id") or agent_id)


def _ask_via_daemon(
    prompt: str,
    *,
    model: Optional[str],
    agent_id: Optional[str],
    cwd: Optional[str],
    api_key: str,
    on_progress: Optional[Callable[[dict], None]] = None,
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
    data = _read_turn_response(
        proc.stdout, timeout=900.0, on_progress=on_progress
    )
    return _finish_turn(data, agent_id=agent_id, on_progress=on_progress)


def ask_inprocess(
    prompt: str,
    *,
    model: Optional[str] = None,
    agent_id: Optional[str] = None,
    cwd: Optional[str] = None,
    api_key: Optional[str] = None,
    on_progress: Optional[Callable[[dict], None]] = None,
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
    result = run_turn(payload, emit=on_progress)
    if result.get("ok"):
        return _finish_turn(result, agent_id=None, on_progress=on_progress)
    return _finish_turn(result, agent_id=agent_id, on_progress=on_progress)


def ask(
    prompt: str,
    *,
    model: Optional[str] = None,
    agent_id: Optional[str] = None,
    cwd: Optional[str] = None,
    api_key: Optional[str] = None,
    on_progress: Optional[Callable[[dict], None]] = None,
) -> Tuple[str, Optional[str]]:
    """
    Send one turn to a local Cursor agent via the long-lived daemon.

    Set KALIGPT_CURSOR_INPROCESS=1 to skip the daemon (single-shot only).
    """
    key = api_key or _resolve_api_key()
    if not key:
        return ("Error: Cursor API key not configured.", None)

    use_inprocess = _inprocess_enabled()

    with _CURSOR_LOCK:
        try:
            if use_inprocess:
                return ask_inprocess(
                    prompt,
                    model=model,
                    agent_id=agent_id,
                    cwd=cwd,
                    api_key=key,
                    on_progress=on_progress,
                )
            return _ask_via_daemon(
                prompt,
                model=model,
                agent_id=agent_id,
                cwd=cwd,
                api_key=key,
                on_progress=on_progress,
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
                        agent_id=agent_id,
                        cwd=cwd,
                        api_key=key,
                        on_progress=on_progress,
                    )
                except Exception as exc2:
                    return (f"Cursor error: {exc2}", None)
            return (f"Cursor error: {exc}", agent_id)


def main(prompt=None):
    from . import cursor_cli as ui
    from .cursor_worker import configure_utf8_stdio
    from .utils.agent_management import AI_MANAGEMENT_OPTIONS, agent_management

    configure_utf8_stdio()
    initialize_configs()
    daemon_pid, daemon_error = _startup_bridge()
    model = CURSOR_MODEL or "composer-2.5"
    cwd = CURSOR_CWD or os.getcwd()
    from . import cursor_sessions as sessions

    agent_id = sessions.last_id()
    resumed = bool(agent_id)

    def toolbar() -> str:
        bits = [
            model,
            f"pid {daemon_pid}" if daemon_pid else ("in-process" if _inprocess_enabled() else "no daemon"),
            _short_toolbar_id(agent_id),
            "Ctrl+C exit",
        ]
        return "  ·  ".join(bits)

    ui.print_startup(
        model=model,
        cwd=cwd,
        inprocess=_inprocess_enabled(),
        daemon_pid=daemon_pid,
        daemon_error=daemon_error,
        agent_id=agent_id,
        resumed=resumed,
    )
    if resumed:
        ui.print_notice(
            "Continuing the previous Cursor agent until you type /new.",
            style="green",
        )

    try:
        session = ui.make_prompt_session(toolbar)
    except Exception:
        session = None

    pending = prompt
    while True:
        try:
            line = ui.read_line(session, initial=pending) if session else (
                pending if pending is not None else input("You › ")
            )
            pending = None
            text = (line or "").strip()
            if not text:
                continue

            cmd = text.lower().replace("-", " ").strip()
            first = cmd.split()[0]

            if first in ui.SLASH_EXIT or cmd in ui.SLASH_EXIT:
                ui.print_notice("Exiting Cursor Agent.", style="green")
                break

            if first == "/help" or cmd == "/help":
                ui.print_help()
                continue

            if first == "/status" or cmd == "/status":
                ui.print_status(
                    model=model,
                    cwd=cwd,
                    inprocess=_inprocess_enabled(),
                    daemon_pid=_daemon_pid() or daemon_pid,
                    agent_id=agent_id,
                )
                continue

            if first == "/msf" or cmd == "/msf":
                ui.print_msf()
                continue

            if first == "/ls" or cmd == "/ls":
                ui.print_sessions(sessions.load_sessions().get("sessions") or [])
                continue

            if first == "/resume" or cmd.startswith("/resume"):
                parts = text.split(maxsplit=1)
                wanted = parts[1].strip() if len(parts) > 1 else sessions.last_id()
                if not wanted:
                    saved = sessions.load_sessions().get("sessions") or []
                    wanted = saved[0].get("id") if saved else None
                if not wanted:
                    ui.print_notice("No saved session to resume. Chat once first.", style="yellow")
                    continue
                agent_id = str(wanted)
                resumed = True
                sessions.remember_session(agent_id, cwd=cwd, model=model)
                ui.print_notice(f"Resuming {agent_id}", style="green")
                continue

            if first == "/new" or cmd == "/new":
                agent_id = None
                resumed = False
                sessions.clear_last()
                ui.print_notice("New Cursor Agent on next message (previous id discarded).")
                continue

            if first == "/clear" or cmd == "/clear":
                ui.console().clear()
                ui.print_startup(
                    model=model,
                    cwd=cwd,
                    inprocess=_inprocess_enabled(),
                    daemon_pid=_daemon_pid() or daemon_pid,
                    daemon_error=None,
                    agent_id=agent_id,
                    resumed=resumed,
                )
                continue

            if cmd in AI_MANAGEMENT_OPTIONS:
                agent_management(cmd)
                initialize_configs()
                model = CURSOR_MODEL or model
                continue

            ui.print_user(text)
            live = ui.TurnLive(ui.console())
            with live:
                response, agent_id = ask(
                    text,
                    model=model,
                    agent_id=agent_id,
                    cwd=cwd,
                    api_key=CURSOR_API_KEY,
                    on_progress=live.handle,
                )
                if response and not ui.is_error_reply(response):
                    live.handle(
                        {
                            "type": "progress",
                            "kind": "assistant",
                            "text": response,
                            "delta": False,
                        }
                    )
            if ui.is_error_reply(response) or not live.saw_assistant:
                ui.print_agent(response, agent_id=agent_id)
            if agent_id and not ui.is_error_reply(response):
                sessions.remember_session(agent_id, cwd=cwd, model=model)
                resumed = True

        except (KeyboardInterrupt, EOFError):
            ui.print_notice("\nExiting Cursor Agent.", style="green")
            break
        except Exception as err:
            ui.print_notice(f"CLI error: {err}", style="red")
            break


def _short_toolbar_id(agent_id: Optional[str]) -> str:
    if not agent_id:
        return "agent —"
    if len(agent_id) <= 18:
        return agent_id
    return agent_id[:8] + "…" + agent_id[-6:]


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(" ".join(sys.argv[1:]))
    else:
        main()
