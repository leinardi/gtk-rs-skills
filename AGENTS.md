# AGENTS.md

Project authority for `gtk-rs-skills`. Read this before changing anything here.

For **how to review** a change to this repo, use the `adversarial-review` skill in
`.agents/skills/` — it owns the review procedure and the review checklist.

## What this is

A Claude Code plugin (and a set of open Agent Skills) for building modern GTK 4 + libadwaita
applications in Rust with gtk-rs:

- `skills/gtk-rs-development/` — the main skill: `SKILL.md` holds the rules that apply to every
  change and routes to 17 topic references in `references/`.
- `skills/gtk-rs-new-app/` — scaffolds a new app: `scripts/new_app.py` copies
  `assets/template/` and substitutes `{{TOKEN}}` placeholders.
- `skills/gtk-rs-review/` — a review checklist for gtk-rs code.
- `.claude-plugin/` — plugin and marketplace manifests. `docs/` — human-facing extras.

What ships is **text an agent acts on** and **a template that becomes someone's project**. The
failure that matters is not a lint error: it is guidance that compiles in nobody's project, an API
that does not exist at the pinned version, or a template that generates an app which does not
build, run or pass validation.

## The rules

1. **Every code claim is checked against the pinned versions.** The baseline is gtk4 0.11 /
   libadwaita 0.9 / glib 0.22 (GTK 4.22, libadwaita 1.9, GNOME 50), stated in
   `skills/gtk-rs-development/SKILL.md`. A Rust snippet must `cargo check` (and pass clippy pedantic
   for code a reader would copy) against those crates; a Blueprint snippet must compile with
   `blueprint-compiler`. An API, signature, feature gate or version number is confirmed in the crate
   source (`~/.cargo/registry/src/*/`), the installed GTK/libadwaita docs or the upstream source —
   never from memory. Anything that cannot be checked is either left out or marked `(verify)`.
2. **The template always generates a working app.** After any change under
   `skills/gtk-rs-new-app/`, `make test` and `make template-check` pass: the generated project is
   `cargo fmt`/`clippy -D warnings` clean, builds with Meson and passes `meson test` (cargo test plus
   desktop, metainfo and schema validation). The `template-check` CI job proves the same through
   flatpak-builder on the GNOME 50 SDK.
3. **References agree with the template and with each other.** The template is the canonical
   setup (config via `option_env!`, resource prefix = `resource-base-path`, schema id and path
   derived from the app id, schema compiled in the build dir, `cargo_vars` env). A reference that
   describes the build must match it; a version bump updates the template, `SKILL.md`'s baseline
   line, `deprecated-apis.md` and every reference that names the old version.
4. **Shipped text stays agent-shaped.** `SKILL.md` descriptions say what the skill does *and*
   when to use it, under 1024 characters; `SKILL.md` bodies stay short and route to references;
   references open with "Read this when…", carry a contents list when long, and end with Pitfalls
   and See also. Cross-references use the exact sibling file names.
5. **Attribution is kept.** Material adapted from third parties is listed in
   `THIRD_PARTY_NOTICES.md` with its licence (glimpse's skill is BSD-3-Clause, the gtk-rs book MIT).
   Real-app code is cited by path, not copied.

## Layout

| Path | What it is |
| --- | --- |
| `skills/*/SKILL.md` | Skill entry points (YAML frontmatter + Markdown). |
| `skills/gtk-rs-development/references/*.md` | Topic references loaded on demand. |
| `skills/gtk-rs-new-app/assets/template/` | The project template; `{{TOKENS}}` are substituted by the generator. Exempt from YAML/Markdown linters and the licence-header check. |
| `skills/gtk-rs-new-app/scripts/new_app.py` | The generator; run as `python3 -I`, standard library only. |
| `tests/` | pytest suite for the generator. |
| `.mk/test.mk` | Local make targets (`test`, `template-generate`, `template-check`, `template-flatpak`). |
| `.mk/help.mk`, `.mk/pre-commit.mk`, `scripts/bootstrap-mk-common.sh` | Fetched from `leinardi/make-common`; change them upstream. |

## Commands

```console
make help             # every target
make pre-commit-install
make check            # pre-commit on every file
make test             # generator unit tests (no GTK needed)
make template-check   # generate + fmt + clippy + meson build/test (needs GTK 4.22, libadwaita 1.9, blueprint-compiler)
make template-flatpak # generate + flatpak-builder (GNOME 50 SDK)
```

Verifying snippets: copy the generated app (`make template-generate`, then work in a copy of
`$TMPDIR/gtk-rs-skills-template-check`), put each snippet in a module with the minimum
scaffolding, and run `cargo check` / `cargo clippy`. Never edit the template while experimenting.

## Commits

[Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) with a scope, enforced
by `conventional-pre-commit` (`commit-msg`) and the `conventional-commits` CI job. Scopes in use:
`development`, `new-app`, `template`, `review`, `references`, `plugin`, `docs`, `ci`, `deps`,
`make`, `tests`.

The type decides the release (commitizen, `.cz.toml`): `feat` → minor, `fix`/`perf`/`refactor` →
patch, `!` or `BREAKING CHANGE:` → major; `build`, `chore`, `ci`, `docs`, `revert`, `style` and
`test` bump nothing. What ships is the skills, references and template, so:

- new guidance, a new reference or skill, a new template capability → `feat`;
- wrong guidance, a snippet that does not compile, a broken template → `fix`;
- CI, tests, repo tooling, README/CONTRIBUTING → `ci`, `test`, `build`, `chore` or `docs` — never
  `perf` or `refactor`, which would cut a release for something users do not receive.

## Releases

See [`docs/release.md`](docs/release.md). Tags are `gtk-rs-skills--v<version>`, created by CI from
the version in `.claude-plugin/plugin.json`.
