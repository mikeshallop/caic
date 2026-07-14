# ai.md — Project Context

Detailed project context, work state, architecture, and configuration have moved to [`ai.md`](ai.md). This file is kept for backward compatibility — the canonical reference is `ai.md`.

## Quick start

```bash
# Development
./venv/bin/uvicorn app:app --host 0.0.0.0 --port 8080 --reload

# Production (via systemd)
sudo systemctl restart caic

# Direct run
./venv/bin/python app.py
```

## Dependencies

```bash
./venv/bin/pip install -r requirements.txt
# Also requires: psutil jinja2 python-multipart pypdf (not in requirements.txt)
```
