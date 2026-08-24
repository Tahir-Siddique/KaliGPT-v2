#!/usr/bin/env python3
"""Local Metasploit Framework helpers for HatsOff / KaliGPT.

Detects msfconsole/msfvenom and searches installed modules.
Does not run exploits or payloads against remote hosts.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any, Optional

_SAFE_QUERY = re.compile(r"^[A-Za-z0-9_./:+* \-]{1,80}$")


def _which(name: str) -> Optional[str]:
    return shutil.which(name)


def _run(argv: list[str], timeout: int = 20) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", f"{argv[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"


def metasploit_status() -> dict[str, Any]:
    """
    Report whether Metasploit Framework is installed on this machine.

    Returns paths for msfconsole / msfvenom and a short version string when
    available. Does not connect to any target.
    """
    console = _which("msfconsole")
    venom = _which("msfvenom")
    version = None
    if console:
        code, out, err = _run([console, "--version"], timeout=25)
        blob = (out or err or "").strip()
        version = blob.splitlines()[0][:200] if blob else None
        if code not in (0, None) and not version:
            version = f"msfconsole exited {code}"
    return {
        "installed": bool(console or venom),
        "msfconsole": console,
        "msfvenom": venom,
        "version": version,
        "hint": (
            None
            if (console or venom)
            else (
                "Metasploit is not on PATH. Install metasploit-framework on Kali "
                "(apt), then restart HatsOff. This helper will not run exploits."
            )
        ),
    }


def metasploit_search(query: str, limit: int = 12) -> dict[str, Any]:
    """
    Search locally installed Metasploit modules by keyword.

    This only queries the local framework catalog (search + exit). It does not
    set RHOSTS, select payloads, or run exploits.
    """
    status = metasploit_status()
    if not status.get("msfconsole"):
        return {
            "ok": False,
            "error": status.get("hint") or "msfconsole is not installed",
            "matches": [],
        }

    raw = (query or "").strip()
    if not raw or not _SAFE_QUERY.match(raw):
        return {
            "ok": False,
            "error": "Query must be a short module keyword (letters, numbers, / . _ -).",
            "matches": [],
        }

    try:
        cap = max(1, min(int(limit), 25))
    except (TypeError, ValueError):
        cap = 12

    console = status["msfconsole"]
    script = f"search {raw}; exit"
    code, out, err = _run([console, "-q", "-x", script], timeout=90)
    text = "\n".join(part for part in (out, err) if part).strip()
    matches: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("msf") or line.lower().startswith("matching"):
            continue
        # Typical catalog line: "0  auxiliary/scanner/ftp/ftp_version  ..."
        parts = line.split()
        for token in parts:
            if "/" in token and not token.startswith("http"):
                matches.append(token)
                break
        if len(matches) >= cap:
            break

    return {
        "ok": code in (0, 124) or bool(matches),
        "query": raw,
        "matches": matches[:cap],
        "note": "Catalog search only — no exploit was run.",
        "raw_tail": text[-1500:] if text else "",
    }
