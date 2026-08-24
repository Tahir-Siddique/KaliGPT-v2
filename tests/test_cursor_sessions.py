"""Persist Cursor CLI agent ids across restarts."""

from pathlib import Path

from agents import cursor_sessions as sessions


def test_remember_and_resume_ignores_cwd(tmp_path, monkeypatch):
    store = tmp_path / "cursor-agent-sessions.json"
    monkeypatch.setattr(sessions, "_SESSIONS_FILE", store)
    monkeypatch.setattr(sessions, "_STATE_DIR", tmp_path)

    cwd = str(tmp_path / "lab")
    Path(cwd).mkdir()
    sessions.remember_session("agent-one", cwd=cwd, model="composer-2.5")
    assert sessions.last_id() == "agent-one"
    assert sessions.last_id_for_cwd(str(tmp_path / "other")) == "agent-one"


def test_clear_last_stops_auto_resume(tmp_path, monkeypatch):
    store = tmp_path / "cursor-agent-sessions.json"
    monkeypatch.setattr(sessions, "_SESSIONS_FILE", store)
    monkeypatch.setattr(sessions, "_STATE_DIR", tmp_path)

    cwd = str(tmp_path)
    sessions.remember_session("agent-two", cwd=cwd, model="composer-2.5")
    sessions.clear_last()
    assert sessions.last_id() is None
    assert sessions.last_id_for_cwd(cwd) is None
    saved = sessions.load_sessions()["sessions"]
    assert saved and saved[0]["id"] == "agent-two"


def test_cli_resume_commands_recognized():
    from agents.cursor_cli import is_cli_command

    assert is_cli_command("/ls")
    assert is_cli_command("/resume")
    assert is_cli_command("/new")
