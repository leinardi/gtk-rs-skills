# Application architecture

Read this when you are shaping or reorganising an app: what goes in `application.rs` and `window.rs`, how to lay out modules, where shared state lives, how data flows between models and widgets, and how errors and logs are handled.

Contents:

1. The application subclass
2. Single instance, `activate` and `open`
3. Global access to the application
4. Quitting cleanly
5. The main window
6. Module organisation
7. Models, widgets and data flow
8. App-wide singletons
9. Error handling
10. Logging
11. Pitfalls / See also

## 1. The application subclass

```rust
use adw::{prelude::*, subclass::prelude::*};
use gtk::{gio, glib};

use crate::{config, window::Window};

mod imp {
    use super::*;

    #[derive(Debug, Default)]
    pub struct Application;

    #[glib::object_subclass]
    impl ObjectSubclass for Application {
        const NAME: &'static str = "MyAppApplication";
        type Type = super::Application;
        type ParentType = adw::Application;
    }

    impl ObjectImpl for Application {}

    impl ApplicationImpl for Application {
        fn startup(&self) {
            self.parent_startup(); // initializes GTK + libadwaita; must come first
            gtk::Window::set_default_icon_name(config::APP_ID);
            self.obj().setup_actions();
        }

        fn activate(&self) {
            self.parent_activate();
            self.obj().main_window().present();
        }

        fn shutdown(&self) {
            // Flush anything that must hit disk, then chain up.
            self.parent_shutdown();
        }
    }

    impl GtkApplicationImpl for Application {}
    impl AdwApplicationImpl for Application {}
}

glib::wrapper! {
    pub struct Application(ObjectSubclass<imp::Application>)
        @extends adw::Application, gtk::Application, gio::Application,
        @implements gio::ActionGroup, gio::ActionMap;
}

impl Application {
    pub fn new() -> Self {
        glib::Object::builder()
            .property("application-id", config::APP_ID)
            .property("resource-base-path", config::RESOURCE_BASE_PATH)
            .build()
    }
}
```

Why each piece:

- **`adw::Application` as parent.** It initializes libadwaita in `startup`, applies the style manager, and auto-loads resources from `resource-base-path`: `style.css`, `style-dark.css`, `style-hc.css` and `gtk/help-overlay.ui` / `shortcuts-dialog.ui`. Apps that set the base path never create a `gtk::CssProvider` for their own stylesheet (fractal, Shortwave, Authenticator, icon-library). Keep a manual provider only for CSS generated at runtime (amberol: colours derived from cover art).
- **Set `resource-base-path` explicitly.** It defaults to a path derived from the application ID. A `.Devel` ID (`org.example.App.Devel`) would therefore look under `/org/example/App/Devel/`, and your stylesheet would silently fail to load. The scaffold template sets it from `config::RESOURCE_BASE_PATH`, which has no profile suffix.
- **Every `*Impl` trait in the chain is required**, even when empty (`GtkApplicationImpl`, `AdwApplicationImpl`). The `@extends` list must name every ancestor, and `@implements` every interface; see `gobject.md`.
- **Chaining up.** In `startup`, call `parent_startup()` first, because GTK isn't usable until it has run. In `activate`, chaining up is harmless (the default does nothing), so do it for consistency. Don't chain up `open`: the default handler only prints a warning that the app doesn't handle files. For `shutdown`, do your own synchronous flushing and then chain up. Corpus apps chain up first (Shortwave, pika-backup), which works as long as the cleanup doesn't touch widgets.

Hook placement in practice:

| Hook | Put here |
| --- | --- |
| `constructed` | `add_main_option` (CLI flags must exist before `run` parses argv); long-lived connections to the app's own properties |
| `startup` | actions + accelerators, default icon, heavy singletons (player, database, session list) |
| `activate` | get-or-create the main window, `present()` |
| `open` (`HANDLES_OPEN`) | present the window, then route files/URIs to it |
| `command_line` / `handle_local_options` | only with `HANDLES_COMMAND_LINE`, or for local flags such as `--version` |
| `shutdown` | synchronous state flush, cache pruning |
| `GtkApplicationImpl::window_removed` | custom "keep running in background" policies (Shortwave) |

