Run `corkern distill` to synthesise cross-project insights from all corkern memory files.

Steps:
1. Ask the user whether to run interactively or auto-save:
   - **Auto** (saves to `memory/global/synthesis.md`): run `corkern distill --auto`
   - **Interactive** (review with Claude): run `corkern distill`
2. Run the chosen command via Bash.
3. If `--auto` was used, read `memory/global/synthesis.md` in the knowledge repo (`$CORKERN_MEMORY_DIR`) and summarise the findings to the user.
4. For any **Stale Candidates** identified: offer to add `status: stale` to their frontmatter — one file at a time, with user confirmation for each.
5. For any **Universal Principles** identified (rules appearing in 2+ projects): offer to add them to `memory/global/principles.md` and remove them from the individual project memory files — with user confirmation.

Always confirm with the user before modifying any memory file.
