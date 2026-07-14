#!/usr/bin/env python3
"""Local command / script runner for HatsOff desktop (Kali Linux friendly)."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from . import provider_router

_MAX_OUTPUT = 32_000
_DEFAULT_TIMEOUT = 120

_PLACEHOLDER_RE = re.compile(
    r"\{\{\s*([a-zA-Z_][\w]*)\s*\}\}|<([a-zA-Z_][\w]*)>|\$\{([a-zA-Z_][\w]*)\}|(YOUR_[A-Z0-9_]+)"
)


def detect_environment() -> Dict[str, Any]:
    """Describe host OS / whether Kali (or WSL Kali) is available."""
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
    wsl_kali = False
    if system == "windows":
        # Prefer `wsl -d kali-linux` when installed
        wsl = shutil.which("wsl")
        if wsl:
            try:
                listed = subprocess.run(
                    [wsl, "-l", "-q"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    encoding="utf-8",
                    errors="ignore",
                )
                names = (listed.stdout or "").lower().replace("\x00", "")
                wsl_kali = "kali" in names
            except Exception:
                wsl_kali = False

    mode = "generic"
    if is_kali:
        mode = "kali-native"
    elif wsl_kali:
        mode = "kali-wsl"
    elif system == "linux":
        mode = "linux"
    elif system == "windows":
        mode = "windows"

    return {
        "system": system,
        "is_kali": is_kali or wsl_kali,
        "is_wsl": is_wsl,
        "wsl_kali": wsl_kali,
        "bash": bash,
        "mode": mode,
        "shell_label": {
            "kali-native": "Kali Linux (/bin/bash)",
            "kali-wsl": "Kali Linux (WSL)",
            "linux": "Linux bash",
            "windows": "Windows shell",
            "generic": "System shell",
        }.get(mode, "System shell"),
    }


def resolve_shell() -> Tuple[Optional[str], List[str]]:
    """
    Return (executable, prefix_argv) for Kali-compatible command execution.

    On Kali/Linux: /bin/bash -lc
    On Windows with kali-linux WSL: wsl -d kali-linux -- bash -lc
    Else: None → subprocess shell=True default
    """
    env = detect_environment()
    if env["mode"] == "kali-native" or (env["system"] == "linux" and env["bash"]):
        bash = env["bash"] or "/bin/bash"
        return bash, [bash, "-lc"]
    if env["mode"] == "kali-wsl":
        wsl = shutil.which("wsl")
        if wsl:
            # Discover exact distro name containing kali
            distro = "kali-linux"
            try:
                listed = subprocess.run(
                    [wsl, "-l", "-q"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    encoding="utf-8",
                    errors="ignore",
                )
                for line in (listed.stdout or "").replace("\x00", "").splitlines():
                    name = line.strip()
                    if "kali" in name.lower():
                        distro = name
                        break
            except Exception:
                pass
            return wsl, [wsl, "-d", distro, "--", "bash", "-lc"]
    if env["system"] != "windows" and env["bash"]:
        return env["bash"], [env["bash"], "-lc"]
    return None, []


def run_command(
    command: str,
    *,
    cwd: Optional[str] = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Run one shell command — prefers Kali/bash when available."""
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
    exe, prefix = resolve_shell()
    env_info = detect_environment()
    try:
        if prefix:
            completed = subprocess.run(
                prefix + [cmd],
                cwd=workdir if env_info["mode"] != "kali-wsl" else None,
                capture_output=True,
                text=True,
                timeout=max(5, int(timeout or _DEFAULT_TIMEOUT)),
            )
        else:
            completed = subprocess.run(
                cmd,
                shell=True,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=max(5, int(timeout or _DEFAULT_TIMEOUT)),
            )
        stdout = (completed.stdout or "")[-_MAX_OUTPUT:]
        stderr = (completed.stderr or "")[-_MAX_OUTPUT:]
        return {
            "ok": completed.returncode == 0,
            "command": cmd,
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": False,
            "cwd": workdir,
            "shell": env_info.get("shell_label"),
        }
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        err = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        return {
            "ok": False,
            "command": cmd,
            "exit_code": None,
            "stdout": str(out)[-_MAX_OUTPUT:],
            "stderr": (str(err) or f"Timed out after {timeout}s")[-_MAX_OUTPUT:],
            "timed_out": True,
            "cwd": workdir,
            "shell": env_info.get("shell_label"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "command": cmd,
            "exit_code": None,
            "stdout": "",
            "stderr": str(exc),
            "timed_out": False,
            "cwd": workdir,
            "shell": env_info.get("shell_label"),
        }


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
        return {"type": "run", "cmd": cmd, "note": "", "ask": "", "input_id": ""}
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
        }
    if not cmd:
        return None
    return {
        "type": "run",
        "cmd": cmd,
        "note": str(item.get("note") or item.get("why") or "").strip(),
        "ask": ask,
        "input_id": "",
        "options": [],
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
) -> Optional[Dict[str, Any]]:
    """
    After a discovery command, ask AI whether the user must choose something
    (e.g. network interface) before later steps.
    """
    rem = remaining_steps[:8]
    out = (last_output or "")[:6000]
    prompt = (
        "You are driving an authorized pentest-lab script runner.\n"
        "A command just finished. Decide if the USER must answer something NOW "
        "before the next steps (choose interface, target from a list, port, yes/no, etc).\n"
        "Prefer asking AFTER discovery commands (ip a, ifconfig, iwconfig, ip -br link, "
        "nmap host list, airmon/iw list, etc), using options taken from the output.\n"
        "If the workflow is Wi‑Fi/monitor-mode related, prefer wireless adapters "
        "(wlan*, wlp*, wl*) — do NOT push Ethernet/VPN uplink ifaces unless the user "
        "clearly needs them.\n"
        "Return ONLY JSON:\n"
        "{\n"
        '  "need_input": true|false,\n'
        '  "id": "iface",\n'
        '  "label": "Which interface?",\n'
        '  "reason": "Next ARP spoof step needs it",\n'
        '  "options": ["eth0", "wlan0"],\n'
        '  "secret": false,\n'
        '  "allow_custom": true\n'
        "}\n"
        "If nothing is needed yet, need_input=false.\n"
        "Do NOT ask for values already provided.\n"
        f"Already known values: {json.dumps(values)}\n"
        f"Last command: {last_cmd}\n"
        f"Last output:\n{out}\n"
        f"Remaining steps: {json.dumps(rem)}\n"
    )
    try:
        raw = _ask_model_text(provider, prompt, model=model, cwd=cwd)
        data = _extract_json_value(raw)
        if not isinstance(data, dict) or not data.get("need_input"):
            return None
        key = str(data.get("id") or data.get("name") or "choice").strip()
        key = re.sub(r"\W+", "_", key).strip("_").lower() or "choice"
        if any(k.lower() == key and str(v).strip() for k, v in values.items()):
            return None
        options = data.get("options") or []
        if not isinstance(options, list):
            options = []
        options = [str(o).strip() for o in options if str(o).strip()][:30]
        return {
            "id": key,
            "label": str(data.get("label") or key).strip(),
            "reason": str(data.get("reason") or "").strip(),
            "options": options,
            "secret": bool(data.get("secret")),
            "allow_custom": bool(data.get("allow_custom", True)),
        }
    except Exception:
        return None


