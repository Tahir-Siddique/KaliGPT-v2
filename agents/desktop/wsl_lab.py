#!/usr/bin/env python3
"""
WSL lab backend for HatsOff on Windows.

- Prefer Kali WSL; otherwise use any installed Linux distro.
- Install missing apt packages via root inside WSL (no interactive sudo).
- Optionally bootstrap Kali WSL itself (needs Windows elevation once).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from typing import Any, Dict, List, Optional, Tuple

_lock = threading.Lock()
_cache: Dict[str, Any] = {}

# Common "command not found" → Debian/Kali package names
_PKG_MAP = {
    "airodump-ng": "aircrack-ng",
    "aireplay-ng": "aircrack-ng",
    "airmon-ng": "aircrack-ng",
    "aircrack-ng": "aircrack-ng",
    "nmap": "nmap",
    "masscan": "masscan",
    "nikto": "nikto",
    "gobuster": "gobuster",
    "ffuf": "ffuf",
    "sqlmap": "sqlmap",
    "hydra": "hydra",
    "john": "john",
    "hashcat": "hashcat",
    "tcpdump": "tcpdump",
    "tshark": "tshark",
    "wireshark": "wireshark",
    "iw": "iw",
    "iwconfig": "wireless-tools",
    "nmcli": "network-manager",
    "ip": "iproute2",
    "arpspoof": "dsniff",
    "bettercap": "bettercap",
    "crackmapexec": "crackmapexec",
    "netexec": "netexec",
    "impacket-smbclient": "impacket-scripts",
    "impacket-secretsdump": "impacket-scripts",
    "msfconsole": "metasploit-framework",
    "msfvenom": "metasploit-framework",
    "searchsploit": "exploitdb",
    "whatweb": "whatweb",
    "wpscan": "wpscan",
    "enum4linux": "enum4linux",
    "smbclient": "smbclient",
    "responder": "responder",
    "curl": "curl",
    "wget": "wget",
    "git": "git",
    "python3": "python3",
    "pip3": "python3-pip",
}


def _log(msg: str) -> None:
    print(f"[HatsOff/WSL] {msg}", file=sys.stderr, flush=True)


def wsl_exe() -> Optional[str]:
    return shutil.which("wsl")


def list_wsl_distros() -> List[str]:
    wsl = wsl_exe()
    if not wsl:
        return []
    try:
        listed = subprocess.run(
            [wsl, "-l", "-q"],
            capture_output=True,
            text=True,
            timeout=8,
            encoding="utf-8",
            errors="ignore",
        )
        raw = (listed.stdout or "").replace("\x00", "")
        names = []
        for line in raw.splitlines():
            name = line.strip()
            if name and name.lower() not in {"docker-desktop", "docker-desktop-data"}:
                names.append(name)
        return names
    except Exception:
        return []


def pick_wsl_distro(prefer_kali: bool = True) -> Optional[str]:
    """Return best WSL distro for lab commands (Kali preferred)."""
    with _lock:
        cached = _cache.get("distro")
        if cached:
            return cached

    names = list_wsl_distros()
    if not names:
        return None
    chosen = None
    if prefer_kali:
        for n in names:
            if "kali" in n.lower():
                chosen = n
                break
    if not chosen:
        chosen = names[0]
    with _lock:
        _cache["distro"] = chosen
    return chosen


def clear_distro_cache() -> None:
    with _lock:
        _cache.pop("distro", None)


def wsl_available() -> bool:
    return bool(wsl_exe() and list_wsl_distros())


def resolve_wsl_shell() -> Tuple[Optional[str], List[str], Optional[str]]:
    """
    Return (wsl_exe, prefix_argv, distro_name) for bash -lc in WSL.
    prefix runs: wsl -d Distro -- bash -lc '<cmd>'
    """
    wsl = wsl_exe()
    if not wsl:
        return None, [], None
    distro = pick_wsl_distro()
    if not distro:
        return None, [], None
    return wsl, [wsl, "-d", distro, "--", "bash", "-lc"], distro


def run_in_wsl(
    command: str,
    *,
    distro: Optional[str] = None,
    as_root: bool = False,
    timeout: int = 300,
) -> Dict[str, Any]:
    wsl = wsl_exe()
    if not wsl:
        return {
            "ok": False,
            "stdout": "",
            "stderr": "WSL not found",
            "exit_code": None,
        }
    use = distro or pick_wsl_distro()
    if not use:
        return {
            "ok": False,
            "stdout": "",
            "stderr": "No WSL distro installed",
            "exit_code": None,
        }
    argv = [wsl, "-d", use]
    if as_root:
        argv += ["-u", "root"]
    argv += ["--", "bash", "-lc", command]
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=max(10, int(timeout)),
            encoding="utf-8",
            errors="ignore",
        )
        return {
            "ok": completed.returncode == 0,
            "stdout": completed.stdout or "",
            "stderr": completed.stderr or "",
            "exit_code": completed.returncode,
            "distro": use,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "stdout": (exc.stdout or "") if isinstance(exc.stdout, str) else "",
            "stderr": f"Timed out after {timeout}s",
            "exit_code": None,
            "distro": use,
        }
    except Exception as exc:
        return {
            "ok": False,
            "stdout": "",
            "stderr": str(exc),
            "exit_code": None,
            "distro": use,
        }


def missing_commands_from_output(stderr: str, stdout: str = "") -> List[str]:
    text = f"{stderr}\n{stdout}"
    found = []
    patterns = [
        r"(?im)^bash:\s*([^:]+):\s*command not found",
        r"(?im)^([^:\s]+):\s*command not found",
        r"(?im)Command '([^']+)' not found",
        r"(?im)/usr/bin/env:\s*'?([^':\s]+)'?:\s*No such file",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            name = m.group(1).strip().split("/")[-1]
            if name and name not in found:
                found.append(name)
    return found


def packages_for_commands(commands: List[str]) -> List[str]:
    pkgs = []
    for cmd in commands:
        base = cmd.strip().split()[0].split("/")[-1] if cmd.strip() else ""
        pkg = _PKG_MAP.get(base) or _PKG_MAP.get(base.lower())
        if pkg and pkg not in pkgs:
            pkgs.append(pkg)
        elif base and base not in pkgs and re.match(r"^[a-zA-Z0-9][\w.+-]*$", base):
            # best-effort: package often matches binary name on Kali
            pkgs.append(base)
    return pkgs


def ensure_packages(
    packages: List[str],
    *,
    distro: Optional[str] = None,
) -> Dict[str, Any]:
    """Install apt packages as root inside WSL."""
    pkgs = [p for p in packages if p]
    if not pkgs:
        return {"ok": True, "installed": [], "stderr": ""}
    use = distro or pick_wsl_distro()
    joined = " ".join(pkgs)
    _log(f"Installing packages in WSL ({use}) as root: {joined}")
    # DEBIAN_FRONTEND=noninteractive; -y; update then install
    script = (
        "export DEBIAN_FRONTEND=noninteractive; "
        "apt-get update -y >/tmp/hatsoff-apt-update.log 2>&1 || true; "
        f"apt-get install -y {joined}"
    )
    result = run_in_wsl(script, distro=use, as_root=True, timeout=600)
    result["installed"] = pkgs if result.get("ok") else []
    result["attempted"] = pkgs
    if result.get("ok"):
        _log(f"Installed OK: {joined}")
    else:
        _log(f"Install failed: {(result.get('stderr') or '')[:400]}")
    return result


def ensure_command_packages_from_failure(
    command: str,
    *,
    stderr: str = "",
    stdout: str = "",
    distro: Optional[str] = None,
) -> Dict[str, Any]:
    """
    If stderr looks like missing binaries, apt-install mapped packages as root.
    Returns install result; caller should re-run the original command.
    """
    missing = missing_commands_from_output(stderr, stdout)
    # Also peek at first token of the user command
    first = (command or "").strip().split()
    # skip env/sudo/timeout wrappers
    skip = {
        "sudo",
        "env",
        "timeout",
        "nice",
        "ionice",
        "stdbuf",
        "command",
        "time",
        "bash",
        "sh",
    }
    i = 0
    while i < len(first):
        tok = first[i]
        if tok in skip or tok.startswith("-"):
            # timeout 45 ... or timeout -s INT ...
            if tok == "timeout":
                i += 1
                while i < len(first) and (
                    first[i].startswith("-")
                    or first[i].isdigit()
                    or first[i] in {"INT", "KILL", "TERM"}
                ):
                    i += 1
                continue
            i += 1
            continue
        if tok not in missing:
            missing.append(tok.split("/")[-1])
        break

    pkgs = packages_for_commands(missing)
    if not pkgs:
        return {"ok": False, "skipped": True, "missing": missing, "attempted": []}
    return ensure_packages(pkgs, distro=distro)


def ensure_packages_native(packages: List[str]) -> Dict[str, Any]:
    """Install apt packages on native Linux as root (already root or sudo -n)."""
    pkgs = [p for p in packages if p]
    if not pkgs:
        return {"ok": True, "installed": [], "stderr": ""}
    joined = " ".join(pkgs)
    script = (
        "export DEBIAN_FRONTEND=noninteractive; "
        "apt-get update -y >/tmp/hatsoff-apt-update.log 2>&1 || true; "
        f"apt-get install -y {joined}"
    )
    _log(f"Installing packages natively as root: {joined}")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        argv = ["bash", "-lc", script]
    else:
        # Non-interactive sudo; may fail if password required
        argv = ["sudo", "-n", "bash", "-lc", script]
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=600,
            encoding="utf-8",
            errors="ignore",
        )
        ok = completed.returncode == 0
        return {
            "ok": ok,
            "installed": pkgs if ok else [],
            "attempted": pkgs,
            "stdout": completed.stdout or "",
            "stderr": completed.stderr or "",
            "exit_code": completed.returncode,
        }
    except Exception as exc:
        return {
            "ok": False,
            "installed": [],
            "attempted": pkgs,
            "stdout": "",
            "stderr": str(exc),
            "exit_code": None,
        }


def ensure_command_packages_native(
    command: str,
    *,
    stderr: str = "",
    stdout: str = "",
) -> Dict[str, Any]:
    """Native Linux variant of ensure_command_packages_from_failure."""
    missing = missing_commands_from_output(stderr, stdout)
    first = (command or "").strip().split()
    skip = {
        "sudo",
        "env",
        "timeout",
        "nice",
        "ionice",
        "stdbuf",
        "command",
        "time",
        "bash",
        "sh",
    }
    i = 0
    while i < len(first):
        tok = first[i]
        if tok in skip or tok.startswith("-"):
            if tok == "timeout":
                i += 1
                while i < len(first) and (
                    first[i].startswith("-")
                    or first[i].isdigit()
                    or first[i] in {"INT", "KILL", "TERM"}
                ):
                    i += 1
                continue
            i += 1
            continue
        if tok not in missing:
            missing.append(tok.split("/")[-1])
        break
    pkgs = packages_for_commands(missing)
    if not pkgs:
        return {"ok": False, "skipped": True, "missing": missing, "attempted": []}
    return ensure_packages_native(pkgs)


def bootstrap_status() -> Dict[str, Any]:
    wsl = wsl_exe()
    distros = list_wsl_distros() if wsl else []
    distro = pick_wsl_distro() if distros else None
    kali = bool(distro and "kali" in distro.lower())
    return {
        "wsl_present": bool(wsl),
        "distros": distros,
        "active_distro": distro,
        "kali": kali,
        "lab_ready": bool(distro),
        "needs_distro_install": bool(wsl) and not distros,
        "needs_wsl_install": not bool(wsl),
        "prefer_kali": not kali,
    }


def install_kali_wsl(*, elevated: bool = True) -> Dict[str, Any]:
    """
    Install Kali Linux WSL distro.
    Uses an elevated PowerShell window so Windows can grant admin rights.
    """
    if sys.platform != "win32":
        return {"ok": False, "stderr": "Only applicable on Windows"}
    wsl = wsl_exe()
    if not wsl:
        # Enable WSL + install Kali (reboot may be required)
        ps = (
            "Write-Host 'Installing WSL + Kali Linux (admin)...'; "
            "wsl --install -d kali-linux --no-launch; "
            "if ($LASTEXITCODE -ne 0) { wsl --install -d kali-linux }; "
            "Write-Host 'Done. A reboot may be required.'; "
            "Start-Sleep -Seconds 8"
        )
    else:
        # Distro only
        existing = list_wsl_distros()
        if any("kali" in n.lower() for n in existing):
            clear_distro_cache()
            return {"ok": True, "stderr": "", "stdout": "Kali already installed", "distro": pick_wsl_distro()}
        ps = (
            "Write-Host 'Installing Kali Linux in WSL (admin)...'; "
            "wsl --install -d kali-linux --no-launch; "
            "if ($LASTEXITCODE -ne 0) { wsl --install -d kali-linux }; "
            "Write-Host 'Done.'; Start-Sleep -Seconds 5"
        )

    _log("Launching elevated PowerShell to install Kali WSL…")
    try:
        # UAC prompt via RunAs
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                f"Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-Command','{ps.replace(chr(39), chr(39)+chr(39))}'",
            ],
            shell=False,
        )
        clear_distro_cache()
        return {
            "ok": True,
            "stdout": "Elevated installer started. Approve the UAC prompt if shown. Reboot if Windows asks.",
            "stderr": "",
            "pending": True,
        }
    except Exception as exc:
        return {"ok": False, "stderr": str(exc), "stdout": ""}


def ensure_lab_backend() -> Dict[str, Any]:
    """
    Make Windows ready to run lab commands:
    - If WSL+distro exists → ready
    - If WSL missing or no distro → start Kali install (UAC)
    """
    status = bootstrap_status()
    if status["lab_ready"]:
        return {"ok": True, "status": status, "action": "none"}
    if sys.platform != "win32":
        return {"ok": False, "status": status, "action": "unsupported"}
    result = install_kali_wsl()
    status2 = bootstrap_status()
    return {
        "ok": bool(result.get("ok")),
        "status": status2,
        "action": "install_kali_wsl",
        "detail": result,
    }
