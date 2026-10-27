# GObject subclassing in Rust

Read this when you are defining a GObject type: a model object, a custom widget, a subclassable base class, or a type with properties, signals, enums, flags or boxed values.

Contents:

1. The two halves: `mod imp` + `glib::wrapper!`
2. Lifecycle: `Default`, `constructed`, `dispose`
3. Properties with `#[derive(glib::Properties)]`
4. Interior mutability: which cell for which field
5. Constructors and `Default`
6. Custom signals
7. Enums, flags and boxed types
8. Subclassable base classes and virtual methods
9. Interfaces
10. Pitfalls / See also

## 1. The two halves: `mod imp` + `glib::wrapper!`

```rust
use std::cell::{Cell, RefCell};

use gtk::{glib, prelude::*, subclass::prelude::*};

mod imp {
    use super::*;

    #[derive(Debug, Default, glib::Properties)]
    #[properties(wrapper_type = super::Task)]
    pub struct Task {
        #[property(get, set)]
        title: RefCell<String>,
        #[property(get, set)]
        done: Cell<bool>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for Task {
        const NAME: &'static str = "MyAppTask"; // GType name: unique per process
        type Type = super::Task;
        // type ParentType = glib::Object;      // the default when omitted
    }

    #[glib::derived_properties]
    impl ObjectImpl for Task {}
}

glib::wrapper! {
    pub struct Task(ObjectSubclass<imp::Task>);
}

impl Task {
    pub fn new(title: &str) -> Self {
        glib::Object::builder().property("title", title).build()
    }
}
```

The `imp` struct holds the state and implements the virtual-method traits (`ObjectImpl`, `WidgetImpl`, …). The wrapper is a cheap, reference-counted handle that the rest of the app passes around. Use `wrapper.imp()` to reach the state and `imp.obj()` to reach the wrapper. No `&mut self` ever exists, which is why every mutable field sits in a cell.

**`@extends` lists every ancestor class and `@implements` every interface** (excluding `GObject` itself). Look them up in the parent's `glib::wrapper!` in the crate source (`src/auto/<type>.rs`) or in the GTK docs' hierarchy section:

| Parent | `glib::wrapper!` tail |
| --- | --- |
| `glib::Object` | *(nothing)* |
| `gtk::Widget` | `@extends gtk::Widget, @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;` |
| `gtk::Box` | `@extends gtk::Box, gtk::Widget, @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Orientable;` |
| `adw::Bin` | `@extends adw::Bin, gtk::Widget, @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;` |
| `adw::NavigationPage` | `@extends adw::NavigationPage, gtk::Widget, @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;` (a direct `gtk::Widget` subclass, not an `adw::Bin`) |
| `adw::Dialog` | `@extends adw::Dialog, gtk::Widget, @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::ShortcutManager;` |
| `adw::PreferencesDialog` | as `adw::Dialog`, with `adw::PreferencesDialog` prepended |
| `adw::ActionRow` | `@extends adw::ActionRow, adw::PreferencesRow, gtk::ListBoxRow, gtk::Widget, @implements gtk::Accessible, gtk::Actionable, gtk::Buildable, gtk::ConstraintTarget;` |
| `adw::ApplicationWindow` | `@extends adw::ApplicationWindow, gtk::ApplicationWindow, gtk::Window, gtk::Widget, @implements gio::ActionGroup, gio::ActionMap, gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Native, gtk::Root, gtk::ShortcutManager;` |
| `adw::Application` | `@extends adw::Application, gtk::Application, gio::Application, @implements gio::ActionGroup, gio::ActionMap;` |

You also need one `impl XxxImpl for T {}` per class in the chain. For `adw::Bin` that means `ObjectImpl`, `WidgetImpl` and `BinImpl`; for `adw::NavigationPage`, `ObjectImpl`, `WidgetImpl` and `NavigationPageImpl`. Bring them into scope with `adw::subclass::prelude::*`, which also re-exports gtk's and glib's preludes.

**`NAME` conventions.** The name must be unique across the whole process, including GTK, libadwaita and any library you load. Prefix it with an app-specific tag: `"SwPlayer"` (Shortwave), `"PkDialogPage"` (pika-backup), `"MyAppTask"`. A duplicate name panics at type registration. Templates refer to the type by this exact string (`template $MyAppTask : …` in Blueprint).

