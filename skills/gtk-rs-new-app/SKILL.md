---
name: gtk-rs-new-app
description: Scaffold a new GNOME-style Rust desktop app with gtk4-rs + libadwaita + Blueprint + Meson + Flatpak, following the layout of well-maintained GNOME Rust apps (app id/Devel profile, GResource, GSettings, desktop/metainfo with validation, icons, i18n, Flatpak manifest, CI, clippy lints). Use whenever the user wants to start, bootstrap, create or template a new GTK 4 / libadwaita / GNOME app in Rust, or asks for a "hello world" gtk-rs project, even if they don't mention Meson or Flatpak. Not for adding features to an existing app (use gtk-rs-development).
---

# New gtk-rs app

Generate a complete, buildable project from the bundled template instead of writing boilerplate by hand: the
template already encodes the conventions of current GNOME Rust apps (and is easy to get subtly wrong — resource
paths, schema ids, Devel profile, Blueprint build ordering).

## 1. Collect the inputs

Ask only for what you cannot infer; propose sensible defaults.

| Input | Example | Notes |
| --- | --- | --- |
| App id | `io.github.alice.Notes` | Reverse DNS, ≥ 3 segments, the last one CamelCase. Must be a domain/repo the user controls (`io.github.<user>.<App>` for GitHub, `page.codeberg.<user>.<App>`, `org.gnome.World.<App>` only for GNOME World). |
| Name | `Notes` | Human-readable; becomes window title, desktop Name. |
| Destination | `~/Projects/notes` | Must be empty or not exist. |
| Summary | `Write down quick notes` | One line, no trailing period (AppStream rule). |
| Developer name / id | `Alice Doe` / `io.github.alice` | Developer id defaults to the app id minus the last segment. |
| Homepage | `https://github.com/alice/notes` | Used for metainfo URLs. |
| License | `GPL-3.0-or-later` | SPDX id; most GNOME apps use GPL-3.0-or-later. |
| Crate name | `notes` | Defaults to kebab-case of the last app id segment; becomes binary, Meson project, gettext domain. |

## 2. Generate

```sh
python3 -I <skill-dir>/scripts/new_app.py \
  --app-id io.github.alice.Notes --name "Notes" --dest ~/Projects/notes \
  --summary "Write down quick notes" --developer "Alice Doe" \
  --homepage https://github.com/alice/notes --license GPL-3.0-or-later
```

`<skill-dir>` is the directory containing this SKILL.md. The script copies `assets/template/`, substitutes the
`{{TOKENS}}` in contents and file names, fails if any placeholder is left, and runs `rustfmt` on
the generated sources when it is installed (long ids would otherwise fail `cargo fmt --check`).

Then initialise git (`git init`, first commit) only if the user wants it.

## 3. What you get

```
Cargo.toml              gtk4 0.11 (gnome_50) + libadwaita 0.9 (v1_9, gtk_v4_22), lints, profiles
meson.build             profile option → .Devel app id + version suffix; post_install
meson.options
src/meson.build         cargo custom_target, cargo-clippy run_target, cargo test, devenv
src/main.rs             tracing → gettext → resources → Application::new().run()
src/config.rs           APP_ID/VERSION/... via option_env! with defaults (plain cargo check works)
src/application.rs      adw::Application subclass: startup actions/accels, about dialog, quit
src/window.rs           CompositeTemplate window, win.say-hello action, window-state save/restore
data/resources/ui/*.blp window.blp + shortcuts-dialog.blp (auto-wired app.shortcuts)
data/resources/         gresource xml (prefix = base path), style.css, bundled metainfo for About
data/*.in(.in)          desktop, metainfo, gschema (id and path = app id) + validation tests
data/icons/             app, Devel and symbolic icons (placeholders)
po/                     POTFILES.in, LINGUAS, gettext setup
build-aux/              Devel Flatpak manifest (GNOME 50), dist-vendor.sh for offline release tarballs
.github/workflows/ci.yml  rustfmt, Flatpak build + tests, clippy inside the SDK
```

## 4. Build and run

```sh
cd ~/Projects/notes
meson setup _build -Dprofile=development
meson compile -C _build
meson devenv -C _build ./src/notes      # run uninstalled (loads build-dir resources + schemas)
meson test -C _build                    # cargo test + desktop/metainfo/schema validation
```

Or with Flatpak: `flatpak-builder --user --install --force-clean _flatpak build-aux/<app-id>.Devel.json`.

Host requirements for Meson builds: Rust ≥ 1.92, GTK ≥ 4.22, libadwaita ≥ 1.9, `blueprint-compiler`,
`desktop-file-utils`, `appstream`. On Ubuntu/Debian: `sudo apt install libgtk-4-dev libadwaita-1-dev
blueprint-compiler desktop-file-utils appstream meson`; on Fedora: `sudo dnf install gtk4-devel libadwaita-devel
blueprint-compiler desktop-file-utils appstream meson`. If the host libraries are older, either build with Flatpak or
lower the scaffold's required versions: the features in `Cargo.toml`, the `dependency()` checks in `meson.build`
and the manifest's `runtime-version` together (e.g. `gnome_48` / `v1_7` for GNOME 48). That is below the tested
baseline, so check every API the app uses against the lower features (`cargo clippy`), and tell the user the
template was only tested on GNOME 50 (`packaging-and-ci.md`, "Tested vs required versions").

Verify the result builds before handing it over; if you cannot build (missing system libraries), say so explicitly
and give the user the install command.

## 5. After generating

- Replace the placeholder icons (`data/icons/`) — App Icon Preview helps; Flathub rejects placeholder icons.
- Fill in the metainfo description, screenshots and `<releases>` before publishing.
- Rename the example `win.say-hello` action and status page into the app's first real feature.
- For everything beyond the scaffold, follow the `gtk-rs-development` skill (start with
  `references/architecture.md` and `references/project-layout-and-build.md`).

## Adapting the template

- **No Flatpak/CI wanted**: delete `build-aux/*.json` and `.github/`; keep `dist-vendor.sh` only if releasing tarballs.
- **GitLab instead of GitHub**: replace `.github/` with a `.gitlab-ci.yml` using GNOME's citemplates
  (`flatpak/flatpak_ci_initiative.yml`) — see `gtk-rs-development/references/packaging-and-ci.md`.
- **Cargo-only (no Meson)**: possible (gtk-rs book style: `glib-build-tools` in `build.rs` +
  `gio::resources_register_include!`), but you lose desktop/metainfo/schema install and validation; only suggest it
  for throwaway prototypes.
- **Large app**: move each widget into its own folder with the `.blp` next to the `.rs`
  (Fractal style) and list them in `data/resources/meson.build` and the gresource XML.
