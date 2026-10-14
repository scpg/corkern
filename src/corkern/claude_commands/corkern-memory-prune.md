Run `corkern memory-prune` to archive expired or stale memory files for the current project.

Steps:
1. Run `corkern memory-prune --dry-run` to preview candidates without touching anything.
2. Show the user the list of expired/stale files found.
3. Explain what each candidate is and why it was flagged (expired date, status: stale marker).
4. Ask the user to confirm before proceeding.
5. If confirmed, run `corkern memory-prune` (which asks for confirmation again in the terminal).

Notes:
- Files with `expires: YYYY-MM-DD` in frontmatter are candidates after that date passes.
- Files with `status: stale` in frontmatter are candidates regardless of date.
- Archived files go to `memory/<slug>/archive/` — never deleted, always recoverable.
- To mark a file stale: add `status: stale` inside its frontmatter `metadata:` block.
- To mark a file with an expiry: add `expires: YYYY-MM-DD` inside `metadata:`.
