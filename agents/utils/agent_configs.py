# use JSON to store and retrieve api_keys, api_models and more
# Updated: 20 March 2026


import json
from pathlib import Path
from typing import List, Dict, Any, Optional

# --- CONFIG FILE PATH (Relative to this module's directory) ---
CONFIG_FILE = Path(__file__).resolve().parent / "api.config.json"
CONFIG_EXAMPLE = Path(__file__).resolve().parent / "api.config.example.json"


# --- CENTRAL CONFIG FILE MANAGEMENT ---

def _load_config() -> Dict[str, Any]:
    """Reads JSON config. Creates a new file with default data if missing or corrupted."""

    default_data = {
        "default_model": "gemini-2.5-flash",
        "default_provider": "ollama",
        "gemini": {
            "api_key": "GEMINI_API_KEY",
            "default_model": "gemini-2.5-flash",
            "models": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-1.5-flash-latest"]
        },
        "chatgpt": {
            "api_key": "OPENAI_API_KEY",
            "default_model": "gpt-4o",
            "models": ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]
        },
        "ollama": {
            "api_key": "http://localhost:11434",  # URL for Ollama
            "default_model": "llama3",
            "models": ["llama3", "mistral", "qwen3:8b"]
        },
        "openrouter": {
            "api_key": "OpenRouter_API_Key",
            "default_model": "z-ai/glm-4.5-air:free",
            "models": [
                "z-ai/glm-4.5-air:free"
            ]
        },
        "litellm": {
            "api_key": "LITELLM_USES_PROVIDER_ENV_KEYS",
            "default_model": "anthropic/claude-sonnet-4-6",
            "models": [
                "anthropic/claude-sonnet-4-6",
                "openai/gpt-4o",
                "gemini/gemini-2.5-flash"
            ]
        },
        "cursor": {
            "api_key": "YOUR_CURSOR_API_KEY",
            "default_model": "composer-2.5",
            "models": [
                "composer-2.5",
                "auto"
            ]
        }
    }

    try:
        if not CONFIG_FILE.exists():
            raise FileNotFoundError  # Force creation if file doesn't exist

        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
            return data

    except (FileNotFoundError, json.JSONDecodeError):
        # File missing or corrupted, create from example when present
        print(f"[!] Config file missing or corrupted. Creating default config at: {CONFIG_FILE}")
        seed = default_data
        if CONFIG_EXAMPLE.exists():
            try:
                with open(CONFIG_EXAMPLE, "r", encoding="utf-8") as f:
                    seed = json.load(f)
            except Exception:
                seed = default_data
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(seed, f, indent=4)
            return seed
        except Exception as e:
            print(f"[!] FATAL: Could not create config file: {e}")
            return {}  # Return empty dict if writing fails


def _save_config(data: Dict[str, Any]) -> bool:
    """Writes the entire configuration dictionary back to the file."""
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=4)
        return True
    except Exception as e:
        print(f"[!] Error saving config file: {e}")
        return False


# --- CONFIGURATION ACCESS AND UPDATE FUNCTIONS ---

# Function 1: Read and return the global default AI model
def get_default_model() -> str:
    """Returns the globally configured default AI model string."""
    data = _load_config()
    return data.get('default_model', "gemini-2.5-flash")  # Fallback for safety

def get_default_provider():
    """Returns the AI provider associated with the global default model."""
    data = _load_config()
    default_provider = data.get('default_provider', 'gemini')  # Fallback to gemini for safety
    return default_provider

# Function 2: Update the global default AI model
def update_default_model(new_model: str) -> bool:
    """Updates the globally configured default AI model."""
    data = _load_config()
    data["default_model"] = new_model
    return _save_config(data)

def update_default_provider(new_provider: str) -> bool:
    """Updates the globally configured default AI provider."""
    data = _load_config()
    data["default_provider"] = new_provider
    return _save_config(data)

# Function 3: Read and return all available AI Vendors
def get_available_ais() -> List[str]:
    """Returns a list of all configured AI provider vendors (e.g., 'gemini', 'chatgpt')."""
    data = _load_config()
    # FIX: Use a list comprehension to exclude the 'default_model' & 'default_provider' key correctly
    return [k for k in data.keys() if k != 'default_model' and k!='default_provider']


# Function 4: Read and return API key of a specific AI
def get_api_key(ai_name: str) -> Optional[str]:
    """Returns the API key for the specified AI vendor."""
    data = _load_config()
    return data.get(ai_name, {}).get("api_key")


# Function 5: Read and return default model of a specific AI
def get_ai_specific_default_model(ai_name: str) -> Optional[str]:
    """Returns the default model configured for a specific AI vendor."""
    data = _load_config()
    return data.get(ai_name, {}).get("default_model")


# Function 6: Read and return all models of a specific AI Vendor
def get_vendor_specific_all_models(ai_name: str) -> List[str]:
    """Returns a list of all known model names for the specified AI vendor."""
    data = _load_config()
    return data.get(ai_name, {}).get("models", [])


# Function 7: Update API key of a specific AI
def update_api_key(ai_name: str, new_key: str) -> bool:
    """Updates the API key for the specified AI vendor."""
    data = _load_config()
    if ai_name in data:
        data[ai_name]["api_key"] = new_key
        return _save_config(data)
    return False


# Function 8: Update default model of a specific AI
def update_ai_specific_default_model(ai_name: str, new_model: str) -> bool:
    """Updates the default model for the specified AI vendor, only if the model is known."""
    data = _load_config()
    if ai_name in data and new_model in data[ai_name].get("models", []):
        data[ai_name]["default_model"] = new_model
        return _save_config(data)
    return False


