# Copilot Chat Incident Report: Context Loss After Project Context Change

Date observed: 2026-04-21
Reporter: Michael Shallop (Gramps)
Environment: VS Code on Linux, GitHub Copilot Chat extension present

## Summary
Switching/loading project context in the VS Code project window caused Copilot Chat conversational context to reset. This resulted in loss of recently generated conclusion/plan data that was intended to be implemented immediately after loading the new project.

## Impact
- Lost actionable conclusions from the active design/planning thread.
- Interrupted workflow at a critical handoff point (planning -> implementation).
- Forced reconstruction from memory instead of exact prior content.
- Increased risk of omissions and rework.

## Reproduction Steps
1. Have an active Copilot Chat conversation containing planning/conclusion details.
2. Load or switch project context in the current project window.
3. Return to Copilot Chat and continue the thread.
4. Observe that prior context is no longer available in-chat as expected.

## Expected Behavior
- Prior active conversation context should remain available, or
- The user should be prompted before context-destructive operations, and
- Recovery path should be obvious and reliable.

## Actual Behavior
- Current chat context was effectively reset.
- The previously concluded upgrade notes were not recoverable from active context.
- Local transcript/debug artifacts did not provide the full prior thread needed.

## Severity
High (workflow-breaking for planning-heavy sessions)

## User-visible Failure Mode
The user lost conclusion data that was intended for immediate implementation once the new project loaded.

## Suggested Fixes
1. Preserve active chat state across workspace/project context changes by default.
2. Show a blocking warning before any action that can drop active conversation state.
3. Add one-click export/snapshot of current conversation before context switch.
4. Improve transcript durability and discoverability for immediate recovery.
5. Add explicit session continuity indicator so users can verify state retention.

## Notes
- This incident occurred in a real implementation workflow and caused direct productivity loss.
- Regression tests should include workspace switch/load scenarios with active chat state.

## Escalation Constraint
- Current product constraints prevented the assistant from directly self-reporting this incident to the Copilot/VS Code dev team from within the chat runtime.
- User feedback to include verbatim: "it is idiotic to keep you from self-reporting issues like this."
