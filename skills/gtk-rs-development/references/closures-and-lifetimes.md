# Closures, references and object lifetimes

Read this when you are writing signal handlers, async blocks or timers that capture GObjects, when something leaks or never finalizes, when a handler fires after its widget is gone, or when a `RefCell` panics with "already borrowed".

Contents:

1. `clone!`: capture syntax
2. `closure!` / `closure_local!` and `#[watch]`
3. Reference cycles: when weak, when strong
4. Weak captures in async code
5. Storing and disconnecting handlers
6. "Current object" patterns: SignalGroup, BindingGroup, BoundObject
7. `RefCell` borrow rules
8. `dispose` vs `Drop`
9. Timers and `SourceId`
10. Pitfalls / See also

## 1. `clone!`: capture syntax

```rust
use gtk::glib::{self, clone};

button.connect_clicked(clone!(
    #[weak(rename_to = imp)]
    self, // inside an imp method: a weak ref to the object
    #[weak]
    entry, // a widget
    #[strong]
    counter, // an Rc<Cell<u32>> nothing else owns
    #[to_owned]
    label, // &str -> String
    move |_| {
        counter.set(counter.get() + 1);
        imp.submit(&entry.text(), &label);
    }
));
```

| Attribute | Captures | If the weak upgrade fails |
| --- | --- | --- |
| `#[weak] x` / `#[weak(rename_to = y)] x` | `x.downgrade()`, upgraded on each call | closure returns early (see below) |
| `#[weak_allow_none] x` | weak; the body receives `Option<T>` | body still runs with `None` |
| `#[strong] x` / `#[strong(rename_to = y)] x` | `x.clone()` | — |
| `#[to_owned] x` | `x.to_owned()` (e.g. `&str` → `String`) | — |
| `#[upgrade_or] expr,` | — | returns `expr` (must be `Copy`) |
| `#[upgrade_or_else] \|\| expr,` | — | calls the (non-async) closure |
| `#[upgrade_or_default]` | — | returns `Default::default()` |
| `#[upgrade_or_panic]` | — | panics |

Rules:

- `self` and fields must be renamed: `#[weak(rename_to = imp)] self`, `#[strong(rename_to = model)] self.model`.
- Inside an `imp` method, `self` is the imp struct. Capturing it weakly works because `#[glib::object_subclass]` implements `Downgrade` for imp types. Use `imp.obj()` in the body to get the wrapper. In a wrapper method, write `#[weak(rename_to = obj)] self`.
- `#[weak]` works on GObjects and on `Rc`/`Arc`. Plain Rust data needs `#[strong]` or `#[to_owned]`.
- The `upgrade_or*` attribute goes after all captures and before the closure. Without one, the closure returns `Default::default()` of its return type (usually `()`). `glib::Propagation` and `glib::ControlFlow` don't implement `Default`, so closures returning them fail to compile until you choose a value: `#[upgrade_or] glib::Propagation::Proceed,` or `#[upgrade_or] glib::ControlFlow::Break,`.
- `glib` must be in scope (`use gtk::glib;`), because the macro expands to `glib::…` paths.
- The legacy `clone!(@weak x => @default-return v, move || …)` syntax was removed in glib 0.20 and no longer compiles. See `deprecated-apis.md`.

## 2. `closure!` / `closure_local!` and `#[watch]`

`closure_local!` builds a `glib::RustClosure` for `connect_closure`, `gtk::ClosureExpression` and `SignalGroup::connect_closure`. It accepts the same capture attributes as `clone!`, plus `#[watch]`:

```rust
// in a wrapper method (impl PlayerView), so `self` is the widget
player.connect_closure(
    "position-changed",
    false,
    glib::closure_local!(
        #[watch(rename_to = view)]
        self,
        move |_player: Player, position: f64| view.set_position(position)
    ),
);
```

`#[watch]` holds a weak reference to one object. When that object is finalized, the closure is invalidated and **the handler is disconnected automatically**. That's the cleanest way to connect a widget to a longer-lived model without storing a `SignalHandlerId` (amberol `window.rs`). Only one object can be watched per closure. Use `closure!` (not `_local`) only when the closure must be `Send`.

## 3. Reference cycles: when weak, when strong

