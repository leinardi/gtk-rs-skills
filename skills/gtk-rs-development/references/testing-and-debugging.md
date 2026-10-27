# Testing and debugging gtk-rs apps

Read this when writing tests (plain logic or GTK widgets), running them headless or in CI, chasing GLib
criticals/warnings, leaks or slow frames, or decoding a runtime warning printed by GTK, GLib or libadwaita.

Contents: 1. Test pyramid · 2. GTK-free unit tests · 3. `#[gtk::test]` · 4. Settings, resources and the
Meson test env · 5. Headless runs · 6. Debug environment variables · 7. GTK Inspector · 8. Finding where a
critical comes from · 9. Leaks · 10. Profiling · 11. Common runtime warnings · Pitfalls · See also

## 1. Test pyramid

Put most logic where it needs neither a display nor GTK: parsing, state machines, formatting, storage,
D-Bus payloads, and GObject models built on `glib::Object`/`gio::ListStore`. Those run under plain
`cargo test`, in parallel, anywhere. Keep a thin layer of `#[gtk::test]` tests for widget behaviour that
genuinely needs GTK (actions, template wiring, property bindings), and accept that they need a display.

Practical consequence for architecture: keep models in modules that import `glib`/`gio` only, not `gtk`
or `adw` (fractal's `session/model/` vs `session/view/`). See `architecture.md`.

## 2. GTK-free unit tests

```rust
#[cfg(test)]
mod tests {
    use gtk::glib;
    use super::*;

    #[test]
    fn store_keeps_items_sorted() {
        // A GObject model (glib::Object subclass over gio::ListStore): no gtk::init(), no display.
        let list = TaskList::new();
        list.add(Task::new("b"));
        list.add(Task::new("a"));
        let first = list.item(0).and_downcast::<Task>().unwrap();
        assert_eq!(first.title(), "a");
    }

    #[test]
    fn loader_completes() {
        // A private context per test: tests run on parallel threads, and only one thread can own a
        // context. block_on() acquires it and makes it thread-default, so spawn_future_local works.
        let ctx = glib::MainContext::new();
        let result = ctx.block_on(async { load_from_str("…").await });
        assert!(result.is_ok());
    }
}
```

Why the private context: `glib::MainContext::default()` is process-global. Once any `#[gtk::test]` in the
same test binary has run, the GTK test thread owns it for the rest of the process, and a plain `#[test]`
that calls `MainContext::default().block_on(...)`, `glib::spawn_future_local` or `timeout_add_local` on it
panics or fails to acquire it. `MainContext::new()` avoids the contention entirely.

GVariant encode/decode, `glib::Bytes`, `glib::DateTime`, `gio::File` on temp dirs and serde round trips all
belong here too.

## 3. `#[gtk::test]`

What exists in gtk4 0.11 (checked in `gtk4-macros-0.11.5/src/lib.rs` and `gtk4-0.11.5/src/lib.rs`):

- `#[gtk::test]` attribute macro: wraps the function in a normal `#[test]` whose body calls
  `gtk::test_synced(move || { … })`.
- `gtk::test_synced` (public but `#[doc(hidden)]`): lazily creates a one-thread `glib::ThreadPool`, runs
  `gtk::init()` on that thread once, then runs every GTK test body on it, propagating panics back to the test
  harness. All GTK tests in a test binary therefore run serially on one thread, while plain `#[test]`s keep
  running in parallel on others.
- `async fn` is supported: the body runs via `glib::MainContext::default().block_on(...)` on the GTK thread.
- The macro resolves the crate name with `proc-macro-crate`, so it works with the `gtk = { package = "gtk4" }`
  rename. There is no `adw::test`; call `adw::init()` yourself (it calls `gtk::init()`, which is a no-op on
  the already initialized thread, then `adw_init()`).

```rust
#[cfg(test)]
mod tests {
    use adw::prelude::*;
    use gtk::{gio, glib};

    use super::*;

    fn drain_events() {
        let ctx = glib::MainContext::default();
        while ctx.pending() {
            ctx.iteration(false);
        }
    }

    #[gtk::test]
    fn say_hello_action_adds_a_toast() {
        adw::init().expect("Failed to initialize libadwaita");
        crate::test_utils::register_resources(); // templates need the bundle, see section 4

        // Windows need a registered application ("New application windows must be added after the
        // GApplication::startup signal" otherwise). NON_UNIQUE avoids talking to a running instance.
        let app = crate::Application::new();
        app.set_flags(gio::ApplicationFlags::NON_UNIQUE);
        app.register(gio::Cancellable::NONE).unwrap();
        let window = Window::new(&app);
        WidgetExt::activate_action(&window, "win.say-hello", None).unwrap();
        drain_events();
        // assert on observable state: a property, a model, a template child's state
    }

    #[gtk::test]
    async fn loading_fills_the_model() {
        let page = LibraryPage::new();
        page.reload().await;
        assert!(page.model().n_items() > 0);
    }
}
```

Notes:

- `gtk::init()` needs a display. Without one, the init job's panic ("Tests failed to initialize gtk:
  … Failed to initialize GTK") is only printed to stderr; the GTK tests then fail with "GTK has not been
  initialized" panics or a flood of criticals, and the test binary can die with SIGSEGV (observed with
  gtk4 0.11.5). Run them under a headless display (section 5). With no `WAYLAND_DISPLAY` set, GTK still
  tries `$XDG_RUNTIME_DIR/wayland-0`, so on a desktop the tests quietly use your session.
