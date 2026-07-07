# cAIc Current WiP Backlog

Last updated: 2026-07-06
Owner: Gramps
Scope: Active roadmap items and backlog.

## Active Roadmap — Roadmap N: AMQP Cluster Nervous System

| Task | Description | Status |
|------|-------------|--------|
| Task 8  | RAG Corpus Management (score-based eviction, pinned sources, operational stats) | **DONE** |
| Task 9  | RabbitMQ install + exchange setup on coordinator | **DONE** |
| Task 10 | AMQP connection layer in jC (`amqp.py`, aio-pika) | **NEXT** |
| Task 11 | Node agent on worker — registration, heartbeat, command listeners | Pending |
| Task 12 | AMQP wiring: inject context into chat/completions pipeline | Pending |
| Task 13 | Query triage via Phi-4-mini (`triage.py`) | Pending |
| Task 14 | Dynamic model swap — publish cmd, handle ready/failed | Pending |
| Task 15 | Cluster status UI panel in templates/index.html | Pending |

## Backlog (Post-Roadmap N)

- B1 — Context loss in follow-up questions (investigation)
- B2 — Bang-prefixed (`!`) search routing
- B3 — Docker distribution (planning doc at `docker.md`)
- HTTPS / reverse proxy (Caddy)
- Conversation search/filter and export tooling
- Keyboard shortcuts, retry button, source-link polish

## Maintenance Rules
- Keep this file as the single source of truth for roadmap tracking.
- Update as work starts or completes.
