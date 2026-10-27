---
name: gtk-rs-development
description: Modern gtk-rs (gtk4-rs + libadwaita) application development guide — architecture, GObject subclassing, properties/signals, clone! and memory, async/threads, Blueprint composite templates, actions, list models, libadwaita adaptive UI, CSS, GSettings, i18n, accessibility, Meson/Flatpak packaging, testing and compile-error diagnosis, distilled from the gtk-rs book and well-maintained GNOME Rust apps (Fractal, Amberol, Authenticator, Shortwave, Pika Backup, Identity...). Use it for ANY change to a Rust GTK 4 app — whenever Cargo.toml depends on gtk4/libadwaita, sources `use gtk::`/`adw::`, or the repo has .blp/.ui templates — including requests phrased only as symptoms ("the list doesn't update", "UI freezes while loading", "window opens too small", "this clone! won't compile", "memory keeps growing"), new widgets/features, refactors, reviews and porting old gtk-rs code. Also use it when answering gtk-rs questions even if no repo is open.
---

# gtk-rs development

Build GTK 4 / libadwaita apps in Rust the way current, well-maintained GNOME apps do. This file holds the rules that
apply to every change; topic details live in `references/` — open the one that matches the task before writing code.

Baseline: **gtk4 0.11 / libadwaita 0.9 / glib 0.22**, GTK 4.22, libadwaita 1.9 (GNOME 50), Rust edition 2024,
Blueprint templates, Meson + Flatpak. For a brand-new project use the `gtk-rs-new-app` skill; to audit code use
`gtk-rs-review`.

## First, orient yourself in the project

1. Read `Cargo.toml`: crate versions and **version features** (`gtk` `gnome_XX`/`v4_XX`, `adw` `v1_X` and `gtk_v4_XX`).
   They decide which APIs exist. Do not use a widget or method newer than the enabled feature; if you need one,
   raise the feature *and* the Meson `dependency()` check *and* the Flatpak runtime together.
2. Detect the house style and follow it rather than imposing the template's: Blueprint vs `.ui` XML vs widgets built
   in code; `mod imp` inline vs `foo/imp.rs`; `tracing` vs `log`; tokio present or not; `config.rs.in` vs `option_env!`.
3. Note the app id and resource base path (`resource-base-path`, gresource `prefix`) — templates, CSS and icons
   resolve against it.

## Verify, don't recall

gtk-rs changes a lot between 0.x releases and much code online is outdated. Before writing a call you are not sure
of, grep the crate source that cargo already downloaded (`~/.cargo/registry/src/*/gtk4-0.11.*/src/auto/...`;
macro syntax in `glib-macros-*/src/lib.rs`, `gtk4-macros-*/src/lib.rs`) and confirm with `cargo check`. When a call
fails to compile, look the error up in `references/compile-traps.md` before rewriting it. When touching code that
looks old, check `references/deprecated-apis.md`.

## Rules that hold everywhere

**Threading.** GTK runs on one thread and widgets are `!Send`. Never block the main loop: use `gio` async APIs
(`*_future`) or `glib::spawn_future_local` for UI-side async, `gio::spawn_blocking` (or a worker thread +
`async_channel`) for blocking/CPU work, and a global tokio runtime only when a dependency requires tokio. Results come
back to widgets on the main thread. Details: `async-and-threads.md`.

**Memory.** Signal closures are `'static` and GObjects are refcounted, so closures that capture the object that owns
the signal source create cycles. Capture with `glib::clone!` using the attribute syntax (`#[weak]`,
`#[weak(rename_to = imp)] self`, `#[upgrade_or]`...). Store `SignalHandlerId`s / `Binding`s for anything connected to a
longer-lived object and disconnect them in `dispose` or when rebinding. Never hold a `RefCell` borrow across a signal
emission, `items_changed`, a callback, or an `.await`. Details: `closures-and-lifetimes.md`.

**GObject shape.** `mod imp` + `glib::wrapper!` listing every ancestor and interface; `#[derive(glib::Properties)]`
with `#[glib::derived_properties]`; signals in a `LazyLock<Vec<Signal>>`; every override chains up
(`parent_constructed()`, `parent_startup()`, ...) — first, unless the reference says otherwise. `OnceCell` for
set-once fields, `glib::WeakRef` for back-references. Details: `gobject.md`.

**Data flow.** Model objects expose properties; widgets bind to them (`bind_property(...).sync_create()`, Blueprint
`bind`, expressions) instead of pushing values by hand from many places. Behaviour is exposed as **actions**
(`app.*` via `gio::ActionEntry` in `startup`, `win.*`/widget actions via `klass.install_action`) so menus, buttons and
shortcuts share one code path. Details: `architecture.md`, `actions-and-shortcuts.md`.