- Using `#[test]` + `gtk::init()` instead works for exactly one test thread; a second thread panics with
  "Attempted to initialize GTK from two different threads.", and any GTK call off that thread panics with
  "GTK may only be used from the main thread." `--test-threads=1` does not help, because libtest still uses
  a fresh thread per test. Use the macro.
- Widgets in tests are usually unrealized and unmapped: allocation-dependent assertions (sizes, visible
  rows of a `ListView`) need the widget in a presented window plus event draining, and are flaky in CI. Test
  the model or the action instead.
- `window.activate_action` is ambiguous on `ApplicationWindow` (`WidgetExt` vs `ActionGroupExt`, E0034);
  use the fully qualified call as above (see `compile-traps.md`).
- Creating the application in tests is fine (pika-backup `src/utils/borg.rs` does); `register()` emits
  `startup` (actions, accels) without `run()`, which would parse the harness arguments and block.
- Exemplars: fractal `src/login/*.rs` (`#[gtk::test]` + resource loading helper `src/utils/tests.rs`),
  pika-backup (async `#[gtk::test]`).

## 4. Settings, resources and the Meson test env

The template's `cargo-test` already runs with `GSETTINGS_SCHEMA_DIR=<build>/data` and
`GSETTINGS_BACKEND=memory` (added on top of `cargo_vars` only for the test), so under `meson test`
`gio::Settings::new(config::APP_ID)` finds the build-dir schema and nothing reaches the user's dconf. GIO
reads these variables once at startup, so they belong in the `test()` env, never in code inside a test, and
never in `cargo_env` (exported by `meson devenv`, where a memory backend would stop the app from saving).

Composite templates also need the gresource bundle. Give tests the build root as a constant (fractal sets
`BUILD_DIR` in `config.rs.in`):

```meson
# src/meson.build: one more key in cargo_vars
'BUILD_ROOT': meson.project_build_root(),
```

```rust
// src/test_utils.rs, #[cfg(test)]
pub fn register_resources() {
    static ONCE: std::sync::Once = std::sync::Once::new();
    ONCE.call_once(|| {
        let root = option_env!("BUILD_ROOT").expect("build through Meson first");
        let res = gio::Resource::load(format!("{root}/data/resources/resources.gresource")).unwrap();
        gio::resources_register(&res);
    });
}

/// Explicit in-memory settings, independent of the process environment
/// (useful for plain `cargo test` inside `meson devenv`).
pub fn memory_settings() -> gio::Settings {
    let root = option_env!("BUILD_ROOT").expect("build through Meson first");
    let source = gio::SettingsSchemaSource::from_directory(
        format!("{root}/data"),
        gio::SettingsSchemaSource::default().as_ref(),
        false,
    )
    .unwrap();
    let schema = source.lookup(crate::config::APP_ID, false).expect("schema not compiled");
    gio::Settings::new_full(&schema, Some(&gio::memory_settings_backend_new()), None)
}
```

Injecting the settings object (constructor argument or property) instead of calling
`gio::Settings::new(APP_ID)` deep inside widgets keeps tests independent of the environment.

Running: `meson test -C _build` (all), `meson test -C _build cargo-test -v` (just Rust, with output),
`meson test -C _build --suite lint`. Pass test filters with
`meson test -C _build cargo-test --test-args 'window::'`. Inside `meson devenv -C _build`, plain
`cargo test` shares caches and constants.

