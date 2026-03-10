# ⚡ JarvisChat

**A lightweight Ollama coding companion that runs on Python 3.13**

![Version](https://img.shields.io/badge/version-1.3.0-blue)
![Python](https://img.shields.io/badge/python-3.13-green)
![License](https://img.shields.io/badge/license-MIT-orange)

JarvisChat is a single-file FastAPI application that provides a clean, responsive web interface for Ollama. It features persistent memory, automatic web search when the model is uncertain, and real-time token tracking.

## Features

- **Persistent Profile/Memory** — Your context is injected into every conversation automatically
- **System Prompt Presets** — Switch between coding assistant, sysadmin, general, or custom modes
- **Streaming Chat** — Real-time token streaming with conversation history
- **Model Switching** — Hot-swap between all installed Ollama models
- **Web Search Integration** — SearXNG kicks in automatically when the model is uncertain (perplexity-based)
- **Weather Queries** — Direct wttr.in integration for weather questions
- **Token Thermometer** — Visual context usage bar with live updates as you type
- **Perplexity & Speed Badges** — See model confidence (PPL) and tokens/sec on each response
- **Copy-to-Clipboard** — One-click copy on all code blocks
- **Dark Theme** — Easy on the eyes for long coding sessions

## Architecture

```
Browser ◄──► app.py (FastAPI) ◄──► Ollama (LLM)
                    │
                    ▼ (when uncertain)
               SearXNG (web search)
```

JarvisChat acts as middleware between your browser and Ollama. When the model's perplexity exceeds a threshold (default 15.0) or it refuses to answer, JarvisChat automatically queries SearXNG, injects the results, and re-prompts the model.

**This is NOT training** — SearXNG is only used at runtime as a fallback for uncertain responses.

## Requirements

- Python 3.11+ (tested on 3.13)
- Ollama running locally (default: `localhost:11434`)
- SearXNG (optional, for web search — default: `localhost:8888`)

## Installation

```bash
# Clone or download app.py
git clone https://llgit.llamachile.shop/gramps/jarvischat.git
cd jarvischat

# Install dependencies
pip install fastapi httpx uvicorn

# Run
python app.py
# or
uvicorn app:app --host 0.0.0.0 --port 8080
```

Open `http://localhost:8080` in your browser.

## Running as a Service

**Important:** Although JarvisChat is a single-file Python application, it's designed to run as a persistent service alongside Ollama — not as a one-off script. Both services should start on boot.

### systemd Service (recommended)

Create `/etc/systemd/system/jarvischat.service`:

```ini
[Unit]
Description=JarvisChat - Ollama Web UI
After=network.target ollama.service
Wants=ollama.service

[Service]
Type=simple
User=jarvischat
WorkingDirectory=/opt/jarvischat
ExecStart=/usr/bin/python3 app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable jarvischat
sudo systemctl start jarvischat
```

### Verify Both Services

```bash
# Check Ollama
systemctl status ollama

# Check JarvisChat
systemctl status jarvischat

# View JarvisChat logs
journalctl -t jarvischat -f
```

## Configuration

Edit these constants at the top of `app.py`:

```python
VERSION = "1.3.0"
OLLAMA_BASE = "http://localhost:11434"
SEARXNG_BASE = "http://localhost:8888"
DEFAULT_MODEL = "deepseek-coder:6.7b"
PERPLEXITY_THRESHOLD = 15.0  # Higher = less likely to trigger search
```

## Database

JarvisChat uses SQLite (`jarvischat.db` in the same directory as `app.py`):

| Table | Purpose |
|-------|---------|
| conversations | Chat sessions with model and timestamps |
| messages | Individual messages with role and content |
| system_presets | Saved system prompt presets |
| profile | Your persistent memory/context |
| settings | App settings (search/profile toggles, default model) |

## Logging

JarvisChat logs to syslog via journald:

```bash
# Follow live logs
journalctl -t jarvischat -f

# View last 100 entries
journalctl -t jarvischat -n 100
```

## Token Thermometer

The vertical bar next to the input shows your context usage in real-time:

- **Green** — Plenty of room
- **Yellow** — 70%+ used
- **Red** — 90%+ used (approaching limit)

The count includes: profile + preset + conversation history + current input. Context size is fetched from Ollama when you switch models.

## Search Flow

1. User sends message → Ollama streams response with logprobs
2. JarvisChat calculates perplexity from logprobs
3. If perplexity > 15.0 OR refusal patterns detected:
   - Yield `{searching: True}` to show spinner
   - Query SearXNG (or wttr.in for weather)
   - Inject results into context
   - Re-prompt Ollama
4. If model still refuses, format raw search results directly
5. Clean hedging phrases from response
6. Yield final response with PPL and t/s badges

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web UI |
| `/api/models` | GET | List Ollama models |
| `/api/ps` | GET | Running models |
| `/api/show` | POST | Model info (context size) |
| `/api/chat` | POST | Stream chat (SSE) |
| `/api/conversations` | GET | List conversations |
| `/api/conversations/{id}` | GET/DELETE | Get/delete conversation |
| `/api/profile` | GET/PUT | Get/update profile |
| `/api/presets` | GET/POST | List/create presets |
| `/api/presets/{id}` | PUT/DELETE | Update/delete preset |
| `/api/settings` | GET/PUT | App settings |
| `/api/search/status` | GET | SearXNG availability |

## Screenshots

*(Add your own screenshot here)*

## TODO

### Active

1. ~~**Mass-delete conversation history**~~ ✓ (v1.3.0)

2. **Verify SearXNG and Docker services persist across reboots**
   - Expand refusal patterns: "As an AI model", "based on my training data", "I don't have the capability"

3. **Input trigger: `search+` prefix**
   - Strip prefix, query SearXNG directly, Ollama summarizes
   - Raw results in expandable div (not tooltip)

4. **Add `profile.example.md`**
   - Recommended default profile with anti-bullshit rules (no "As an AI", no OpenAI mentions)

### Backlog

5. Conversation search/filter by keyword
6. Export conversation to markdown/text
7. Keyboard shortcuts (Ctrl+N new chat, Ctrl+Enter send)
8. ~~Token count estimate before sending~~ ✓ (v1.2.9)
9. Model info display — context length, VRAM usage from Ollama `/api/ps`
10. Retry button on assistant messages
11. Source links — clickable links when search used
12. Allow conversation renaming
13. Multiple profiles — coding/sysadmin/general
14. Auto-generate conversation tags (client-side KWIC, top 5, filterable badges)
15. **Image input support**
    - Pull vision model (llava, llama3.2-vision, etc.)
    - Frontend: file input / drag-drop, base64 encode
    - Backend: pass `images` array to Ollama `/api/chat`

## Version History

| Version | Changes |
|---------|---------|
| 1.3.0 | Delete all conversations button |
| 1.2.9 | Token thermometer with live context tracking |
| 1.2.8 | Logo in sidebar, llama emoji tagline |
| 1.2.7 | Tokens per second (t/s) badge on responses |
| 1.2.6 | wttr.in weather integration, improved search extraction |
| 1.2.5 | SearXNG infoboxes/answers, smarter query building |
| 1.2.4 | Perplexity badges, hedging cleanup |
| 1.2.3 | SearXNG integration with perplexity-based triggering |
| 1.2.0 | System prompt presets, settings persistence |
| 1.1.0 | Profile memory, model switching |
| 1.0.0 | Initial release |

## License

MIT

---

## A Note from Gramps

I named my AI machine "jarvis" after the AI assistant in *Iron Man* (2008) — because it's an awesome name. When I started building a local coding companion to talk to it, "JarvisChat" just made sense.

This project is in active development. Eventually it'll get packaged up as a Docker thing, but for now while I'm iterating fast, a single-file Python service does the job.

---

*Built with 🦙 by Gramps at the Llama Chile Shop*