def plan_script_from_text(
    provider: str,
    source_text: str,
    *,
    model: Optional[str] = None,
    cwd: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build an ordered lab script. Prefer discover-then-ask mid-run.
    Do NOT require a big upfront inputs form — use {{placeholders}} and type=ui steps.
    """
    snippet = (source_text or "").strip()
    if len(snippet) > 12000:
        snippet = snippet[:12000]
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
        '    {"type":"ui","input_id":"iface","ask":"Which interface should we use?",'
        '"options":[],"note":"choose after listing"},\n'
        '    {"type":"run","cmd":"sudo arpspoof -i {{iface}} -t {{target}} {{gateway}}",'
        '"note":"spoof — target/gateway asked only when needed"}\n'
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- Prefer mid-run questions (type=ui) AFTER discovery commands, not a big form at start.\n"
        "- Use {{placeholders}} in later run steps; the UI will pause when they are still missing.\n"
        "- After discovery output the runner may call AI again to build dropdown options "
        "(Ethernet/other NICs usually stay up even if a Wi‑Fi iface enters monitor mode).\n"
        "- For Wi‑Fi/monitor-mode work, prefer wireless interfaces in asks — not eth/VPN uplink cards.\n"
        "- For values that cannot be shell commands (choose iface, pick host, password), "
        "use type=ui — those run in the HatsOff UI, not the shell.\n"
        "- Order: recon/list → ask user → exploit/action.\n"
        "- Max 20 steps. One command per run step.\n\n"
        f"TEXT:\n{snippet}\n"
    )
    try:
        raw = _ask_model_text(provider, prompt, model=model, cwd=cwd)
        data = _extract_json_value(raw)
        if isinstance(data, list):
            steps = [s for s in (_normalize_step(x) for x in data) if s]
            return {"inputs": [], "steps": steps, "summary": "Ordered command list"}
        if not isinstance(data, dict):
            raise ValueError("Unexpected plan type")
        steps = [s for s in (_normalize_step(x) for x in (data.get("steps") or [])) if s]
        return {
            "inputs": [],
            "steps": steps,
            "summary": str(data.get("summary") or "").strip(),
        }
    except Exception:
        plan = _fallback_plan(snippet)
        plan["inputs"] = []
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
    rendered_plan = apply_inputs_to_steps(
        [
            {
                "cmd": s.get("cmd") or "",
                "note": s.get("note") or "",
                "ask": s.get("ask") or "",
                "type": s.get("type") or "run",
                "input_id": s.get("input_id") or "",
                "options": s.get("options") or [],
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
        result = run_command(cmd, cwd=cwd, timeout=timeout)
        yield {"type": "step_done", "index": idx, **result}
        if stop_on_error and not result.get("ok"):
            yield {"type": "stopped", "index": idx, "reason": "command failed"}
            break

        # After discovery output, optionally ask the user (iface etc.)
        if (
            analyze_output
            and pause_on_ask
            and provider
            and result.get("ok")
            and idx + 1 < len(steps)
        ):
            combined = ((result.get("stdout") or "") + "\n" + (result.get("stderr") or "")).strip()
            if combined:
                yield {
                    "type": "step_progress",
                    "index": idx,
                    "message": "AI is reading output for the next choice…",
                }
                suggestion = suggest_input_after_output(
                    provider,
                    last_cmd=cmd,
                    last_output=combined,
                    remaining_steps=steps[idx + 1 :],
                    values=known,
                    model=model,
                    cwd=cwd,
                )
                if suggestion:
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
                yield {
                    "type": "step_progress",
                    "index": idx,
                    "message": "",
                    "clear": True,
                }

    yield {"type": "finished", "values": known}
