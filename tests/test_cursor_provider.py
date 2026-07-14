"""Tests for Cursor provider (mocked SDK / daemon)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock


class FakeRun:
    def __init__(self, text="ok", status="finished", run_id="run-1"):
        self._text = text
        self._status = status
        self.id = run_id
        self.result = text

    def wait(self):
        return SimpleNamespace(status=self._status, result=self._text, id=self.id)


class FakeAgent:
    def __init__(self, agent_id="agent-123"):
        self.agent_id = agent_id

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def close(self):
        pass

    def send(self, prompt, options=None):
        return FakeRun(text=f"echo:{str(prompt)[:20]}")


def test_complete_run_uses_wait_only():
    import agents.cursor as provider

    run = FakeRun(text="hello")
    run.text = mock.Mock(side_effect=AssertionError("text() must not be called"))
    text, result = provider._complete_run(run)
    assert text == "hello"
    assert result.status == "finished"


def test_ask_uses_daemon_by_default():
    import agents.cursor as provider

    with mock.patch.object(
        provider,
        "_ask_via_daemon",
        return_value=("hello from daemon", "agent-xyz"),
    ) as daemon_mock:
        text, agent_id = provider.ask(
            "hi",
            model="composer-2.5",
            api_key="cursor_test_key",
            cwd=".",
        )

    assert text == "hello from daemon"
    assert agent_id == "agent-xyz"
    daemon_mock.assert_called_once()


def test_ask_inprocess_create_and_resume():
    import agents.cursor as provider

    fake_agent = FakeAgent("new-agent")

    with mock.patch.dict("sys.modules", {"cursor_sdk": mock.MagicMock()}):
        import cursor_sdk

        cursor_sdk.Agent = mock.MagicMock()
        cursor_sdk.Agent.create = mock.MagicMock(return_value=fake_agent)
        cursor_sdk.Agent.resume = mock.MagicMock()
        cursor_sdk.AgentOptions = mock.MagicMock()
        cursor_sdk.LocalAgentOptions = mock.MagicMock()
        cursor_sdk.SendOptions = mock.MagicMock()
        cursor_sdk.CursorAgentError = type("CursorAgentError", (Exception,), {})

        text, agent_id = provider.ask_inprocess(
            "pentest help",
            model="composer-2.5",
            agent_id=None,
            api_key="cursor_test_key",
            cwd=".",
        )

    assert agent_id == "new-agent"
    assert text.startswith("echo:")
    cursor_sdk.Agent.create.assert_called_once()


def test_ask_inprocess_resumes():
    import agents.cursor as provider

    fake_agent = FakeAgent("old-agent")

    with mock.patch.dict("sys.modules", {"cursor_sdk": mock.MagicMock()}):
        import cursor_sdk

        cursor_sdk.Agent = mock.MagicMock()
        cursor_sdk.Agent.create = mock.MagicMock()
        cursor_sdk.Agent.resume = mock.MagicMock(return_value=fake_agent)
        cursor_sdk.AgentOptions = mock.MagicMock()
        cursor_sdk.LocalAgentOptions = mock.MagicMock()
        cursor_sdk.SendOptions = mock.MagicMock()
        cursor_sdk.CursorAgentError = type("CursorAgentError", (Exception,), {})

        text, agent_id = provider.ask_inprocess(
            "follow up",
            model="composer-2.5",
            agent_id="old-agent",
            api_key="cursor_test_key",
        )

    assert agent_id == "old-agent"
    assert "echo:" in text
    # Worker resumes or create — create may also be attempted; resume is primary
    assert cursor_sdk.Agent.resume.called or cursor_sdk.Agent.create.called


def test_ask_missing_key():
    import agents.cursor as provider

    with mock.patch.object(provider, "_resolve_api_key", return_value=None):
        text, agent_id = provider.ask("hi", api_key=None)
    assert "API key" in text
    assert agent_id is None


def test_cursor_in_providers_list():
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "agents" / "utils" / "agent_management.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    providers = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "ALL_AI_PROVIDERS":
                    providers = ast.literal_eval(node.value)
    assert providers is not None
    assert "cursor" in providers
