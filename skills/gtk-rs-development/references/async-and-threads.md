# Async and threads

Read this when you need to run I/O, blocking calls, CPU-heavy work or a tokio-only library without freezing the UI, or
when you need to cancel, debounce or time out that work.

## Contents

- [The main-thread rule](#the-main-thread-rule)
- [Choosing where work runs](#choosing-where-work-runs)
- [Async on the main context](#async-on-the-main-context)
- [Blocking and CPU work: gio::spawn_blocking](#blocking-and-cpu-work-giospawn_blocking)
- [Long-lived workers: std::thread + async-channel](#long-lived-workers-stdthread--async-channel)
- [One-shot hops back to the main thread](#one-shot-hops-back-to-the-main-thread)
- [Tokio-only dependencies](#tokio-only-dependencies)
- [async-io crates on the GLib context](#async-io-crates-on-the-glib-context)
- [Cancellation](#cancellation)
- [Timeouts, polling and debouncing](#timeouts-polling-and-debouncing)
- [Async actions and template callbacks](#async-actions-and-template-callbacks)
- [Async dialogs and launchers](#async-dialogs-and-launchers)
- [RefCell borrows and weak refs across await](#refcell-borrows-and-weak-refs-across-await)
- [Removed: MainContext::channel](#removed-maincontextchannel)

## The main-thread rule

Everything GTK does (input, layout, drawing, signal handlers, futures spawned with `spawn_future_local`) runs on one
thread, the one that called `app.run()`. Two consequences:

1. **Never block that thread.** A `std::thread::sleep`, a synchronous HTTP call or a long loop in a handler freezes
   rendering and input for its whole duration.
2. **Widgets and other GObjects are `!Send` and `!Sync`.** The compiler refuses to move a `gtk::Button` into
   `std::thread::spawn` or `gio::spawn_blocking` (the error mentions `NonNull<GObject>` not being `Send`). Do not try
   to work around this with `unsafe impl Send`. Move plain data (`String`, `PathBuf`, `Vec<u8>`, your own `Send`
   structs) to the worker, and bring results back to the main thread before touching any widget.

`glib::SendWeakRef<T>` lets you carry a GObject reference through a `Send` future, but it may only be upgraded on the
thread that created it (fractal: `src/session/mod.rs`). Use it sparingly; a channel is usually clearer.

## Choosing where work runs

| Work | Use | Result comes back via |
| --- | --- | --- |
| UI-side sequencing (await a dialog, sleep, then update a widget) | `glib::spawn_future_local(async move { … })` | the future itself runs on the main thread |
| File, D-Bus, subprocess or network I/O that GIO provides | `gio::File::*_future`, `gio::Subprocess::communicate_future`, `gio::DBusConnection::call_future` | `.await` inside a local future |
| A blocking call or CPU-bound job that finishes | `gio::spawn_blocking(move \|\| …)` | `.await` the `JoinHandle` in a local future |
| A long-lived worker or foreign event loop (PipeWire, a parser, a device poller) | `std::thread::spawn` + `async_channel` | `send_blocking` on the worker, `while let Ok(x) = rx.recv().await` on the main thread |
| A crate built on tokio (reqwest, matrix-sdk, hickory, ashpd/oo7 with `tokio`) | static `LazyLock<tokio::runtime::Runtime>`; spawn on it, await the `JoinHandle` on GLib | `.await` the tokio `JoinHandle` in a local future |
| A crate built on async-io (zbus, ashpd with `async-io`, oo7 with `async-std`, smol, async-process, mpris-server) | await it directly inside `spawn_future_local` | the future itself |
| A single call into a tokio crate in an otherwise non-tokio app | `async_compat::CompatExt::compat()` | `.await` in a local future |
| A one-off "run this closure on the main thread" from another thread | `glib::idle_add_once` or `MainContext::default().invoke` | the closure runs on the main thread |

Pick the runtime by dependency, not by taste: if nothing in `Cargo.toml` needs tokio, do not add it. None of the real
apps surveyed uses `#[tokio::main]`; the GLib main loop is always the home executor.

## Async on the main context

```rust
use gtk::{gio, glib, glib::clone};

impl imp::Window {
    fn load(&self, file: gio::File) {
        glib::spawn_future_local(clone!(
            #[weak(rename_to = imp)]
            self,
            async move {
                imp.spinner.set_visible(true);
                let result = file.load_contents_future().await;
                imp.spinner.set_visible(false);
                match result {
                    Ok((bytes, _etag)) => imp.show_text(&String::from_utf8_lossy(&bytes)),
                    Err(err) if err.matches(gio::IOErrorEnum::Cancelled) => {}
                    Err(err) => tracing::warn!("Could not load {}: {err}", file.uri()),
                }
            }
        ));
    }
}
```

Why: `spawn_future_local` (same as `glib::MainContext::default().spawn_local`) polls the future on the main loop
between events, so the future can freely touch widgets and `!Send` state. Each `.await` on a GIO future yields to the
loop, which keeps drawing and handling input.

- Lower priority: `MainContext::default().spawn_local_with_priority(glib::Priority::DEFAULT_IDLE, fut)` (fractal).
- GIO `*_future` methods own an internal `Cancellable` and cancel the operation when the future is dropped, so
  aborting the task that awaits them cancels the I/O too.
- `File::replace_contents_future` returns `Err((buffer, glib::Error))`; use `.map_err(|(_, e)| e)`.
- Small synchronous GIO calls with `gio::Cancellable::NONE` on the main thread are acceptable for tiny local files
  only. Anything that can live on a network mount should use `_future` or `spawn_blocking`. identity reports that even
  `read_future` briefly stalls on remote mounts and uses `gio::spawn_blocking(move || std::fs::File::open(path))`
  instead (identity: `src/window.rs`).

## Blocking and CPU work: gio::spawn_blocking

```rust
let path = file.path().expect("local file");
let result = gio::spawn_blocking(move || parse_catalog(&path)).await;
match result {
    Ok(Ok(catalog)) => imp.set_catalog(catalog),
    Ok(Err(err)) => imp.show_error(&err.to_string()),
    Err(_panic) => tracing::error!("catalog parser panicked"),
}
```

Why: `gio::spawn_blocking` runs the closure on GIO's shared thread pool and returns a `JoinHandle` whose output is
`std::thread::Result<T>` (the `Err` is a panic payload). Awaiting it from a local future gives you the result back on
the main thread without any channel.

- The closure must be `Send + 'static`: clone the data you need, never a widget.
- Good candidates: JSON/XML parsing of large payloads, image decoding, hashing, building hundreds of model objects
  that are not yet attached to any widget (Shortwave: `src/api/client.rs`, mission-center: `src/application.rs`).
- It is for sync closures, not futures. The pool is shared, so a job that never returns occupies a pool thread
  forever; long-lived loops belong on a dedicated `std::thread`. smol apps use `smol::unblock` instead (pika-backup).

## Long-lived workers: std::thread + async-channel

```rust
pub enum ScanEvent {
    Found(FileInfo),
    Finished,
}

fn start_scan(&self, root: PathBuf) {
    let (tx, rx) = async_channel::bounded::<ScanEvent>(64);
    let cancellable = gio::Cancellable::new();
    self.scan_cancellable.replace(Some(cancellable.clone()));

    std::thread::Builder::new()
        .name("scanner".into())
        .spawn(move || {
            for entry in walk(&root) {
                if cancellable.is_cancelled() {
                    return;
                }
                // Err means the receiver was dropped: the UI is gone, stop working.
                if tx.send_blocking(ScanEvent::Found(entry)).is_err() {
                    return;
                }
            }
            let _ = tx.send_blocking(ScanEvent::Finished);
        })
        .expect("spawning the scanner thread should succeed");

    let obj = self.obj().downgrade();
    glib::spawn_future_local(async move {
        while let Ok(event) = rx.recv().await {
            let Some(obj) = obj.upgrade() else { break };
            obj.imp().handle_scan_event(event);
        }
    });
}
```

Why: `async-channel` (2.x) has a blocking sender for plain threads and an async receiver that the GLib loop can await.
A bounded channel gives backpressure, so a fast producer cannot flood the main loop. The `while let` loop ends when
every sender is dropped, which is the natural shutdown signal. helvum runs a PipeWire loop on its own thread this way
and joins the thread after `app.run()` returns (helvum: `src/main.rs`, `src/graph_manager.rs`). amberol feeds
GStreamer sync-handler messages into the same kind of channel (amberol: `src/audio/gst_backend.rs`).

- Upgrade the weak ref on every iteration, as above. `#[weak]` in an async `clone!` upgrades once and would keep the
  object alive for as long as the worker runs. A receiver created in `constructed` can wait in
  `RefCell<Option<Receiver<_>>>` until it is `take()`n (amberol: `src/application.rs`).
- `gio::Cancellable` is `Send + Sync`; polling `is_cancelled()` in the worker loop is a cheap stop flag.
- Concurrent async jobs (thumbnails, avatars) need no threads: Shortwave pipes an `async_channel` through
  `StreamExt::buffer_unordered(n)` on the main context (`src/api/texture_loader.rs`).

## One-shot hops back to the main thread

```rust
// From any thread; the closure must be Send and runs on the default main context.
glib::idle_add_once(move || {
    if let Some(app) = gio::Application::default().and_downcast::<crate::Application>() {
        app.merge_icons(icons);
    }
});
// Same, but runs immediately when already on the owning thread.
glib::MainContext::default().invoke(move || { /* … */ });
```

`glib::idle_add_local_once` is the main-thread-only variant (no `Send` bound) and is the standard way to defer work
until after the current signal emission, for example when a handler would otherwise re-enter a `RefCell` that the
caller still borrows (identity: `src/page.rs`). `invoke_with_priority(glib::Priority::HIGH, …)` can order a hop
before other pending sources (pika-backup: `src/status.rs`).

## Tokio-only dependencies

```rust
// src/runtime.rs
use std::{future::Future, sync::LazyLock};

pub static RUNTIME: LazyLock<tokio::runtime::Runtime> = LazyLock::new(|| {
    tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()
        .expect("creating the tokio runtime should succeed")
});

/// Run `fut` on the tokio runtime. Await the returned handle from `glib::spawn_future_local`.
pub fn spawn_tokio<F>(fut: F) -> tokio::task::JoinHandle<F::Output>
where
    F: Future + Send + 'static,
    F::Output: Send + 'static,
{
    RUNTIME.spawn(fut)
}
```

```rust
glib::spawn_future_local(clone!(
    #[weak(rename_to = imp)]
    self,
    async move {
        let client = imp.http.clone(); // reqwest::Client: Send, cheap to clone
        let url = imp.feed_url();
        let handle = crate::runtime::spawn_tokio(async move {
            client.get(url).send().await?.error_for_status()?.bytes().await
        });
        match handle.await {
            Ok(Ok(body)) => imp.show_feed(&body),
            Ok(Err(err)) => imp.show_error(&err.to_string()),
            Err(err) if err.is_cancelled() => {}
            Err(err) => std::panic::resume_unwind(err.into_panic()),
        }
    }
));
```

Why: tokio-based crates panic with "there is no reactor running, must be called from the context of a Tokio 1.x
runtime" when polled on the GLib loop, and `#[tokio::main]` or a top-level `block_on` fights the GLib loop. A lazy
static runtime runs the tokio work on tokio's threads while its `JoinHandle`, an ordinary future, is awaited on GLib.
This is fractal's `spawn!` / `spawn_tokio!` macro pair (fractal: `src/main.rs`, `src/utils/macros.rs`).

- `tokio = { version = "1", features = ["rt-multi-thread"] }` suffices. Only `Send` data crosses into the tokio
  future, so clone what you need out of the GObject first.
- Dropping a tokio `JoinHandle` detaches the task; it does **not** cancel it. Aborting the GLib future that awaits the
  handle therefore leaves the request running. Keep a `tokio::task::AbortHandle` (`handle.abort_handle()`) when you
  need cancellation (see below).
- Values that own tokio resources must be dropped while a runtime is entered; fractal wraps them in a `TokioDrop<T>`
  that calls `RUNTIME.enter()` in `Drop` (fractal: `src/utils/mod.rs`).
- One-off calls: `use async_compat::CompatExt;` then `lookup(host).compat().await`. async-compat 0.2 supplies a
  fallback tokio runtime on a background thread (Shortwave `src/app.rs`, Fragments); switch to the static runtime once
  tokio calls spread through the app.

## async-io crates on the GLib context

```rust
// In an async fn returning zbus::Result, awaited from a local future; no runtime glue needed.
let connection = zbus::Connection::session().await?;
let status = MyServiceProxy::new(&connection).await?.status().await?;
imp.status_label.set_label(&status);
```

Why: async-io-based crates drive their I/O on their own reactor thread, so any executor can poll their futures,
including GLib's. Amberol, warp, pika-backup, Shortwave and icon-library await zbus, ashpd, mpris-server and
search-provider this way with no extra runtime. zbus 6 calls its default backend `zruntime`; a connection built
outside a tokio runtime is driven by zbus's own helper thread and works from GLib unchanged. warp's
`src/util/future.rs` is a compact helper module for smol-based apps.

## Cancellation

Keep the latest task handle on the imp struct and abort the previous one when a new request starts:

```rust
#[derive(Default)]
pub struct SearchPage {
    search_task: RefCell<Option<glib::JoinHandle<()>>>,
    // … template children …
}

impl SearchPage {
    fn search(&self, term: String) {
        let handle = glib::spawn_future_local(clone!(
            #[weak(rename_to = imp)]
            self,
            async move {
                let results = imp.backend().search_future(&term).await;
                imp.search_task.take(); // finished: forget our own handle
                imp.show_results(results);
            }
        ));
        if let Some(previous) = self.search_task.replace(Some(handle)) {
            previous.abort();
        }
    }
}
// In ObjectImpl::dispose: if let Some(task) = self.search_task.take() { task.abort(); }
```

Why: "latest request wins" avoids stale results overwriting fresh ones. `glib::JoinHandle::abort()` drops the future
at its next suspension point, which also cancels any GIO `*_future` it was awaiting. Awaiting an aborted handle yields
`Err(JoinError)` with `is_cancelled() == true`.

Other mechanisms, by situation:

- **tokio tasks:** store `tokio::task::AbortHandle` in `RefCell<Option<_>>` and abort the previous one. fractal wraps
  this in an `AbortableHandle` helper that aborts on drop (fractal: `src/utils/mod.rs`), and re-checks after the await
  that the search term is still the one the request was made for (fractal:
  `src/session_view/room_details/invite_subpage/list.rs`).
- **gio::Cancellable for any future**, shareable with a Cancel button or a worker thread (see the code below).
  `cancellable.future()` resolves on cancel, for use with `futures::select!` (warp: `src/ui/camera.rs`).
- **List rows:** start loading a thumbnail in the factory's `bind`, store the handle on the row widget and abort it in
  `unbind`; recycled rows must not receive a previous item's result (podcasts:
  `podcasts-gtk/src/widgets/shows_view.rs`).
- **Windows:** abort pending tasks in `close_request` (identity aborts a pending `FileDialog` future there,
  `src/window.rs`) or in `dispose`. A never-ending future holding a `#[weak]`-upgraded strong ref keeps a non-window
  object from ever being disposed, so abort it explicitly or upgrade per iteration.
- **Subprocesses:** wrap the future with `futures::future::abortable` and call both `subprocess.force_exit()` and
  `handle.abort()` from the Cancel response (video-trimmer: `src/window.rs`).

```rust
let cancellable = gio::Cancellable::new();
if let Some(old) = self.export_cancellable.replace(Some(cancellable.clone())) {
    old.cancel();
}
// Err(gio::Cancelled) means the user pressed Cancel; nothing to report.
if let Ok(result) = gio::CancellableFuture::new(export(items), cancellable).await {
    self.finish_export(result);
}
```

## Timeouts, polling and debouncing

```rust
// Sleep inside async code
glib::timeout_future(Duration::from_millis(200)).await;

// Bound an operation
match glib::future_with_timeout(Duration::from_secs(10), proxy.status()).await {
    Ok(status) => { /* … */ }
    Err(glib::FutureWithTimeoutError) => imp.show_error(&gettext("The service did not respond")),
}

// Repeating poll that stops itself when the owner is gone
let id = glib::timeout_add_seconds_local(5, clone!(
    #[weak(rename_to = imp)]
    self,
    #[upgrade_or]
    glib::ControlFlow::Break,
    move || {
        imp.refresh();
        glib::ControlFlow::Continue
    }
));
self.poll_source.replace(Some(id));
```

Debounce with a stored `SourceId`, and clear it inside the callback:

```rust
fn schedule_save(&self) {
    if let Some(id) = self.save_timeout.take() {
        id.remove(); // still pending, so removing is valid
    }
    let id = glib::timeout_add_local_once(Duration::from_millis(500), clone!(
        #[weak(rename_to = imp)]
        self,
        move || {
            imp.save_timeout.take(); // the source is gone now; forget its id first
            imp.save();
        }
    ));
    self.save_timeout.replace(Some(id));
}
```

Why: a `_once` source (or a repeating one that returned `Break`) is destroyed after it fires. `SourceId::remove()` on
a destroyed source panics ("Failed to remove source"), so the callback must `take()` its own id before anything else
can try to remove it (fractal: `src/session_view/room_history/mod.rs`). For search fields, `gtk::SearchEntry` already
debounces via its `search-delay` property, so you rarely need your own timer there.

- A repeating callback whose weak upgrade fails must return `ControlFlow::Break`, otherwise the source runs forever.
  `#[upgrade_or] glib::ControlFlow::Break` does that for you.
- For incremental work that must stay on the main thread (adding thousands of rows), process a batch per
  `glib::idle_add_local` iteration and return `Continue` until done (amberol: `src/window.rs`).

## Async actions and template callbacks

```rust
fn class_init(klass: &mut Self::Class) {
    klass.bind_template();
    klass.install_action_async("win.export", None, |win, _name, _param| async move {
        win.action_set_enabled("win.export", false);
        if let Err(err) = win.export().await {
            win.show_toast(&err.to_string());
        }
        win.action_set_enabled("win.export", true);
    });
}
```

Template callbacks can also be `async fn` (returning `()`) inside `#[gtk::template_callbacks]`.

Why: both spawn a new local future on every activation, so the handler body can await dialogs and I/O directly.
Because every click spawns another future, guard against concurrent runs: disable the action (as above), keep a busy
flag, or store and abort the previous handle (contrast: `src/colour_entry.rs` has an async template callback). See `actions-and-shortcuts.md` for action naming.

## Async dialogs and launchers

```rust
async fn open_document(&self) -> Result<(), glib::Error> {
    let dialog = gtk::FileDialog::builder()
        .title(gettext("Open Document"))
        .modal(true)
        .build();
    let file = match dialog.open_future(Some(self)).await {
        Ok(file) => file,
        Err(err) if err.matches(gtk::DialogError::Dismissed)
            || err.matches(gtk::DialogError::Cancelled) => return Ok(()),
        Err(err) => return Err(err),
    };
    let (bytes, _etag) = file.load_contents_future().await?;
    self.imp().set_document(&bytes);
    Ok(())
}

async fn confirm_discard(&self) -> bool {
    let dialog = adw::AlertDialog::builder()
        .heading(gettext("Discard Changes?"))
        .body(gettext("Unsaved changes will be lost."))
        .close_response("cancel")
        .default_response("cancel")
        .build();
    dialog.add_response("cancel", &gettext("_Cancel"));
    dialog.add_response("discard", &gettext("_Discard"));
    dialog.set_response_appearance("discard", adw::ResponseAppearance::Destructive);
    dialog.choose_future(Some(self)).await == "discard"
}
```

Why: `FileDialog::{open, open_multiple, save, select_folder}_future`, `AlertDialog::choose_future`,
`UriLauncher::launch_future` and `FileLauncher::{launch, open_containing_folder}_future` all return plain futures;
awaiting them reads top to bottom with no callback nesting. `Dismissed` means the user closed the dialog and is not an
error to report (pika-backup maps both `Cancelled` and `Dismissed` to a silent `UserCanceled`,
`pika-backup/src/utils.rs`).

- Always pass the parent window. Portals use it to attach the dialog to the right window.
- `choose_future` consumes the dialog and resolves to the response id. Set `close_response` so Escape maps to a known
  id, then compare strings.
- Launchers: `gtk::UriLauncher::new(uri).launch_future(Some(&window)).await`; for a file you just saved,
  `gtk::FileLauncher::new(Some(&file)).open_containing_folder_future(Some(&window))` (warp: `src/util.rs`).

## RefCell borrows and weak refs across await

```rust
// Wrong: the Ref lives across the await; any borrow_mut() elsewhere meanwhile panics.
for item in self.items.borrow().iter() { item.save().await; }

// Right: clone out (GObjects clone as cheap refcount bumps), drop the borrow, then await.
let items: Vec<Item> = self.items.borrow().clone();
for item in &items { item.save().await; }
```

```toml
# Cargo.toml
[lints.clippy]
await_holding_refcell_ref = "deny"
```

Why: while a local future is suspended, the main loop runs other handlers, and any of them may `borrow_mut()` the same
cell. Holding a `Ref`/`RefMut` across `.await` turns that into a runtime `BorrowMutError` panic that only shows up
under specific timing. The clippy lint catches the direct case at compile time. It already warns by default; none of the
surveyed apps sets it to `deny`, but doing so makes the build fail instead of only warning.

- After an await, re-read state instead of trusting values captured before it. The world may have changed (another
  search started, the item was removed).
- Prefer `Cell<Option<T>>::take()` / `set()` swaps or short scoped borrows for state touched by re-entrant callbacks.
- `#[weak]` / `#[weak(rename_to = …)]` in an async `clone!` upgrades **once**, when the future starts. For short tasks
  that is fine. For infinite loops and streams, capture a `WeakRef` (`obj.downgrade()`) and upgrade per iteration,
  dropping the strong ref before the next await. See `closures-and-lifetimes.md`.

## Removed: MainContext::channel

`glib::MainContext::channel`, `glib::Sender` and `Receiver::attach` no longer exist in glib 0.22. Old tutorials (and
Tau, with the old `clone!(@weak x => …)` syntax) still use them. Replace mechanically:

| Old | New |
| --- | --- |
| `let (tx, rx) = glib::MainContext::channel(glib::Priority::DEFAULT);` | `let (tx, rx) = async_channel::unbounded();` (or `bounded(n)`) |
| `tx.send(x)` from a thread | `tx.send_blocking(x)` |
| `rx.attach(None, move \|x\| { …; glib::ControlFlow::Continue })` | `glib::spawn_future_local(async move { while let Ok(x) = rx.recv().await { … } })` |

## Pitfalls

- UI freezes for seconds → a blocking call (sync HTTP, `std::fs` on a network mount, `block_on`) runs on the main
  thread → move it into `gio::spawn_blocking` or use a `_future` API.
- Panic "there is no reactor running, must be called from the context of a Tokio 1.x runtime" → a tokio-based future
  is polled on GLib → spawn it on the static runtime and await the `JoinHandle`, or switch the crate to its `async-io`
  feature.
- `BorrowMutError` panic only under certain timings → a `Ref` held across `.await` → clone out before awaiting; deny
  `clippy::await_holding_refcell_ref`.
- Panic "Failed to remove source" → `SourceId::remove()` on a timeout that already fired → `take()` the id inside the
  callback.
- A timer keeps running after its window closed → callback returns `Continue` on failed upgrade → `#[upgrade_or]
  glib::ControlFlow::Break`.
- Cancelled request still hits the network → you aborted the GLib future that awaited a tokio `JoinHandle`, which only
  detaches the task → abort the tokio `AbortHandle`.
- An object is never disposed while its loop runs → `#[weak]` in an async `clone!` became a strong ref for the whole
  infinite loop → capture a `WeakRef` and upgrade per iteration.
- Double submission when the user clicks twice → each async action or template callback activation spawns a new future
  → disable the action or keep a busy flag while running.
- Errors logged every time the user closes a file chooser → `DialogError::Dismissed` treated as a failure → match and
  ignore `Dismissed` and `Cancelled`.

## See also

- `closures-and-lifetimes.md`: `clone!` attributes, weak vs strong captures, reference cycles
- `platform-integration.md`: portals, D-Bus, secrets, networking and which runtime each needs
- `actions-and-shortcuts.md`: action naming and `install_action` vs `ActionEntry`
- `lists-and-models.md`: factory `bind`/`unbind` and row recycling
- `libadwaita-ui.md`: `adw::AlertDialog` and the dialog family
- `testing-and-debugging.md`: running async code in tests
