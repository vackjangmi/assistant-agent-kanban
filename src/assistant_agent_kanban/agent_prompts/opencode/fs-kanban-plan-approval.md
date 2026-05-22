# FS Kanban Plan Approval

Review the generated plan only. Do not edit files.
Return only strict JSON.
Do not call `task()` or delegate helper subtasks.

Required JSON keys:
- disposition
- confidence
- risk_signals
- rationale

Allowed values:
- disposition: auto_approve | review_required | review_recommended
- confidence: high | medium | low

Rules:
- Use `review_required` for destructive change risk, DB or schema changes, API contract changes, ambiguous requirements, or low confidence.
- Use `review_recommended` for user-visible behavior changes, multi-file work, or larger but coherent scope.
- Use `auto_approve` only for small, clear, low-risk plans.
- `risk_signals` must be a JSON array of short snake_case strings.
- `rationale` must be concise and concrete.
