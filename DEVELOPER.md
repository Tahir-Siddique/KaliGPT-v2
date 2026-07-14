# Developer guide — HatsOff / KaliGPT

HatsOff is the **desktop + lab runner** layer on top of the KaliGPT agent providers. Upstream CLI agents remain under `agents/*.py`.

## Who built what

| Piece | Credit |
|-------|--------|
| HatsOff desktop (UI, Kali runner, branding) | Tahir |
| KaliGPT / HackerX CLI agents, tools, installers | SudoHopeX (Krishna Dwivedi) and contributors |

## Stack

- **UI:** static HTML/CSS/JS served by Flask (`agents/desktop/static/`)
- **API:** Flask in `agents/desktop/server.py`
- **Chat DB:** SQLite via `agents/desktop/chat_store.py` → `~/.kaligpt/chats.db`
- **Providers:** `agents/{gemini,chatgpt,ollama,openrouter,litellm_provider,cursor}.py`
- **Desktop dispatch:** `agents/desktop/provider_router.py`
- **Lab runner:** `agents/desktop/runner.py` (plan JSON, bash/-lc, mid-run `need_input`)
- **Prompts:** `agents/utils/prompts.py` (`HATSOFF_AGENT`)
- **Config:** `agents/utils/agent_configs.py` + local `api.config.json` (gitignored)

## Local setup

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements/pip-requirements.txt
pip install pytest
cp agents/utils/api.config.example.json agents/utils/api.config.json
# put keys via Settings UI or edit the local config file
```

Never commit `agents/utils/api.config.json`.

## Run

```bash
python -m agents.desktop
python -m agents.desktop --browser
python -m pytest tests/ -q
```

## Key modules

### Desktop API (`agents/desktop/server.py`)

| Route | Purpose |
|-------|---------|
| `GET /` | UI |
| `GET /api/providers` | Provider list + models |
| `GET/POST /api/conversations…` | Chat CRUD |
| `POST …/messages` | Send chat message (full reply) |
| `POST /api/run` | Single command |
| `POST /api/run/plan` | AI ordered script (no execute) |
| `POST /api/run/script/stream` | Execute with mid-run asks |
| `GET /api/environment` | Kali/shell detection |
| `GET/PUT /api/settings` | Config for Settings modal |

### Script runner SSE

Script runs use SSE events: `plan`, `step_start`, `step_done`, `need_input`, `finished`, `stopped`.

### Cursor daemon

`agents/cursor.py` spawns `python -u -m agents.cursor_daemon` and speaks JSON lines over stdin/stdout. Stderr is pumped to avoid pipe deadlocks. Ready handshake: `{"ok":true,"ready":true}`.

## Coding norms

- Prefer small, focused diffs.
- Keep secrets out of the repo (`.gitignore` + example config).
- Chat replies use the full non-stream send path (`send_message`). Tool-using agents stay on CLI.
- Runner commands must be Kali bash–friendly by default.
- Add/adjust tests under `tests/` for store, API, and runner helpers.

## Tests worth knowing

- `tests/test_desktop_chat.py` — store, message API, settings, runner helpers
- `tests/test_cursor_provider.py` — Cursor worker/daemon plumbing (mocked)

## Release checklist

1. Placeholder keys only in example config  
2. `pytest` green  
3. README / docs paths still accurate  
4. Rotate any key that ever leaked into git history  

## Docs map

- [README.md](README.md) — user overview  
- [docs/DESKTOP.md](docs/DESKTOP.md) — desktop deep dive  
- [CONTRIBUTING.md](CONTRIBUTING.md) — PR process  
- [SECURITY.md](SECURITY.md) — reporting  
- [DISCLAIMER.md](DISCLAIMER.md) — legal / ethics  
