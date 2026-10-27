# Settings and persistent state

Read this when the app needs to remember something: preferences via GSettings, window size, user data in JSON or SQLite, or secrets. It also covers testing code that touches GSettings.

Contents:

1. GSettings schema and the single `gio::Settings` instance
2. Binding settings to properties
3. Settings-backed actions
4. Saving and restoring window state
5. A typed settings wrapper
6. GSettings in tests
7. Documents and app data: serde + atomic writes
8. Databases
9. Secrets
10. Worked example: the book's To-Do persistence
11. Pitfalls / See also

## 1. GSettings schema and the single `gio::Settings` instance

```xml
<!-- data/@APP_ID@.gschema.xml.in (scaffold template) -->
<schemalist gettext-domain="@gettext-package@">
  <schema id="@app-id@" path="/org/example/App/">
    <key name="window-width" type="i"><default>800</default><summary>Window width</summary></key>
    <key name="window-height" type="i"><default>600</default><summary>Window height</summary></key>
    <key name="is-maximized" type="b"><default>false</default><summary>Window maximized state</summary></key>
    <key name="sort-order" type="s">
      <choices><choice value="name"/><choice value="date"/></choices>
      <default>'date'</default>
      <summary>List sort order</summary>
    </key>
  </schema>
</schemalist>
```

- **Schema ID = application ID.** The template substitutes `@app-id@`, so a `.Devel` build reads `org.example.App.Devel` and keeps its settings separate from the installed stable app. Construct with `gio::Settings::new(config::APP_ID)`.
- `path` starts and ends with `/`. Key types are GVariant format strings (`b`, `i`, `u`, `d`, `s`, `as`, `(ii)`), and defaults are in GVariant text form, so strings need single quotes: `'date'`. Use `<choices>` (or an `<enum>`) for enumerated strings. The schema compiler then rejects invalid values, and `create_action` can offer them as radio items.
- `gio::Settings::new` **aborts the process** if the schema isn't installed or compiled. The template compiles the configured schema into the build dir (a `gschemas-compiled` custom target) and `meson devenv` exports `GSETTINGS_SCHEMA_DIR`, which handles this for development runs. A plain `cargo run` without devenv crashes at the first `Settings::new`.
- Create **one** instance and share it. Keep it on the application (fractal `Application::default().settings()`) or lazily on the window (the template: `OnceCell<gio::Settings>` + `get_or_init`). Every `Settings::new` re-resolves the schema. Shortwave constructs one per access, which works but is wasteful.
- Change notification: `settings.connect_changed(Some("sort-order"), |s, key| …)`. The settings object outlives your widgets, so capture `#[weak]` and disconnect in `dispose` (see `closures-and-lifetimes.md`), or prefer a binding.

## 2. Binding settings to properties

```rust
let settings = Application::default().settings();

// key <-> property, both directions, applied immediately
settings.bind("show-hidden", &*self.show_hidden_row, "active").build();

// one direction only, or inverted
settings.bind("compact", &*obj, "compact").get().build();
settings.bind("animations", &*self.reduce_motion_row, "active").invert_boolean().build();

// custom conversion
settings
    .bind("sort-order", &*self.sort_dropdown, "selected")
    .mapping(|variant, _type| {
        let index: u32 = match variant.get::<String>()?.as_str() {
            "name" => 0,
            _ => 1,
        };
        Some(index.to_value())
    })
    .set_mapping(|value, _variant_type| {
        let index = value.get::<u32>().ok()?;
        Some((if index == 0 { "name" } else { "date" }).to_variant())
    })
    .build();
```

Why: a binding writes to dconf on change, updates the widget when another process changes the key (for example `gsettings set`), and needs no handler bookkeeping. This is the book's preferred form and what preferences dialogs in the corpus use (Shortwave binds `background-playback` to an app property).