**UI.** Composite templates in Blueprint, loaded from the GResource; libadwaita widgets first (`ToolbarView`,
`HeaderBar`, `NavigationSplitView` + `Breakpoint`, `AlertDialog`, `PreferencesDialog`, `Toast`, `StatusPage`,
`AboutDialog::from_appdata`); `ListView` + models for anything large; style via classes and libadwaita CSS variables,
never hard-coded colors. All user-visible strings translatable; icon-only buttons get a tooltip/accessible label.
Details: `templates-and-blueprint.md`, `libadwaita-ui.md`, `lists-and-models.md`, `css-drawing-animation.md`,
`accessibility-and-i18n.md`.

**Platform.** Design for Wayland and Flatpak: no window positioning, no global grabs, files through `gtk::FileDialog`
(portal), other access through portals (ashpd); state under XDG dirs (`glib::user_data_dir()` etc.); preferences in
GSettings with schema id = app id. Details: `platform-integration.md`, `settings-and-state.md`.

**Errors.** Library code returns `Result`; the UI layer decides how to show it (toast for transient, `AlertDialog`
for blocking) and logs once with `tracing`. A dismissed/cancelled dialog is not an error. `unwrap()` only for true
invariants (template children, resources compiled into the binary).

## Which reference to open

| Task / symptom | Reference |
| --- | --- |
| App/window structure, module layout, where state lives, error handling, logging | `references/architecture.md` |
| Subclassing, properties, signals, enums/boxed types, base classes, interfaces | `references/gobject.md` |
| `clone!`, weak refs, leaks, handler disconnects, `RefCell` panics, dispose | `references/closures-and-lifetimes.md` |
| GSettings, window state, persisting data (JSON/sqlite) | `references/settings-and-state.md` |
| UI freezes, background work, tokio, channels, cancellation, timeouts, async dialogs | `references/async-and-threads.md` |
| Portals, D-Bus, notifications, background, secrets, MPRIS, networking, GStreamer | `references/platform-integration.md` |
| Cargo/Meson setup, config constants, GResource, Blueprint build, schemas, desktop/metainfo, icons | `references/project-layout-and-build.md` |
| Flatpak manifests, Flathub offline builds, CI, release checklist | `references/packaging-and-ci.md` |
| Tests, headless runs, inspector, criticals/warnings, leaks, profiling | `references/testing-and-debugging.md` |
| Blueprint syntax, composite templates, template callbacks, bindings/expressions | `references/templates-and-blueprint.md` |
| Actions, menus, accelerators, shortcuts dialog | `references/actions-and-shortcuts.md` |
| ListView/GridView/ColumnView, factories, filters/sorters, custom ListModel, boxed lists | `references/lists-and-models.md` |
| Adaptive layout, navigation, dialogs, preferences, toasts, about, style manager | `references/libadwaita-ui.md` |
| CSS, style classes, custom drawing (snapshot), measure/allocate, animations | `references/css-drawing-animation.md` |
| Accessibility, translations (gettext), POTFILES, RTL | `references/accessibility-and-i18n.md` |
| A gtk-rs call does not compile | `references/compile-traps.md` |
| Old API / porting old code / "is this still current?" | `references/deprecated-apis.md` |

Load only what the task needs; most tasks need one or two references.

## Workflow for a change

1. Orient (above), then open the matching reference(s).
2. Write the change in the project's existing style. Prefer extending an existing widget/model over parallel ad-hoc
   state. New widget → new subclass with its own template; new behaviour → action; new preference → GSettings key
   (+ `POTFILES.in` / schema updates).
3. Keep generated/registered things in sync: new `.blp` → Meson blueprint list + gresource XML (+ `POTFILES.in`);
   new GSettings key → schema; new translatable `.rs` file → `POTFILES.in`; new custom type referenced only from a
   template → `ensure_type()`.
4. Check: `cargo fmt`, `cargo clippy --all-targets -- -D warnings`, `cargo test`; if the project uses Meson,
   `meson compile -C _build` and `meson test -C _build` (validates desktop/metainfo/schema). Run the app when the change
   is visible (`meson devenv -C _build ./src/<bin>` or the Flatpak) and watch the terminal for GTK criticals.

## Definition of done

- No blocking I/O or long computation on the main thread; worker threads never touch widgets.
- Every closure stored in a longer-lived object captures weakly or is disconnected; no `RefCell` borrow is held across a
  callback, emission or `.await` (`clippy::await_holding_refcell_ref` is clean).
- Overrides chain up; `glib::wrapper!` lists the full ancestry; template child names match the template.
- New strings are translatable and their files are in `POTFILES.in`; icon-only buttons have tooltips.
- No API from `deprecated-apis.md`'s left column in new code; nothing newer than the enabled crate features.
- `cargo clippy -D warnings` and the Meson tests pass; running the app prints no new GTK criticals or warnings.

## Delegating to subagents

A subagent does not inherit this skill. When you hand gtk-rs work to one, tell it to load the `gtk-rs-development`
skill (and name the relevant reference files) before writing code.
