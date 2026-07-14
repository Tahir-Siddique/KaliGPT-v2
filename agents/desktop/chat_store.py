#!/usr/bin/env python3
"""SQLite persistence for desktop chat conversations and messages."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def default_store_path() -> Path:
    return Path.home() / ".kaligpt" / "chats.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatStore:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else default_store_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT 'New chat',
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    cursor_agent_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conversation
                    ON messages(conversation_id, created_at);
                """
            )

    def create_conversation(
        self,
        provider: str,
        model: str,
        title: str = "New chat",
        cursor_agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        conv_id = str(uuid.uuid4())
        now = _utc_now()
        meta_json = json.dumps(metadata or {})
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations
                    (id, title, provider, model, cursor_agent_id, created_at, updated_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (conv_id, title, provider, model, cursor_agent_id, now, now, meta_json),
            )
        return self.get_conversation(conv_id)

    def list_conversations(self, query: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if query and query.strip():
                like = f"%{query.strip()}%"
                rows = conn.execute(
                    """
                    SELECT * FROM conversations
                    WHERE title LIKE ? OR provider LIKE ? OR model LIKE ?
                    ORDER BY updated_at DESC
                    """,
                    (like, like, like),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM conversations ORDER BY updated_at DESC"
                ).fetchall()
        return [self._conversation_dict(r, include_messages=False) for r in rows]

    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if not row:
                return None
            messages = conn.execute(
                """
                SELECT id, role, content, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY created_at ASC, rowid ASC
                """,
                (conversation_id,),
            ).fetchall()
        return self._conversation_dict(row, messages=messages)

    def rename_conversation(self, conversation_id: str, title: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE conversations
                SET title = ?, updated_at = ?
                WHERE id = ?
                """,
                (title.strip() or "New chat", _utc_now(), conversation_id),
            )
            if cur.rowcount == 0:
                return None
        return self.get_conversation(conversation_id)

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM conversations WHERE id = ?",
                (conversation_id,),
            )
            return cur.rowcount > 0

    def update_conversation_meta(
        self,
        conversation_id: str,
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        cursor_agent_id: Optional[str] = None,
        title: Optional[str] = None,
        metadata_update: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        conv = self.get_conversation(conversation_id)
        if not conv:
            return None
        meta = dict(conv.get("metadata") or {})
        if metadata_update:
            meta.update(metadata_update)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE conversations
                SET provider = ?,
                    model = ?,
                    cursor_agent_id = ?,
                    title = ?,
                    metadata = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    provider if provider is not None else conv["provider"],
                    model if model is not None else conv["model"],
                    cursor_agent_id if cursor_agent_id is not None else conv.get("cursor_agent_id"),
                    title if title is not None else conv["title"],
                    json.dumps(meta),
                    _utc_now(),
                    conversation_id,
                ),
            )
        return self.get_conversation(conversation_id)

    def append_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
    ) -> Dict[str, Any]:
        msg_id = str(uuid.uuid4())
        now = _utc_now()
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if not exists:
                raise KeyError(f"Conversation not found: {conversation_id}")
            conn.execute(
                """
                INSERT INTO messages (id, conversation_id, role, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (msg_id, conversation_id, role, content, now),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
        return {"id": msg_id, "role": role, "content": content, "created_at": now}

    def get_messages(self, conversation_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, role, content, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY created_at ASC, rowid ASC
                """,
                (conversation_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def _conversation_dict(
        self,
        row: sqlite3.Row,
        include_messages: bool = True,
        messages: Optional[List[sqlite3.Row]] = None,
    ) -> Dict[str, Any]:
        data = {
            "id": row["id"],
            "title": row["title"],
            "provider": row["provider"],
            "model": row["model"],
            "cursor_agent_id": row["cursor_agent_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "metadata": json.loads(row["metadata"] or "{}"),
        }
        if include_messages:
            if messages is None:
                data["messages"] = self.get_messages(row["id"])
            else:
                data["messages"] = [dict(m) for m in messages]
        return data
