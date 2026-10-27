# Compile traps, keyed by error text

Read this when gtk-rs code fails to compile with an error you do not immediately understand. Search this
file for the error code or message first, before you rewrite the call. Each entry gives the symptom, the
real cause and the form that compiles.

Several entries are adapted from the `developing-gtk-apps` skill in
[alex-oleshkevich/glimpse](https://github.com/alex-oleshkevich/glimpse) (BSD-3-Clause; see
`THIRD_PARTY_NOTICES.md` at the plugin root). They have been checked against gtk4 0.11.5, libadwaita 0.9.2
and glib 0.22.10.

## Contents

- [Verify before you assert](#verify-before-you-assert)
- [The rule behind half of these errors](#the-rule-behind-half-of-these-errors)
- [Errors](#errors)
- [Feature-gate traps](#feature-gate-traps)
- [One-liners](#one-liners)

## Verify before you assert

Memory and older examples are unreliable for gtk-rs: `clone!` syntax, channels, builders and dialog APIs
changed between 0.x releases. Before writing a call you are unsure of:

1. **Grep the installed crate source**. Generated bindings live under `src/auto/`, hand-written ones under `src/`.
   Methods of `*Ext` traits have no `pub`, so search for `fn name`:

   ```sh
   REG=~/.cargo/registry/src/*/
   grep -rn "fn bind_property" $REG/glib-0.22.*/src/object.rs
   grep -rn "fn connect_selected_item_notify" $REG/gtk4-0.11.*/src/
   grep -n -B2 "mod toggle_group;" $REG/libadwaita-0.9.*/src/auto/mod.rs   # version feature gate (v1_7)
   ```

   Proc-macro syntax (`clone!`, `closure_local!`, `Properties`, `CompositeTemplate`, `template_callbacks`) is
   documented in the doc comments of `glib-macros-*/src/lib.rs` and `gtk4-macros-*/src/lib.rs`. If the crates are
   not downloaded yet, run `cargo fetch`.
2. **Confirm with `cargo check`**. A grep finds a symbol; only the type checker confirms the call shape.
   Use the project itself, or keep a scratch crate with the same dependencies and a warm `target/`.
3. docs.rs pinned to the exact crate version (`docs.rs/gtk4/0.11.5`) renders the same content; the
   "latest" docs can describe an unreleased API.

## The rule behind half of these errors

An E0599/E0277/E0034 that names a trait unrelated to your widget (`IsA<TypeModule>`, `IsA<gtk4::Dialog>`,
`TypeModuleExt`) usually means method resolution picked the wrong `*Ext` trait because the method you meant
does not exist on that type. The real cause is one of:

- the property is **construct-only**, so there is no setter: pass it at construction;
- the method lives behind a **prelude you have not imported** (`adw::prelude::*`, `gio::prelude::*`, a sibling crate's prelude);
- the name is a **signal**, not a method: connect or emit it;
- the type needs a **crate version feature** you have not enabled.

## Errors

### `E0433: cannot find module or crate 'glib' in this scope` inside `clone!` / `closure_local!` / `#[glib::object_subclass]`

Older rustc words it `failed to resolve: use of undeclared crate or module 'glib'`.

The macros expand to `glib::...` paths, so the `glib` *module* must be in scope in the file that uses them.
Importing only the macro is not enough in a crate without a direct `glib` dependency:

```rust
use gtk::glib; // or: use gtk::{gio, glib};

button.connect_clicked(glib::clone!(
    #[weak]
    label,
    move |_| label.set_label("clicked")
));
```

### `E0599: no method named 'set_title' found ...` (or any obvious method) on a widget

Missing prelude. Import the preludes of every crate whose types you use:

```rust
use adw::prelude::*;            // re-exports gtk::prelude, gio::prelude and glib::prelude
use adw::subclass::prelude::*;  // inside subclass code: ObjectImpl, WidgetImpl, ..., ObjectSubclassIsExt (imp())
```

Sibling crates (`soup`, `gst`, `sourceview5`, `shumate`, `vte4`) each have their own `prelude`.
`self.imp()` on a wrapper and `self.obj()` inside `imp` come from the subclass prelude.

### `E0034: multiple applicable items in scope` (`activate_action`, `set_state`, ...)

Windows implement `Widget`, `gio::ActionGroup` and `gio::ActionMap`, and their `*Ext` traits define methods with the
same name (`WidgetExt::activate_action` vs `ActionGroupExt::activate_action`). Qualify the trait:

```rust
// Detailed "win."/"app." name, resolved up the widget tree:
WidgetExt::activate_action(&window, "win.save", None).ok();
// Unprefixed name, directly on the action group:
ActionGroupExt::activate_action(&window, "save", None);
```

### `E0599: no associated function or constant named 'builder' found for struct 'ToastOverlay'`

Older rustc words it `no function or associated item named 'builder' found`.

gir only generates a `Builder` for types with writable construct properties. `adw::ToastOverlay`,
`adw::ShortcutsSection` and `adw::ShortcutsItem` have none; use the constructors and setters:

```rust
let overlay = adw::ToastOverlay::new();
overlay.set_child(Some(&content));
let section = adw::ShortcutsSection::new(Some("General"));
section.add(adw::ShortcutsItem::new("Quit", "<Control>q")); // add() takes the item by value
```

### `E0599: the method 'response' exists for reference '&AlertDialog', but its trait bounds were not satisfied`

The notes list `AlertDialog: IsA<gtk4::Dialog>` and `IsA<MessageDialog>` as unsatisfied.

`adw::AlertDialog` is not a `gtk::Dialog`; `response` is a signal. Await the choice instead of wiring the signal:

```rust
let response = dialog.choose_future(Some(&window)).await;
if response == "delete" { /* ... */ }
```

To drive it from a test: `dialog.emit_by_name::<()>("response", &[&"delete"]);`.

### `E0599: the method 'set_name' exists for reference '&TextTag', but its trait bounds were not satisfied`

The notes list `TextTag: IsA<TypeModule>` among the unsatisfied bounds.

`name` is construct-only on `gtk::TextTag`, so `set_name` resolved to `TypeModuleExt::set_name`. Set it at
construction: `gtk::TextTag::new(Some("bold"))` or `gtk::TextTag::builder().name("bold").build()`.

### `E0277: the trait bound 'MyWidget: IsA<gtk::Widget>' is not satisfied` (or `ObjectType` errors) after subclassing

The `glib::wrapper!` declaration is missing ancestors. List **every** ancestor class in `@extends` and the
interfaces in `@implements`, nearest first:

```rust
glib::wrapper! {
    pub struct Window(ObjectSubclass<imp::Window>)
        @extends adw::ApplicationWindow, gtk::ApplicationWindow, gtk::Window, gtk::Widget,
        @implements gio::ActionGroup, gio::ActionMap, gtk::Accessible, gtk::Buildable,
                    gtk::ConstraintTarget, gtk::Native, gtk::Root, gtk::ShortcutManager;
}
```

### `E0277: the trait bound 'imp::X: WidgetImpl' is not satisfied` (or `WindowImpl`, `BoxImpl`, ...)

Every class in the parent chain needs an `impl` block in `imp`, even if empty:
`impl WidgetImpl for X {}`, `impl WindowImpl for X {}`, `impl ApplicationWindowImpl for X {}`,
`impl AdwApplicationWindowImpl for X {}`.

### `E0308: mismatched types` around signal handlers returning `bool` / `Inhibit` / `Continue`

Handlers that stop propagation return `glib::Propagation` (`close_request`, key/scroll controllers):
`window.connect_close_request(|_| glib::Propagation::Proceed);`. Sources return `glib::ControlFlow`.
`Inhibit` and `glib::Continue` no longer exist.

### `E0308: mismatched types` — signature shapes to check first

- `adw::ToggleGroup::add` and `adw::ShortcutsSection::add` take the child **by value**.
- Nullable setters take `Option<&T>`: `label.set_tooltip_text(Some("hint"))`, `window.set_child(Some(&child))`,
  `dialog.present(Some(&window))`. Use `None::<&gtk::Widget>` when inference fails.
- `gio::Cancellable::NONE`, `gio::File::NONE`, `gtk::Widget::NONE` are typed `None` constants for generic `Option<&impl IsA<T>>` params.
- `glib::compute_checksum_for_bytes(glib::ChecksumType::Sha256, &bytes)` takes `&glib::Bytes`; wrap slices with `glib::Bytes::from(...)`.

### `E0382: borrow of moved value` / `E0505` when a closure captures a GObject

You moved the object into the first closure. GObjects are reference-counted; clone (or weak-capture) per closure:

```rust
button.connect_clicked(glib::clone!(
    #[weak] window,
    move |_| window.close()
));
```

### `E0373: closure may outlive the current function` / `'static` bound errors in `connect_*`

Signal closures must be `'static`. Do not capture `&self` or borrowed locals; capture owned clones or weak refs
via `clone!`. Inside `imp`, use `#[weak(rename_to = imp)] self` and call methods through `imp`.

### `E0277: '*mut ...' cannot be sent between threads safely` / `Rc<...> cannot be sent`

You moved a GTK object into `std::thread::spawn`, `gio::spawn_blocking` or `tokio::spawn`. Widgets and most
GObjects are `!Send`. Send plain data to the worker and the result back through an `async_channel`, or await
the `JoinHandle` from `glib::spawn_future_local`. See `async-and-threads.md`.

### `already mutably borrowed: BorrowError` / `already borrowed: BorrowMutError` (runtime panic)

Not a compile error, but the most common gtk-rs panic. A `RefCell` borrow was held while calling something that
re-entered the same object (emitting a signal, `items_changed`, `notify`, a callback, or an `.await`). Clone or take
the value out, drop the borrow, then call. See `closures-and-lifetimes.md`.

### `Couldn't find template child` / `Invalid object type 'MyWidget'` (runtime)

The `#[template_child]` field name (or `id = ...`) does not match an `id` in the Blueprint/UI, or a custom type used
as `$MyWidget` was not registered before `init_template()`. Fields of type `TemplateChild<MyWidget>` register their
type automatically; types referenced only in the template need `MyWidget::ensure_type()` in `class_init`.
See `templates-and-blueprint.md`.

## Feature-gate traps

- **A widget or method "does not exist" but is in the docs** → enable the version feature:
  `gtk` `v4_xx` / `gnome_xx`, `adw` `v1_x`. Examples: `adw::ToggleGroup`/`WrapBox` need `v1_7`, `adw::ShortcutsDialog`
  needs `v1_8`, `gtk::Widget::set_limit_events` needs `v4_18`.
- **adw widgets do not implement `gtk::Accessible`** (no `update_property`, failed upcast) → enable adw's `gtk_v4_10`
  (or the higher `gtk_v4_*` that matches your runtime). In libadwaita 0.9 most widget wrappers only list
  `gtk::Accessible` in `@implements` with that feature.
- **Compiles but fails at startup with undefined symbols** → you enabled a feature newer than the installed library
  (or the Flatpak runtime). Keep crate features ≤ the oldest runtime you ship on, and keep Meson's `dependency()` version
  checks in sync.
- **Deprecation warnings only appear after raising a feature** → deprecations are `cfg_attr(feature = "v4_12", deprecated)`.
  That is expected: fix them, they are telling you the replacement exists on your target.

## One-liners

- `gdk::ContentFormats::contain_mime_type` — singular `contain`.
- Unix signal handlers moved out of `glib` in the 0.22 series: add the `glib-unix` crate and use
  `glib_unix::unix_signal_add_local(signum, || glib::ControlFlow::Continue)`.
- `WidgetExt::allocation()` is deprecated since GTK 4.12: `widget.compute_bounds(&parent)` returns `Option<graphene::Rect>`.
- `gtk::Widget::activate_action` returns `Result<(), glib::BoolError>`; `ActionGroupExt::activate_action` returns `()`.
- `gio::Settings::set_*` returns `Result<(), glib::BoolError>` (the key may be non-writable); do not silently drop it in
  code paths where it matters.
- `gtk::Builder::object::<T>("id")` returns `Option<T>`; prefer templates over `Builder` lookups in new code.
- With gettext-rs 0.8, `setlocale` is `unsafe`; with 0.7 it was safe.
- `std::env::set_var` is `unsafe` in edition 2024 (some apps set `GSK_RENDERER` before GTK init this way).
