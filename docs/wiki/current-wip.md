# cAIc Current WiP Backlog

Last updated: 2026-07-14
Owner: Gramps
Scope: Active roadmap items and backlog.

## Completed

- **B7 (v0.19.0)** — Apple Silicon worker support. gpu.py darwin branch, hardware.py darwin VRAM, node_agent/agent.py macOS VRAM reporting.
- **B5 (v0.19.1)** — Default model auto-pull. model_pull.py + ensure_model() in app.py lifespan.
- **B6 (v0.19.2)** — Waterfall direction toggle (NEW/OLD), scroll/lock fixes, toast notifications, execCopy, modelLabel, Ctrl+Enter.
- **B8 (v0.19.3)** — Private Chat mode. Backend skip-DB/skip-RAG/skip-search flag, frontend PRIVATE badge, info popup.
- **WireGuard TLS (v0.19.4)** — Self-signed WireGuard mesh encrypts all inter-node traffic (AMQP, inference, RPC). No code changes to cAIc. Documented in wiki/WireGuard-Setup.md + docker.md §5.4.

## Backlog

- B1 — Context loss in follow-up questions (investigation)
- B2 — Bang-prefixed (`!`) search routing
- B3 — Docker distribution (planning doc at `docker.md`, not yet implemented)
- **B4 — RAG Corpus Management UI (deferred)** — browse, search, edit, delete individual RAG entries
- HTTPS / reverse proxy (Caddy)
- Conversation search/filter and export tooling
- Keyboard shortcuts, retry button, source-link polish

## Maintenance Rules
- Keep this file as the single source of truth for roadmap tracking.
- Update as work starts or completes.
