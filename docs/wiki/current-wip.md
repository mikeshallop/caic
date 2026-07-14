# cAIc Current WiP Backlog

Last updated: 2026-07-14
Owner: Gramps
Scope: Active roadmap items and backlog.

## Completed

- **B8 (v0.19.3)** — Private Chat mode. Backend skip-DB/skip-RAG/skip-search flag, frontend PRIVATE badge, info popup.
- **WireGuard TLS (v0.19.4)** — Self-signed WireGuard mesh encrypts all inter-node traffic (AMQP, inference, RPC). No code changes to cAIc. Documented in wiki/WireGuard-Setup.md + docker.md §5.4.
- **At-Rest Encryption (v0.20.0)** — AES-256-GCM encrypts all query-derived text at rest. crypto.py with auto-keygen, key stored as `heartbeat_interval_ms` in settings. All 12 storage paths wired (SQLite: messages, conversations, memories, upload_context; Qdrant: RAG chunks, ingest, upload). 200 tests pass.
- **v0.21.0** — Scrollbar/DOM fixes, perplexity persistence per message, all service URLs env-overridable, single-node deployment docs, DOM pairing bugfix, hardware.py Qdrant URL bugfix.

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
