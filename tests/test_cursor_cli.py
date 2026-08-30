"""CLI chrome for the HatsOff Cursor Agent."""

from agents.cursor_cli import (
    _short_id,
    _short_path,
    is_cli_command,
    is_error_reply,
)


def test_cli_commands_recognized():
    assert is_cli_command("/msf")
    assert is_cli_command("/help")
    assert is_cli_command("/status")
    assert is_cli_command(" /New ")
    assert is_cli_command("/ls")
    assert is_cli_command("/resume")
    assert is_cli_command("/exit")
    assert is_cli_command("/quit")
    assert not is_cli_command("scan the lab")
    assert not is_cli_command("/change model")


def test_error_reply_detection():
    assert is_error_reply("Cursor startup failed: Bridge request failed")
    assert is_error_reply("Error: Cursor API key not configured.")
    assert not is_error_reply("Here is a Kali nmap plan.")


def test_console_width_honors_columns(monkeypatch):
    from agents.utils.parse_n_print_response import get_console_width

    monkeypatch.setenv("COLUMNS", "72")
    assert get_console_width() == 72
    monkeypatch.setenv("COLUMNS", "8")
    assert get_console_width() >= 20


def test_short_helpers():
    assert _short_id(None) == "not started"
    assert "…" in _short_id("a" * 40)
    assert _short_id("short-id") == "short-id"
    home = _short_path("/Users/dev/Desktop/PTP_PARTB/KaliGPT-v2")
    assert home.startswith("~") or "KaliGPT" in home