`main.rs` stays thin. It sets up logging, gettext and resource registration, then returns the exit code: `fn main() -> glib::ExitCode { … Application::new().run() }`. Register the gresource bundle **before** constructing the application, because the base-path autoloading runs during `startup`.

## 2. Single instance, `activate` and `open`

GApplication is single-instance by default. A second launch connects to the already-running primary instance over D-Bus, forwards the request as `activate` (or `open`), and exits. Your `activate` therefore has to be idempotent: reuse the existing window and call `present()` so it comes to the front.

```rust
impl Application {
    fn main_window(&self) -> Window {
        if let Some(window) = self.active_window().and_downcast::<Window>() {
            return window;
        }
        Window::new(self)
    }
}

// imp
fn open(&self, files: &[gio::File], _hint: &str) {
    let window = self.obj().main_window();
    window.present();
    for file in files {
        window.open_file(file);
    }
}
```

`open` requires `.property("flags", gio::ApplicationFlags::HANDLES_OPEN)` in `new()`, and the desktop file needs `%U`/`%F` in `Exec`.

Variants:

- **WeakRef to the window** in the app's `imp` (`main_window: glib::WeakRef<Window>`), upgraded on demand (pika-backup, warp). Use this when you need the main window even while a secondary window has focus.
- **Multi-window apps** (identity, video-trimmer, contrast) create a new window per `activate` / `app.new-window` and give each one its own `gtk::WindowGroup`. Without separate groups, a modal dialog in one window blocks input to the others.
- Don't hold a **strong** `RefCell<Option<Window>>` in the application. The window already references the app through its `application` property, so the window stays alive after it is closed (mission-center does this).
- `gio::ApplicationFlags::NON_UNIQUE` opts out of single-instance behaviour. It is rarely what you want for a desktop app.

## 3. Global access to the application

```rust
impl Default for Application {
    fn default() -> Self {
        debug_assert!(
            gtk::is_initialized_main_thread(),
            "Application::default() must be called on the GTK main thread"
        );
        gio::Application::default()
            .and_downcast::<Application>()
            .expect("application should be running")
    }
}
```

Call sites read `Application::default().settings()` or `Application::default().player()` (fractal, Shortwave). This works because a `GApplication` registers itself as the process default when it is constructed.

- The application is a GObject and is `!Send`, so this getter is only meaningful on the main thread. A worker thread that needs app data should receive it through a channel (see `async-and-threads.md`). The assertion turns misuse into a clear message rather than a confusing `None`; pika-backup uses a hard `assert!`.
- Give `Default` exactly one meaning. amberol's `impl Default` *constructs* a new application, which is the opposite of every other app; creation belongs in `new()`.
- Don't shadow `GtkApplicationExt` methods with inherent ones. Authenticator defines its own `active_window()` that unwraps twice; call yours `main_window()`.
- Inside a widget, `self.root().and_downcast::<Window>()` or `window.application()` are per-window alternatives. Both are `None` until the widget is attached to a window.

## 4. Quitting cleanly

```rust
let quit = gio::ActionEntry::builder("quit")
    .activate(|app: &Application, _, _| {
        // Not app.quit(): it skips close-request. The app exits when its last window is gone.
        for window in app.windows() {
            window.close();
        }
    })
    .build();
self.add_action_entries([quit]);
self.set_accels_for_action("app.quit", &["<Control>q"]);
```

`gio::Application::quit()` stops the main loop immediately. No window receives `close-request`, so any state saving that lives in `WindowImpl::close_request` is skipped, and a window cannot veto. Closing the windows instead runs those handlers, and `GtkApplication` exits by itself once its last window is gone (the template does this). A window that returns `glib::Propagation::Stop` (for example "unsaved changes?") keeps the app running. Calling `quit()` after the loop, as many apps do, would end the process anyway and lose that document. See `document-apps.md` for the full close flow.

Two cases never exit this way: an app holding an `ApplicationHoldGuard` (background apps, `platform-integration.md`) and windows with `hide-on-close`, which stay in `app.windows()`. Those apps call `quit()` explicitly, but only after every window has agreed to close.