## 5. Headless runs

```sh
# X11 server in memory; dbus-run-session gives GIO/a11y a session bus
xvfb-run -a -s "-screen 0 1024x768x24" dbus-run-session -- meson test -C _build --print-errorlogs

# Headless Wayland compositor (fractal .gitlab-ci/test.yml); flags differ between mutter versions
export XDG_RUNTIME_DIR="$(mktemp -d)"
dbus-run-session -- mutter --headless --wayland --no-x11 --virtual-monitor 1024x768 -- \
    meson test -C _build
```

- GTK picks Wayland when `WAYLAND_DISPLAY` is set and falls back to X11 via `DISPLAY`; force with
  `GDK_BACKEND=wayland` or `GDK_BACKEND=x11` to make failures explicit.
- `xvfb-run` is the most common choice today (pika-backup, packetry, `flatpak-github-actions` `run-tests`),
  but it relies on GTK's X11 backend. Upstream has been steering everything towards Wayland and the X11 and
  Broadway paths are on their way out: the X11 backend is deprecated since GTK 4.18 and the Broadway renderer since
  GTK 4.20 (GTK NEWS),
  so prefer a headless Wayland compositor for new CI setups. Broadway (`gtk4-broadwayd` + `GDK_BACKEND=broadway`)
  is not a practical test backend.
- `GTK_A11Y=none` silences AT-SPI bus warnings when no accessibility bus exists.
- Inside Flatpak: `flatpak-builder` test steps have no display. Either add
  `"test-args": ["--socket=x11", "--share=network"]` to the `build-options` (top-level or the module's; not the
  module itself, where flatpak-builder ignores it) and wrap the build in `xvfb-run`
  (warp, pika-backup), use the GitHub action's `run-tests: true`, or run GTK tests in a separate job on a
  host image with GTK installed.
- Set `LANG=C.UTF-8` in CI so gettext and sorting behave deterministically.

## 6. Debug environment variables

| Variable | Effect |
| --- | --- |
| `RUST_LOG=my_app=debug` | `tracing` filter (template default: `my_app=info,warn`); the Devel Flatpak sets it |
| `RUST_BACKTRACE=1` | backtrace on panic |
| `G_MESSAGES_DEBUG=all` (or `Gtk`, `Adwaita`, `GLib-GIO`) | show GLib/GTK `debug`/`info` messages; independent of `RUST_LOG` |
| `G_DEBUG=fatal-criticals` / `fatal-warnings` | abort on the first critical / warning, so a debugger or core dump points at it |
| `G_ENABLE_DIAGNOSTIC=1` | warn about deprecated properties and signals at runtime |
| `GTK_DEBUG=interactive` | open the Inspector at startup |
| `GTK_DEBUG=help` / `GDK_DEBUG=help` / `GSK_DEBUG=help` | list the debug flags your GTK build supports (e.g. `actions`, `builder`, `keybindings`, `size-request`) |
| `GSK_RENDERER=cairo` / `gl` / `ngl` / `vulkan` | switch renderer to isolate rendering/driver bugs |
| `GOBJECT_DEBUG=instance-count` | enables live instance counts in the Inspector's Statistics page |
| `GSETTINGS_BACKEND=memory` | settings are not persisted |
| `ADW_DEBUG_COLOR_SCHEME=prefer-dark`, `ADW_DEBUG_HIGH_CONTRAST=1` | force libadwaita appearance (`ADW_DEBUG_ACCENT_COLOR` and `ADW_DEBUG_ADAPTIVE_PREVIEW=1` also exist) |

Flatpak: `flatpak run --env=G_MESSAGES_DEBUG=all --env=GTK_DEBUG=interactive com.example.MyApp.Devel`.

Rust `tracing` and GLib logging are separate streams: `RUST_LOG` does not show GTK messages and
`G_MESSAGES_DEBUG` does not show yours. video-trimmer routes Rust `log` into GLib (`glib::GlibLogger`) so a
single `G_MESSAGES_DEBUG` controls both and the journal gets structured fields; with `tracing` (template)
just set both variables.

## 7. GTK Inspector

Open it with `GTK_DEBUG=interactive`, or enable the keybinding once and press Ctrl+Shift+D (or Ctrl+Shift+I):

