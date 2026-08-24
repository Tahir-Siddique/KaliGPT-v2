"""HatsOff-on-Cursor-Agent profile."""

from __future__ import annotations

from unittest import mock


def test_hatsoff_options_are_a_named_cursor_agent():
    from agents.hatsoff_cursor import (
        HATSOFF_AGENT_KEY,
        HATSOFF_AGENT_NAME,
        hatsoff_agent_options,
    )

    opts = hatsoff_agent_options(
        api_key="cursor_test_key",
        model="composer-2.5",
        cwd=".",
        include_kaligpt_tools=True,
    )
    assert opts.name == HATSOFF_AGENT_NAME
    assert opts.model == "composer-2.5"
    assert HATSOFF_AGENT_KEY in (opts.agents or {})
    definition = opts.agents[HATSOFF_AGENT_KEY]
    prompt = getattr(definition, "prompt", "") or definition.get("prompt", "")
    assert "HatsOff" in prompt
    assert "Cursor Agent" in prompt
    tools = getattr(opts.local, "custom_tools", None) or {}
    assert "web_request_analysis" in tools
    assert "search_as_RAG" in tools
    assert "execute_generic_linux_command" not in tools


def test_hatsoff_tool_wrapper_coerces_keyword_list():
    from agents.hatsoff_cursor import _coerce_kwargs
    from agents.utils.tools.opensearchapi import search_as_RAG

    kwargs = _coerce_kwargs(search_as_RAG, {"list_of_keywords": "nmap smb"})
    assert kwargs["list_of_keywords"] == ["nmap smb"]


def test_run_turn_creates_hatsoff_agent(monkeypatch):
    from types import SimpleNamespace

    import agents.cursor_worker as worker

    class FakeRun:
        def wait(self):
            return SimpleNamespace(status="finished", result="hatsoff-ok", id="run-1")

    class FakeAgent:
        agent_id = "agent-hatsoff"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def send(self, prompt, options=None):
            return FakeRun()

    captured = {}

    def fake_create(opts):
        captured["opts"] = opts
        return FakeAgent()

    monkeypatch.setattr(
        worker,
        "_hatsoff_options",
        lambda api_key, model, cwd, include_tools=True: SimpleNamespace(
            name="HatsOff", include_tools=include_tools
        ),
    )

    fake_sdk = mock.MagicMock()
    fake_sdk.Agent.create = fake_create
    fake_sdk.Agent.resume = mock.Mock()
    fake_sdk.CursorAgentError = type("CursorAgentError", (Exception,), {})
    fake_sdk.SendOptions = mock.Mock()
    monkeypatch.setitem(__import__("sys").modules, "cursor_sdk", fake_sdk)

    result = worker.run_turn(
        {
            "prompt": "hi",
            "api_key": "k",
            "model": "composer-2.5",
            "cwd": ".",
            "system_prompt": "HatsOff rules",
        }
    )
    assert result["ok"] is True
    assert result["text"] == "hatsoff-ok"
    assert result["agent_id"] == "agent-hatsoff"
    assert captured["opts"].name == "HatsOff"
