# JarvisChat v1.4.0

Lightweight Ollama coding companion with FTS5 memory system.

## New in v1.4.0
- **FTS5 Memory System**: Say "remember that..." to store facts, they're automatically retrieved by relevance
- **Forget command**: Say "forget about..." to remove memories
- **Memory toggle**: Enable/disable memory injection from topbar
- **Refactored structure**: Separated frontend from backend for maintainability

## File Structure

```
/opt/jarvischat/
├── app.py              # FastAPI backend (~600 lines)
├── jarvischat.db       # SQLite database (auto-created)
├── static/
│   └── logo.jpg        # Your logo (optional)
└── templates/
    └── index.html      # Frontend
```

## Installation

```bash
# Backup existing
cd /opt/jarvischat
cp app.py app.py.bak

# Create directories
mkdir -p templates static

# Copy new files (from wherever you downloaded them)
cp /path/to/new/app.py .
cp /path/to/new/templates/index.html templates/

# Extract logo from old app.py if you want (or just let it fail gracefully)
# The frontend handles missing logo with onerror="this.style.display='none'"

# Restart service
sudo systemctl restart jarvischat
```

## Memory Commands

In chat, you can say:
- "remember that I prefer Rust over Go" → stores as preference
- "remember that JarvisChat runs on port 8080" → stores as infrastructure  
- "note that the deadline is Friday" → stores as general
- "forget about the deadline" → removes matching memories

Memories are automatically searched and injected based on your message content.

## API Endpoints

### Memory
- `GET /api/memories` - List all memories
- `POST /api/memories` - Add memory `{"fact": "...", "topic": "general"}`
- `DELETE /api/memories/{rowid}` - Delete memory
- `GET /api/memories/search?q=rust` - Search memories
- `GET /api/memories/stats` - Get counts by topic

### Existing
- `GET /api/models` - List Ollama models
- `POST /api/chat` - Send message (streaming)
- `GET /api/profile` - Get profile
- `PUT /api/settings` - Update settings

## Dependencies

```bash
pip install fastapi uvicorn httpx psutil jinja2 python-multipart --break-system-packages
```

## Testing Memory

```bash
# Add a memory via API
curl -X POST http://jarvis:8080/api/memories \
  -H "Content-Type: application/json" \
  -d '{"fact": "User prefers native installs over Docker", "topic": "preference"}'

# Search memories
curl "http://jarvis:8080/api/memories/search?q=docker"

# Or in chat, just say:
# "remember that I hate yaml"
# Then ask: "what markup languages should I avoid?"
```