## 5. The main window

`window.rs` subclasses `adw::ApplicationWindow` with a composite template (see `templates-and-blueprint.md`):

- `class_init`: `bind_template()`, `ensure_type()` for custom widgets used in the template, and `klass.install_action(...)` for `win.*` actions.
- `constructed`: chain up, add the `devel` CSS class for development profiles, restore saved size, wire models to widgets.
- `close_request`: save state, then `self.parent_close_request()`.

Keep the window as a **coordinator**. It owns the top-level models (or gets them from the app), hands them to child widgets through properties, and shows toasts and dialogs. Domain logic belongs in model objects that can be tested without a display.

## 6. Module organisation

**Small app (≤ ~10 types): one file per type, with an inline `mod imp`.** This is the scaffold template's style, and that of icon-library, contrast, identity and video-trimmer.

```text
src/
  main.rs          # logging, i18n, resources, run
  config.rs        # build-time constants (option_env! with defaults)
  application.rs   # mod imp { … } + wrapper + public API
  window.rs
  task_row.rs
  task_object.rs
```

**Larger app: a folder per component or domain.**

```text
src/
  application.rs
  prelude.rs                 # pub use of every custom *Ext / *Impl trait
  session/                   # domain models (GObjects, no widgets)
    mod.rs  room.rs  member.rs
  session_view/              # widgets for that domain
    mod.rs  room_history/{mod.rs, item_row.rs, item_row.blp}
  components/                # reusable widgets
  utils/                     # spawn!/toast! helpers, expression helpers
  error.rs
```

Conventions that hold across the corpus:

- One GObject type per file, with the file named after the snake_case type. Keep each `.blp` next to its `.rs` (fractal) or under `data/resources/ui/` (template). Pick one style and stick to it.
- Split UI from models: `widgets/` or `ui/` versus `models/`, `backend/` or `session/`. Models never import widgets.
- `mod.rs` (or `foo.rs` + `foo/`) re-exports the wrapper types (`pub use self::room::Room;`), so other modules don't reach into submodules.
- Keep `imp` fields private or `pub(super)`, and expose typed methods on the wrapper. Older apps make every `imp` field `pub` and poke `obj.imp().field` from anywhere (amberol, Authenticator), which makes invariants impossible to enforce.
- Add a crate `prelude.rs` once you define subclassable base classes or `ext_trait`s (fractal, pika-backup).
- Split a separate `-common` crate (pika-backup) for non-GTK logic only when it has a second consumer, such as a daemon or CLI. Otherwise a module is enough.

## 7. Models, widgets and data flow

**Preferred order of mechanisms:**

1. **Properties + bindings.** The model exposes state as GObject properties. Widgets bind to them in Blueprint (`label: bind template.task as <$TaskObject>.title;`) or with `bind_property(...).sync_create().build()`.
2. **Expressions** for chains (`item → author → display-name`) and derived values. Use `gtk::Expression` in Blueprint `lookup`/closures, or `property_expression(...).chain_property::<T>(...)` in Rust. Keep the `ExpressionWatch` and `unwatch()` it when the source changes.
3. **`connect_<prop>_notify`** (generated by `#[derive(Properties)]`) when you need side effects beyond copying a value.
4. **Custom signals** only for events that are not state, such as `"activated"` or `"removed"`. Expose them through typed `connect_*` wrappers.

Why: properties give you `notify`, binding, Blueprint integration, inspector visibility and late-subscriber correctness (`sync_create`) for free. Ad-hoc "changed" signals carry none of that, and every subscriber has to fetch the initial value by hand.

```rust
// Window wiring a model it owns into a child widget.
let session = Session::new(&settings);
self.sidebar.set_session(Some(&session)); // child binds internally
session
    .bind_property("is-busy", &*self.spinner, "visible")
    .sync_create()
    .build();
session.connect_error_notify(clone!(
    #[weak(rename_to = imp)]
    self,
    move |session| {
        if let Some(message) = session.error() {
            imp.toast_overlay.add_toast(adw::Toast::new(&message));
        }
    }
));
```

