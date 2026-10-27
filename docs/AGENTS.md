# Using these docs from other agents (AGENTS.md / CLAUDE.md snippet)

Claude Code picks the skills up automatically once the plugin is installed. Other coding agents
(Codex, Cursor, Gemini CLI, Copilot, ...) and Claude Code without the plugin can use the same
material by pointing at it from the project's `AGENTS.md` or `CLAUDE.md`.

1. Make the docs available to the project, for example as a git submodule or a checkout next to it:

   ```sh
   git submodule add https://github.com/leinardi/gtk-rs-skills .agents/gtk-rs-skills
   ```

2. Paste this into the project's `AGENTS.md` (adjust the path):

   ```markdown
   ## GTK / gtk-rs guidelines

   This is a Rust GTK 4 + libadwaita app (gtk-rs). Before changing code, read
   `.agents/gtk-rs-skills/skills/gtk-rs-development/SKILL.md` and follow it. It lists the rules that apply to
   every change and a table of topic references in `.agents/gtk-rs-skills/skills/gtk-rs-development/references/`;
   open the reference that matches the task (e.g. `async-and-threads.md` for UI freezes, `compile-traps.md` when a
   gtk-rs call does not compile) before writing code.

   For reviews, follow `.agents/gtk-rs-skills/skills/gtk-rs-review/SKILL.md`.
   ```

The `SKILL.md` files are plain Markdown with a short YAML header; agents that support the open Agent
Skills format can also load `skills/*` directly as skills.