GTK objects are reference-counted. A closure stored in a signal handler keeps everything it captured strongly alive for as long as the handler stays connected.

| Handler connected on… | Capture | Why |
| --- | --- | --- |
| your own object or its template children | `#[weak]` self | the closure lives inside you; a strong capture is a self-cycle and you never finalize |
| a child widget, referring to a sibling | `#[weak]` | the parent owns both |
| a **longer-lived** object (app, settings, shared model) | `#[weak]` **and** store + disconnect the handler | weak stops the leak; without disconnecting, the dead handler keeps firing (and returning early) forever, and handlers pile up |
| a short-lived object you own, with a one-shot callback | `#[strong]` is fine | the closure is dropped after it runs (`AlertDialog` response, a `spawn_future_local` that completes) |
| shared plain data (`Rc<Cell<_>>`) | `#[strong]` in at least one place | otherwise nothing owns it and every `#[weak]` upgrade fails (the book's counter example) |

Who owns whom: parents own children, a window owns its content, `GtkApplication` owns its windows, a `ListStore` owns its items. Anything pointing back up (child → parent, row → list, model → window) should be a `glib::WeakRef` field or a weak capture.

Strong captures are fine for one-shot work: Authenticator spawns `clone!(#[strong] app, async move { app.start_search_provider().await })` at startup. The future finishes and the reference is released.

## 4. Weak captures in async code

```rust
glib::spawn_future_local(clone!(
    #[weak(rename_to = obj)]
    self,
    async move {
        let data = load().await; // obj is held STRONGLY for the whole await
        obj.set_data(data);
    }
));
```

`clone!` on an `async move` block upgrades the weak reference **once, when the future first runs**, and then holds a strong reference until the future completes. For a short task this is what you want. For a long-lived loop it pins the object alive indefinitely. Keep a `WeakRef` and upgrade per iteration instead:

```rust
let weak = self.obj().downgrade();
glib::spawn_future_local(async move {
    while let Ok(event) = receiver.recv().await {
        let Some(obj) = weak.upgrade() else { break }; // window gone: stop
        obj.handle_event(event);
        // `obj` drops here, before the next await
    }
});
```

Also keep the `JoinHandle` (or a `gio::Cancellable`) for long tasks, so `dispose` can abort them. See `async-and-threads.md`.

## 5. Storing and disconnecting handlers

```rust
#[derive(Default)]
pub struct RoomPage {
    room: RefCell<Option<Room>>,
    room_handlers: RefCell<Vec<glib::SignalHandlerId>>,
    title_binding: RefCell<Option<glib::Binding>>,
}

impl RoomPage {
    fn set_room(&self, room: Option<Room>) {
        // 1. disconnect from the old object
        if let Some(old) = self.room.take() {
            for id in self.room_handlers.take() {
                old.disconnect(id);
            }
        }
        if let Some(binding) = self.title_binding.take() {
            binding.unbind();
        }
        // 2. connect to the new one
        if let Some(room) = &room {
            let id = room.connect_name_notify(clone!(
                #[weak(rename_to = imp)]
                self,
                move |room| imp.update_header(room)
            ));
            self.room_handlers.replace(vec![id]);
            let binding = room
                .bind_property("topic", &*self.subtitle, "label")
                .sync_create()
                .build();
            self.title_binding.replace(Some(binding));
        }
        // 3. store, then notify
        self.room.replace(room);
        self.obj().notify_room();
    }
}

// in ObjectImpl:
fn dispose(&self) {
    self.set_room(None);
}
```

- A `SignalHandlerId` is not `Copy`, and `disconnect` consumes it, so store IDs in `RefCell<Option<_>>` or `RefCell<Vec<_>>` and `take()` them.
- Disconnecting an ID twice, or on the wrong object, triggers a GLib critical. Always disconnect on the object you connected to, which is why the code takes the old object out first.
- A `glib::Binding` stays alive while both its source and target live. Call `unbind()` when retargeting. For list rows, bind in `bind` and unbind in `unbind` (see `lists-and-models.md`).
- An `ExpressionWatch` from `expression.bind(...)` needs `unwatch()` under the same rules (fractal `room_details/general_page.rs`).
- The book's To-Do app (todo/8) stores the `items-changed` handler ID of the current collection and disconnects it on switch. Otherwise each switch adds another handler.

## 6. "Current object" patterns

**`glib::SignalGroup`**: connect once, retarget many times.

```rust
// constructed()
let signals = glib::SignalGroup::new::<Player>();
signals.connect_closure(
    "track-changed",
    false,
    glib::closure_local!(
        #[weak(rename_to = imp)]
        self,
        move |player: Player| imp.update_track(&player)
    ),
);
signals.connect_notify_local(
    Some("volume"),
    clone!(
        #[weak(rename_to = imp)]
        self,
        move |_, _| imp.update_volume()
    ),
);
self.player_signals.set(signals).expect("constructed runs once");

// whenever the player changes:
self.player_signals.get().unwrap().set_target(Some(&player)); // or None::<&Player>
```

**`glib::BindingGroup`**: the same idea for property bindings.

```rust
let bindings = glib::BindingGroup::new();
bindings.bind("title", &*self.title_label, "label").sync_create().build();
bindings.bind("artist", &*self.artist_label, "label").sync_create().build();
self.track_bindings.set(bindings).unwrap();
// later:
self.track_bindings.get().unwrap().set_source(Some(&track));
```

Both disconnect from the previous target and reconnect to the new one automatically. Neither holds a strong reference to the target. They replace the hand-written `Vec<SignalHandlerId>` / `Vec<glib::Binding>` bookkeeping from section 5 (identity `picture.rs` uses `SignalGroup` for the surface). They need glib's `v2_74` feature, which gtk's `gnome_47`+ features enable transitively, so the template's `gnome_50` is enough. `BindingGroup::bind(...).build()` returns `()`: the group owns the bindings.

**fractal's `BoundObject<T>` / `BoundObjectWeakRef<T>`** (`fractal: src/utils/mod.rs`) bundle "the object + the handler IDs I connected on it". Setting a new object disconnects the old handlers, and `Drop` disconnects too. They implement `glib::property::Property`, so they can be `#[property]` storage. Write a small version when SignalGroup's string-based connections feel too loose:

```rust
pub struct BoundObject<T: ObjectType> {
    inner: RefCell<Option<(T, Vec<glib::SignalHandlerId>)>>,
}
impl<T: ObjectType> BoundObject<T> {
    pub fn set(&self, obj: T, handlers: Vec<glib::SignalHandlerId>) {
        self.disconnect();
        self.inner.replace(Some((obj, handlers)));
    }
    pub fn obj(&self) -> Option<T> {
        self.inner.borrow().as_ref().map(|(o, _)| o.clone())
    }
    pub fn disconnect(&self) {
        if let Some((obj, handlers)) = self.inner.take() {
            for id in handlers {
                obj.disconnect(id);
            }
        }
    }
}
```

## 7. `RefCell` borrow rules

A `borrow_mut()` while any other borrow is alive panics. GTK calls back into your code synchronously, so a borrow held across *any* call that can emit a signal is a latent panic. When the panic unwinds through a C callback, the process aborts.

```rust
// Wrong: set_label emits notify → a handler calls self.items.borrow_mut() → panic
let items = self.items.borrow();
self.label.set_label(&items[0].title());

// Right: clone out what you need, drop the borrow, then call out
let title = self.items.borrow().first().map(Item::title);
if let Some(title) = title {
    self.label.set_label(&title);
}
```

Calls that can re-enter include property setters (notify), `emit_by_name`, `ListStore::append`/`splice`/`remove` (`items-changed` handlers run synchronously), `set_visible_child`, `present()`, `close()`, and calling any user-provided callback.

- Never hold a borrow across `.await`. Other main-loop tasks run during the await and will hit the same cell. Clippy's `await_holding_refcell_ref` catches some of these cases.
- Prefer `Cell<T>` (`get`/`set`/`replace`) for `Copy` data, since it can't panic.
- To run a callback stored in a cell, clone it out first: `let cb = self.on_done.borrow().clone(); if let Some(cb) = cb { cb(); }`. Store such callbacks as `Rc<dyn Fn()>`.
- `take()` + `replace()` beats a long `borrow_mut()` when you need to mutate and then call out.

## 8. `dispose` vs `Drop`

- `dispose` runs when the object is being torn down, before its references are released. It can run more than once, so make it idempotent (`take()` everything). This is where you break cycles: disconnect handlers on other objects, unwatch expressions, cancel tasks, remove timers, and unparent children you parented manually.
- `Drop` on the imp struct runs at finalize, which only happens after the last strong reference is gone. If a cycle keeps the object alive, `Drop` never runs, so it can't be what breaks the cycle. `Drop` is fine for releasing plain Rust resources. fractal's `BoundObject` disconnecting in `Drop` works because it also gets explicit calls.
- **Template widgets.** A widget deriving directly from `gtk::Widget` must dispose its template children:

```rust
fn dispose(&self) {
    self.dispose_template(); // GTK ≥ 4.8; unparents template children
}
```

  Subclasses of containers such as `adw::Bin`, `gtk::Box`, windows and dialogs don't need it, because the parent class unparents its own children (calling it there is harmless; contrast does). A `gtk::Widget` subclass that calls `child.set_parent(&*obj)` by hand must call `child.unparent()` in `dispose` (fractal `ContextMenuBin`), and should set a layout manager in `class_init`.

## 9. Timers and `SourceId`

```rust
pub struct SearchBar {
    debounce: RefCell<Option<glib::SourceId>>,
}

fn queue_search(&self) {
    if let Some(id) = self.debounce.take() {
        id.remove(); // still pending: cancel it
    }
    let id = glib::timeout_add_local_once(Duration::from_millis(250), clone!(
        #[weak(rename_to = imp)]
        self,
        move || {
            imp.debounce.take(); // the source is finished: forget its ID first
            imp.run_search();
        }
    ));
    self.debounce.replace(Some(id));
}

fn dispose(&self) {
    if let Some(id) = self.debounce.take() {
        id.remove();
    }
}
```

`SourceId::remove()` panics ("Failed to remove source") if the source has already finished. A `_once` timer is finished once its callback runs, and a repeating `timeout_add_local` is finished after it returns `glib::ControlFlow::Break`. **Clear the stored ID inside the callback** (identity `window.rs` does this). Otherwise a later cancel or `dispose` removes a dead source and panics. For repeating timers, take the ID right before returning `Break`.

A repeating `timeout_add_local` callback returns `glib::ControlFlow`, which has no `Default`, so a `#[weak]` capture needs an explicit fallback. Use `#[upgrade_or] glib::ControlFlow::Break,` so the timer removes itself once the object is gone. `#[upgrade_or] Continue` would keep it firing into nothing.

## Pitfalls

- **Window or widget never finalized; memory grows on each open/close** → strong self-capture or a strong parent reference → `#[weak]` self, and `WeakRef` fields for back-references. Check with the GTK inspector's object statistics.
- **Handler fires N times after N switches** → re-connected without disconnecting → store the IDs and disconnect, or use `SignalGroup`/`BindingGroup`.
- **"already mutably borrowed: BorrowError", followed by an abort** → borrow held across a call that re-enters → clone out, drop the borrow, then call.
- **Panic "Failed to remove source"** → `remove()` on a finished timer → `take()` the ID inside the callback.
- **Repeating timer keeps running after the widget is gone** → `#[upgrade_or] ControlFlow::Continue` (or a manual `downgrade` that ignores failure) → use `#[upgrade_or] glib::ControlFlow::Break`.
- **`the trait Default is not implemented for ControlFlow/Propagation` inside `clone!`** → a `#[weak]` capture without a fallback → add `#[upgrade_or] …`.
- **Widget stays alive while an async loop waits** → `clone!(#[weak])` on the async block upgraded once → keep a `WeakRef` and upgrade per iteration.
- **GLib critical "instance has no handler with id"** → double disconnect, or disconnect on the wrong object → `take()` the IDs and disconnect on the object that was connected.
- **`#[weak]` Rc state never updates** → nothing holds it strongly → use `#[strong]` in at least one owner.
- **Template children leak or warn on finalize in a `gtk::Widget` subclass** → missing `dispose_template()`.

## See also

`gobject.md`, `async-and-threads.md`, `lists-and-models.md`, `architecture.md`, `testing-and-debugging.md`, `deprecated-apis.md`