```sh
gsettings set org.gtk.gtk4.Settings.Debug enable-inspector-keybinding true
# For a Flatpak app the setting lives in the sandbox:
flatpak run --command=gsettings com.example.MyApp.Devel set org.gtk.gtk4.Settings.Debug enable-inspector-keybinding true
```

Most useful pages: **Objects** (widget tree, live property editing, CSS nodes and classes, size groups,
accessibility properties), **CSS** (live-edit stylesheet), **Recorder** (render nodes per frame: find
overdraw and expensive nodes), **Actions** (every `app.`/`win.` action, its state and enabled flag),
**Resources** (browse registered GResources: the fastest way to check paths and aliases), **Statistics**
(instance counts, needs `GOBJECT_DEBUG=instance-count`), and the accessibility overlay.

## 8. Finding where a critical comes from

```sh
G_DEBUG=fatal-criticals meson devenv -C _build rust-gdb --args ./src/my-app
(gdb) run
(gdb) bt            # stops at the critical; look for the first frame in your crate
```

Without `G_DEBUG`, set breakpoints instead: `break g_return_if_fail_warning` (most `assertion '…' failed`
criticals) and `break g_log_writer_default` (all structured GLib/GTK log output when no custom writer is
installed). `G_DEBUG=fatal-warnings` makes warnings fatal too, which is noisy if a library emits harmless
ones; fix or filter those first.

In Flatpak: install the debug extensions (`flatpak install org.gnome.Sdk//50 org.gnome.Sdk.Debug//50`, plus
your app's `.Debug` extension, which `debug = true` in the release profile makes useful), then
`flatpak run --devel --command=bash com.example.MyApp.Devel` and run `gdb /app/bin/my-app` inside.
Crashes outside a debugger: `flatpak-coredumpctl com.example.MyApp.Devel`.

## 9. Leaks

GObject wrappers are reference counted, so Rust leaks look like "the window closed but its imp never
dropped". Usual causes: a closure holding a strong clone of the widget it is connected on (use
`#[weak]` in `clone!`), a handler connected on a long-lived object (application, `gio::Settings`, a
session) that captures a widget and is never disconnected, a `glib::SourceId`/`JoinHandle` loop that never
ends, or an `Rc` cycle in `imp` fields. Details and fixes: `closures-and-lifetimes.md`.

Check that teardown happens:

```rust
impl ObjectImpl for Window {
    fn dispose(&self) {
        tracing::debug!("Disposing {}", self.obj().type_().name());
        // gtk::Widget subclasses with templates: self.dispose_template();
    }
}
```

```rust
#[gtk::test]
fn page_is_freed_after_removal() {
    let page = Page::new();
    let freed = std::rc::Rc::new(std::cell::Cell::new(false));
    page.add_weak_ref_notify_local(glib::clone!(#[strong] freed, move || freed.set(true)));
    let weak = page.downgrade();
    drop(page);
    assert!(weak.upgrade().is_none() && freed.get(), "something still holds a reference");
}
```

Interactive: start with `GOBJECT_DEBUG=instance-count`, open the Inspector's Statistics page, open and
close the dialog a few times and watch whether the type's count returns to its baseline. `obj.ref_count()`
is useful in a temporary debug print to see whether a reference is still held; do not assert on exact
counts, since GTK holds transient references.

## 10. Profiling

- **Sysprof** is the GNOME profiler: system-wide CPU stacks plus GTK frame timings and layout/snapshot
  marks. Launch the app from Sysprof, or `sysprof-cli capture.syscap -- ./src/my-app` inside
  `meson devenv`, then open the capture. Stacks need frame pointers: the Devel manifest sets
  `RUSTFLAGS=-C force-frame-pointers=yes` and the GNOME runtime is built with them.
- Profile a release-profile build (`-Dprofile=default`) with `debug = true`; debug builds of your own code
  exaggerate Rust overhead.
- Rendering: Inspector Recorder for node trees, `GSK_DEBUG=help` for renderer flags (recent GTK can show GSK
  profiling information in the Inspector with `GSK_DEBUG=profile`).
- Typical hot spots: synchronous IO or heavy work in a signal handler (move it to `gio::spawn_blocking`
  or an async task, see `async-and-threads.md`), creating widgets for every item instead of a `ListView`
  (`lists-and-models.md`), and `queue_draw`/`queue_resize` in a tick callback.

## 11. Common runtime warnings

