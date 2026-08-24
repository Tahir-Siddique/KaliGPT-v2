"""Persist HatsOff Cursor CLI agent ids so a restart can resume."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_STATE_DIR = Path.home() / ".kaligpt"
_SESSIONS_FILE = _STATE_DIR / "cursor-agent-sessions.json"


def sessions_path() -> Path:
    return _SESSIONS_FILE


def _normalize_cwd(cwd: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(cwd or os.getcwd())))


def load_sessions() -> dict[str, Any]:
    try:
        data = json.loads(_SESSIONS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("last_id", None)
            data.setdefault("sessions", [])
            return data
    except Exception:
        pass
    return {"last_id": None, "sessions": []}


def save_sessions(data: dict[str, Any]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _SESSIONS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def remember_session(agent_id: Optional[str], *, cwd: str, model: str) -> None:
    if not agent_id:
        return
    data = load_sessions()
    data["last_id"] = agent_id
    sessions = [s for s in (data.get("sessions") or []) if s.get("id") != agent_id]
    sessions.insert(
        0,
        {
            "id": agent_id,
            "cwd": _normalize_cwd(cwd),
            "model": model,
            "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        },
    )
    data["sessions"] = sessions[:20]
    save_sessions(data)


def clear_last() -> None:
    data = load_sessions()
    data["last_id"] = None
    save_sessions(data)


def last_id() -> Optional[str]:
    """Most recently used Cursor agent id, if any."""
    value = load_sessions().get("last_id")
    return str(value) if value else None


def last_id_for_cwd(cwd: str) -> Optional[str]:
    """Always the last agent until /new; cwd is ignored on purpose."""
    return last_id()
