# Developer Architecture Guide

This document explains how JarvisChat is structured, why key guardrails exist, and what the test suite validates.

## 1. System Overview

JarvisChat is a single-process FastAPI service with a Jinja2 frontend and SQLite persistence.

Primary files:

- `app.py`: API, middleware, streaming/chat logic, auth, memory, skills, and DB bootstrap
- `templates/index.html`: main WebUX, settings panels, auth flow, streaming UI handlers
- `jarvischat.db`: runtime SQLite database created and migrated at startup

Core runtime integrations:

- Ollama for chat/model interaction
- SearXNG for web search (optional)
- wttr.in for weather shortcut queries
- rocm-smi for GPU stats when available

## 2. Request/Response Architecture

### 2.1 Chat Pipeline (`/api/chat`)

1. Validate session, role, origin, rate, and payload limits in middleware
2. Persist user message and conversation metadata
3. Build system prompt from enabled profile, memory context, and active skills metadata
4. Stream model response over SSE token-by-token
5. Evaluate uncertainty/refusal; if needed, trigger search augmentation and stream augmented result
6. Persist final assistant message and emit terminal SSE event

### 2.2 Explicit Search Pipeline (`/api/search`)

1. Persist search-as-message into the target/new conversation
2. Emit `searching` SSE event
3. Pull web results from SearXNG
4. Summarize with Ollama via SSE stream
5. Persist summary and emit `done` event (plus raw results payload)

### 2.3 Settings/Control Surface

- Profile, presets, memory, conversation management, and settings APIs
- Skills APIs for phase-1 registry and enable/disable controls
- Auth/session APIs for guest/admin role handling and keepalive

## 3. Data Model (SQLite)

Key tables:

- `conversations`: conversation headers and timestamps
- `messages`: ordered chat history entries
- `profile`: singleton row for injected profile prompt
- `settings`: runtime toggles and selected defaults
- `system_presets`: named reusable system prompts
- `skills`: per-skill enabled state and timestamp
- `memories` (FTS5 virtual table): searchable user memory facts

Design notes:

- Startup is idempotent: tables are created if missing and defaults seeded only when absent
- No connection pool: each request opens a short-lived SQLite connection

## 4. Security Implementations

This section documents explicit controls currently in code.

### 4.1 Auth Model

- Guest session is default for conversational access
- Admin unlock uses 4-digit PIN and creates admin-capable session
- Admin required for write/destructive routes
- Session heartbeat/timeout and explicit logout/revoke flow

### 4.2 PIN and Session Hardening

- Admin PIN hashed with PBKDF2-HMAC-SHA256 + salt
- Failed PIN attempts tracked per client IP
- Lockout window enforced after max failed attempts

### 4.3 Browser and API Abuse Controls

- Origin checks on state-changing requests
- Rate limiting by endpoint category and identity (IP/session)
- Payload size limits per route class
- Settings key allowlist to block arbitrary configuration injection
- IP allowlist/CIDR gate with optional trusted proxy forwarding mode

### 4.4 Output and Error Safety

- Search result URLs sanitized to `http`/`https` only
- Client-safe error envelopes with incident key correlation
- Full stack traces and diagnostic metadata logged server-side only

### 4.5 Operational Auditability

- Structured audit events for auth actions, admin operations, and guardrail denials
- Incident logs include event type, key, path/method context, and runtime metadata

## 5. Skills Framework (Phase 1)

Goal: introduce a governed skills control plane inside the local JarvisChat sandbox.

Current behavior:

- Built-in skill registry defined server-side
- Per-skill enable/disable persisted in DB
- Global `skills_enabled` master toggle in settings
- Active skills injected into system prompt with bounded text budget
- API endpoints to list skills, list active skills, and toggle skill state
- WebUX settings panel to control master/per-skill toggles

Non-goals in phase 1:

- No unrestricted shell/tool execution
- No external connector execution (filesystem, Gmail, etc.)

## 6. Testing Strategy and Validation Intent

The test suite validates both behavior and guardrail assumptions.

### 6.1 What We Test

- Auth capability separation (guest vs admin)
- URL sanitization safety for outbound links
- Rate and payload guardrails
- IP allowlist behavior
- Safe error envelope behavior and SSE error leakage prevention
- Streaming chat/search and memory command paths
- Skills framework toggles and prompt-injection behavior

### 6.2 Why These Tests Matter

- Confirms security controls are active and regression-resistant
- Ensures streaming UX protocol remains stable (`token`, `searching`, `done`, `error`)
- Verifies policy intent: dangerous actions require admin capability
- Validates new features preserve prior guarantees

### 6.3 Internal Process Validation

For substantive changes, Definition of Done includes:

1. Implement code change
2. Add/adjust tests proving behavior and guardrail intent
3. Update README release notes for user-facing impact
4. Update wiki architecture/security/testing docs for maintainers
5. Validate with targeted test runs before merge/deploy

This process is intentionally explicit so design decisions remain auditable over time.

## 7. Deployment and Operations Notes

- Primary deployment target: local/homelab systemd service
- Required dependency: Ollama
- Optional dependency: SearXNG
- Recommended log review path: system journal for startup, guardrail denials, and incidents

## 8. Contribution Guidance

When adding a feature:

1. Define security posture first (who can execute, what can fail, and failure mode)
2. Implement smallest safe slice with clear limits
3. Add tests that prove both happy path and guardrail path
4. Update this wiki and README in the same change