_PLACEHOLDER_KEYS = {
    "",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "YOUR_GEMINI_API_KEY",
    "YOUR_CHATGPT_API_KEY",
    "YOUR_OPENROUTER_API_KEY",
    "YOUR_CURSOR_API_KEY",
    "OpenRouter_API_Key",
    "CURSOR_API_KEY",
    "LITELLM_USES_PROVIDER_ENV_KEYS",
}


def mask_secret(value: Optional[str]) -> Dict[str, Any]:
    """Return a UI-safe view of a secret without exposing the full value."""
    raw = (value or "").strip()
    is_set = bool(raw) and raw not in _PLACEHOLDER_KEYS
    if not is_set:
        return {"configured": False, "masked": "", "hint": ""}
    if len(raw) <= 8:
        return {"configured": True, "masked": "••••••••", "hint": ""}
    return {
        "configured": True,
        "masked": f"{raw[:4]}…{raw[-4:]}",
        "hint": raw[-4:],
    }


def get_settings() -> Dict[str, Any]:
    """Full settings payload for the desktop Settings UI (secrets masked)."""
    data = _load_config()
    preferred = ["gemini", "chatgpt", "ollama", "openrouter", "litellm", "cursor"]
    providers = [p for p in preferred if p in data] + [
        k for k in data.keys()
        if k not in preferred and k not in ("default_model", "default_provider")
    ]
    out_providers = []
    for name in providers:
        block = data.get(name) or {}
        key_info = mask_secret(block.get("api_key"))
        out_providers.append(
            {
                "id": name,
                "label": _provider_label(name),
                "key_field": "base_url" if name == "ollama" else "api_key",
                "key_label": "Base URL" if name == "ollama" else "API key",
                "api_key_configured": key_info["configured"],
                "api_key_masked": key_info["masked"],
                "default_model": block.get("default_model"),
                "models": list(block.get("models") or []),
                "help": _provider_help(name),
            }
        )
    return {
        "default_provider": data.get("default_provider", "gemini"),
        "default_model": data.get("default_model"),
        "config_path": str(CONFIG_FILE),
        "providers": out_providers,
    }


def update_provider_settings(
    ai_name: str,
    *,
    api_key: Optional[str] = None,
    default_model: Optional[str] = None,
    models: Optional[List[str]] = None,
    set_as_default_provider: bool = False,
) -> bool:
    """Update provider API key / models from the Settings UI."""
    data = _load_config()
    if ai_name not in data or not isinstance(data.get(ai_name), dict):
        return False

    if api_key is not None:
        # Empty string clears to placeholder for non-ollama; keep typed URL for ollama
        data[ai_name]["api_key"] = api_key.strip()

    if models is not None:
        cleaned = [m.strip() for m in models if m and str(m).strip()]
        if cleaned:
            data[ai_name]["models"] = cleaned

    if default_model is not None and default_model.strip():
        model = default_model.strip()
        model_list = list(data[ai_name].get("models") or [])
        if model not in model_list:
            model_list.append(model)
            data[ai_name]["models"] = model_list
        data[ai_name]["default_model"] = model
        data["default_model"] = model

    if set_as_default_provider:
        data["default_provider"] = ai_name

    return _save_config(data)


def _provider_label(name: str) -> str:
    labels = {
        "gemini": "Gemini",
        "chatgpt": "ChatGPT / OpenAI",
        "ollama": "Ollama",
        "openrouter": "OpenRouter",
        "litellm": "LiteLLM",
        "cursor": "Cursor",
    }
    return labels.get(name, name.title())


def _provider_help(name: str) -> str:
    help_text = {
        "gemini": "Paste your Google AI Studio / Gemini API key.",
        "chatgpt": "Paste your OpenAI API key (sk-…).",
        "ollama": "Local Ollama server URL, e.g. http://localhost:11434",
        "openrouter": "Paste your OpenRouter key (sk-or-v1-…).",
        "litellm": "Optional gateway hint; LiteLLM mostly uses each vendor’s own env/keys.",
        "cursor": "Paste your Cursor API key from Dashboard → Integrations (cursor_…).",
    }
    return help_text.get(name, "Paste the credential for this provider.")


# --- ADDITIONAL UTILITIES (From your original list) ---

def add_ai_provider(ai_name: str, api_key: str, default_model: str, models: List[str]) -> bool:
    """Adds a new AI provider to the configuration file."""
    data = _load_config()
    data[ai_name] = {
        "api_key": api_key,
        "default_model": default_model,
        "models": models
    }
    return _save_config(data)


def remove_ai_provider(ai_name: str) -> bool:
    """Removes an AI provider from the configuration file."""
    data = _load_config()
    if ai_name in data:
        del data[ai_name]
        return _save_config(data)
    return False


if __name__ == "__main__":
    # Example usage for testing the functions
    print("--- TESTING CONFIG ACCESS ---")
    print("All Available AI's: ", get_available_ais())
    # print("Default model (Global): ", get_default_model())
    # print("Gemini API Key: ", get_api_key('gemini'))
    # print("Ollama Models: ", get_vendor_specific_all_models('ollama'))
    #
    # # Example update
    # print("\n--- TESTING UPDATE ---")
    # new_key = "ANTHROPIC-NEW-KEY-123"
    # print(f"Updating claude key...")
    # # Add Claude first if it's not in the default config for testing the update
    # add_ai_provider('claude', 'old-key', 'claude-3-haiku', ['claude-3-haiku', 'claude-3-opus'])
    #
    # update_api_key('claude', new_key)
    # print("Claude API Key (After Update): ", get_api_key('claude'))
    #
    # update_default_model('llama3')
    # print("Default model (Global, after update): ", get_default_model())
