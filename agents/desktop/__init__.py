# HatsOff desktop chat package
# Local ChatGPT-style UI with persisted conversations

from .chat_store import ChatStore, default_store_path

__all__ = ["ChatStore", "default_store_path"]
