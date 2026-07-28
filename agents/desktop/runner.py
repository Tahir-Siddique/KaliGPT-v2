#!/usr/bin/env python3
"""Local command / script runner for HatsOff desktop (Kali Linux friendly)."""

from __future__ import annotations

import json
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from . import provider_router
from . import session_cleanup

_MAX_OUTPUT = 32_000
_DEFAULT_TIMEOUT = 120
_LOG_SNIP = 1200


def _log(msg: str) -> None:
    """Always print to the HatsOff process console (stderr)."""
    print(f"[HatsOff] {msg}", file=sys.stderr, flush=True)


def _snip(text: str, limit: int = _LOG_SNIP) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[:limit] + f"\n… [{len(t) - limit} more chars]"


def _log_command_result(result: Dict[str, Any]) -> None:
    cmd = result.get("command") or ""
    ok = bool(result.get("ok"))
    code = result.get("exit_code")
    timed_out = bool(result.get("timed_out"))
    applied = result.get("applied_timeout")
    status = "OK" if ok else "FAIL"
    if timed_out:
        status = "TIMEOUT-OK" if ok else "TIMEOUT"
    _log(f"── {status}  exit={code}  limit={applied}s")
    _log(f"$ {cmd}")
    out = (result.get("stdout") or "").strip()
    err = (result.get("stderr") or "").strip()
    if out:
        _log(f"stdout:\n{_snip(out)}")
    if err:
        _log(f"stderr:\n{_snip(err)}")
    if not out and not err and not ok:
        _log("(no stdout/stderr captured)")
    _log("──")

_TIMEOUT_CMD_RE = re.compile(
    r"(?i)\btimeout(?:\s+-(?:s\s+\w+|k\s+\d+|[a-zA-Z]))*\s+(\d+)\s+"
)
_SURVEY_TOOL_RE = re.compile(
    r"(?i)\b(airodump-ng|tcpdump|tshark|dumpcap|bettercap|kismet)\b"
)


def effective_timeout(command: str, fallback: int = _DEFAULT_TIMEOUT) -> int:
    """
    Prefer the GNU `timeout N` inside the command (+ grace) so HatsOff doesn't
    sit another full minute after the survey was supposed to stop.
    """
    cmd = command or ""
    m = _TIMEOUT_CMD_RE.search(cmd)
    if m:
        return max(10, int(m.group(1)) + 20)
    if _SURVEY_TOOL_RE.search(cmd):
        # Captures run forever unless wrapped — keep bounded
        return max(60, min(int(fallback or _DEFAULT_TIMEOUT), 90))
    return max(5, int(fallback or _DEFAULT_TIMEOUT))


def command_succeeded(command: str, *, exit_code: Optional[int], timed_out: bool) -> bool:
    """Survey tools stopped by GNU timeout (124) are a success for HatsOff."""
    if timed_out and _SURVEY_TOOL_RE.search(command or ""):
        # Outer HatsOff timeout on airodump — still usable output maybe
        return True
    if exit_code == 0:
        return True
    # GNU coreutils timeout → 124 when the limit is hit (expected for airodump)
    if exit_code == 124 and _TIMEOUT_CMD_RE.search(command or ""):
        return True
    return False


# Commands that take down Ethernet / all networking — blocked in HatsOff scripts
_BLOCKED_NET_KILL = re.compile(
    r"(?i)("
    r"airmon-ng\s+check\s+kill|"
    r"airmon-ng\s+start\b|"  # often invokes check kill / kills NM
    r"systemctl\s+(stop|disable|mask)\s+"
    r"(NetworkManager|NetworkManager\.service|networking|wpa_supplicant|systemd-networkd)"
    r"|"
    r"service\s+(network-manager|networking|NetworkManager|wpa_supplicant)\s+stop|"
    r"nmcli\s+networking\s+off|"
    r"nmcli\s+radio\s+(all|wifi)\s+off|"
    r"killall\s+(NetworkManager|wpa_supplicant|wpa_cli)|"
    r"pkill\s+.*\b(NetworkManager|wpa_supplicant)\b|"
    r"rfkill\s+block\s+all|"
    r"ip\s+link\s+set\s+(eth\w*|enp\w*|eno\w*|ens\w*|em\w*)\s+down|"
    r"ifconfig\s+(eth\w*|enp\w*|eno\w*|ens\w*)\s+down|"
    r"ifdown\s+(eth\w*|enp\w*|eno\w*|ens\w*)|"
    r"nmcli\s+(device|dev)\s+(disconnect|down)\s+(eth\w*|enp\w*|eno\w*|ens\w*)|"
    r"dhclient\s+-r\s+(eth\w*|enp\w*|eno\w*|ens\w*)"
    r")"
)

# Safe read-only recon run BEFORE the AI plans relevant commands
_PREKNOWLEDGE_COMMANDS: List[Tuple[str, str]] = [
    ("links", "ip -br link 2>/dev/null || ip link"),
    ("addrs", "ip -br addr 2>/dev/null || ip addr"),
    ("routes", "ip route show default 2>/dev/null; ip route | head -n 20"),
    (
        "nm_devices",
        "nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status 2>/dev/null || true",
    ),
    ("iw_dev", "iw dev 2>/dev/null || true"),
    ("wifi", "iwconfig 2>/dev/null | head -n 50 || true"),
]


def command_kills_ethernet(cmd: str) -> Optional[str]:
    """
    Return a reason if this command would knock out eth/internet.

    Used only for **agent auto-run** (script stream / planned steps) so HatsOff
    can keep collecting data without dropping the uplink. Manual single-command
    Run from the UI is intentionally unrestricted.
    """
    text = (cmd or "").strip()
    if not text:
        return None
    if _BLOCKED_NET_KILL.search(text):
        return (
            "Auto-run skipped: this would disconnect internet / stop NetworkManager / "
            "bring Ethernet down. Prefer wifi-only monitor mode "
            "(nmcli device set <wlan> managed no + iw set type monitor). "
            "You can still run this command manually with the Run button if you choose."
        )
    return None


def gather_preknowledge(*, timeout_each: int = 25) -> Dict[str, Any]:
    """
    Run safe, read-only lab recon so the planner can emit relevant commands
    (real ifaces, routes, wifi adapters) without guessing.
    """
    sections: List[str] = []
    raw: Dict[str, str] = {}
    ran: List[str] = []
    _log("Gathering pre-knowledge (safe recon)…")
    for key, cmd in _PREKNOWLEDGE_COMMANDS:
        # Pre-knowledge is always auto-run — never include net-kill cmds
        if command_kills_ethernet(cmd):
            continue
        try:
            result = run_command(cmd, timeout=timeout_each, auto_install=False)
        except Exception as exc:
            raw[key] = f"(failed: {exc})"
            continue
        text = (
            (result.get("stdout") or "")
            + (("\n" + result.get("stderr")) if (result.get("stderr") or "").strip() else "")
        ).strip()
        raw[key] = text[:5000]
        ran.append(cmd)
        if text:
            sections.append(f"### {key}\n$ {cmd}\n{text[:5000]}")
    blob = "\n\n".join(sections).strip()
    _log(f"Pre-knowledge gathered ({len(sections)} sections, {len(blob)} chars)")
    return {
        "ok": bool(sections),
        "text": blob[:14000],
        "sections": raw,
        "commands": ran,
    }


