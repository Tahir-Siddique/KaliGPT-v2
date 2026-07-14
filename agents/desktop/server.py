#!/usr/bin/env python3
"""Local Flask API for HatsOff desktop chat."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

from ..utils.agent_configs import (
    get_ai_specific_default_model,
    get_available_ais,
    get_default_provider,
    get_settings,
    get_vendor_specific_all_models,
    update_default_provider,
    update_provider_settings,
)
from .chat_store import ChatStore
from . import provider_router
from . import runner as command_runner

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(store: Optional[ChatStore] = None) -> Flask:
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    chat_store = store or ChatStore()

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/api/health")
    def health():
        env = command_runner.detect_environment()
        return jsonify(
            {
                "ok": True,
                "store": str(chat_store.db_path),
                "environment": env,
            }
        )

    @app.get("/api/environment")
    def environment():
        return jsonify(command_runner.detect_environment())

    @app.get("/api/providers")
    def providers():
        names = get_available_ais()
        # Prefer known desktop-capable order; include any extras from config
        preferred = ["gemini", "chatgpt", "ollama", "openrouter", "litellm", "cursor"]
        ordered = [p for p in preferred if p in names] + [
            p for p in names if p not in preferred
        ]
        payload = []
        for name in ordered:
            payload.append(
                {
                    "id": name,
                    "default_model": get_ai_specific_default_model(name),
                    "models": get_vendor_specific_all_models(name),
                }
            )
        return jsonify(
            {
                "providers": payload,
                "default_provider": get_default_provider(),
            }
        )

    @app.get("/api/conversations")
    def list_conversations():
        q = request.args.get("q")
        return jsonify({"conversations": chat_store.list_conversations(q)})

    @app.post("/api/conversations")
    def create_conversation():
        body = request.get_json(silent=True) or {}
        provider = (body.get("provider") or get_default_provider() or "gemini").strip()
        model = (
            body.get("model")
            or get_ai_specific_default_model(provider)
            or "default"
        )
        title = (body.get("title") or "New chat").strip() or "New chat"
        conv = chat_store.create_conversation(provider=provider, model=model, title=title)
        return jsonify(conv), 201

    @app.get("/api/conversations/<conversation_id>")
    def get_conversation(conversation_id: str):
        conv = chat_store.get_conversation(conversation_id)
        if not conv:
            return jsonify({"error": "Not found"}), 404
        return jsonify(conv)

    @app.patch("/api/conversations/<conversation_id>")
    def patch_conversation(conversation_id: str):
        body = request.get_json(silent=True) or {}
        if "title" in body:
            conv = chat_store.rename_conversation(conversation_id, str(body["title"]))
        else:
            conv = chat_store.update_conversation_meta(
                conversation_id,
                provider=body.get("provider"),
                model=body.get("model"),
                cursor_agent_id=body.get("cursor_agent_id"),
            )
        if not conv:
            return jsonify({"error": "Not found"}), 404
        return jsonify(conv)

    @app.delete("/api/conversations/<conversation_id>")
    def delete_conversation(conversation_id: str):
        if not chat_store.delete_conversation(conversation_id):
            return jsonify({"error": "Not found"}), 404
        return jsonify({"ok": True})

    @app.post("/api/conversations/<conversation_id>/messages")
    def post_message(conversation_id: str):
        conv = chat_store.get_conversation(conversation_id)
        if not conv:
            return jsonify({"error": "Not found"}), 404

        body = request.get_json(silent=True) or {}
        content = (body.get("content") or "").strip()
        if not content:
            return jsonify({"error": "content is required"}), 400

        provider = (body.get("provider") or conv["provider"]).strip()
        model = body.get("model") or conv["model"]
        cwd = body.get("cwd") or os.getcwd()

        # Persist provider/model selection on the conversation
        chat_store.update_conversation_meta(
            conversation_id, provider=provider, model=model
        )

        prior = chat_store.get_messages(conversation_id)
        meta = conv.get("metadata") or {}
        # Title generation: first user turn only (never again once locked)
        needs_ai_title = (
            len(prior) == 0
            and not meta.get("title_generated")
            and (conv.get("title") or "New chat") in ("New chat", "")
        )
        user_msg = chat_store.append_message(conversation_id, "user", content)

        try:
            result = provider_router.send_message(
                provider,
                content,
                prior,
                model=model,
                cursor_agent_id=conv.get("cursor_agent_id"),
                cwd=cwd,
            )
            assistant_text = result.get("content") or ""
            new_agent_id = result.get("cursor_agent_id")
            if new_agent_id:
                chat_store.update_conversation_meta(
                    conversation_id, cursor_agent_id=new_agent_id
                )
        except Exception as exc:
            assistant_text = f"Error: {exc}"

        assistant_msg = chat_store.append_message(
            conversation_id, "assistant", assistant_text
        )

        # One-shot AI title after the first successful reply only
        if needs_ai_title and assistant_text and not str(assistant_text).lower().startswith(
            ("error:", "cursor error", "cursor startup")
        ):
            try:
                title = provider_router.generate_chat_title(
                    provider,
                    content,
                    assistant_text,
                    model=model,
                    cwd=cwd,
                )
                chat_store.rename_conversation(conversation_id, title)
            except Exception:
                chat_store.rename_conversation(
                    conversation_id,
                    content[:60] + ("…" if len(content) > 60 else ""),
                )
            # Lock so later messages never trigger another title call
            chat_store.update_conversation_meta(
                conversation_id, metadata_update={"title_generated": True}
            )
        elif needs_ai_title:
            # Even on failed first replies, don't keep retrying title forever
            chat_store.update_conversation_meta(
                conversation_id, metadata_update={"title_generated": True}
            )

        updated = chat_store.get_conversation(conversation_id)
        return jsonify(
            {
                "user_message": user_msg,
                "assistant_message": assistant_msg,
                "conversation": updated,
            }
        )

    @app.post("/api/conversations/<conversation_id>/messages/stream")
    def post_message_stream(conversation_id: str):
        conv = chat_store.get_conversation(conversation_id)
        if not conv:
            return jsonify({"error": "Not found"}), 404

        body = request.get_json(silent=True) or {}
        content = (body.get("content") or "").strip()
        if not content:
            return jsonify({"error": "content is required"}), 400

        provider = (body.get("provider") or conv["provider"]).strip()
        model = body.get("model") or conv["model"]
        cwd = body.get("cwd") or os.getcwd()

        chat_store.update_conversation_meta(
            conversation_id, provider=provider, model=model
        )

        prior = chat_store.get_messages(conversation_id)
        meta = conv.get("metadata") or {}
        needs_ai_title = (
            len(prior) == 0
            and not meta.get("title_generated")
            and (conv.get("title") or "New chat") in ("New chat", "")
        )
        user_msg = chat_store.append_message(conversation_id, "user", content)

        def _sse(payload: Dict[str, Any]) -> str:
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        def generate():
            assistant_text = ""
            try:
                # Padding comment defeats some WSGI/webview buffers so early tokens flush
                yield ": " + (" " * 2048) + "\n\n"
                yield _sse({"type": "user_message", "user_message": user_msg})
                for event in provider_router.stream_message(
                    provider,
                    content,
                    prior,
                    model=model,
                    cursor_agent_id=conv.get("cursor_agent_id"),
                    cwd=cwd,
                ):
                    etype = event.get("type")
                    if etype == "token":
                        # Flush each token as its own chunk so the UI can type live
                        yield _sse(event)
                    elif etype == "error":
                        assistant_text = f"Error: {event.get('error') or 'stream failed'}"
                        yield _sse({"type": "error", "error": event.get("error")})
                        break
                    elif etype == "done":
                        assistant_text = event.get("content") or ""
                        new_agent_id = event.get("cursor_agent_id")
                        if new_agent_id:
                            chat_store.update_conversation_meta(
                                conversation_id, cursor_agent_id=new_agent_id
                            )
            except Exception as exc:
                assistant_text = f"Error: {exc}"
                yield _sse({"type": "error", "error": str(exc)})

            assistant_msg = chat_store.append_message(
                conversation_id, "assistant", assistant_text
            )

            # Send done BEFORE title generation so typing isn't blocked on a 2nd AI call
            updated = chat_store.get_conversation(conversation_id)
            yield _sse(
                {
                    "type": "done",
                    "user_message": user_msg,
                    "assistant_message": assistant_msg,
                    "conversation": updated,
                }
            )

            if needs_ai_title and assistant_text and not str(
                assistant_text
            ).lower().startswith(("error:", "cursor error", "cursor startup")):
                try:
                    title = provider_router.generate_chat_title(
                        provider,
                        content,
                        assistant_text,
                        model=model,
                        cwd=cwd,
                    )
                    chat_store.rename_conversation(conversation_id, title)
                except Exception:
                    chat_store.rename_conversation(
                        conversation_id,
                        content[:60] + ("…" if len(content) > 60 else ""),
                    )
                chat_store.update_conversation_meta(
                    conversation_id, metadata_update={"title_generated": True}
                )
                updated = chat_store.get_conversation(conversation_id)
                yield _sse(
                    {
                        "type": "title",
                        "title": updated.get("title"),
                        "conversation": updated,
                    }
                )
            elif needs_ai_title:
                chat_store.update_conversation_meta(
                    conversation_id, metadata_update={"title_generated": True}
                )

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
                "Content-Type": "text/event-stream; charset=utf-8",
            },
            direct_passthrough=False,
        )

    @app.post("/api/defaults")
    def set_defaults():
        body = request.get_json(silent=True) or {}
        provider = body.get("provider")
        model = body.get("model")
        ok = True
        if provider:
            ok = update_default_provider(provider) and ok
        if provider and model:
            ok = update_provider_settings(
                provider, default_model=model, set_as_default_provider=True
            ) and ok
        return jsonify({"ok": ok})

    @app.get("/api/settings")
    def settings_get():
        return jsonify(get_settings())

    @app.put("/api/settings")
    def settings_put():
        body = request.get_json(silent=True) or {}
        default_provider = body.get("default_provider")
        providers = body.get("providers") or []

        if default_provider:
            if not update_default_provider(str(default_provider)):
                return jsonify({"error": "Failed to update default provider"}), 500

        for item in providers:
            if not isinstance(item, dict):
                continue
            name = (item.get("id") or "").strip()
            if not name:
                continue
            api_key = item.get("api_key")
            # Only update key when user typed a new value (non-empty, not masked placeholder)
            if isinstance(api_key, str) and api_key.strip() and "…" not in api_key and "•" not in api_key:
                key_arg = api_key
            else:
                key_arg = None

            models = item.get("models")
            if isinstance(models, str):
                models = [m.strip() for m in models.split(",") if m.strip()]
            elif isinstance(models, list):
                models = [str(m).strip() for m in models if str(m).strip()]
            else:
                models = None

            ok = update_provider_settings(
                name,
                api_key=key_arg,
                default_model=item.get("default_model"),
                models=models,
                set_as_default_provider=(name == default_provider),
            )
            if not ok:
                return jsonify({"error": f"Failed to update provider: {name}"}), 400

        return jsonify(get_settings())

    @app.post("/api/run")
    def run_one():
        body = request.get_json(silent=True) or {}
        command = (body.get("command") or body.get("cmd") or "").strip()
        if not command:
            return jsonify({"error": "command is required"}), 400
        cwd = body.get("cwd") or os.getcwd()
        timeout = int(body.get("timeout") or 120)
        values = body.get("inputs") or body.get("values") or {}
        if isinstance(values, dict) and values:
            command = command_runner.apply_inputs(command, {str(k): str(v) for k, v in values.items()})
        result = command_runner.run_command(command, cwd=cwd, timeout=timeout)
        return jsonify(result)

    @app.post("/api/run/prepare")
    def run_prepare():
        """AI: detect inputs needed before running a single command."""
        body = request.get_json(silent=True) or {}
        command = (body.get("command") or body.get("cmd") or "").strip()
        if not command:
            return jsonify({"error": "command is required"}), 400
        provider = (body.get("provider") or get_default_provider() or "gemini").strip()
        model = body.get("model")
        cwd = body.get("cwd") or os.getcwd()
        try:
            prepared = command_runner.prepare_single_command(
                provider, command, model=model, cwd=cwd
            )
            return jsonify(prepared)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.post("/api/run/plan")
    def run_plan():
        """AI: order commands + list inputs the user must provide."""
        body = request.get_json(silent=True) or {}
        source = (body.get("source") or body.get("text") or "").strip()
        if not source:
            return jsonify({"error": "source is required"}), 400
        provider = (body.get("provider") or get_default_provider() or "gemini").strip()
        model = body.get("model")
        cwd = body.get("cwd") or os.getcwd()
        try:
            plan = command_runner.plan_script_from_text(
                provider, source, model=model, cwd=cwd
            )
            if not plan.get("steps"):
                return jsonify({"error": "No runnable commands found"}), 400
            return jsonify(plan)
        except Exception as exc:
            return jsonify({"error": f"Failed to plan commands: {exc}"}), 500

    @app.post("/api/run/script/stream")
    def run_script_stream():
        body = request.get_json(silent=True) or {}
        cwd = body.get("cwd") or os.getcwd()
        timeout = int(body.get("timeout") or 120)
        stop_on_error = body.get("stop_on_error", True)
        pause_on_ask = body.get("pause_on_ask", True)
        provider = (body.get("provider") or get_default_provider() or "gemini").strip()
        model = body.get("model")
        values = body.get("inputs") or body.get("values") or {}
        if not isinstance(values, dict):
            values = {}
        values = {str(k): str(v) for k, v in values.items()}

        steps = body.get("steps")
        if not steps:
            source = (body.get("source") or body.get("text") or "").strip()
            if not source:
                return jsonify({"error": "source or steps required"}), 400
            try:
                plan = command_runner.plan_script_from_text(
                    provider, source, model=model, cwd=cwd
                )
                steps = plan.get("steps") or []
            except Exception as exc:
                return jsonify({"error": f"Failed to plan commands: {exc}"}), 500
            if not steps:
                return jsonify({"error": "No runnable commands found"}), 400

        normalized = []
        for item in steps:
            if isinstance(item, str):
                normalized.append(
                    {
                        "type": "run",
                        "cmd": item,
                        "note": "",
                        "ask": "",
                        "input_id": "",
                        "options": [],
                    }
                )
            elif isinstance(item, dict):
                step = command_runner._normalize_step(item)
                if step:
                    normalized.append(step)
        if not normalized:
            return jsonify({"error": "No runnable commands found"}), 400

        # Do not pre-substitute away placeholders — runner pauses mid-stream for them
        def _sse(payload: Dict[str, Any]) -> str:
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        def generate():
            yield ": " + (" " * 1024) + "\n\n"
            try:
                for event in command_runner.run_script_stream(
                    normalized,
                    cwd=cwd,
                    timeout=timeout,
                    stop_on_error=bool(stop_on_error),
                    pause_on_ask=bool(pause_on_ask),
                    values=values,
                    provider=provider,
                    model=model,
                    analyze_output=bool(body.get("analyze_output", True)),
                ):
                    yield _sse(event)
            except Exception as exc:
                yield _sse({"type": "error", "error": str(exc)})

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    return app


def run_server(host: str = "127.0.0.1", port: int = 8765, store: Optional[ChatStore] = None):
    app = create_app(store=store)
    app.run(host=host, port=port, threaded=True, use_reloader=False)
