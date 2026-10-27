# gtk-rs skills

Agent skills and reference documentation for building modern GTK 4 + libadwaita applications in Rust
with [gtk-rs](https://gtk-rs.org). Distilled from the official [gtk-rs
book](https://gtk-rs.org/gtk4-rs/stable/latest/book/) and from well-maintained GNOME Rust apps
listed on [gtk-rs.org](https://gtk-rs.org/) (Fractal, Amberol, Authenticator, Shortwave, Pika
Backup, Warp, Fragments, Identity, Video Trimmer, Mission Center, Podcasts, Helvum, Icon Library,
Contrast, Solanum).

Targets **gtk4 0.11 / libadwaita 0.9 / glib 0.22** (GTK 4.22, libadwaita 1.9, GNOME 50), Rust
edition 2024, Blueprint, Meson and Flatpak.

## Skills

| Skill | Use it for |
| --- | --- |
| [`gtk-rs-development`](skills/gtk-rs-development/SKILL.md) | Any change to a gtk-rs app: rules that apply everywhere, plus 17 topic references loaded on demand. |
| [`gtk-rs-new-app`](skills/gtk-rs-new-app/SKILL.md) | Scaffolding a new app from a tested template (Meson, Cargo, Blueprint, GSettings, metainfo, Flatpak, CI). |
| [`gtk-rs-review`](skills/gtk-rs-review/SKILL.md) | Reviewing gtk-rs code or diffs for freezes, leaks, re-entrancy panics, outdated APIs and packaging mistakes. |

### References (`skills/gtk-rs-development/references/`)

| Topic | File |
| --- | --- |
| App/window structure, module layout, data flow, errors, logging | [architecture.md](skills/gtk-rs-development/references/architecture.md) |
| Subclassing, properties, signals, enums/boxed, base classes | [gobject.md](skills/gtk-rs-development/references/gobject.md) |
| `clone!`, weak refs, handler lifetimes, `RefCell` re-entrancy | [closures-and-lifetimes.md](skills/gtk-rs-development/references/closures-and-lifetimes.md) |
| GSettings, window state, persistence | [settings-and-state.md](skills/gtk-rs-development/references/settings-and-state.md) |
| Main loop, threads, tokio, channels, cancellation | [async-and-threads.md](skills/gtk-rs-development/references/async-and-threads.md) |
| Portals, D-Bus, notifications, secrets, GStreamer | [platform-integration.md](skills/gtk-rs-development/references/platform-integration.md) |
| Cargo + Meson, config constants, GResource, Blueprint build, data files | [project-layout-and-build.md](skills/gtk-rs-development/references/project-layout-and-build.md) |
| Flatpak, Flathub, CI, releases | [packaging-and-ci.md](skills/gtk-rs-development/references/packaging-and-ci.md) |
| Tests, headless runs, inspector, warnings, leaks, profiling | [testing-and-debugging.md](skills/gtk-rs-development/references/testing-and-debugging.md) |
| Blueprint and composite templates | [templates-and-blueprint.md](skills/gtk-rs-development/references/templates-and-blueprint.md) |
| Actions, menus, accelerators, shortcuts dialog | [actions-and-shortcuts.md](skills/gtk-rs-development/references/actions-and-shortcuts.md) |
| List views, factories, models, filters and sorters | [lists-and-models.md](skills/gtk-rs-development/references/lists-and-models.md) |
| Adaptive libadwaita UI, dialogs, preferences, toasts | [libadwaita-ui.md](skills/gtk-rs-development/references/libadwaita-ui.md) |
| CSS, custom drawing, layout, animations | [css-drawing-animation.md](skills/gtk-rs-development/references/css-drawing-animation.md) |
| Accessibility and internationalisation | [accessibility-and-i18n.md](skills/gtk-rs-development/references/accessibility-and-i18n.md) |
| Compile errors keyed by error text | [compile-traps.md](skills/gtk-rs-development/references/compile-traps.md) |
| Deprecated APIs and their replacements | [deprecated-apis.md](skills/gtk-rs-development/references/deprecated-apis.md) |

The references are plain Markdown and work as human documentation too.

## Installation

### Claude Code (plugin)

```text
/plugin marketplace add leinardi/gtk-rs-skills
/plugin install gtk-rs-skills@gtk-rs-skills
```

For a local checkout: `/plugin marketplace add /path/to/gtk-rs-skills`.

### Claude Code (without the plugin system)

Copy or symlink the skill directories into `~/.claude/skills/` (all projects) or
`<project>/.claude/skills/`:

```sh
ln -s "$PWD/skills/gtk-rs-development" ~/.claude/skills/gtk-rs-development
ln -s "$PWD/skills/gtk-rs-new-app" ~/.claude/skills/gtk-rs-new-app
ln -s "$PWD/skills/gtk-rs-review" ~/.claude/skills/gtk-rs-review
```

### Other agents

The skills follow the open Agent Skills layout (`SKILL.md` + resources). For agents that only read
`AGENTS.md`, see [docs/AGENTS.md](docs/AGENTS.md) for a snippet that points them at the guide.

## Scaffolding a new app

Ask your agent to "create a new GTK app called Notes", or run the generator yourself:

```sh
python3 -I skills/gtk-rs-new-app/scripts/new_app.py \
  --app-id io.github.you.Notes --name Notes --dest ~/Projects/notes
cd ~/Projects/notes
meson setup _build -Dprofile=development && meson compile -C _build
meson devenv -C _build ./src/notes
```

The generated project passes `cargo clippy -- -D warnings` (pedantic) and `meson test` (desktop,
metainfo and schema validation), and runs without GTK warnings.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md). In short:

```sh
make pre-commit-install   # pre-commit + commit-msg hooks
make check                # every pre-commit hook on every file
make test                 # generator unit tests
make template-check       # generate an app from the template, lint, build and test it
```

The guidance pins specific crate versions. When gtk4-rs/libadwaita release new versions, bump the
template (`Cargo.toml` features, `meson.build` checks, Flatpak runtime), the baseline line in
`skills/gtk-rs-development/SKILL.md` and `deprecated-apis.md`, re-verify the snippets, and run
`make template-check`. Releases are described in [docs/release.md](docs/release.md).

## License

MIT, see [LICENSE](LICENSE). Some material is adapted from third-party sources; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
