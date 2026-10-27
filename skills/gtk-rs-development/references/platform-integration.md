# Platform integration

Read this when the app needs to talk to the desktop: portals, sandboxed file access, background running,
notifications, D-Bus services and activation, search providers, MPRIS, secrets, HTTP, GStreamer or suspend inhibition.

## Contents

- [Pick crates by runtime first](#pick-crates-by-runtime-first)
- [Portals with ashpd](#portals-with-ashpd)
- [File access in the sandbox](#file-access-in-the-sandbox)
- [Background running and autostart](#background-running-and-autostart)
- [Camera, screenshot and color picker](#camera-screenshot-and-color-picker)
- [Notifications](#notifications)
- [D-Bus: zbus or gio](#d-bus-zbus-or-gio)
- [D-Bus activation](#d-bus-activation)
- [GNOME Shell search provider](#gnome-shell-search-provider)
- [MPRIS media controls](#mpris-media-controls)
- [Secrets with oo7](#secrets-with-oo7)
- [Networking](#networking)
- [GStreamer](#gstreamer)
- [Inhibiting suspend](#inhibiting-suspend)
- [Data persistence](#data-persistence)

Crate versions below are the current majors on crates.io as of October 2026: ashpd 0.13, zbus 6, oo7 0.6, gstreamer
0.25, mpris-server 0.11, search-provider 0.13, reqwest 0.13, soup3 0.9, async-channel 2. The Rust snippets were
compile-checked against those releases with gtk4 0.11.5 and libadwaita 0.9.2.

## Pick crates by runtime first

| Crate | Runtime feature | Where you await it |
| --- | --- | --- |
| ashpd 0.13 | `async-io` | directly in `glib::spawn_future_local` (amberol, warp, Shortwave, pika-backup) |
| ashpd 0.13 | `tokio` | inside the static tokio runtime via `spawn_tokio` (fractal, Authenticator) |
| zbus 6 | default (`default-rt`, zbus's own helper thread) | directly on GLib |
| zbus 6 | `tokio` | connection must be built while a tokio runtime is current |
| mpris-server, search-provider (default features) | async-io via zbus | directly on GLib, `!Send` objects |
| oo7 0.6 | `tokio` (default) or `async-std` (async-io underneath) | `tokio`: via `spawn_tokio` (Authenticator) |
| reqwest 0.13 | always tokio | via `spawn_tokio`, a tokio thread, or `async-compat` |

Why: a future built for one runtime panics or hangs when polled by another. If the app has no other tokio dependency,
choose the async-io features and skip tokio entirely; if it already runs a tokio runtime for reqwest or matrix-sdk,
use the `tokio` features consistently so every portal call goes through the same `spawn_tokio` helper. `ashpd`'s
`async-std` feature (contrast, Fragments) is outdated because async-std is discontinued. See `async-and-threads.md`
for the runtime helpers.

oo7's runtime feature is forwarded to ashpd (`tokio` enables `ashpd/tokio`, `async-std` enables `ashpd/async-io`), and
ashpd refuses to build with both ("You can't enable both async-io & tokio features at once"). Pick one runtime for
both crates: oo7 `tokio` with ashpd `tokio`, or oo7 `async-std` with ashpd `async-io`.

ashpd 0.13, oo7 0.6 and search-provider 0.13 are built on zbus 5 (the apps pair them with `zbus = "5"`); mpris-server
0.11 moved to zbus 6 (0.10 is the zbus 5 release). If you depend on zbus 6 directly as well, `cargo tree -d` will show
two zbus copies whose types are not interchangeable; keep your own zbus code independent of those crates' types, or
match their zbus major until they move.

## Portals with ashpd

```toml
ashpd = { version = "0.13", default-features = false, features = ["gtk4", "async-io", "background"] }
```

```rust
use ashpd::WindowIdentifier;

// Parent the portal dialog to our window so it is modal and positioned correctly.
let identifier = WindowIdentifier::from_native(&window).await;
```

- Enable one feature per portal you use (`background`, `camera`, `screenshot`, `settings`, `flatpak`, …; the color
  picker is part of `screenshot`) plus `gtk4` (or `gtk4_wayland`) for `WindowIdentifier::from_native`, which returns
  `Option<WindowIdentifier>`; request builders accept the `Option` as is.
- Always pass an identifier when the request shows UI; without it the dialog floats unparented.
- `ashpd::is_sandboxed()` is a cheap synchronous check; cache it in a `static APP_IS_SANDBOXED: LazyLock<bool> =
  LazyLock::new(ashpd::is_sandboxed);` (pika-backup).
- Unsandboxed apps that use portals tied to an app id (camera) should call
  `ashpd::register_host_app(APP_ID.try_into()?).await` first; it takes an `ashpd::AppID` (Authenticator:
  `src/widgets/camera.rs`).
- Map errors for users: `ashpd::Error::Response(ashpd::desktop::ResponseError::Cancelled)` is the user saying no and
  should not show an error; `ashpd::Error::Portal(ashpd::PortalError::NotAllowed(_))` means the permission was denied
  in Settings (warp: `src/util/error.rs`).
- For dark style and accent color, use `adw::StyleManager`, not the settings portal. Read other desktop settings with
  the settings portal and watch them as a stream, upgrading a weak ref per item (fractal:
  `src/system_settings/linux.rs`).

## File access in the sandbox

```rust
let dialog = gtk::FileDialog::builder().title(gettext("Choose a Folder")).modal(true).build();
let folder = dialog.select_folder_future(Some(&window)).await?; // ignore Dismissed, see async-and-threads.md
// In Flatpak this is a document-portal path such as /run/user/1000/doc/ab12cd34/Photos.
// Use it like any other gio::File; prefer the URI when handing it to other libraries.
self.imp().settings.set_string("last-folder", &folder.uri()).ok();
```

Why: `gtk::FileDialog` goes through the FileChooser portal when sandboxed, and the portal exports the chosen file or
folder through the documents portal (FUSE under `/run/user/<uid>/doc/`). The app gets access to exactly what the user
picked, with no `--filesystem` permission, so prefer the dialog over broad filesystem holes in the manifest.

- Treat a stored path or URI as a hint: on the next start, check that it still opens and otherwise ask again. Hand
  files to other components by URI (GStreamer `playbin` takes `file.uri()`, identity: `src/page.rs`).
- To reveal a file in the file manager or open it with the default app, use
  `gtk::FileLauncher::new(Some(&file)).open_containing_folder_future(Some(&window))` or `.launch_future(…)`. Raw
  `OpenURI` D-Bus calls are no longer needed (warp: `src/util.rs`).
- Derive app-private paths from `glib::user_data_dir()`, `user_config_dir()`, `user_cache_dir()` and
  `user_runtime_dir()`. In Flatpak they point at `~/.var/app/<app-id>/…`, which is what you want for your own data.
  Never build paths from `$HOME` or the `dirs` crate.
- When you really need the *host* user directory (pika-backup must find the host's `~/.local/share`), Flatpak exports
  `HOST_XDG_DATA_HOME`, `HOST_XDG_CONFIG_HOME`, `HOST_XDG_CACHE_HOME` (and `HOST_XDG_STATE_HOME` on newer Flatpak).
  They may be unset (Flatpak forwards them only when the host defines the variable), so fall back to `glib::home_dir().join(".local/share")`
  (pika-backup: `pika-backup-common/src/utils/host.rs`). Reading host paths still requires a matching filesystem
  permission.

## Background running and autostart

```rust
// imp struct: background_hold: RefCell<Option<gio::ApplicationHoldGuard>>,
async fn request_background(&self, window: &gtk::Window) {
    if !ashpd::is_sandboxed() {
        self.imp().background_hold.replace(Some(self.hold()));
        return;
    }
    let identifier = ashpd::WindowIdentifier::from_native(window).await;
    let response = ashpd::desktop::background::Background::request()
        .identifier(identifier)
        .reason(&*gettext("Keep playing music after the window is closed"))
        .auto_start(false)
        .send()
        .await
        .and_then(|request| request.response());
    match response {
        Ok(bg) if bg.run_in_background() => {
            self.imp().background_hold.replace(Some(self.hold()));
        }
        Ok(_) | Err(ashpd::Error::Response(ashpd::desktop::ResponseError::Cancelled)) => {
            self.imp().settings.set_boolean("run-in-background", false).ok();
        }
        Err(err) => tracing::warn!("Background portal request failed: {err}"),
    }
}
```

Why: the background portal asks the user (once) whether the app may keep running without windows and lets the
compositor show it as a background app. `gio::ApplicationExtManual::hold()` returns an `ApplicationHoldGuard` that
keeps `GApplication` alive after the last window closes; dropping the guard releases it, so storing it in a
`RefCell<Option<_>>` makes "stop running in background" a simple `take()` (amberol: `src/application.rs`).

- Autostart: `.auto_start(true).command(["my-app", "--background"])`. Set `.dbus_activatable(false)` when the
  autostart command is a separate daemon binary, otherwise activation would start the UI (pika-backup:
  `pika-backup/src/utils.rs`, which keeps the long-running monitor in its own binary).
- The background status line shown by GNOME is set through `BackgroundProxy::new().await?` and its
  `set_status(SetStatusOptions::default().set_message(…))`; keep the message short (Shortwave ellipsizes to 96
  characters, `src/app.rs`).

## Camera, screenshot and color picker

- **Camera:** `ashpd::desktop::camera::Camera::new().await?`, then `request_access(CameraAccessOptions::default())`
  and `open_pipe_wire_remote(OpenPipeWireRemoteOptions::default())`, which returns an `OwnedFd` for a PipeWire stream. The `aperture` crate
  (0.12) turns that into a `Viewfinder` widget (Authenticator: `src/widgets/camera.rs`; warp wraps the request in a
  `gio::Cancellable` future, `src/ui/camera.rs`).
- **Screenshot:** `ashpd::desktop::screenshot::ScreenshotRequest::default().identifier(id).modal(true)
  .interactive(true).send().await?.response()?` gives a URI; open it with `gio::File::for_uri`.
- **Color picker:** `ashpd::desktop::Color::pick().identifier(id).send().await?.response()?`. contrast waits 200 ms
  with `glib::timeout_future` first so the popover closes before the picker grabs input.

## Notifications

```rust
fn notify_download_finished(&self, download: &Download) {
    // Skip if the user is looking at the app already.
    if self.active_window().is_some_and(|w| w.is_active()) {
        return;
    }
    let target = download.id().to_variant();
    let notification = gio::Notification::new(&gettext("Download Finished"));
    notification.set_body(Some(&download.title()));
    notification.set_default_action_and_target_value("app.show-download", Some(&target));
    notification.add_button_with_target_value(&gettext("Open"), "app.open-download", Some(&target));
    self.send_notification(Some(&format!("download-{}", download.id())), &notification);
}
```

Why: `gio::Notification` goes through the notification portal in Flatpak and through `org.freedesktop.Notifications`
or GNOME Shell's GTK interface outside it; the app does not care. Buttons and the default click trigger `app.*`
actions with a target, so they work even when the app was not running and is started by the click (that requires D-Bus
activation, below). A stable id per logical event means a newer notification replaces the older one instead of
stacking (Shortwave: `src/app.rs`).

- Only `app.` actions work; `win.` actions are not reachable from the notification server.
- `withdraw_notification(&id)` when the event becomes irrelevant (fractal withdraws once a message is read;
  Authenticator after the OTP period).
- `set_priority(gio::NotificationPriority::High)` for urgent events. `set_category` is gated on gio's `v2_70`
  feature, which gtk's `gnome_*` features already enable.
- Notifications require a desktop file whose name matches the app id; uninstalled `cargo run` builds may show nothing.

## D-Bus: zbus or gio

Use zbus for your own interfaces (typed, derive-based) and gio only for one-off calls or when you already have a
`gio::DBusConnection` (the `GApplication` one). `GApplication` already exports your actions as `org.gtk.Actions` on
its object path, so simple remote control may need no custom interface.

Export an interface with zbus and forward calls to the UI over a channel:

```rust
// zbus = "6"
pub enum Command {
    StartBackup(String),
}

struct Service {
    commands: async_channel::Sender<Command>,
}

#[zbus::interface(name = "org.example.App1")]
impl Service {
    async fn start_backup(&self, config_id: String) -> zbus::fdo::Result<()> {
        self.commands
            .send(Command::StartBackup(config_id))
            .await
            .map_err(|err| zbus::fdo::Error::Failed(err.to_string()))
    }
}

async fn serve(app: &crate::Application) -> zbus::Result<zbus::Connection> {
    let (tx, rx) = async_channel::unbounded();
    let connection = zbus::connection::Builder::session()
        .name(format!("{}.Service", crate::config::APP_ID))
        .serve_at("/org/example/App", Service { commands: tx })
        .build()
        .await?;

    let app = app.downgrade();
    glib::spawn_future_local(async move {
        while let Ok(command) = rx.recv().await {
            let Some(app) = app.upgrade() else { break };
            app.handle_command(command);
        }
    });
    Ok(connection) // keep it alive, e.g. in a OnceCell on the imp struct
}
```

Why: zbus interface objects must be `Send + Sync` and their methods run on zbus's executor, not on the GTK thread, so
they cannot hold GObjects. Holding only a `Sender` keeps the boundary clean and every UI change happens on the main
thread (pika-backup: `pika-backup/src/dbus.rs`). In zbus 6 the builder methods return `Self` and report invalid names
or paths from `build()`, so there is no `?` until then. Dropping the `Connection` unregisters the name and objects.

With gio, the equivalent is `connection.register_object(path, &interface_info).method_call(…).build()`, where the
interface comes from `gio::DBusNodeInfo::for_xml(XML)?.lookup_interface(name)`. The closure runs on the main thread
and receives a `gio::DBusMethodInvocation` that you must answer on every path (`return_value`, `return_result`,
`return_gerror`, or `return_future_local` for async work). Register in `ApplicationImpl::dbus_register(&self,
connection, object_path)` so the object lives on the app's own connection. No surveyed app does this; prefer zbus
unless avoiding the dependency matters.

In Flatpak, the app may own its app id and names below it (`<app-id>.Service`) without extra permissions; talking to
other services needs `--talk-name=…` in the manifest.

## D-Bus activation

```ini
# data/<app-id>.desktop.in.in: add
DBusActivatable=true
```

```ini
# data/<app-id>.service.in
[D-BUS Service]
Name=@app-id@
Exec=@bindir@/@bin@ --gapplication-service
```

```meson
service_conf = configuration_data()
service_conf.set('app-id', application_id)
service_conf.set('bindir', get_option('prefix') / get_option('bindir'))
service_conf.set('bin', meson.project_name())
configure_file(
  input: '@0@.service.in'.format(base_id),
  output: '@0@.service'.format(application_id),
  configuration: service_conf,
  install: true,
  install_dir: datadir / 'dbus-1' / 'services',
)
```

Why: with `DBusActivatable=true`, the shell launches the app by calling `Activate` (or `ActivateAction` for
notification buttons and desktop actions) on the bus name, and D-Bus starts the process from the `.service` file if it
is not running. `--gapplication-service` tells `GApplication` to run as a service and stay alive for the activation
call instead of treating it as a normal launch. Flatpak exports service files whose name starts with the app id
automatically (amberol, icon-library, Shortwave all ship one).

- The service `Name` must equal the application id exactly, including a `.Devel` suffix for development builds, and
  the desktop file must be named `<app-id>.desktop`. Generate both from the same Meson variable.

## GNOME Shell search provider

Use the `search-provider` crate 0.13 with default features (named `async-std`, async-io via zbus) and implement
`SearchProviderImpl` on your `Application` (synchronous `initial_result_set`, `result_metas`, `activate_result`, …).
Start it from a local future in `ObjectImpl::constructed` with
`SearchProvider::new(app.clone(), format!("{APP_ID}.SearchProvider"), object_path).await` (icon-library:
`src/application.rs`). That future only resolves on an error: after registering it keeps serving requests, so there is
no provider value to store; keep the `glib::JoinHandle` if you need to stop it. It also panics if there is no session
bus. Ship:

- `data/<app-id>.search-provider.ini` with `DesktopId=<app-id>.desktop`, `BusName=<app-id>.SearchProvider`,
  `ObjectPath=…`, `Version=2`, installed to `datadir/gnome-shell/search-providers`.
- `data/<app-id>.SearchProvider.service.in` (`Name=<app-id>.SearchProvider`, `Exec=… --gapplication-service`).

Avoid Authenticator's shape (provider on tokio, blocking a worker with `futures_executor::block_on` while the main
thread answers); running the provider on GLib keeps everything single-threaded. Return no results while the app is
locked.

## MPRIS media controls

```rust
// mpris-server = "0.11"
let player = mpris_server::Player::builder(crate::config::APP_ID)
    .identity("My Player")
    .desktop_entry(crate::config::APP_ID)
    .can_play(true)
    .can_pause(true)
    .can_raise(true)
    .build()
    .await?;
let player = Rc::new(player);
player.connect_play_pause(clone!(#[weak] app, move |_| app.toggle_playback()));
glib::spawn_future_local(player.run()); // serves the D-Bus interface
self.imp().mpris.set(player).ok();
```

Why: mpris-server is zbus/async-io based and its `Player` is `!Send`, so it lives on the main context and is updated
with `player.set_metadata(…).await` / `set_playback_status(…).await` from local futures whenever your player GObject
notifies (Shortwave: `src/audio/media_controls/mpris.rs`). Build it from a spawned future and store it in a
`OnceCell`; podcasts' `RUNTIME.block_on(...)` inside `Default` blocks the main thread at construction. Cover art is
passed as a `file://` URL to a cached image.

## Secrets with oo7

```rust
// oo7 = { version = "0.6", default-features = false, features = ["tokio", "native_crypto"] }
pub static KEYRING: OnceLock<oo7::Keyring> = OnceLock::new();

fn attributes(account: &str) -> HashMap<&str, &str> {
    HashMap::from([("application", crate::config::APP_ID), ("account", account)])
}

pub async fn init() -> Result<(), Box<oo7::Error>> {
    let keyring = oo7::Keyring::new().await?;
    keyring.unlock().await?;
    let _ = KEYRING.set(keyring);
    Ok(())
}

pub async fn store_token(account: &str, token: &str) -> Result<(), Box<oo7::Error>> {
    let keyring = KEYRING.get().expect("keyring initialized");
    keyring.create_item("My App token", &attributes(account), token.as_bytes(), true).await?;
    Ok(())
}

pub async fn token(account: &str) -> Result<Option<Vec<u8>>, Box<oo7::Error>> {
    let keyring = KEYRING.get().expect("keyring initialized");
    let items = keyring.search_items(&attributes(account)).await?;
    match items.first() {
        Some(item) => Ok(Some(item.secret().await?.to_vec())),
        None => Ok(None),
    }
}
```

Why: oo7 is a pure-Rust Secret Service client. Inside Flatpak it uses the secret portal and an app-private encrypted
file keyring; outside it talks to the host Secret Service over D-Bus. libsecret is a C dependency with no maintained
Rust binding, and no surveyed app uses it. With the `tokio` feature, call these functions through `spawn_tokio`
(Authenticator: `src/models/keyring.rs`).

- Always include an `application` attribute so your items do not collide with other apps on the host keyring.
- `create_item(…, replace = true)` overwrites an existing item with the same attributes.
- Plan for a missing or locked keyring (no Secret Service on minimal desktops): show an explanation and let the user
  continue without saving credentials. pika-backup has a recovery path for corrupted file keyrings
  (`pika-backup-common/src/utils/oo7_workaround.rs`).
- `oo7::Error` is large enough to trip clippy's `result_large_err`, hence the `Box` (`?` boxes it for you).
- `item.secret()` returns `oo7::Secret`, a zeroize-on-drop enum (`Text` / `Blob`) that derefs to `[u8]`. Return the
  `Secret` itself instead of `.to_vec()` when you want the bytes wiped on drop.

## Networking

reqwest (0.13) is the common choice and always needs tokio: use the static runtime and `spawn_tokio` (fractal,
podcasts), a dedicated current-thread runtime on its own thread (Shortwave: `src/api/http.rs`), or `.compat().await`
for occasional calls (Fragments). Keep one `reqwest::Client` and clone it; it pools connections. Deserialize large
responses in `gio::spawn_blocking`.

Without tokio, libsoup through the `soup3` crate (0.9, imported as `soup`, needs `soup::prelude::*`) integrates with the
GLib loop natively and honours GNOME proxy settings: `soup::Session::new()`, `soup::Message::new("GET", url)?` and
`session.send_and_read_future(&message, glib::Priority::DEFAULT).await`, which yields `glib::Bytes`. No surveyed app
uses it. In Flatpak, network access needs `--share=network`.

## GStreamer

For "play this file", skip GStreamer code entirely and use GTK's media widgets, which use GStreamer underneath:

```rust
let video = gtk::Video::for_file(Some(&file)); // or video.set_file(Some(&file)) on a template child
video.set_autoplay(true);
// Audio only, or custom controls:
let media = gtk::MediaFile::for_file(&file);
media.play();
```

For custom pipelines:

```rust
// main.rs, before app.run()
gst::init().expect("GStreamer should initialize");

// imp struct
pipeline: RefCell<Option<gst::Element>>,
bus_watch: RefCell<Option<gst::bus::BusWatchGuard>>,

// setup
let sink = gst::ElementFactory::make("gtk4paintablesink").build()?;
let paintable = sink.property::<gdk::Paintable>("paintable");
self.picture.set_paintable(Some(&paintable));
let playbin = gst::ElementFactory::make("playbin3").property("video-sink", &sink).build()?;
playbin.set_property("uri", file.uri());
let guard = playbin.bus().expect("pipelines have a bus").add_watch_local(clone!(
    #[weak(rename_to = imp)]
    self,
    #[upgrade_or]
    glib::ControlFlow::Break,
    move |_bus, message| {
        imp.on_bus_message(message);
        glib::ControlFlow::Continue
    }
))?;
self.bus_watch.replace(Some(guard));
self.pipeline.replace(Some(playbin));

// ObjectImpl::dispose
if let Some(pipeline) = self.pipeline.take() {
    let _ = pipeline.set_state(gst::State::Null);
}
self.bus_watch.take();
```

Why: `gtk4paintablesink` (from gst-plugin-gtk4 0.15) produces a `gdk::Paintable` that a `gtk::Picture` shows with
zero-copy where possible. `add_watch_local` dispatches bus messages on the main thread, and the watch is removed as
soon as the returned `BusWatchGuard` is dropped, so it must be stored. Setting the pipeline to `Null` in `dispose`,
synchronously, stops streaming threads and releases devices before the paintable goes away (identity: `src/page.rs`).

- State changes can block; identity pre-rolls to `Paused` inside `playbin.call_async(…)` so the UI never waits.
- Messages from GStreamer's own threads (a `set_sync_handler`) must cross to the UI through
  `async_channel::Sender::send_blocking` (amberol: `src/audio/gst_backend.rs`).
- gst-plugin-gtk4 must exist at runtime: either rely on the GNOME runtime's plugin or build it in the Flatpak
  manifest; identity links it statically and calls `gstgtk4::plugin_register_static()`.
- For "run a pipeline and await the result" (thumbnails, waveforms), resolve a oneshot from the bus watch on EOS or
  error and await it in a local future (fractal: `src/utils/media/video.rs`).
- `gstreamer-play` (`gst_play::Play`) is a simpler player API for audio apps (amberol, podcasts).

## Inhibiting suspend

```rust
// imp struct: inhibit_cookie: Cell<Option<u32>>,
fn set_playing(&self, playing: bool) {
    let app = self.obj();
    if playing && self.inhibit_cookie.get().is_none() {
        let cookie = app.inhibit(
            app.active_window().as_ref(),
            gtk::ApplicationInhibitFlags::SUSPEND,
            Some(&gettext("Playing audio")),
        );
        // 0 means the request failed
        self.inhibit_cookie.set((cookie != 0).then_some(cookie));
    } else if !playing && let Some(cookie) = self.inhibit_cookie.take() {
        app.uninhibit(cookie);
    }
}
```

Why: `gtk::Application::inhibit` uses the inhibit portal or the session manager, so it works in and out of Flatpak.
The cookie is the only handle for releasing it; losing it keeps the system awake until the app exits (Shortwave,
amberol: `src/inhibit_controller.rs`). Use `IDLE` for "keep the screen on" (video playback) and `LOGOUT` for unsaved
work.

## Data persistence

Settings, window state, file locations and databases are covered in `settings-and-state.md`. In short: `gio::Settings`
for preferences, `glib::user_*_dir()` for files, atomic writes, and blocking database work off the main thread.

## Pitfalls

- Portal call panics "there is no reactor running" → ashpd or oo7 built with `tokio` but awaited on GLib → use ashpd's
  `async-io` (oo7's `async-std`) feature, or call through `spawn_tokio`.
- Portal dialog appears behind or unattached to the window → no `WindowIdentifier` → pass
  `WindowIdentifier::from_native(&window).await`.
- App quits when the last window closes despite background permission → hold guard not stored → keep `self.hold()` in
  `RefCell<Option<ApplicationHoldGuard>>`.
- Notification button does nothing → `win.` action, or no D-Bus activation for a closed app → use an `app.` action and
  ship the `.service` file with `DBusActivatable=true`.
- zbus interface fails to compile with "cannot be sent between threads" → interface struct holds a GObject or `Rc` →
  hold an `async_channel::Sender` and handle commands on the main thread.
- D-Bus activation starts a second instance or nothing → service `Name`, desktop file name and app id differ (often
  the `.Devel` suffix) → generate all three from the same Meson variable.
- Bus messages stop arriving right after setup → `BusWatchGuard` dropped at end of scope → store it.
- Video freezes or crashes on window close → pipeline still running when the paintable is dropped → set state `Null`
  in `dispose`.
- System stays awake after playback stops → inhibit cookie overwritten or never released → store it in
  `Cell<Option<u32>>` and `uninhibit` on stop.

## See also

- `async-and-threads.md`: runtimes, `spawn_tokio`, channels and cancellation used throughout this file
- `settings-and-state.md`: GSettings, paths and persistence
- `actions-and-shortcuts.md`: app actions targeted by notifications and D-Bus activation
- `packaging-and-ci.md`: Flatpak permissions (`--share=network`, `--talk-name`), service and search-provider files
- `project-layout-and-build.md`: Meson `configure_file` and data installation
- `architecture.md`: where long-running services and background daemons belong
