Run `corkern harvest` to extract memories from past Claude conversations for the current project.

Steps:
1. Run `corkern prune` first to show which conversations are new, changed or already harvested.
2. Run `corkern harvest` (detects the project from the current directory). With several conversations it asks which to process — answer `all` or numbers.
3. Review the new memory files it lists in `memory/<slug>/` of the knowledge repo; they are already added to `MEMORY.md`. Existing files are never overwritten (a new version gets a `.harvested-<time>` suffix) — merge those with the user.
4. If it saved a `harvested-<date>.md` bulk file instead, run `corkern refine`.

Flags:
- `corkern harvest --interactive` — opens an interactive Claude session with the conversations as context, so the user can add their own recollections.
- `corkern harvest --all` — reprocesses already harvested conversations.

Secrets in transcripts are replaced by a marker before anything is committed. After harvesting, run `/corkern-distill` to check whether any extracted memories should be promoted to global.