## 2. Lifecycle: `Default`, `constructed`, `dispose`

`glib::Object::builder()` runs in this order:

1. `imp::T::default()` (or `ObjectSubclass::new()` if you override it)
2. `instance_init` (templates call `init_template()` here)
3. construct and construct-only properties are set
4. `constructed()`

Later, the object is torn down with `dispose()` (possibly more than once), then `finalize`, which drops the Rust struct.

```rust
impl ObjectImpl for TaskRow {
    fn constructed(&self) {
        self.parent_constructed(); // chain up first
        let obj = self.obj();
        obj.add_css_class("task-row");
        // Construct-only properties are readable here, but not in Default.
        self.setup_bindings();
    }

    fn dispose(&self) {
        // Disconnect handlers on objects you don't own; unparent manually parented children.
        if let Some(id) = self.model_handler.take() {
            self.obj().model().disconnect(id);
        }
    }
}
```

- Put setup that needs property values in `constructed`. `Default` runs before any property is set.
- Use `dispose` to break cycles and disconnect from other objects; `Drop` is too late for anything that keeps *you* alive. Details are in `closures-and-lifetimes.md`.
- `dispose` is the one override you do **not** chain up: glib calls the parent class's `dispose` right after yours, and there is no `parent_dispose()` (writing one is E0599). Every other override (`constructed`, `startup`, `close_request`, ...) chains up explicitly.
- A widget deriving directly from `gtk::Widget` with a template calls `self.dispose_template()` in `dispose`. One that parents children by hand calls `child.unparent()` there.

## 3. Properties with `#[derive(glib::Properties)]`

```rust
#[derive(Debug, Default, glib::Properties)]
#[properties(wrapper_type = super::Session)]
pub struct Session {
    /// Shown in the sidebar.
    #[property(get, set)]
    name: RefCell<String>,

    #[property(get, set = Self::set_unread, explicit_notify, minimum = 0)]
    unread: Cell<u32>,

    #[property(get, construct_only)]
    account: OnceCell<Account>,

    #[property(get, set, nullable)]
    avatar: RefCell<Option<gdk::Texture>>,

    #[property(get, set, nullable)]
    parent: glib::WeakRef<Folder>,

    #[property(get, set, builder(SyncState::default()))]
    state: Cell<SyncState>,

    #[property(get = Self::is_busy, type = bool)]
    is_busy: PhantomData<bool>,

    #[property(name = "display-name", get, type = String, member = display_name)]
    #[property(name = "email", get, type = String, member = email)]
    profile: RefCell<Profile>, // plain struct with display_name/email fields
}

impl Session {
    fn set_unread(&self, unread: u32) {
        if self.unread.replace(unread) == unread {
            return; // no change, no notify
        }
        self.obj().notify_unread();
        self.obj().notify_is_busy();
    }

    fn is_busy(&self) -> bool {
        self.state.get() == SyncState::Syncing
    }
}
```

Generated on the wrapper: `name()`, `set_name()`, `connect_name_notify()` and `notify_name()` (no setter for `construct_only`). Doc comments on a field are copied to the generated accessors. Pair the derive with `#[glib::derived_properties]` on `impl ObjectImpl`, which fills in `properties()`, `set_property()` and `property()`.

Options (verified in glib-macros 0.22.9):