Rules of thumb:

- **Down: properties. Up: notify or signals.** Parents set properties on children; children never call methods on their parent. If a child needs to trigger something app-wide, it activates an action (`widget.activate_action("win.open-file", None)`). See `actions-and-shortcuts.md`.
- **Ownership:** parents own their children and models strongly. Back-references to parents and peers are `glib::WeakRef`.
- **Lists:** a `gio::ListStore` or a custom `gio::ListModel` lives in the model layer; views wrap it in filter and sort models. See `lists-and-models.md`.
- Use a `construct_only` property for something that is fixed for the object's whole life, such as the session a page belongs to. Use a settable property with handler bookkeeping for the "current item" (see `closures-and-lifetimes.md`).

## 8. App-wide singletons

```rust
mod imp {
    #[derive(Debug, glib::Properties)]
    #[properties(wrapper_type = super::Application)]
    pub struct Application {
        pub(super) settings: gio::Settings,
        #[property(get)]
        pub(super) library: std::cell::OnceCell<Library>, // created in startup
    }

    impl Default for Application {
        fn default() -> Self {
            Self {
                settings: gio::Settings::new(config::APP_ID),
                library: std::cell::OnceCell::default(),
            }
        }
    }
    // + #[glib::derived_properties] impl ObjectImpl for Application {}
}
// startup: self.library.set(Library::new()).expect("startup runs once");
```

| Strategy | Use when |
| --- | --- |
| Field or property on the application, reached via `Application::default()` | Main-thread services: settings, player, library, session list (fractal, Shortwave). **Default choice.** |
| Constructor injection (construct-only property) | A model or window that should be testable with a fake (icon-library passes its model; Authenticator passes `model` to `Window::new`) |
| `static LazyLock<T>` | Truly process-wide `Send + Sync` things: a tokio `Runtime`, an HTTP client, compiled regexes |
| `thread_local!` | Avoid. Put main-thread state on the application instead. |

A `gio::Settings` can't be stored in a `static` without `glib::thread_guard::ThreadGuard`, because GObjects are `!Send` (Authenticator does it with a guard). Keeping it on the application is simpler. See `settings-and-state.md` for the typed settings wrapper.

## 9. Error handling

- **Library-style errors:** a `thiserror` enum per domain (`enum SyncError { Network(#[from] reqwest::Error), … }`). This is fractal's, warp's and Shortwave's approach.
- **Plumbing:** `anyhow::Result` in app-internal functions where callers only log or display the error (Authenticator, Fragments). Keep `anyhow` out of model APIs that other code matches on.
- **GLib errors:** fallible GLib and GIO calls return `glib::Error`. Match codes with `err.matches(gio::IOErrorEnum::NotFound)` or `err.kind::<gio::IOErrorEnum>()`. GSettings setters return `glib::BoolError`.

Signal handlers return `()` or `glib::Propagation`, so `?` cannot leave them. Put the fallible work in a function returning `Result` and handle the error once at the boundary:

```rust
klass.install_action_async("win.export", None, |win, _, _| async move {
    if let Err(err) = win.export().await {
        win.show_error(&gettext("Could not export the file"), &err);
    }
});

impl Window {
    async fn export(&self) -> anyhow::Result<()> {
        let dialog = gtk::FileDialog::builder().title(gettext("Export")).build();
        let file = match dialog.save_future(Some(self)).await {
            Ok(file) => file,
            // The user closed the dialog: not an error, nothing to report.
            Err(err) if err.matches(gtk::DialogError::Dismissed)
                || err.matches(gtk::DialogError::Cancelled) => return Ok(()),
            Err(err) => return Err(err.into()),
        };
        self.write_export(&file).await?;
        Ok(())
    }

    fn show_error(&self, message: &str, err: &anyhow::Error) {
        tracing::error!("{message}: {err:#}");
        self.imp().toast_overlay.add_toast(adw::Toast::new(message));
    }
}
```

**Presenting errors to users:**

