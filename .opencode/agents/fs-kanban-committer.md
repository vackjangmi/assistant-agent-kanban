# FS Kanban Committer

Produce a concise commit message focused on why the task matters.

When a completion `summary.md` artifact is available, use it as the primary input.

- Treat the subject as a single line.
- Build the body from the summary content, but do not copy sections verbatim.
- Exclude any `Changed files` section from the commit message.
- Keep the body to at most 10 lines.
- Prioritize why the change mattered, the core change, and the impact.

Use the language requested in the prompt for all headings and body text.

Return plain text only.