| Option | Meaning |
| --- | --- |
| `get` / `get = path` | readable; custom getter `fn(&self) -> T` on the imp, or a closure |
| `set` / `set = path` | writable; custom setter `fn(&self, T)` |
| `construct` | always set during construction (default value if not given) |
| `construct_only` | settable only at construction; no public setter is generated |
| `nullable` | setter takes `Option<T>`; required for `Option<_>` storage and nullable objects |
| `explicit_notify` | `notify` fires only when you call `notify_<name>()`; use it with a custom setter that skips no-op writes |
| `default` / `default = expr` | default value (`default` uses `Default::default()`, useful for enums) |
| `minimum = expr`, `maximum = expr` | numeric bounds, enforced by GObject |
| `builder(args).method(...)` | required ParamSpec builder args (enum default) plus extra builder calls |
| `name = "kebab-name"` | override the property name (default: field name with `_` → `-`) |
| `type = T` | property type when it differs from the storage (`PhantomData`, `member`, conversions) |
| `member = field` | expose one field of the struct inside a `RefCell` as its own property |
| `override_class = T` / `override_interface = I` | implement a property inherited from a class or interface (e.g. `gtk::Actionable`'s `action-name`) |
| `lax_validation` | skip strict ParamSpec validation for this property |

Struct-level: `#[properties(wrapper_type = super::T)]`. Add `ext_trait` (or `ext_trait = MyTPropertiesExt`) to generate the accessors as a trait, so that **subclasses** inherit them (pika-backup `DialogPage`). The trait is defined in the module containing the derive; re-export it.

Why `explicit_notify` + equality check: plain `set` notifies on every write, even with an identical value, and that wakes up every binding and expression. The `if old == new { return }` setter is fractal's canonical form.

Gotchas:

- Mutate through the setter. Writing `self.title.replace(...)` skips `notify`, so bindings go stale. If you must write the cell directly, call `self.obj().notify_title()` afterwards.
- Enum properties need a default: `builder(MyEnum::Variant)` or `default` (when the enum implements `Default`).
- For a `OnceCell` with `construct_only`, the generated getter panics if construction didn't set the cell. Always pass the property in `new()`.
- Don't hand-write a method with the same name as a generated accessor; it conflicts. Use a custom getter or setter, or `ext_trait`.
- A property is public API with a notify cost. Purely internal state can be a plain field with a plain method (book advice).

## 4. Interior mutability: which cell for which field

| Field holds | Storage | Notes |
| --- | --- | --- |
| `Copy` scalar or enum | `Cell<T>` | `get`/`set`/`replace`; can never panic |
| `String`, `Vec`, non-`Copy` struct | `RefCell<T>` | keep borrows short (see `closures-and-lifetimes.md`) |
| Optional owned object | `RefCell<Option<T>>` | `take()` in `dispose` or when rebinding |
| Set once (construct-only dependency, lazy service) | `std::cell::OnceCell<T>` | `get_or_init` for lazy; panics on a second `set` |
| Back-reference to parent or peer | `glib::WeakRef<T>` | works directly as `#[property]` storage (`Option<T>` value) |
| Computed value | `PhantomData<T>` + `get = Self::fn` | no storage |
| Object + its signal handlers | fractal's `BoundObject<T>` / `BoundObjectWeakRef<T>` | see `closures-and-lifetimes.md` |
| Template child | `TemplateChild<T>` | see `templates-and-blueprint.md` |

Use `std::cell::OnceCell`, `std::sync::{LazyLock, OnceLock}`. The `once_cell` and `lazy_static` crates are legacy (amberol).

## 5. Constructors and `Default`

```rust
impl TaskRow {
    pub fn new(task: &Task) -> Self {
        glib::Object::builder().property("task", task).build()
    }
}

// Only for types with a zero-argument constructor:
impl Default for Spinner {
    fn default() -> Self {
        glib::Object::new()
    }
}
```

- Prefer construct properties over "new() then call setters", so that `constructed` sees a complete object. Property names are kebab-case strings. A misspelled name or a wrong value type panics at runtime, so keep the `new()` next to the struct.
- If the imp struct needs a non-`Default` field (for example `gio::Settings::new(APP_ID)`), write `impl Default for imp::T` by hand (fractal), or use a `OnceCell` with `get_or_init` (the template's window).
- `impl Default for Application` conventionally means "the running app" (see `architecture.md`), not a new instance.

## 6. Custom signals

```rust
use std::sync::LazyLock;
use glib::subclass::Signal;

#[glib::derived_properties]
impl ObjectImpl for TaskList {
    fn signals() -> &'static [Signal] {
        static SIGNALS: LazyLock<Vec<Signal>> = LazyLock::new(|| {
            vec![
                Signal::builder("task-activated")
                    .param_types([Task::static_type()])
                    .build(),
                Signal::builder("close-requested")
                    .return_type::<bool>() // handlers return "handled"
                    .run_last()
                    .build(),
            ]
        });
        SIGNALS.as_ref()
    }
}

impl TaskList {
    fn emit_task_activated(&self, task: &Task) {
        self.emit_by_name::<()>("task-activated", &[task]);
    }

    pub fn connect_task_activated<F: Fn(&Self, &Task) + 'static>(
        &self,
        f: F,
    ) -> glib::SignalHandlerId {
        self.connect_closure(
            "task-activated",
            false,
            glib::closure_local!(|obj: Self, task: Task| f(&obj, &task)),
        )
    }
}
```

- The typed `connect_*` wrapper keeps the signal name and argument types in one place. Call sites look like built-in signals and get a compile error instead of a runtime panic. fractal wraps every custom signal this way. Raw `connect_closure("…")` or `connect_local` at call sites (amberol) is untyped.
- `emit_by_name::<R>` takes the return type as a turbofish. A mismatch with the declared `return_type` panics.
- Builder extras: `.action()` (activatable through `klass.add_binding_signal`), `.detailed()`, `.run_first()`, `.class_handler(...)`, `.accumulator(...)`.
- **Prefer a property over a "changed" signal** when the thing is state. `notify` comes free and is bindable.
- glib-macros 0.22.9 has no `#[glib::signals]` attribute macro. Write `fn signals()` by hand as above. `OnceLock` + `get_or_init` (the book) is equivalent.

## 7. Enums, flags and boxed types

```rust
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, glib::Enum)]
#[enum_type(name = "MyAppSyncState")]
pub enum SyncState {
    #[default]
    Idle,
    Syncing,
    #[enum_value(name = "Failed", nick = "failed")]
    Error,
}

#[glib::flags(name = "MyAppFilterFlags")]
pub enum FilterFlags {
    UNREAD = 0b01,
    STARRED = 0b10,
    #[flags_value(skip)]
    ALL = Self::UNREAD.bits() | Self::STARRED.bits(),
}

impl Default for FilterFlags {
    fn default() -> Self {
        Self::empty()
    }
}

#[derive(Debug, Clone, PartialEq, glib::Boxed)]
#[boxed_type(name = "MyAppStation", nullable)]
pub struct StationInfo {
    pub name: String,
    pub url: String,
}
```

- **`glib::Enum`**: registers a GType so the enum can be a property, appear in Blueprint (`state: syncing;` uses the nick), and be bound. Nicks default to kebab-case. Give it a `#[default]`.
- **`#[glib::flags]`** generates a `bitflags` type (no extra dependency) with a GType. Combined values need `#[flags_value(skip)]`.
- **`glib::Boxed`**: carries plain Rust data (`Clone`) through properties, signals and `glib::Value`. Add `nullable` to use it as an `Option<T>` property. Use `glib::SharedBoxed` with an `Arc<Inner>` newtype for large data you don't want to deep-clone. Shortwave's `SwStationMetadata` is serde + Boxed.
- For a list item that is just data, a small GObject with properties (section 1) usually beats Boxed, because list views need GObjects anyway.
- `glib::ErrorDomain` derive turns an enum into a `glib::Error` domain. This is rarely needed in apps; use `thiserror`.
- `#[derive(glib::Variant)]` makes a struct storable in GVariant (actions, settings). See `settings-and-state.md`.

## 8. Subclassable base classes and virtual methods

**Allowing subclasses without vfuncs** (pika-backup `DialogPage`):

```rust
pub trait PageImpl: adw::subclass::prelude::NavigationPageImpl {}
unsafe impl<T: PageImpl> IsSubclassable<T> for Page {}
```

Subclasses then use `type ParentType = Page;`, implement `PageImpl` (plus every ancestor `Impl`), and list `Page` first in `@extends`. Generate property accessors with `ext_trait` so subclasses see them.

**With a virtual method.** This follows fractal's `ContextMenuBin`, `SessionInfo` and `PillSource`:

```rust
mod imp {
    #[repr(C)]
    pub struct ItemClass {
        parent_class: glib::object::ObjectClass, // widgets: glib::object::Class<gtk::Widget>
        pub(super) summary: fn(&super::Item) -> String,
    }

    unsafe impl ClassStruct for ItemClass {
        type Type = Item;
    }

    #[derive(Debug, Default)]
    pub struct Item;

    #[glib::object_subclass]
    impl ObjectSubclass for Item {
        const NAME: &'static str = "MyAppItem";
        const ABSTRACT: bool = true; // can't be instantiated directly
        type Type = super::Item;
        type Class = ItemClass;
    }

    impl ObjectImpl for Item {}
}

glib::wrapper! {
    pub struct Item(ObjectSubclass<imp::Item>);
}

/// Public API, callable on Item and every subclass.
pub trait ItemExt: IsA<Item> {
    fn summary(&self) -> String {
        let this = self.upcast_ref::<Item>();
        (this.class().as_ref().summary)(this)
    }
}
impl<O: IsA<Item>> ItemExt for O {}

/// What subclasses implement.
pub trait ItemImpl: ObjectImpl {
    fn summary(&self) -> String {
        String::new() // default for an optional vfunc
    }
}

unsafe impl<T> IsSubclassable<T> for Item
where
    T: ItemImpl,
    T::Type: IsA<Item>,
{
    fn class_init(class: &mut glib::Class<Self>) {
        Self::parent_class_init::<T>(class.upcast_ref_mut());
        class.as_mut().summary = |this| {
            let this = this.downcast_ref::<T::Type>().expect("subclass of Item");
            this.imp().summary()
        };
    }
}
```

Recipe: a `#[repr(C)]` class struct whose first field is the parent class, `unsafe impl ClassStruct`, `type Class = …`, an `XxxExt` trait blanket-implemented for `IsA<Xxx>`, an `XxxImpl` trait, and `IsSubclassable` wiring each vfunc to a trampoline. Re-export `ItemExt` and `ItemImpl` from a crate `prelude.rs` (fractal `src/prelude.rs`). The only `unsafe` lines are the two trait impls, which is normal for this pattern. The template's `unsafe_code = "deny"` lint rejects them, so put `#[allow(unsafe_code)]` on the module that holds them (the same applies to the `IsSubclassable` line above).

Before writing a vfunc, ask whether a property, a signal, or a closure passed at construction would do. Most apps never need a custom class struct.

## 9. Interfaces

None of the corpus apps define a custom interface. Implementing an existing one, such as `gio::ListModel`, `gtk::Buildable` or `gtk::Orientable`, is common:

```rust
#[glib::object_subclass]
impl ObjectSubclass for TaskStore {
    const NAME: &'static str = "MyAppTaskStore";
    type Type = super::TaskStore;
    type Interfaces = (gio::ListModel,);
}

impl ListModelImpl for TaskStore {
    fn item_type(&self) -> glib::Type { Task::static_type() }
    fn n_items(&self) -> u32 { u32::try_from(self.items.borrow().len()).unwrap_or(u32::MAX) }
    fn item(&self, position: u32) -> Option<glib::Object> {
        self.items.borrow().get(position as usize).map(|t| t.clone().upcast())
    }
}
// wrapper: @implements gio::ListModel;
```

Defining your own interface uses `#[glib::object_interface] impl ObjectInterface for Foo { const NAME: …; type Interface = FooInterface; }` with a `#[repr(C)]` interface struct starting with `gobject_ffi::GTypeInterface`, plus `glib::wrapper! { pub struct Foo(ObjectInterface<imp::Foo>); }`. Copy the working example from glib's own tests (`glib-0.22.*/src/subclass/object.rs`, `Dummy`) rather than writing it from memory. In Rust apps, a trait object or an abstract base class (section 8) is usually simpler.

## Pitfalls

- **Panic "Type X has already been registered"** → two types share a `NAME` (often a copy-pasted file) → give each a unique, prefixed name.
- **`the trait bound … IsA<gtk::Widget>` errors on wrapper methods** → incomplete `@extends` or `@implements` → copy the parent's list from its `glib::wrapper!`.
- **"missing trait implementation `BinImpl`" compile error** → every class in the chain needs its `Impl` → add the empty impls.
- **Panic "property 'foo' of type 'X' not found"** → kebab/snake mismatch, or the property is on the imp but `#[glib::derived_properties]` is missing → use `"foo-bar"` strings and add the attribute.
- **Binding never updates** → field written directly, or `explicit_notify` without calling `notify_<name>()` → go through the setter, or notify by hand.
- **Panic "can't set value of OnceCell multiple times"** → `set` on a set-once cell, for example `startup` running twice in tests → `get_or_init`, or a `RefCell<Option<_>>`.
- **Enum property won't compile ("expected 2 arguments")** → no default given → add `builder(MyEnum::X)` or `default`.
- **Getter panics on `unwrap` inside generated code** → `OnceCell` construct-only property never passed to the builder → pass it in `new()`.

## See also

`closures-and-lifetimes.md`, `architecture.md`, `templates-and-blueprint.md`, `lists-and-models.md`, `settings-and-state.md`, `compile-traps.md`, `deprecated-apis.md`