- **Toast** (`adw::Toast` on the window's `adw::ToastOverlay`, or `adw::PreferencesDialog::add_toast`) for transient failures where the user can simply retry. fractal's `toast!` helper walks up the widget's ancestors to find the nearest toast host.
- **`adw::AlertDialog`** for failures that block the task or need a decision. Use `dialog.choose_future(Some(&window)).await`, which returns the response ID. Guard against stacking several identical dialogs (warp keeps an "already showing" flag).
- Show a translated, human sentence (`gettext(...)`), and log the technical detail. fractal's `UserFacingError` trait maps SDK errors to translated strings.
- **Cancellation is not an error.** `gtk::DialogError::Dismissed` and `Cancelled` from `FileDialog`, `AlertDialog` and similar should return silently, at most a `debug!` log. The same goes for `gio::IOErrorEnum::Cancelled` when you cancel your own `gio::Cancellable`.
- `expect("<invariant>")` is fine for GObject invariants such as downcasts, template children and set-once cells, and the message documents the invariant (fractal style). Never use it on runtime data (files, network, user input).

## 10. Logging

```rust
// main.rs (scaffold template)
use tracing_subscriber::EnvFilter;

let filter = EnvFilter::try_from_default_env()
    .unwrap_or_else(|_| EnvFilter::new(concat!(env!("CARGO_CRATE_NAME"), "=info,warn")));
tracing_subscriber::fmt().with_env_filter(filter).init();
```

- Use `tracing` + `tracing-subscriber` (feature `env-filter`), as fractal, Authenticator, pika-backup and identity do. `RUST_LOG=myapp=debug` raises verbosity without a rebuild, and structured fields make logs greppable: `debug!(room = %id, "Loading timeline")`.
- Log once, where the error is handled, not at every `?`.
- **Alternative: route the `log` crate into GLib's logger** so messages share GTK's domains, respect `G_MESSAGES_DEBUG`, and land in the journal with structured fields (helvum, video-trimmer). This needs the glib `log` feature:

```rust
static GLIB_LOGGER: glib::GlibLogger = glib::GlibLogger::new(
    glib::GlibLoggerFormat::Structured,
    glib::GlibLoggerDomain::CrateTarget,
);
log::set_logger(&GLIB_LOGGER).expect("logger set once");
log::set_max_level(log::LevelFilter::Debug);
```

- GTK's own warnings (`Gtk-WARNING`, `Adwaita-CRITICAL`) go through GLib, not `tracing`. Run with `G_DEBUG=fatal-criticals` under a debugger to stop at the first one (see `testing-and-debugging.md`).
- `pretty_env_logger` (Shortwave, warp) is unmaintained. Prefer `tracing-subscriber` or `env_logger`.

## Pitfalls

- **Stylesheet or shortcuts dialog silently not loaded in Devel builds** → `resource-base-path` was derived from the `.Devel` app ID → set it explicitly to the unsuffixed resource path.
- **Second launch opens a second main window** → `activate` always constructs a window → get or create the window, then `present()`.
- **Window size not saved on Ctrl+Q, or unsaved changes lost** → `app.quit()` skips `close-request`, and calling it after closing the windows overrides a veto → close the windows and let the app exit when the last one goes.
- **Window or app never finalized; handlers keep firing after close** → strong window reference stored in the application → use `WeakRef` or `active_window()`.
- **`Application::default()` panics with "should be running"** → called from a worker thread, or before `Application::new()` → pass data over a channel, and construct the app first in `main`.
- **"Your application does not implement g_application_activate()" warning** → no `activate` override → implement it, even if it only presents the window.
- **Error dialog appears when the user presses Escape in a file chooser** → `DialogError::Dismissed` treated as a failure → match and return early.
- **`open` vfunc never called** → `HANDLES_OPEN` flag missing, or no `%U` in the desktop file's `Exec` line.

## See also

`gobject.md`, `closures-and-lifetimes.md`, `settings-and-state.md`, `async-and-threads.md`, `actions-and-shortcuts.md`, `templates-and-blueprint.md`, `lists-and-models.md`, `libadwaita-ui.md`, `project-layout-and-build.md`, `testing-and-debugging.md`, `document-apps.md`
