# JarvisChat Current WiP Backlog

Last updated: 2026-04-27
Owner: Gramps + Copilot
Scope: issues, bugs, security exposures, and feature enhancements.

Total identified items: 26

## Priority Definitions
- P0: Critical risk or data-loss/security exposure; do first.
- P1: High impact reliability/correctness work.
- P2: Important feature/UX improvements.
- P3: Nice-to-have polish.

## Top 10 (Urgency Order)
1. [P0][DONE] Add authentication/authorization for all write and admin endpoints.
2. [P0][DONE] Add CSRF/origin protection for browser-initiated state-changing requests.
3. [P0][DONE] Block unsafe URL schemes in rendered search-result links (e.g., javascript:).
4. [P0][DONE] Add rate limiting and request body size limits for chat/search/profile APIs.
5. [P1][DONE] Restrict settings updates to an allowlist of valid keys.
6. [P1] Add pagination + hard caps on list endpoints (memories, conversations, message history).
7. [P1][DONE] Stop returning raw exception text to clients; use safe error envelopes.
8. [P1][DONE] Add automated tests for chat streaming, auto-search trigger, and memory command paths.
9. [P2] Implement skills/tool-call framework (MCP-style) with per-skill enable controls.
10. [P2] Implement heartbeat/check-in pipeline with scheduler + summary endpoint.

## Item 1 Executive Summary (Scope + Security)

- Status: Complete. Guest/admin capability split implemented with admin-only write enforcement, origin checks on state-changing requests, audit logging, and endpoint capability tests.

- Decision: JarvisChat is local-first by design. Primary mode is same-host Ollama; optional mode allows RFC1918 LAN endpoints only.
- Constraint: Public Internet AI endpoints are out of scope unless explicitly enabled in a future advanced mode.
- Risk: Even on LAN, unauthenticated write/admin endpoints permit unauthorized data tampering and deletion.
- Requirement: Add mandatory admin authentication for all POST/PUT/DELETE routes and destructive actions.
- Authentication shape (scope-locked): two capability tiers only: guest (chat-only) and admin (4-digit PIN unlock).
- Scope guardrail: Avoid full RBAC. Keep capability split minimal: conversational chat for guest, advanced/destructive actions for admin.
- Definition of done:
	1. Auth required on all state-changing endpoints.
	2. Destructive actions require admin authorization.
	3. Endpoint configuration rejects non-local/non-RFC1918 AI backends by default.
	4. Strong rate limiting + lockout controls in place for PIN attempts.
	5. Security events logged for failed and successful admin actions.

## Full Backlog (Sorted by Priority)

### P0 Critical
1. Add auth for write/admin endpoints (`POST/PUT/DELETE` routes, mass delete, profile/settings changes).
2. Add CSRF or strict origin checks for browser session protection.
3. Validate/sanitize outbound href URLs before rendering in HTML (allow http/https only).
4. Add per-IP rate limiting on `/api/chat`, `/api/search`, `/api/profile`, `/api/settings`.
5. Enforce request size limits (message/profile text and JSON body) to prevent memory abuse.

### P1 High
6. Add settings key allowlist in `/api/settings` to prevent arbitrary key injection.
7. Add pagination (`limit`, `offset`) with enforced maximums for list APIs.
8. Add DB indexes and query hygiene for scalability (`messages.conversation_id`, timestamps).
9. Replace raw exception leakage to clients with generic safe error messages + server-side logs.
10. Add request/response timeout and retry policy consistency across external calls.
11. Add endpoint-level audit logging for destructive operations.
12. Add unit/integration tests for: remember/forget parsing, refusal detection, search fallback, SSE done/error shape.
13. Add conversation title sanitization and length constraints.
14. Ensure default preset semantics are correct (currently all seeded presets are marked default).

### P2 Important Features
15. Skills system: load markdown skill files with YAML frontmatter from skills directory.
16. Skills registry API: list/enable/disable skills and expose active skills to UI.
17. Inject active skill instructions into system prompt with bounded token budget.
18. Tool execution guardrails: allowlist, confirmation mode, and execution logs.
19. Heartbeat scheduler (cron/systemd timer) for daily check-ins.
20. Heartbeat endpoint for generated briefings and anomaly summaries.
21. Model info UI panel (description, updated date, best-use purpose).
22. Default model selection improvements and persistence validation.
23. Hidden model list support (exclude models from dropdown).
24. Model update action from UI (trigger controlled model pull).

### P3 Nice to Have
25. Conversation search/filter and export tooling.
26. Keyboard shortcuts, retry button, and source-link polish.

## Maintenance Rules
- Keep this file as the single source of truth.
- Update item priority/status whenever work starts or completes.
- Mirror the Top 10 summary in README and keep counts aligned.