| Message | Meaning and fix |
| --- | --- |
| `Trying to snapshot GtkFoo 0x… without a current allocation` | A widget is drawn but was never allocated: a custom widget's `size_allocate` skipped a visible child, `snapshot_child` is called for a hidden/unallocated child, or a child was added to a custom parent without layout. Allocate every visible child each time, or use `gtk::BinLayout`/`BoxLayout` (`css-drawing-animation.md`). |
| `Failed to retrieve template child. Please check that all fields of type … have a #[template_child] attribute.` (Rust panic) | A `TemplateChild` was accessed but not bound: the field name does not match the Blueprint id (use `#[template_child(id = "…")]`), the `#[template_child]` attribute is missing, `bind_template()`/`init_template()` is missing, or the field was accessed before `instance_init`/after `dispose`. GTK's own variant: "Unable to retrieve child object '…' from class template for type '…'". |
| `Invalid object type 'MyFoo'` (GtkBuilder) | A custom type used in a template (`$MyFoo` in Blueprint) was not registered yet. Call `MyFoo::ensure_type()` in the parent's `class_init` before `bind_template()`. |
| `Settings schema 'com.example.MyApp.Devel' is not installed` (aborts) | Schema not compiled or not found: run via `meson devenv` (sets `GSETTINGS_SCHEMA_DIR`), install the app, or in tests set the schema dir. Also check that the schema id equals the running app id (Devel vs stable). |
| `Could not load resources from …` (template's panic) / `Unable to load resource for composite template for type '…': The resource at '…' does not exist` | Bundle not registered before the class was initialized, wrong prefix/alias, or the app ran outside `meson devenv`. Check paths in the Inspector's Resources page and compare with `#[template(resource = …)]`. |
| `Finalizing GtkFoo 0x…, but it still has children left` | A `gtk::Widget` subclass did not unparent its children: call `self.dispose_template()` (templates) or unparent each child in `dispose`. |
| `gtk_widget_set_parent: assertion '_gtk_widget_get_parent (widget) == NULL' failed` | The same widget instance was added to two parents; create a new one or remove it first. |
| `Allocation height too small. Tried to allocate …, but GtkFoo needs at least …` | Size constraints conflict (e.g. a window `height-request` smaller than content minimum, or a custom `measure` returning wrong minima). Fix `measure` or use scrolling/breakpoints. |
| `Theme parser error: style.css:L:C-…` | Invalid CSS or a removed property/selector; libadwaita named colors like `@accent_color` are deprecated in favour of `var(--accent-color)`. |
| `Using GtkSettings:gtk-application-prefer-dark-theme with libadwaita is unsupported` | Use `adw::StyleManager::default().set_color_scheme(...)`. |
| `Source ID N was not found when attempting to remove it` | `SourceId::remove()` on a source that already ended (its callback returned `ControlFlow::Break`). Take the id out of the `Option` when the callback breaks. |
| `GTK may only be used from the main thread.` / `GTK has not been initialized. Call gtk::init first.` (panic) | GTK called from a worker thread or a plain `#[test]`; send results back to the main context (`async-and-threads.md`) or use `#[gtk::test]`. |

## Pitfalls

- GTK tests fail or the test binary segfaults in CI, stderr shows "Tests failed to initialize gtk" →
  no display → `xvfb-run` or headless
  mutter, or move logic out of GTK.
- Plain `#[test]` panics about the main context only when run together with GTK tests → the GTK test thread
  owns the default context → use `glib::MainContext::new().block_on(...)`.
- Second GTK-using `#[test]` panics "Attempted to initialize GTK from two different threads" → used
  `#[test]` + `gtk::init()` → use `#[gtk::test]`.
- Plain `cargo test` writes to your real settings or aborts with "schema not installed" → it bypassed the
  Meson test env → run `meson test`, or inject `memory_settings()`.
- App launched in `meson devenv` stops persisting settings → `GSETTINGS_BACKEND=memory` added to
  `cargo_env`/`cargo_vars` (exported by devenv) → add it only in the `test()` env, as the template does.
- `G_MESSAGES_DEBUG=all` shows nothing from your code → your logs go through `tracing` → set `RUST_LOG`.
- Backtrace in gdb has only `??` frames in Flatpak → missing `.Debug` extensions or `--devel` → install
  them and run with `--devel`.

## See also

`project-layout-and-build.md`, `packaging-and-ci.md`, `closures-and-lifetimes.md`, `async-and-threads.md`,
`templates-and-blueprint.md`, `architecture.md`, `compile-traps.md`, `css-drawing-animation.md`.
