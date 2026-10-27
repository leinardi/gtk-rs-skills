# Deprecated and outdated APIs → current replacements

Read this when reviewing or modernising gtk-rs code, when porting an old example or blog post, or when
you are about to write an API from memory and are not sure it is still current. gtk-rs churns hard across
0.x releases and much of the code on the internet (including older parts of the gtk-rs book prose)
predates the current idioms.

Baseline for "current": gtk4 0.11 / libadwaita 0.9 / glib 0.22, GTK 4.22, libadwaita 1.9.

## Contents

- [Rust bindings (glib / gtk-rs idioms)](#rust-bindings-glib--gtk-rs-idioms)
- [GTK widgets and APIs](#gtk-widgets-and-apis)
- [libadwaita](#libadwaita)
- [CSS and styling](#css-and-styling)
- [Build, packaging and ecosystem](#build-packaging-and-ecosystem)
- [How to check whether something is deprecated](#how-to-check-whether-something-is-deprecated)

## Rust bindings (glib / gtk-rs idioms)

| Outdated | Current | Notes |
| --- | --- | --- |
| `clone!(@weak x => @default-return v, move \|_\| ...)` | `clone!(#[weak] x, #[upgrade_or] v, move \|_\| ...)` | Attribute syntax since glib 0.20. `@strong` → `#[strong]`, `@weak self as this` → `#[weak(rename_to = this)] self`. See `closures-and-lifetimes.md`. |
| `glib::MainContext::channel`, `glib::Sender`, `rx.attach(...)` | `async_channel::unbounded()` + `glib::spawn_future_local(async move { while let Ok(msg) = rx.recv().await { ... } })` | Removed in glib 0.20. Worker threads use `sender.send_blocking(msg)`. |
| `MainContext::default().spawn_local(fut)` | `glib::spawn_future_local(fut)` | The old spelling still works; the free function is shorter and is what the book uses. |
| `Inhibit(false)` / `Inhibit(true)` | `glib::Propagation::Proceed` / `glib::Propagation::Stop` | Signal handlers that used to return `Inhibit` (e.g. `close-request`, key handlers). |
| `glib::Continue(true)` / `Continue(false)` | `glib::ControlFlow::Continue` / `glib::ControlFlow::Break` | Timeout and idle sources. |
| `once_cell::sync::Lazy`, `lazy_static!` | `std::sync::LazyLock`, `std::sync::OnceLock`, `std::cell::OnceCell` | Signals: `static SIGNALS: LazyLock<Vec<Signal>> = LazyLock::new(...)`. |
| Hand-written `properties()` + `set_property()` + `property()` with `ParamSpec*::builder()` and `match pspec.name()` | `#[derive(glib::Properties)]` + `#[properties(wrapper_type = super::X)]` + `#[glib::derived_properties]` on `impl ObjectImpl` | Also generates typed getters/setters/`connect_*_notify` on the wrapper. See `gobject.md`. |
| `fn properties() -> &'static [ParamSpec] { Self::derived_properties() }` forwarding by hand | `#[glib::derived_properties]` | The attribute writes the three forwarding functions. |
| `RefCell<Option<T>>` for a field set exactly once | `std::cell::OnceCell<T>` (+ `construct_only` property if it comes from the constructor) | Avoids `unwrap()` on every access and documents intent. |
| `obj.downgrade()` + `weak.upgrade().unwrap()` by hand | `clone!(#[weak] obj, ...)`; `glib::WeakRef<T>` fields for back-references |  |
| `ObjectExt::set_data` / `data` (unsafe) | Typed fields in the `imp` struct, or properties | `set_data` is `unsafe` and untyped. |
| `gio::ListStore::new(T::static_type())` | `gio::ListStore::new::<T>()` |  |
| `fn main() { app.run(); }` ignoring the exit code | `fn main() -> glib::ExitCode { ... app.run() }` | `run_with_args` remains for custom argv. |
| `#[tokio::main]` around a GTK app | GLib owns the main thread; keep a `static RUNTIME: LazyLock<tokio::runtime::Runtime>` and spawn onto it | See `async-and-threads.md`. |
| `WidgetExt::allocation()`, `allocated_width()`, `allocated_height()` | `widget.compute_bounds(&target)`, `widget.width()` / `height()` | Deprecated since GTK 4.12; clippy with `-D warnings` rejects it once `v4_12`+ is enabled. |
| `widget.style_context().add_class(...)` | `widget.add_css_class(...)` / `remove_css_class` / `set_css_classes` | `StyleContext` is deprecated since GTK 4.10. |
| `StyleContext::add_provider_for_display(...)` | `gtk::style_context_add_provider_for_display(&display, &provider, priority)` | Free function in `gtk::`. |
| `CssProvider::load_from_data(&str)` | `CssProvider::load_from_string(&str)` / `load_from_resource(path)` | `load_from_data` is deprecated since GTK 4.12. |
| `glib::signal::Inhibit`, `gtk::Inhibit` imports | Remove; use `glib::Propagation` |  |
| `gtk::glib::clone` imported alone (`use gtk::glib::clone;`) | `use gtk::glib;` then `glib::clone!(...)` | The macro expands to `glib::...` paths, so the `glib` module must be in scope. |

## GTK widgets and APIs

| Outdated | Current | Notes |
| --- | --- | --- |
| `gtk::TreeView`, `gtk::ListStore`, `gtk::TreeStore`, `TreeIter`, `CellRenderer*` | `gtk::ListView` / `GridView` / `ColumnView` + `gio::ListStore` + `SignalListItemFactory`; `gtk::TreeListModel` for trees | Deprecated since GTK 4.10. See `lists-and-models.md`. |
| `gtk::ComboBox`, `ComboBoxText` | `gtk::DropDown` (+ `gtk::StringList`), or `adw::ComboRow` in preference-style lists |  |
| `gtk::EntryCompletion` | A `gtk::Popover` with a `ListView`, or `gtk::SearchEntry` filtering a list | Deprecated since GTK 4.10. |
| `gtk::Dialog`, `gtk::MessageDialog` | `adw::Dialog`, `adw::AlertDialog` (`choose_future`) | `gtk::AlertDialog` exists but in libadwaita apps prefer the adw one (adaptive, HIG styling). |
| `gtk::FileChooserDialog`, `FileChooserNative`, `FileChooserWidget` | `gtk::FileDialog` with `open_future`, `save_future`, `select_folder_future`, `open_multiple_future` | Goes through the portal automatically under Flatpak. |
| `gtk::ColorChooserDialog`, `FontChooserDialog` | `gtk::ColorDialog` / `ColorDialogButton`, `gtk::FontDialog` / `FontDialogButton` |  |
| `gtk::AppChooserDialog`, `gtk::show_uri` | `gtk::FileLauncher` / `gtk::UriLauncher` (`launch_future`) |  |
| `gtk::ShortcutsWindow`, `gtk/help-overlay.ui`, `win.show-help-overlay` | `adw::ShortcutsDialog` in `shortcuts-dialog.ui` at the resource base path; libadwaita provides `app.shortcuts` (Ctrl+?) | libadwaita 1.8+ (crate feature `v1_8`). |
| `gtk::Spinner` | `adw::Spinner` / `adw::SpinnerPaintable` | libadwaita 1.6+. |
| `gtk::InfoBar` | `adw::Banner` (persistent) or `adw::Toast` (transient) | Deprecated since GTK 4.10. |
| `gtk::Statusbar` | `adw::Toast`, or status shown in content | Deprecated since GTK 4.10. |
| `gtk::LockButton`, `gtk::VolumeButton` | App-specific controls | Deprecated since GTK 4.10. |
| `gtk::DrawingArea` + cairo for new custom widgets | `WidgetImpl::snapshot` with GSK nodes / `gsk::PathBuilder` | DrawingArea is still fine for quick cairo plots; snapshot is GPU-friendly. See `css-drawing-animation.md`. |
| X11-specific code (`gdk4_x11`), positioning windows, global grabs | Wayland-first design: portals, actions with accelerators | The X11 backend is deprecated since GTK 4.18 (Broadway renderer since 4.20) and GNOME 49+ ships no X11 session. A Wayland client cannot place its own windows or grab global shortcuts. |
| Manual `EventControllerKey` dispatch tables for app shortcuts | Actions + `set_accels_for_action`, `klass.add_binding_action`, `gtk::ShortcutController` | See `actions-and-shortcuts.md`. |
| Synchronous `gio::File::load_contents(Cancellable::NONE)` on the main thread for user files | `load_contents_future`, `replace_contents_future`, or `gio::spawn_blocking` | Blocking I/O freezes the UI on slow or remote (GVfs) files. |
| Callback-style `FileDialog::open(..., cb)` / `AlertDialog::choose(..., cb)` | The `*_future().await` variants | Linear code, easy cancellation handling. |

## libadwaita

| Outdated | Current | Since |
| --- | --- | --- |
| `adw::Leaflet`, `adw::Flap`, `adw::Squeezer`, `adw::ViewSwitcherTitle` | `adw::NavigationSplitView`, `adw::OverlaySplitView`, `adw::NavigationView`, `adw::Breakpoint`, `adw::ViewSwitcher` in the header bar + `adw::ViewSwitcherBar` | 1.4 |
| `adw::MessageDialog` | `adw::AlertDialog` | 1.5 |
| `adw::PreferencesWindow` | `adw::PreferencesDialog` | 1.5 |
| `adw::AboutWindow`, `gtk::AboutDialog` | `adw::AboutDialog` (ideally `AboutDialog::from_appdata`) | 1.5 |
| Window-based dialogs with `set_transient_for` | `adw::Dialog::present(Some(&parent_widget))` | 1.5 |
| Manual `adw::HeaderBar` + `gtk::Box` stacks | `adw::ToolbarView` with `[top]`/`[bottom]` bars | 1.4 |
| `gtk::Switch` / `gtk::SpinButton` inside `adw::ActionRow` | `adw::SwitchRow`, `adw::SpinRow` | 1.4 |
| `gtk::Button` styled as a row in a boxed list | `adw::ButtonRow` | 1.6 |
| A `gtk::Box` of linked `ToggleButton`s | `adw::ToggleGroup` | 1.7 |
| `gtk::FlowBox` used only for wrapping chips | `adw::WrapBox` | 1.7 |
| `.dim-label` style class | `.dimmed` | 1.7 |
| `adw::StyleManager::set_color_scheme(ForceDark)` unconditionally | Follow the system; only offer an override if the user asks | — |

## CSS and styling

| Outdated | Current |
| --- | --- |
| `@define-color my_color #...;` and `@accent_bg_color`, `@window_bg_color` named colors | CSS custom properties: `var(--accent-bg-color)`, `var(--window-bg-color)`; define your own with `:root { --my-color: ...; }`; mix with `color-mix(in srgb, var(--accent-color) 20%, transparent)` |
| `style-dark.css`, `style-hc.css`, `style-hc-dark.css` | One `style.css` with `@media (prefers-color-scheme: dark)` and `@media (prefers-contrast: more)` blocks |
| Loading `style.css` with a hand-made `CssProvider` | Put it at `<resource-base-path>/style.css`; `adw::Application` loads it automatically |
| `gtk::Settings::set_gtk_application_prefer_dark_theme` | `adw::StyleManager::default().set_color_scheme(...)` (only when the user asked for an override) |

## Build, packaging and ecosystem

| Outdated | Current |
| --- | --- |
| `.cargo/config` (no extension) in vendored tarballs | `.cargo/config.toml` |
| `cargo vendor` for the dist tarball | `cargo vendor-filterer --platform=x86_64-unknown-linux-gnu --platform=aarch64-unknown-linux-gnu` (smaller) |
| Blueprint `custom_target(..., output: '.')` | List the generated `.ui` files explicitly so ninja orders the GResource step correctly |
| `appdata.xml` installed to `share/appdata` | `<id>.metainfo.xml` installed to `share/metainfo` |
| Schema id or path hard-coded to the release app id | Template both from the build's application id (dconf keys values by path) so `.Devel` builds keep separate settings |
| `pretty_env_logger` (unmaintained) | `tracing` + `tracing-subscriber` with `EnvFilter`, or `glib::GlibLogger` |
| ashpd with the `async-std` feature | ashpd's `async-io` feature (awaited on the GLib main context) or `tokio` if you already run tokio. oo7 0.6 still names its async-io option `async-std`. |
| gettext-rs 0.7 safe `setlocale` | gettext-rs 0.8: `setlocale` is `unsafe`; call it first thing in `main` with a SAFETY comment |
| `--share=network` in a Flathub (stable) manifest's `build-args` | Offline builds: vendored tarball or `cargo-sources.json` from flatpak-cargo-generator |

## How to check whether something is deprecated

1. Grep the generated bindings in the cargo registry; deprecated items carry an attribute:

   ```sh
   grep -n -B4 'fn allocation(' ~/.cargo/registry/src/*/gtk4-0.11.*/src/auto/widget.rs
   # #[cfg_attr(feature = "v4_12", deprecated = "Since 4.12")]
   ```

   The attribute only fires when the matching version feature is enabled, which is one reason to enable the
   feature for the GTK version you actually ship on.
2. Build with `cargo clippy -- -D warnings`; `deprecated` warnings then fail the build.
3. Check the C docs (docs.gtk.org/gtk4, gnome.pages.gitlab.gnome.org/libadwaita/doc/1-latest) for a
   "Deprecated since" banner; the Rust bindings follow them.

## See also

- `compile-traps.md` — errors you hit when mixing old and new APIs
- `closures-and-lifetimes.md`, `gobject.md`, `async-and-threads.md` — the current idioms in depth
- `libadwaita-ui.md`, `css-drawing-animation.md` — current widgets and styling
