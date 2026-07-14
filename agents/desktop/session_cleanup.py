#!/usr/bin/env python3
"""
Session cleanup stack for HatsOff lab runs.

Tracks reversible host changes (monitor mode, NetworkManager stop, etc.)
and restores them when HatsOff exits — even if the window is closed mid-script.
"""

from __future__ import annotations

import atexit
import re
import threading
from typing import Any, Dict, List, Optional

_lock = threading.Lock()
_stack: List[Dict[str, Any]] = []
_ran = False


def pending() -> List[Dict[str, Any]]:
    with _lock:
        return [dict(x) for x in _stack]


def clear() -> None:
    with _lock:
        _stack.clear()


def push(
    *,
    kind: str,
    description: str,
    commands: List[str],
    key: Optional[str] = None,
) -> None:
    """Register cleanup cmds (LIFO). Duplicate keys replace older entries."""
    cmds = [c.strip() for c in commands if str(c).strip()]
    if not cmds:
        return
    entry = {
        "kind": kind,
        "description": description,
        "commands": cmds,
        "key": key or f"{kind}:{cmds[0]}",
    }
    with _lock:
        global _ran
        _ran = False
        if entry["key"]:
            _stack[:] = [e for e in _stack if e.get("key") != entry["key"]]
        _stack.append(entry)


def _infer_from_command(cmd: str) -> Optional[Dict[str, Any]]:
    """Detect common Kali lab mutations and map them to safe reverses."""
    text = (cmd or "").strip()
    if not text:
        return None
    low = text.lower()

    # airmon-ng start wlan0  →  airmon-ng stop wlan0mon (or iface+mon)
    m = re.search(r"\bairmon-ng\s+start\s+(\S+)", text, re.I)
    if m:
        iface = m.group(1).strip()
        mon = iface if iface.endswith("mon") else f"{iface}mon"
        return {
            "kind": "monitor_mode",
            "description": f"Restore {iface} from monitor mode ({mon})",
            "commands": [f"sudo airmon-ng stop {mon}"],
            "key": f"monitor:{iface}",
        }

    # iw / iwconfig → monitor
    m = re.search(
        r"\biw(?:\s+dev)?\s+(\S+)\s+set\s+(?:type\s+)?monitor\b",
        text,
        re.I,
    )
    if m:
        iface = m.group(1).strip()
        return {
            "kind": "monitor_mode",
            "description": f"Set {iface} back to managed mode",
            "commands": [
                f"sudo ip link set {iface} down || true",
                f"sudo iw dev {iface} set type managed || sudo iwconfig {iface} mode managed",
                f"sudo ip link set {iface} up || true",
            ],
            "key": f"monitor:{iface}",
        }

    m = re.search(r"\biwconfig\s+(\S+)\s+mode\s+monitor\b", text, re.I)
    if m:
        iface = m.group(1).strip()
        return {
            "kind": "monitor_mode",
            "description": f"Set {iface} back to managed mode",
            "commands": [
                f"sudo ifconfig {iface} down || true",
                f"sudo iwconfig {iface} mode managed",
                f"sudo ifconfig {iface} up || true",
            ],
            "key": f"monitor:{iface}",
        }

    # create monitor iface: iw phy phy0 interface add wlan0mon type monitor
    m = re.search(
        r"\biw\s+.*\binterface\s+add\s+(\S+)\s+type\s+monitor\b",
        text,
        re.I,
    )
    if m:
        mon = m.group(1).strip()
        return {
            "kind": "monitor_mode",
            "description": f"Remove monitor interface {mon}",
            "commands": [f"sudo iw dev {mon} del || sudo ip link delete {mon}"],
            "key": f"monitor-iface:{mon}",
        }

    # stop NetworkManager (often required before monitor mode)
    if re.search(
        r"\b(systemctl\s+stop\s+(NetworkManager|NetworkManager\.service)|"
        r"service\s+network-manager\s+stop|"
        r"airmon-ng\s+check\s+kill)\b",
        text,
        re.I,
    ):
        return {
            "kind": "network_manager",
            "description": "Restart NetworkManager",
            "commands": [
                "sudo systemctl start NetworkManager || sudo service network-manager start || true"
            ],
            "key": "network_manager",
        }

    # rfkill block wifi — rare but recoverable
    if re.search(r"\brfkill\s+block\s+wifi\b", low):
        return {
            "kind": "rfkill",
            "description": "Unblock Wi‑Fi (rfkill)",
            "commands": ["sudo rfkill unblock wifi || true"],
            "key": "rfkill:wifi",
        }

    return None


def register_from_run(
    cmd: str,
    *,
    ok: bool,
    explicit_cleanup: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    After a successful shell step, register a revert if the command changed
    host networking state — or if the plan provided an explicit cleanup cmd.
    """
    if not ok:
        return None
    if explicit_cleanup and str(explicit_cleanup).strip():
        entry = {
            "kind": "plan_cleanup",
            "description": f"Plan cleanup for: {(cmd or '')[:80]}",
            "commands": [str(explicit_cleanup).strip()],
            "key": f"plan:{(cmd or '')[:60]}",
        }
        push(**entry)
        return entry
    inferred = _infer_from_command(cmd)
    if inferred:
        push(**inferred)
        return inferred
    return None


def run_pending(*, cwd: Optional[str] = None, reason: str = "exit") -> List[Dict[str, Any]]:
    """Execute cleanup stack in reverse order. Safe to call multiple times."""
    global _ran
    with _lock:
        if _ran or not _stack:
            items = []
        else:
            items = list(reversed(_stack))
            _stack.clear()
            _ran = True

    if not items:
        return []

    # Local import avoids circular import at module load
    from .runner import run_command

    results: List[Dict[str, Any]] = []
    print(f"[HatsOff] Reverting lab changes on {reason} ({len(items)} action(s))…")
    for item in items:
        desc = item.get("description") or item.get("kind")
        print(f"  → {desc}")
        for c in item.get("commands") or []:
            res = run_command(c, cwd=cwd, timeout=60)
            results.append(
                {
                    "description": desc,
                    "command": c,
                    "ok": bool(res.get("ok")),
                    "stdout": res.get("stdout") or "",
                    "stderr": res.get("stderr") or "",
                }
            )
            if not res.get("ok"):
                print(f"    [!] cleanup failed: {c}")
                err = (res.get("stderr") or "").strip()
                if err:
                    print(f"        {err[:200]}")
    print("[HatsOff] Cleanup finished.")
    return results


def install_exit_hooks() -> None:
    """Register atexit + common signals so closing HatsOff still reverts."""
    atexit.register(lambda: run_pending(reason="atexit"))

    try:
        import signal

        def _handler(signum, _frame):
            run_pending(reason=f"signal:{signum}")
            raise SystemExit(0)

        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, _handler)
            except Exception:
                pass
    except Exception:
        pass