- Builder flags: `.get()`, `.set()`, `.get_no_changes()` (read once, then don't follow changes), `.invert_boolean()`, `.no_sensitivity()`. With no flags, the binding is bidirectional. By default the target widget is also made insensitive when the key is locked down.
- The default mapping already converts between `s` keys and `glib::Enum` properties by nick, and between numbers and compatible numeric types. Write a `mapping` only for real conversions.
- The binding lives until the target object is finalized. Remove it earlier with `gio::Settings::unbind(&object, "property")`.
- To bind into your own GObject, give it a property (`gobject.md`) and bind to that. Keep GSettings out of models that must be testable without a schema.
- Preference dialogs that need Apply/Cancel semantics can use `settings.delay()` and later `apply()` / `revert()`. Most GNOME apps apply instantly.

## 3. Settings-backed actions

```rust
// startup (or window constructed)
let app = self.obj();
app.add_action(&settings.create_action("show-hidden")); // bool key -> stateful toggle
app.add_action(&settings.create_action("sort-order"));  // s key with choices -> radio
```

```blueprint
menu primary_menu {
  section {
    item { label: _("Show Hidden Files"); action: "app.show-hidden"; }
  }
  section {
    item { label: _("Sort by Name"); action: "app.sort-order"; target: "name"; }
    item { label: _("Sort by Date"); action: "app.sort-order"; target: "date"; }
  }
}
```

`create_action` returns a `gio::Action` whose name is the key and whose state tracks the key. Menus render check and radio items automatically, and activating them writes the setting. React to changes through a binding or `connect_changed`, not through the action. Shortwave, icon-library and the book's To-Do app (filter) use this. More on actions in `actions-and-shortcuts.md`.

## 4. Saving and restoring window state

```rust
impl ObjectImpl for imp::Window {
    fn constructed(&self) {
        self.parent_constructed();
        self.obj().load_window_state();
    }
}

impl WindowImpl for imp::Window {
    fn close_request(&self) -> glib::Propagation {
        if let Err(err) = self.obj().save_window_state() {
            tracing::warn!("Failed to save the window state: {err}");
        }
        self.parent_close_request()
    }
}

impl Window {
    fn load_window_state(&self) {
        let settings = self.settings();
        self.set_default_size(settings.int("window-width"), settings.int("window-height"));
        if settings.boolean("is-maximized") {
            self.maximize();
        }
    }

    fn save_window_state(&self) -> Result<(), glib::BoolError> {
        let settings = self.settings();
        let (width, height) = self.default_size();
        settings.set_int("window-width", width)?;
        settings.set_int("window-height", height)?;
        settings.set_boolean("is-maximized", self.is_maximized())?;
        Ok(())
    }
}
```

- Use `default_size()`, not `width()`/`height()`. It is the unmaximized size, which is what you want to restore. Saving the allocated size of a maximized window makes "unmaximize" do nothing.
- Save once in `close_request`, not on every `notify::default-width`. Notify-based saving (mission-center) writes dconf on every resize step.
- Window state is lost on Ctrl+Q unless `app.quit` closes the windows first. See `architecture.md` §4.
- Binding `default-width` directly with `settings.bind` looks tempting, but it captures maximized sizes and fires during resizes; no corpus app does it.
- Multi-window apps usually persist only the last-closed window's size. Per-document geometry belongs in your own state file (§7).

## 5. A typed settings wrapper

Stringly-typed keys spread across the codebase rot quickly. Wrap them once (Authenticator `src/models/settings.rs` is the cleanest example in the corpus):

```rust
#[derive(Debug, Clone)]
pub struct Settings(gio::Settings);

impl Settings {
    const SHOW_HIDDEN: &'static str = "show-hidden";
    const SORT_ORDER: &'static str = "sort-order";

    pub fn new() -> Self {
        Self(gio::Settings::new(config::APP_ID))
    }

    pub fn show_hidden(&self) -> bool {
        self.0.boolean(Self::SHOW_HIDDEN)
    }

    pub fn sort_order(&self) -> SortOrder {
        self.0.string(Self::SORT_ORDER).parse().unwrap_or_default()
    }

    pub fn set_sort_order(&self, order: SortOrder) -> Result<(), glib::BoolError> {
        self.0.set_string(Self::SORT_ORDER, order.as_str())
    }

    pub fn connect_show_hidden_changed<F: Fn(bool) + 'static>(&self, f: F) -> glib::SignalHandlerId {
        self.0.connect_changed(Some(Self::SHOW_HIDDEN), move |s, key| f(s.boolean(key)))
    }

    pub fn bind_show_hidden(&self, object: &impl IsA<glib::Object>, property: &str) {
        self.0.bind(Self::SHOW_HIDDEN, object, property).build();
    }

    pub fn inner(&self) -> &gio::Settings {
        &self.0 // for create_action and one-off binds
    }
}
```

Store one instance on the application and reach it via `Application::default().settings()`. A `static` isn't possible without `glib::thread_guard::ThreadGuard`, because `gio::Settings` is `!Send` (Authenticator does it with a guard). A `strum` enum of keys (Shortwave) is an equivalent alternative to the constants.

## 6. GSettings in tests

GSettings needs a compiled schema, and by default it writes to the real dconf database. For tests, set two environment variables **before the first GIO call**:

- `GSETTINGS_SCHEMA_DIR=<build>/data`, the directory where the build wrote `gschemas.compiled`.
- `GSETTINGS_BACKEND=memory`, so writes stay in-process and tests don't touch the developer's settings.

Set them in the Meson `test()` environment (or a `.cargo/config.toml` `[env]` table for plain `cargo test`) rather than with `std::env::set_var`. In edition 2024, `set_var` is `unsafe`, and it races with the parallel test harness.

For a self-contained test with no environment setup, load the schema explicitly:

```rust
fn test_settings() -> gio::Settings {
    let source = gio::SettingsSchemaSource::from_directory(
        env!("GSETTINGS_TEST_SCHEMA_DIR"), // e.g. set via [env] in .cargo/config.toml
        gio::SettingsSchemaSource::default().as_ref(),
        false,
    )
    .expect("compiled schemas");
    let schema = source.lookup(config::APP_ID, false).expect("schema present");
    let backend = gio::memory_settings_backend_new();
    gio::Settings::new_full(&schema, Some(&backend), None)
}
```

This is one more reason to inject `Settings` (or the typed wrapper) into models, instead of calling `gio::Settings::new` deep inside them. See `testing-and-debugging.md` for running GTK tests on the main thread.

## 7. Documents and app data: serde + atomic writes

GSettings is for small preferences. Anything user-created or larger (lists, documents, caches, history) goes in files under the XDG directories:

| Kind | Directory | Example |
| --- | --- | --- |
| user-created data | `glib::user_data_dir()` | tasks, library, collections |
| configuration too structured for GSettings | `glib::user_config_dir()` | backup profiles (pika-backup) |
| state that can be lost without harm | `glib::user_state_dir()` | recent searches, scroll positions |
| regenerable data | `glib::user_cache_dir()` | thumbnails, waveforms (amberol) |

Inside Flatpak these already resolve to `~/.var/app/<app-id>/…`. Still append the app ID, so native runs don't collide with other apps.

```rust
#[derive(Debug, Default, serde::Serialize, serde::Deserialize)]
pub struct Library {
    pub version: u32,
    pub items: Vec<ItemData>,
}

fn library_path() -> std::path::PathBuf {
    glib::user_data_dir().join(config::APP_ID).join("library.json")
}

pub fn load() -> anyhow::Result<Library> {
    match std::fs::read(library_path()) {
        Ok(bytes) => Ok(serde_json::from_slice(&bytes)?),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(Library::default()), // first run
        Err(err) => Err(err.into()),
    }
}

pub fn save(library: &Library) -> anyhow::Result<()> {
    let path = library_path();
    std::fs::create_dir_all(path.parent().expect("has parent"))?;
    let json = serde_json::to_vec_pretty(library)?;
    glib::file_set_contents(&path, &json)?; // writes a temp file, then renames: atomic
    Ok(())
}
```

Why atomic: a plain `File::create` + write truncates first. A crash or power loss mid-write leaves an empty or partial file, and the next start loses everything. `glib::file_set_contents` (or `tempfile::NamedTempFile::persist`) writes beside the target and renames over it. For async I/O on the main loop, `gio::File::replace_contents_future(bytes, None, false, gio::FileCreateFlags::REPLACE_DESTINATION)` gives the same guarantee without blocking.

- **When to save:** on change, debounced (a short `timeout_add_local_once`, see `closures-and-lifetimes.md` §9), plus a final flush in `close_request` or `shutdown`. Saving only on window close (the book's To-Do) loses everything on a crash.
- **Off the main thread:** for anything bigger than a few KB, serialize on the main thread (GObjects are `!Send`), then write with `gio::spawn_blocking(move || save(&snapshot)).await`. See `async-and-threads.md`.
- **Keep the serde structs plain.** Convert to and from GObjects at the boundary (`TaskObject::from_task_data`, `task_data()`), and add a `version` field so you can migrate later.
- **Corrupt file:** don't overwrite it with an empty default. Rename it aside (`library.json.bak`), log the error and show a toast.

## 8. Databases

Use SQLite when data is relational, large, or queried selectively.

- **diesel** + `diesel_migrations` with embedded migrations (`embed_migrations!`) is the corpus choice (Shortwave `src/database/connection.rs`, Authenticator, podcasts). They use an r2d2 pool with `max_size(1)` behind a `LazyLock`, with the database at `user_data_dir()/<app>.db`, and run migrations on first use.
- **rusqlite** (with `bundled`, or system SQLite in Flatpak) is lighter for a handful of tables. Use `PRAGMA user_version` for migrations.
- Run queries off the main thread (`gio::spawn_blocking`, or the app's tokio runtime) and return plain structs. Build GObjects on the main thread afterwards.
- Never keep a connection or transaction open across an `await` on the main thread.

## 9. Secrets

Passwords and tokens never go into GSettings or JSON. Use the Secret portal or Secret Service through the `oo7` crate (pika-backup, podcasts). Authenticator uses a keyring module of its own. See `platform-integration.md` for setup, Flatpak permissions and the async API.

## 10. Worked example: the book's To-Do persistence

The gtk-rs book's To-Do app (listings `todo/1`–`todo/10`) shows the minimal layering:

- `TaskData { completed: bool, content: String }` is a serde struct. The `TaskObject` GObject stores it in `RefCell<TaskData>` and exposes the fields as properties with `#[property(name = "completed", get, set, type = bool, member = completed)]`.
- The filter preference (`all` / `open` / `done`) is a GSettings `s` key. `settings.create_action("filter")` drives the menu, and `connect_changed(Some("filter"), …)` re-applies the `gtk::FilterListModel` filter.
- Data lives at `glib::user_data_dir()/<APP_ID>/data.json`. `constructed` restores it (a missing file means first run), and `close_request` maps the `gio::ListStore` to `Vec<CollectionData>` and writes it with `serde_json::to_writer` before `parent_close_request()`.
- When switching collections, todo/8 disconnects the previous collection's `items-changed` handler through a stored `SignalHandlerId`.

What to change for production: write atomically (§7) instead of `File::create`, save on change with a debounce instead of only on close, handle errors with a toast instead of `expect`, and do the write in `spawn_blocking` once the data grows.

## Pitfalls

- **App aborts at startup: "Settings schema 'org.example.App.Devel' is not installed"** → schema not compiled or `GSETTINGS_SCHEMA_DIR` not set → run through `meson devenv`, or install and compile the schema.
- **Devel and stable builds overwrite each other's settings** → hard-coded schema ID → use `@app-id@`/`config::APP_ID`.
- **Tests change the developer's real preferences** → no memory backend → `GSETTINGS_BACKEND=memory`.
- **Restored window is huge after unmaximizing** → saved `width()` instead of `default_size()`.
- **Window size not saved on Ctrl+Q** → `quit()` skipped `close-request` → close the windows first.
- **Settings handler fires after the window closed** → `connect_changed` on the shared settings with a strong capture, never disconnected → `#[weak]` + disconnect in `dispose`, or use `bind`.
- **Data file empty after a crash** → non-atomic write → `glib::file_set_contents` / `replace_contents_future`.
- **"Type mismatch" panic from `settings.get::<T>()`** → Rust type doesn't match the key's GVariant type (e.g. `i32` for a `u` key) → match the schema type.

## See also

`architecture.md`, `actions-and-shortcuts.md`, `closures-and-lifetimes.md`, `async-and-threads.md`, `platform-integration.md`, `testing-and-debugging.md`, `project-layout-and-build.md`, `gobject.md`
