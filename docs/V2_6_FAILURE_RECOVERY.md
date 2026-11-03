# Alex v2.6 — Agent Failure Recovery & Resilience

Adds bounded recovery for failed, timed-out, and uncertain agent results.

## Rules
- Only failed/timeout/uncertain results are retry candidates.
- Recovery is bounded by `max_retries` (default 2).
- Exhausted retries escalate and pause the mission rather than looping forever.
- Non-retryable states stop recovery.
- Recovery never grants new tools, permissions, or budget.
- Idempotency remains the protection against duplicate requests.