def sanitize_plan_steps(
    steps: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Drop auto-run steps that would disconnect internet (manual Run still allowed)."""
    kept: List[Dict[str, Any]] = []
    blocked: List[Dict[str, str]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        stype = (step.get("type") or "run").lower()
        cmd = (step.get("cmd") or "").strip()
        cleanup = (step.get("cleanup") or "").strip()
        if stype == "run" and cmd:
            reason = command_kills_ethernet(cmd)
            if reason:
                blocked.append({"cmd": cmd, "reason": reason})
                _log(f"Blocked auto-run plan step (internet risk): {cmd}")
                continue
        if cleanup and command_kills_ethernet(cleanup):
            step = {**step, "cleanup": ""}
            _log(f"Stripped unsafe auto-run cleanup from step: {cmd or step.get('ask')}")
        kept.append(step)
    return kept, blocked


_PLACEHOLDER_RE = re.compile(
    r"\{\{\s*([a-zA-Z_][\w]*)\s*\}\}|<([a-zA-Z_][\w]*)>|\$\{([a-zA-Z_][\w]*)\}|(YOUR_[A-Z0-9_]+)"
)

# Paths written by lab tools (airodump -w, nmap -oN, redirects, tee)
_OUTPUT_PATH_RE = re.compile(
    r"(?ix)"
    r"(?:(?:-w|--write|-oN|-oX|-oG|-oA|-o)\s+|tee(?:\s+-a)?\s+|(?:>>|>)\s*)"
    r"['\"]?(/[^\s;|&'\"]+|[A-Za-z]:\\[^\s;|&'\"]+|[\w./-]+\.(?:csv|cap|pcap|txt|log|xml|gnmap|json))"
)

_DISCOVERY_CMD_RE = re.compile(
    r"(?i)\b("
    r"ip|iw|iwconfig|ifconfig|nmcli|airodump-ng|aireplay-ng|airmon-ng|"
    r"nmap|masscan|arp-scan|netdiscover|tcpdump|tshark|cat|ls|iwlist"
    r")\b"
)


def _candidate_log_paths(command: str) -> List[str]:
    """Guess files a command wrote (prefix paths expand to airodump -01.csv etc.)."""
    found: List[str] = []
    for m in _OUTPUT_PATH_RE.finditer(command or ""):
        p = (m.group(1) or "").strip().strip("'\"")
        if p and p not in found:
            found.append(p)
    # Common HatsOff survey prefix even if regex missed flags
    if re.search(r"(?i)airodump-ng", command or ""):
        for p in ("/tmp/hatsoff_survey", "/tmp/hatsoff_survey-01.csv"):
            if p not in found:
                found.append(p)
    expanded: List[str] = []
    for p in found:
        expanded.append(p)
        base = p
        # airodump -w PREFIX → PREFIX-01.csv
        if not re.search(r"\.\w+$", base) or base.endswith(".csv") is False:
            for suf in ("-01.csv", "-01.kismet.csv", "-01.cap", ".csv", ".txt", ".log"):
                if not base.endswith(suf):
                    expanded.append(base + suf)
        # Also try PREFIX-01.csv when path already ends oddly
        if "-01." not in base and not base.endswith(".csv"):
            expanded.append(base + "-01.csv")
    # de-dupe preserve order
    out: List[str] = []
    for p in expanded:
        if p not in out:
            out.append(p)
    return out[:40]


def collect_command_logs(command: str, *, max_chars: int = 14000) -> str:
    """
    Read log/artifact files produced by the last command (via Kali/WSL shell).
    Essential for airodump/nmap which write files instead of useful stdout.
    """
    paths = _candidate_log_paths(command)
    if not paths:
        return ""
    # Prefer newest matching files under /tmp for survey prefix
    shell_script = (
        "paths=("
        + " ".join(shlex_quote(p) for p in paths)
        + "); "
        "for p in \"${paths[@]}\"; do "
        "  if [ -f \"$p\" ]; then "
        "    echo \"===== FILE: $p =====\"; "
        "    # Prefer last 200 lines so large dumps stay useful"
        "    tail -n 200 \"$p\" 2>/dev/null || cat \"$p\" 2>/dev/null; "
        "    echo; "
        "  elif [ -d \"$p\" ]; then "
        "    echo \"===== DIR: $p =====\"; ls -la \"$p\" 2>/dev/null; echo; "
        "  else "
        "    # glob PREFIX* (airodump)"
        "    for g in \"$p\"*; do "
        "      [ -f \"$g\" ] || continue; "
        "      echo \"===== FILE: $g =====\"; "
        "      tail -n 200 \"$g\" 2>/dev/null; echo; "
        "    done; "
        "  fi; "
        "done"
    )
    try:
        # Avoid recursive auto_install when collecting logs
        result = run_command(shell_script, timeout=45, auto_install=False)
        text = ((result.get("stdout") or "") + "\n" + (result.get("stderr") or "")).strip()
        if not text or "===== FILE:" not in text:
            return ""
        if len(text) > max_chars:
            return text[-max_chars:]
        return text
    except Exception as exc:
        _log(f"collect_command_logs failed: {exc}")
        return ""


def shlex_quote(s: str) -> str:
    """POSIX single-quote for embedding paths in bash -lc."""
    return "'" + (s or "").replace("'", "'\"'\"'") + "'"


def build_analysis_log(
    command: str,
    *,
    stdout: str = "",
    stderr: str = "",
    artifacts: Optional[str] = None,
) -> str:
    """Combine stdout/stderr with on-disk command artifacts for AI analysis."""
    parts: List[str] = []
    out = (stdout or "").strip()
    err = (stderr or "").strip()
    if out:
        parts.append("=== STDOUT ===\n" + out)
    if err:
        parts.append("=== STDERR ===\n" + err)
    art = artifacts
    if art is None and (
        _OUTPUT_PATH_RE.search(command or "") or _DISCOVERY_CMD_RE.search(command or "")
    ):
        _log("Reading command log files for AI analysis…")
        art = collect_command_logs(command)
    if art:
        parts.append("=== LOG FILES / ARTIFACTS ===\n" + art)
    combined = "\n\n".join(parts).strip()
    if len(combined) > 16000:
        combined = combined[-16000:]
    return combined



def detect_environment() -> Dict[str, Any]:
    """Describe host OS / whether Kali (or WSL) is available for lab Run."""
    from . import wsl_lab

    system = platform.system().lower()
    release = platform.release()
    version = platform.version()
    is_wsl = False
    if system == "linux":
        try:
            is_wsl = "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower()
        except Exception:
            is_wsl = "microsoft" in version.lower() or "wsl" in release.lower()

    os_release = ""
    is_kali = False
    if system == "linux":
        try:
            os_release = Path("/etc/os-release").read_text(encoding="utf-8", errors="ignore")
            is_kali = "kali" in os_release.lower()
        except Exception:
            pass

    bash = shutil.which("bash")
    wsl_distros: List[str] = []
    wsl_distro: Optional[str] = None
    wsl_kali = False
    wsl_present = False
    if system == "windows":
        wsl_present = bool(wsl_lab.wsl_exe())
        wsl_distros = wsl_lab.list_wsl_distros() if wsl_present else []
        wsl_distro = wsl_lab.pick_wsl_distro() if wsl_distros else None
        wsl_kali = bool(wsl_distro and "kali" in wsl_distro.lower())

    mode = "generic"
    if is_kali:
        mode = "kali-native"
    elif wsl_kali:
        mode = "kali-wsl"
    elif wsl_distro:
        mode = "wsl-linux"
    elif system == "linux":
        mode = "linux"
    elif system == "windows":
        mode = "windows"

    shell_labels = {
        "kali-native": "Kali Linux (/bin/bash)",
        "kali-wsl": f"Kali Linux (WSL: {wsl_distro})",
        "wsl-linux": f"WSL ({wsl_distro})",
        "linux": "Linux bash",
        "windows": "Windows shell",
        "generic": "System shell",
    }

    return {
        "system": system,
        "is_kali": is_kali or wsl_kali,
        "is_wsl": is_wsl or bool(wsl_distro),
        "wsl_present": wsl_present if system == "windows" else is_wsl,
        "wsl_kali": wsl_kali,
        "wsl_distro": wsl_distro,
        "wsl_distros": wsl_distros,
        "lab_ready": bool(
            is_kali
            or (system == "linux" and bash)
            or wsl_distro
        ),
        "bash": bash,
        "mode": mode,
        "shell_label": shell_labels.get(mode, "System shell"),
        # Windows lab Run requires a WSL distro (Kali preferred).
        "run_allowed": bool(
            is_kali
            or (system == "linux" and bash)
            or wsl_distro
        ),
        "bootstrap": wsl_lab.bootstrap_status() if system == "windows" else None,
    }


def resolve_shell() -> Tuple[Optional[str], List[str]]:
    """
    Return (executable, prefix_argv) for lab command execution.

    On Kali/Linux: /bin/bash -lc
    On Windows with any WSL distro (Kali preferred): wsl -d Distro -- bash -lc
    Else: None → subprocess shell=True (Windows cmd)
    """
    from . import wsl_lab

    env = detect_environment()
    if env["mode"] == "kali-native" or (env["system"] == "linux" and env["bash"]):
        bash = env["bash"] or "/bin/bash"
        return bash, [bash, "-lc"]
    if env["mode"] in {"kali-wsl", "wsl-linux"}:
        wsl, prefix, _distro = wsl_lab.resolve_wsl_shell()
        if wsl and prefix:
            return wsl, prefix
    if env["system"] == "windows":
        # Prefer WSL even if cache was empty earlier
        wsl, prefix, _distro = wsl_lab.resolve_wsl_shell()
        if wsl and prefix:
            return wsl, prefix
    if env["system"] != "windows" and env["bash"]:
        return env["bash"], [env["bash"], "-lc"]
    return None, []


def _ensure_missing_packages(
    command: str,
    *,
    stderr: str = "",
    stdout: str = "",
    env_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Install missing tools as root (WSL -u root, or local sudo/root)."""
    from . import wsl_lab

    info = env_info or detect_environment()
    mode = info.get("mode")
    if mode in {"kali-wsl", "wsl-linux"} or (
        info.get("system") == "windows" and info.get("wsl_distro")
    ):
        return wsl_lab.ensure_command_packages_from_failure(
            command,
            stderr=stderr,
            stdout=stdout,
            distro=info.get("wsl_distro"),
        )
    if info.get("system") == "linux":
        return wsl_lab.ensure_command_packages_native(
            command, stderr=stderr, stdout=stdout
        )
    return {"ok": False, "skipped": True, "attempted": []}


def _run_once(
    cmd: str,
    *,
    prefix: List[str],
    workdir: str,
    env_info: Dict[str, Any],
    use_timeout: int,
) -> Dict[str, Any]:
    wsl_cwd = env_info.get("mode") in {"kali-wsl", "wsl-linux"}
    if prefix:
        completed = subprocess.run(
            prefix + [cmd],
            cwd=None if wsl_cwd else workdir,
            capture_output=True,
            text=True,
            timeout=use_timeout,
        )
    else:
        completed = subprocess.run(
            cmd,
            shell=True,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=use_timeout,
        )
    stdout = (completed.stdout or "")[-_MAX_OUTPUT:]
    stderr = (completed.stderr or "")[-_MAX_OUTPUT:]
    code = completed.returncode
    ok = command_succeeded(cmd, exit_code=code, timed_out=False)
    if code == 124 and _TIMEOUT_CMD_RE.search(cmd):
        note = "Stopped after planned timeout (exit 124) — treating as success for survey capture."
        stderr = f"{stderr.rstrip()}\n{note}" if stderr else note
    return {
        "ok": ok,
        "command": cmd,
        "exit_code": code,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": False,
        "cwd": workdir,
        "shell": env_info.get("shell_label"),
        "applied_timeout": use_timeout,
    }


def run_command(
    command: str,
    *,
    cwd: Optional[str] = None,
    timeout: int = _DEFAULT_TIMEOUT,
    auto_install: bool = True,
) -> Dict[str, Any]:
    """Run one shell command — prefers Kali/bash/WSL when available."""
    from . import wsl_lab

    cmd = (command or "").strip()
    if not cmd:
        return {
            "ok": False,
            "command": cmd,
            "exit_code": None,
            "stdout": "",
            "stderr": "Empty command",
            "timed_out": False,
        }

    workdir = cwd or os.getcwd()
    env_info = detect_environment()

    # Windows without WSL: start elevated Kali/WSL install, then stop this run.
    if env_info.get("system") == "windows" and not env_info.get("wsl_distro"):
        _log("No WSL distro — starting elevated lab backend install…")
        boot = wsl_lab.ensure_lab_backend()
        env_info = detect_environment()
        if not env_info.get("wsl_distro"):
            detail = (boot.get("detail") or {}).get("stdout") or (
                "Approve the UAC prompt to install WSL/Kali, then reboot if Windows asks."
            )
            result = {
                "ok": False,
                "command": cmd,
                "exit_code": None,
                "stdout": "",
                "stderr": (
                    "Lab commands on Windows need WSL. "
                    f"{detail}"
                ),
                "timed_out": False,
                "cwd": workdir,
                "shell": env_info.get("shell_label"),
                "bootstrap": boot,
            }
            _log_command_result(result)
            return result

    exe, prefix = resolve_shell()
    use_timeout = effective_timeout(cmd, timeout)
    shell = env_info.get("shell_label") or "shell"
    _log(f"RUN  [{shell}]  timeout≈{use_timeout}s")
    _log(f"$ {cmd}")
    try:
        result = _run_once(
            cmd,
            prefix=prefix,
            workdir=workdir,
            env_info=env_info,
            use_timeout=use_timeout,
        )
        # Missing binary → apt install as root, then retry once
        if (
            auto_install
            and not result.get("ok")
            and not result.get("timed_out")
            and (
                env_info.get("mode") in {"kali-wsl", "wsl-linux", "kali-native", "linux"}
                or env_info.get("wsl_distro")
            )
        ):
            from . import wsl_lab as _wl

            missing = _wl.missing_commands_from_output(
                result.get("stderr") or "", result.get("stdout") or ""
            )
            looks_missing = bool(missing) or (
                result.get("exit_code") in {127, 1}
                and "not found" in (result.get("stderr") or "").lower()
            )
            if looks_missing or missing:
                _log("Command looks missing — attempting root apt install…")
                install = _ensure_missing_packages(
                    cmd,
                    stderr=result.get("stderr") or "",
                    stdout=result.get("stdout") or "",
                    env_info=env_info,
                )
                result["auto_install"] = {
                    "attempted": install.get("attempted") or install.get("installed") or [],
                    "ok": bool(install.get("ok")),
                    "stderr": (install.get("stderr") or "")[-800:],
                }
                if install.get("ok") or (
                    install.get("attempted") and not install.get("skipped")
                ):
                    _log("Retrying command after package install…")
                    retry = _run_once(
                        cmd,
                        prefix=prefix,
                        workdir=workdir,
                        env_info=env_info,
                        use_timeout=use_timeout,
                    )
                    retry["auto_install"] = result["auto_install"]
                    retry["retried_after_install"] = True
                    _log_command_result(retry)
                    return retry
        _log_command_result(result)
        return result
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        err = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        err = (str(err) or f"Timed out after {use_timeout}s")[-_MAX_OUTPUT:]
        ok = command_succeeded(cmd, exit_code=None, timed_out=True)
        if ok:
            err = (
                f"{err.rstrip()}\n"
                f"[survey] Outer timeout after {use_timeout}s — stopping capture; "
                "checking any files written under /tmp."
            )[-_MAX_OUTPUT:]
        result = {
            "ok": ok,
            "command": cmd,
            "exit_code": None,
            "stdout": str(out)[-_MAX_OUTPUT:],
            "stderr": err,
            "timed_out": True,
            "cwd": workdir,
            "shell": env_info.get("shell_label"),
            "applied_timeout": use_timeout,
        }
        _log_command_result(result)
        return result
    except Exception as exc:
        result = {
            "ok": False,
            "command": cmd,
            "exit_code": None,
            "stdout": "",
            "stderr": str(exc),
            "timed_out": False,
            "cwd": workdir,
            "shell": env_info.get("shell_label"),
        }
        _log(f"EXCEPTION while running command: {exc}")
        _log_command_result(result)
        return result


def _ask_model_text(
    provider: str,
    prompt: str,
    *,
    model: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    pieces: List[str] = []
    for event in provider_router.stream_message(
        provider, prompt, [], model=model, cwd=cwd
    ):
        if event.get("type") == "token":
            pieces.append(event.get("text") or "")
        elif event.get("type") == "done":
            if event.get("content"):
                return str(event["content"]).strip()
            break
        elif event.get("type") == "error":
            raise RuntimeError(event.get("error") or "model failed")
    return "".join(pieces).strip()


def _strip_fences(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_json_value(raw: str) -> Any:
    text = _strip_fences(raw)
    obj_match = re.search(r"\{[\s\S]*\}", text)
    arr_match = re.search(r"\[[\s\S]*\]", text)
    # Prefer object if it looks like a plan
    if obj_match and (text.strip().startswith("{") or '"steps"' in text or '"inputs"' in text):
        return json.loads(obj_match.group(0))
    if arr_match:
        return json.loads(arr_match.group(0))
    if obj_match:
        return json.loads(obj_match.group(0))
    raise ValueError("No JSON found in model response")


def _normalize_input(item: Any) -> Optional[Dict[str, Any]]:
    if isinstance(item, str):
        key = re.sub(r"\W+", "_", item.strip()).strip("_").lower() or "value"
        return {
            "id": key,
            "label": item.strip() or key,
            "placeholder": "",
            "required": True,
            "secret": False,
            "reason": "",
            "default": "",
        }
    if not isinstance(item, dict):
        return None
    key = str(item.get("id") or item.get("name") or item.get("key") or "").strip()
    if not key:
        return None
    key = re.sub(r"\W+", "_", key).strip("_").lower()
    return {
        "id": key,
        "label": str(item.get("label") or item.get("name") or key).strip(),
        "placeholder": str(item.get("placeholder") or item.get("example") or "").strip(),
        "required": bool(item.get("required", True)),
        "secret": bool(item.get("secret") or item.get("password")),
        "reason": str(item.get("reason") or item.get("why") or "").strip(),
        "default": str(item.get("default") or "").strip(),
    }


def _normalize_step(item: Any) -> Optional[Dict[str, Any]]:
    if isinstance(item, str):
        cmd = item.strip()
        if not cmd:
            return None
        return {"type": "run", "cmd": cmd, "note": "", "ask": "", "input_id": "", "cleanup": ""}
    if not isinstance(item, dict):
        return None
    step_type = str(item.get("type") or "run").strip().lower()
    if step_type in {"ask", "ask_user", "ui", "choice", "input"}:
        step_type = "ui"
    else:
        step_type = "run"
    cmd = str(item.get("cmd") or item.get("command") or "").strip()
    ask = str(item.get("ask") or item.get("question") or item.get("confirm") or "").strip()
    input_id = str(item.get("input_id") or item.get("id") or "").strip()
    if step_type == "ui":
        if not ask and not cmd:
            return None
        if not input_id:
            input_id = "choice"
        return {
            "type": "ui",
            "cmd": cmd,
            "note": str(item.get("note") or "").strip(),
            "ask": ask or cmd or "Provide a value to continue",
            "input_id": re.sub(r"\W+", "_", input_id).strip("_").lower() or "choice",
            "options": list(item.get("options") or []) if isinstance(item.get("options"), list) else [],
            "cleanup": "",
        }
    if not cmd:
        return None
    cleanup = str(
        item.get("cleanup") or item.get("revert") or item.get("undo") or ""
    ).strip()
    return {
        "type": "run",
        "cmd": cmd,
        "note": str(item.get("note") or item.get("why") or "").strip(),
        "ask": ask,
        "input_id": "",
        "options": [],
        "cleanup": cleanup,
    }


def apply_inputs(template: str, values: Dict[str, str]) -> str:
    """Replace {{id}}, <id>, ${id}, and YOUR_* style tokens."""
    text = template or ""

    def repl_braces(match: re.Match) -> str:
        key = (match.group(1) or match.group(2) or match.group(3) or match.group(4) or "").strip()
        lookup = key.lower() if not key.startswith("YOUR_") else key
        # map YOUR_TARGET -> target
        if lookup.startswith("your_"):
            lookup = lookup[5:].lower()
        for k, v in values.items():
            if k.lower() == lookup.lower() or k.lower() == key.lower():
                return str(v)
        return match.group(0)

    text = _PLACEHOLDER_RE.sub(repl_braces, text)
    # Also direct {{key}} already handled; do explicit pass for values
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", str(v))
        text = text.replace("{{ " + k + " }}", str(v))
        text = text.replace("<" + k + ">", str(v))
        text = text.replace("${" + k + "}", str(v))
    return text


def apply_inputs_to_steps(
    steps: List[Dict[str, Any]], values: Dict[str, str]
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for step in steps:
        out.append(
            {
                **step,
                "cmd": apply_inputs(step.get("cmd") or "", values),
                "note": apply_inputs(step.get("note") or "", values),
                "ask": apply_inputs(step.get("ask") or "", values),
            }
        )
    return out


def _inputs_from_placeholders(commands: List[str]) -> List[Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    for cmd in commands:
        for match in _PLACEHOLDER_RE.finditer(cmd or ""):
            key = match.group(1) or match.group(2) or match.group(3) or match.group(4) or ""
            if key.startswith("YOUR_"):
                key = key[5:].lower()
            key = re.sub(r"\W+", "_", key).strip("_").lower()
            if not key or key in found:
                continue
            found[key] = {
                "id": key,
                "label": key.replace("_", " ").title(),
                "placeholder": "",
                "required": True,
                "secret": key in {"password", "passwd", "secret", "token", "api_key"},
                "reason": f"Referenced in command as placeholder",
                "default": "",
            }
    return list(found.values())


def _fallback_plan(text: str) -> Dict[str, Any]:
    steps = _fallback_extract(text)
    # Keep placeholders this time so the user can fill them
    raw_steps: List[Dict[str, Any]] = []
    for block in re.findall(
        r"```(?:bash|sh|shell|zsh|powershell|ps1|cmd)?\n([\s\S]*?)```", text, flags=re.I
    ):
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            raw_steps.append({"cmd": line, "note": "", "ask": ""})
            if len(raw_steps) >= 20:
                break
    if not raw_steps:
        raw_steps = steps
    inputs = _inputs_from_placeholders([s["cmd"] for s in raw_steps])
    return {"inputs": inputs, "steps": raw_steps, "summary": "Fallback extract from code blocks"}


def _fallback_extract(text: str) -> List[Dict[str, str]]:
    steps: List[Dict[str, str]] = []
    for block in re.findall(
        r"```(?:bash|sh|shell|zsh|powershell|ps1|cmd)?\n([\s\S]*?)```", text, flags=re.I
    ):
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            steps.append({"cmd": line, "note": "", "ask": ""})
            if len(steps) >= 20:
                return steps
    return steps


def unresolved_placeholders(template: str, values: Dict[str, str]) -> List[str]:
    missing: List[str] = []
    for match in _PLACEHOLDER_RE.finditer(template or ""):
        key = match.group(1) or match.group(2) or match.group(3) or match.group(4) or ""
        if key.startswith("YOUR_"):
            key = key[5:].lower()
        key = re.sub(r"\W+", "_", key).strip("_").lower()
        if not key:
            continue
        if not any(k.lower() == key for k, v in values.items() if str(v).strip()):
            if key not in missing:
                missing.append(key)
    return missing


def suggest_input_after_output(
    provider: str,
    *,
    last_cmd: str,
    last_output: str,
    remaining_steps: List[Dict[str, Any]],
    values: Dict[str, str],
    model: Optional[str] = None,
    cwd: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Read command logs/output and decide:
    - extracted_values: clear answers taken from the logs (auto-fill placeholders)
    - ask: optional UI question when the user must choose among candidates
    """
    rem = remaining_steps[:8]
    out = (last_output or "")[:14000]
    prompt = (
        "You are driving an authorized pentest-lab script runner.\n"
        "A command just finished. READ its logs/output carefully (stdout, stderr, "
        "and any LOG FILES / ARTIFACTS such as airodump CSV). Identify answers "
        "for the next steps from those logs — do NOT invent values that are not there.\n\n"
        "Rules:\n"
        "- Fill extracted_values when the logs clearly show the value "
        "(one wireless iface, one BSSID/ESSID the user likely wants, open port, IP, etc.).\n"
        "- If several candidates exist for a key the next steps need, set need_input=true "
        "and put those candidates in options (copied from the logs).\n"
        "- If exactly one sensible candidate exists, put it in extracted_values and "
        "need_input=false (do not bother the user).\n"
        "- For Wi‑Fi/monitor workflows prefer wlan*/wlp*/wl* — NEVER eth*/enp* for monitor mode.\n"
        "- Parse airodump CSV blocks: BSSID, channel, ESSID / Station lines when present.\n"
        "- Do NOT overwrite values already in 'Already known values'.\n"
        "- Keys must match placeholders the remaining steps use "
        "(iface, bssid, essid, channel, target, host, port, …).\n\n"
        "Return ONLY JSON:\n"
        "{\n"
        '  "extracted_values": {"iface": "wlan0", "bssid": "AA:BB:CC:DD:EE:FF"},\n'
        '  "need_input": true|false,\n'
        '  "id": "bssid",\n'
        '  "label": "Which AP / BSSID to target?",\n'
        '  "reason": "Taken from airodump survey CSV",\n'
        '  "options": ["AA:BB:… (HomeWiFi ch6)", "11:22:…"],\n'
        '  "secret": false,\n'
        '  "allow_custom": true,\n'
        '  "finding": "one-line summary of what the logs showed"\n'
        "}\n"
        "If nothing useful is in the logs, extracted_values={} and need_input=false.\n"
        f"Already known values: {json.dumps(values)}\n"
        f"Last command: {last_cmd}\n"
        f"Last logs/output:\n{out}\n"
        f"Remaining steps: {json.dumps(rem)}\n"
    )
    empty: Dict[str, Any] = {
        "extracted_values": {},
        "need_input": False,
        "ask": None,
        "finding": "",
    }
    try:
        raw = _ask_model_text(provider, prompt, model=model, cwd=cwd)
        data = _extract_json_value(raw)
        if not isinstance(data, dict):
            return empty
        extracted_raw = data.get("extracted_values") or data.get("values") or {}
        extracted: Dict[str, str] = {}
        if isinstance(extracted_raw, dict):
            for k, v in extracted_raw.items():
                key = re.sub(r"\W+", "_", str(k)).strip("_").lower()
                val = str(v).strip()
                if not key or not val:
                    continue
                # Don't overwrite known
                if any(
                    existing.lower() == key and str(ev).strip()
                    for existing, ev in values.items()
                ):
                    continue
                # Strip "ESSID (note)" style option labels down to first token when MAC-like
                extracted[key] = val
        finding = str(data.get("finding") or data.get("summary") or "").strip()
        ask = None
        if data.get("need_input"):
            key = str(data.get("id") or data.get("name") or "choice").strip()
            key = re.sub(r"\W+", "_", key).strip("_").lower() or "choice"
            if not any(k.lower() == key and str(v).strip() for k, v in values.items()):
                if key not in extracted or len(data.get("options") or []) > 1:
                    options = data.get("options") or []
                    if not isinstance(options, list):
                        options = []
                    options = [str(o).strip() for o in options if str(o).strip()][:30]
                    ask = {
                        "id": key,
                        "label": str(data.get("label") or key).strip(),
                        "reason": str(data.get("reason") or finding or "").strip(),
                        "options": options,
                        "secret": bool(data.get("secret")),
                        "allow_custom": bool(data.get("allow_custom", True)),
                    }
        return {
            "extracted_values": extracted,
            "need_input": bool(ask),
            "ask": ask,
            "finding": finding,
        }
    except Exception as exc:
        _log(f"suggest_input_after_output failed: {exc}")
        return empty


def plan_script_from_text(
    provider: str,
    source_text: str,
    *,
    model: Optional[str] = None,
    cwd: Optional[str] = None,
    preknowledge: Optional[Dict[str, Any]] = None,
    gather_facts: bool = True,
) -> Dict[str, Any]:
    """
    Build an ordered lab script. Prefer discover-then-ask mid-run.
    Optionally gather safe pre-knowledge (ifaces/routes/wifi) first so commands
    match the real lab — never plan internet-killing commands.
    """
    snippet = (source_text or "").strip()
    if len(snippet) > 12000:
        snippet = snippet[:12000]

    prek = preknowledge
    if gather_facts and prek is None:
        try:
            prek = gather_preknowledge()
        except Exception as exc:
            _log(f"preknowledge failed: {exc}")
            prek = {"ok": False, "text": "", "sections": {}, "commands": []}

    prek_block = ""
    if prek and (prek.get("text") or "").strip():
        prek_block = (
            "\n\nLAB PRE-KNOWLEDGE (already gathered with safe read-only commands — "
            "use these facts when choosing ifaces/targets; skip redundant identical "
            "list commands unless a later step needs a refresh):\n"
            f"{prek.get('text')}\n"
        )

    prompt = (
        "Prepare an authorized **Kali Linux** pentest-lab script that gathers facts THEN asks the user.\n"
        "Commands must be Kali-compatible bash one-liners (nmap, ip, iwconfig, msfconsole,\n"
        "msfvenom, crackmapexec/netexec, impacket-*, gobuster, etc.). Prefer tools that\n"
        "ship with Kali. Use `sudo` when needed. Avoid Windows-only PowerShell unless asked.\n"
        "Return ONLY JSON:\n"
        "{\n"
        '  "summary": "one sentence",\n'
        '  "steps": [\n'
        '    {"type":"run","cmd":"ip -br link","note":"list interfaces"},\n'
        '    {"type":"ui","input_id":"iface","ask":"Which wireless interface for monitor mode?",'
        '"options":[],"note":"wifi only — keep eth for internet"},\n'
        '    {"type":"run","cmd":"sudo nmcli device set {{iface}} managed no; '
        'sudo ip link set {{iface}} down; sudo iw dev {{iface}} set type monitor; '
        'sudo ip link set {{iface}} up","note":"monitor mode on WIFI only — do not touch eth",'
        '"cleanup":"sudo ip link set {{iface}} down; sudo iw dev {{iface}} set type managed; '
        'sudo ip link set {{iface}} up; sudo nmcli device set {{iface}} managed yes"},\n'
        '    {"type":"run","cmd":"sudo timeout -s INT -k 5 30 airodump-ng -w /tmp/hatsoff_survey '
        '--output-format csv {{iface}}","note":"wifi survey — intentional stop after 30s",'
        '"cleanup":""}\n'
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- Prefer mid-run questions (type=ui) AFTER discovery commands, not a big form at start.\n"
        "- Use {{placeholders}} in later run steps; the UI will pause when they are still missing.\n"
        "- After discovery output the runner reads stdout AND log files (airodump CSV, nmap -o*, …),\n"
        "  then AI extracts answers (iface/BSSID/ESSID/…) or asks with options taken from those logs.\n"
        "- **AUTO-RUN ONLY — keep Ethernet/internet up** while HatsOff collects data.\n"
        "  Do NOT put these in the auto-run `steps` list:\n"
        "  `airmon-ng check kill`, `airmon-ng start`, `systemctl stop NetworkManager`,\n"
        "  `service network-manager stop`, `nmcli networking off`, `rfkill block all`,\n"
        "  `ip link set eth*/enp* down`, killall NetworkManager/wpa_supplicant.\n"
        "  Prefer wifi-only monitor mode so auto-run can finish and show results.\n"
        "  (You may still *mention* those commands in notes for the operator to run "
        "manually — just do not include them as type=run auto steps.)\n"
        "- For monitor mode: ONLY the chosen wireless iface. Prefer:\n"
        "  `nmcli device set <wlan> managed no` + `iw dev <wlan> set type monitor` "
        "(or equivalent). Prefer that over airmon-ng for auto-run.\n"
        "- Never choose eth*/enp*/wired for monitor mode. Options should be wlan*/wlp*/wl* only "
        "for Wi‑Fi tasks.\n"
        "- If LAB PRE-KNOWLEDGE is present, tailor commands to those real interfaces/IPs "
        "and prefer wifi adapters listed there for wireless work.\n"
        "- Long-running captures (`airodump-ng`, `tcpdump`, …) MUST be wrapped as:\n"
        "  `sudo timeout -s INT -k 5 30 <cmd>` (30–45s is enough for a lab survey).\n"
        "  Exit code 124 from timeout is expected success — do not add a follow-up that "
        "assumes airodump runs forever.\n"
        "- When a step changes host state, set `cleanup` to reverse ONLY that wifi iface "
        "(managed yes + type managed). Do not restart/stop NM as part of monitor setup.\n"
        "- For values that cannot be shell commands (choose iface, pick host, password), "
        "use type=ui — those run in the HatsOff UI, not the shell.\n"
        "- Order: recon/list → ask user → exploit/action.\n"
        "- Max 20 steps. One command per run step.\n"
        f"{prek_block}\n"
        f"TEXT:\n{snippet}\n"
    )
    try:
        raw = _ask_model_text(provider, prompt, model=model, cwd=cwd)
        data = _extract_json_value(raw)
        if isinstance(data, list):
            steps = [s for s in (_normalize_step(x) for x in data) if s]
            steps, blocked = sanitize_plan_steps(steps)
            return {
                "inputs": [],
                "steps": steps,
                "summary": "Ordered command list",
                "preknowledge": prek,
                "blocked_steps": blocked,
            }
        if not isinstance(data, dict):
            raise ValueError("Unexpected plan type")
        steps = [s for s in (_normalize_step(x) for x in (data.get("steps") or [])) if s]
        steps, blocked = sanitize_plan_steps(steps)
        return {
            "inputs": [],
            "steps": steps,
            "summary": str(data.get("summary") or "").strip(),
            "preknowledge": prek,
            "blocked_steps": blocked,
        }
    except Exception:
        plan = _fallback_plan(snippet)
        plan["inputs"] = []
        steps, blocked = sanitize_plan_steps(plan.get("steps") or [])
        plan["steps"] = steps
        plan["preknowledge"] = prek
        plan["blocked_steps"] = blocked
        return plan


def prepare_single_command(
    provider: str,
    command: str,
    *,
    model: Optional[str] = None,
    cwd: Optional[str] = None,
) -> Dict[str, Any]:
    """Detect whether a single command needs user inputs before running."""
    cmd = (command or "").strip()
    auto = _inputs_from_placeholders([cmd])
    if auto:
        return {
            "command": cmd,
            "inputs": auto,
            "needs_input": True,
            "ask": "",
            "summary": "Fill placeholders before running",
        }
    # If fully concrete, just run — don't force a pre-form
    return {"command": cmd, "inputs": [], "needs_input": False, "ask": "", "summary": ""}


# Back-compat alias used by older tests/callers
def plan_commands_from_text(
    provider: str,
    source_text: str,
    *,
    model: Optional[str] = None,
    cwd: Optional[str] = None,
) -> List[Dict[str, str]]:
    plan = plan_script_from_text(provider, source_text, model=model, cwd=cwd)
    return [{"cmd": s.get("cmd") or "", "note": s.get("note") or ""} for s in plan.get("steps") or []]


def run_script_stream(
    steps: List[Dict[str, Any]],
    *,
    cwd: Optional[str] = None,
    timeout: int = _DEFAULT_TIMEOUT,
    stop_on_error: bool = True,
    pause_on_ask: bool = True,
    values: Optional[Dict[str, str]] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    analyze_output: bool = True,
) -> Iterator[Dict[str, Any]]:
    """
    Run steps with mid-run UI pauses:

    - type=ui → need_input (question in UI)
    - unresolved {{placeholders}} → need_input for those ids
    - after a successful run, optionally AI suggests a choice from output
      (keeps working when Ethernet stays up while a Wi‑Fi iface is in monitor mode)
    """
    known = {str(k): str(v) for k, v in (values or {}).items()}
    # Never execute internet-killing steps even if the model slipped them in
    steps, blocked_upfront = sanitize_plan_steps(
        [
            {
                "cmd": s.get("cmd") or "",
                "note": s.get("note") or "",
                "ask": s.get("ask") or "",
                "type": s.get("type") or "run",
                "input_id": s.get("input_id") or "",
                "options": s.get("options") or [],
                "cleanup": s.get("cleanup") or "",
            }
            for s in steps
        ]
    )
    if blocked_upfront:
        yield {
            "type": "blocked_steps",
            "steps": blocked_upfront,
            "message": (
                f"Auto-run skipped {len(blocked_upfront)} step(s) that would "
                "disconnect internet (manual Run still allowed)."
            ),
        }
    rendered_plan = apply_inputs_to_steps(
        [
            {
                "cmd": s.get("cmd") or "",
                "note": s.get("note") or "",
                "ask": s.get("ask") or "",
                "type": s.get("type") or "run",
                "input_id": s.get("input_id") or "",
                "options": s.get("options") or [],
                "cleanup": s.get("cleanup") or "",
            }
            for s in steps
        ],
        known,
    )
    # Preserve type/input_id/options through apply (ask/cmd/note only are substituted)
    for i, s in enumerate(steps):
        rendered_plan[i]["type"] = s.get("type") or "run"
        rendered_plan[i]["input_id"] = s.get("input_id") or ""
        rendered_plan[i]["options"] = s.get("options") or []
        rendered_plan[i]["cleanup"] = apply_inputs(s.get("cleanup") or "", known)

    yield {"type": "plan", "steps": rendered_plan, "values": known}

    for idx, step in enumerate(steps):
        step_type = (step.get("type") or "run").lower()
        cmd_tmpl = step.get("cmd") or ""
        ask_tmpl = step.get("ask") or ""
        cmd = apply_inputs(cmd_tmpl, known)
        ask = apply_inputs(ask_tmpl, known)
        note = apply_inputs(step.get("note") or "", known)

        if pause_on_ask and step_type == "ui":
            yield {
                "type": "need_input",
                "index": idx,
                "id": step.get("input_id") or "choice",
                "label": ask or "Provide a value",
                "reason": note,
                "options": list(step.get("options") or []),
                "secret": False,
                "allow_custom": True,
                "cmd": cmd,
                "remaining": steps[idx:],
                "values": known,
            }
            return

        missing = unresolved_placeholders(cmd_tmpl, known) if step_type == "run" else []
        # also check ask fields with placeholders for confirm-only? skip
        if pause_on_ask and missing:
            mid = missing[0]
            yield {
                "type": "need_input",
                "index": idx,
                "id": mid,
                "label": mid.replace("_", " ").title(),
                "reason": f"Needed for: {cmd_tmpl}",
                "options": [],
                "secret": mid in {"password", "passwd", "secret", "token", "api_key"},
                "allow_custom": True,
                "cmd": cmd_tmpl,
                "remaining": steps[idx:],
                "values": known,
            }
            return

        if pause_on_ask and ask and step_type == "run":
            # Destructive confirm as yes/no UI, not shell
            yield {
                "type": "need_input",
                "index": idx,
                "id": f"confirm_{idx}",
                "label": ask,
                "reason": note or cmd,
                "options": ["yes", "no"],
                "secret": False,
                "allow_custom": False,
                "cmd": cmd,
                "remaining": steps[idx:],
                "values": known,
                "confirm_continue": True,
            }
            return

        yield {
            "type": "step_start",
            "index": idx,
            "cmd": cmd,
            "note": note,
            "message": "Running command…",
        }
        blocked = command_kills_ethernet(cmd)
        if blocked:
            _log(f"BLOCKED step {idx + 1}: {blocked}")
            result = {
                "ok": False,
                "command": cmd,
                "exit_code": None,
                "stdout": "",
                "stderr": blocked,
                "timed_out": False,
                "cwd": cwd or os.getcwd(),
            }
            yield {"type": "step_done", "index": idx, **result}
            yield {"type": "stopped", "index": idx, "reason": "blocked_ethernet_kill"}
            break
        _log(f"step {idx + 1}/{len(steps)} starting…")
        result = run_command(cmd, cwd=cwd, timeout=timeout)
        auto = result.get("auto_install") or {}
        if auto.get("attempted"):
            pkgs = ", ".join(auto.get("attempted") or [])
            status = "OK" if auto.get("ok") else "failed"
            yield {
                "type": "step_progress",
                "index": idx,
                "message": f"Auto-installed packages as root ({status}): {pkgs}",
            }
        # Pull on-disk logs (airodump CSV, nmap -oN, …) so UI + AI can read them
        file_logs = ""
        if result.get("ok") and (
            _OUTPUT_PATH_RE.search(cmd) or re.search(r"(?i)airodump-ng|nmap\s+.*-o", cmd)
        ):
            yield {
                "type": "step_progress",
                "index": idx,
                "message": "Reading command log files…",
            }
            file_logs = collect_command_logs(cmd)
            if file_logs:
                result = {**result, "log_files": file_logs}
        yield {"type": "step_done", "index": idx, **result}
        if result.get("ok"):
            cleanup_cmd = apply_inputs(step.get("cleanup") or "", known)
            registered = session_cleanup.register_from_run(
                cmd, ok=True, explicit_cleanup=cleanup_cmd or None
            )
            if registered:
                yield {
                    "type": "cleanup_registered",
                    "index": idx,
                    "description": registered.get("description"),
                    "commands": registered.get("commands") or [],
                }
        if stop_on_error and not result.get("ok"):
            yield {"type": "stopped", "index": idx, "reason": "command failed"}
            break

        # After discovery: read stdout + on-disk logs, extract answers, ask only if needed
        if (
            analyze_output
            and pause_on_ask
            and provider
            and result.get("ok")
            and idx + 1 < len(steps)
        ):
            combined = build_analysis_log(
                cmd,
                stdout=result.get("stdout") or "",
                stderr=result.get("stderr") or "",
                artifacts=file_logs or result.get("log_files") or None,
            )
            looks_discovery = bool(_DISCOVERY_CMD_RE.search(cmd))
            has_files = bool(file_logs) or "=== LOG FILES" in combined
            if combined or looks_discovery:
                if not combined:
                    combined = (
                        "(no stdout/stderr and no log files found — "
                        "still check if a choice is needed from remaining steps)"
                    )
                msg = (
                    "AI is reading command logs for the next answer…"
                    if has_files
                    else "AI is reading output for the next choice…"
                )
                yield {
                    "type": "step_progress",
                    "index": idx,
                    "message": msg,
                }
                _log(f"AI analyze after step {idx + 1} (log chars={len(combined)})…")
                out_q: "queue.Queue[Tuple[str, Any]]" = queue.Queue()

                def _analyze():
                    try:
                        out_q.put(
                            (
                                "ok",
                                suggest_input_after_output(
                                    provider,
                                    last_cmd=cmd,
                                    last_output=combined,
                                    remaining_steps=steps[idx + 1 :],
                                    values=known,
                                    model=model,
                                    cwd=cwd,
                                ),
                            )
                        )
                    except Exception as exc:
                        out_q.put(("err", exc))

                worker = threading.Thread(target=_analyze, daemon=True)
                worker.start()
                tick = 0
                while worker.is_alive():
                    worker.join(timeout=1.5)
                    if worker.is_alive():
                        tick += 1
                        yield {
                            "type": "step_progress",
                            "index": idx,
                            "message": msg,
                            "heartbeat": True,
                            "tick": tick,
                        }
                        yield {"type": "keepalive"}
                kind, payload = out_q.get()
                if kind == "err":
                    _log(f"AI analyze error: {payload}")
                    yield {
                        "type": "step_progress",
                        "index": idx,
                        "message": f"AI analyze skipped: {payload}",
                        "clear": False,
                    }
                    analysis = None
                else:
                    analysis = payload

                if isinstance(analysis, dict):
                    extracted = analysis.get("extracted_values") or {}
                    if isinstance(extracted, dict) and extracted:
                        known.update({str(k): str(v) for k, v in extracted.items()})
                        bits = ", ".join(f"{k}={v}" for k, v in extracted.items())
                        finding = analysis.get("finding") or ""
                        note = f"From logs: {bits}"
                        if finding:
                            note = f"{finding} · {bits}"
                        _log(f"AI extracted from logs: {bits}")
                        yield {
                            "type": "values_from_logs",
                            "index": idx,
                            "values": extracted,
                            "finding": finding,
                            "message": note,
                        }
                        yield {
                            "type": "step_progress",
                            "index": idx,
                            "message": note,
                            "clear": False,
                        }
                    suggestion = analysis.get("ask") if analysis.get("need_input") else None
                    if suggestion:
                        _log(
                            f"AI ask → id={suggestion.get('id')} "
                            f"options={len(suggestion.get('options') or [])} "
                            f"label={suggestion.get('label')}"
                        )
                        yield {
                            "type": "need_input",
                            "index": idx,
                            "after_step": idx,
                            "id": suggestion["id"],
                            "label": suggestion["label"],
                            "reason": suggestion.get("reason") or "",
                            "options": suggestion.get("options") or [],
                            "secret": bool(suggestion.get("secret")),
                            "allow_custom": bool(suggestion.get("allow_custom", True)),
                            "cmd": cmd,
                            "remaining": steps[idx + 1 :],
                            "values": known,
                        }
                        return
                    if not extracted:
                        _log("AI analyze → no values extracted, no input needed")
                yield {
                    "type": "step_progress",
                    "index": idx,
                    "message": "",
                    "clear": True,
                }

    cleanup_results = session_cleanup.run_pending(cwd=cwd, reason="script_finished")
    yield {
        "type": "finished",
        "values": known,
        "cleanup_pending": session_cleanup.pending(),
        "cleanup_results": cleanup_results,
    }
