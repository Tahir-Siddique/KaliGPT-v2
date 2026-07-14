# HatsOff desktop guide

Local ChatGPT-style UI for Kali Linux labs: multi-provider AI chat, and a command/script runner that pauses for mid-run choices.

## Requirements

- Python 3.10+ (3.11–3.13 common on Kali)
- GUI deps for a native window (optional if you use `--browser`)
- At least one AI provider key (or Ollama)

## Install

### Recommended (`./install`)

```bash
chmod +x ./install
./install            # install + launch
./install --no-run   # install only
./install --update   # git pull + refresh deps
hatsoff              # later launches
```

The installer creates `~/.local/bin/hatsoff` and a desktop menu entry.  
If an old `.venv` was created **without** `--system-site-packages`, `./install` recreates it so GTK/`gi` works for the native window.

### Manual

```bash
cd ~/Desktop/KaliGPT
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements/pip-requirements.txt
cp agents/utils/api.config.example.json agents/utils/api.config.json
```

### Native window (GTK)

```bash
sudo apt install -y python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1
```

`--system-site-packages` lets the venv import system `gi`. Without it you will see `No module named 'gi'` and HatsOff opens the browser instead.

## Launch

After `./install`:

| Command | Behavior |
|---------|----------|
| `hatsoff` | Maximized native window when possible |
| `hatsoff --browser` | System browser |
| `hatsoff --no-window` | Server only; open the printed URL |
| `hatsoff --port 9000` | Custom port |

Or from a activated venv:

| Command | Behavior |
|---------|----------|
| `python -m agents.desktop` | Same as `hatsoff` |
| `python -m agents.desktop --browser` | System browser |
| `python -m agents.desktop --no-window` | Server only; open the printed URL |
| `python -m agents.desktop --port 9000` | Custom port |

Default URL: `http://127.0.0.1:8765/`

Chats: `~/.kaligpt/chats.db`  
Config: `agents/utils/api.config.json` (local, gitignored)

## Settings

Use the sidebar profile / gear → **Settings**:

- Default provider
- Per-provider API key / Ollama URL
- Model lists

No `.env` required for normal use. Optional Cursor override: `CURSOR_API_KEY`.

## Chat features

### Titles

After the first successful turn in a new chat, HatsOff asks the model for a short sidebar title once (`title_generated` lock).

### Run (play)

On fenced code blocks: **Run** + **Copy**.

- Confirms before executing
- Uses Kali-compatible shell resolution (`bash -lc` on Kali; WSL Kali on Windows when available)
- Shows stdout/stderr under the block

### Run script (AI ordered)

On assistant messages with code:

1. AI plans Kali bash steps (`type: run` / `type: ui`)
2. Discovery commands run first
3. Mid-run **dropdown** for choices (iface, hosts, yes/no…)
4. Later steps fill `{{placeholders}}`
5. Destructive steps can require an explicit confirm

Non-shell choices use UI steps — they never hit the shell.

## Environment API

```http
GET /api/environment
```

Returns shell mode (`kali-native`, `kali-wsl`, `linux`, `windows`) used by the sidebar label and runner.

## Cursor provider

Cursor runs through a long-lived daemon (`agents.cursor_daemon`) so multi-turn resume works.

```bash
pip install -U cursor-sdk
PYTHONPATH=. python -u -m agents.cursor_daemon
```

If the daemon dies, HatsOff now prints stderr tail in the error. Bypass:

```bash
export KALIGPT_CURSOR_INPROCESS=1
```

Prefer Gemini/Ollama/OpenRouter on Kali if Cursor’s local bridge is unavailable.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `No module named 'gi'` | Install GTK packages; recreate venv with `--system-site-packages` |
| Opens browser only | OK — use `--browser`, or fix GTK as above |
| Cursor daemon exited | Install `cursor-sdk`; check daemon log in the error; or switch provider |
| Commands fail on Windows host | Use Kali VM/WSL; runner prefers Kali bash when detected |
| Empty config / missing keys | Copy `api.config.example.json` → `api.config.json`, or use Settings |

## Architecture (short)

```
Browser / pywebview
        │
        ▼
agents/desktop/server.py     Flask API + static UI
        │
        ├─ provider_router   send to Gemini, OpenAI, …, Cursor
        ├─ chat_store        SQLite conversations
        └─ runner            plan + bash execution + mid-run asks
```

See [DEVELOPER.md](../DEVELOPER.md) for module map and contribution notes.
