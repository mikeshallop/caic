# JarvisChat TODO

## Current Tasks

1. Verify SearXNG and Docker services persist across reboots
   - Expand refusal patterns: "As an AI model", "based on my training data", "I don't have the capability"
2. Conversation search/filter by keyword
3. Export conversation to markdown/text
4. Keyboard shortcuts (Ctrl+N new chat, Ctrl+Enter send)
5. Retry button on assistant messages
6. Source links — clickable links when search used
7. Allow conversation renaming
8. Multiple profiles — coding/sysadmin/general
9. Auto-generate conversation tags (client-side KWIC, top 5, filterable badges)
10. Image input support — pull vision model, file input/drag-drop, base64 encode, pass `images` array to Ollama `/api/chat`
11. Split-screen option for btop display
12. Skills as markdown files — `/opt/jarvischat/skills/`, YAML frontmatter + instructions, injected into context for tool calls
13. Heartbeats / proactive check-ins — cron + endpoint for daily briefings, HA anomaly alerts
14. Model info button — (i) icon next to Model dropdown, shows div with model description, last updated date, best-use purpose
15. Set default model — toggle any model as the default selection
16. Hide/remove model from list — exclude models from dropdown
17. Update model function — trigger `ollama pull` for selected model from UI
18. Add mouseover tooltip to SEND button

## Completed

- ✓ Explicit web search button + orange styling (v1.5.0)
- ✓ Add `profile.example.md` (v1.4.0)
- ✓ Mass-delete conversation history (v1.3.0)
- ✓ Token count estimate before sending (v1.2.9)
