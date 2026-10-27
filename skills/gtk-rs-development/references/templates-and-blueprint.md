# Composite templates and Blueprint

Read this when you write or debug a widget whose UI lives in a `.blp` (or `.ui`) file: Blueprint syntax, the
`#[derive(CompositeTemplate)]` side, bindings, template callbacks, and the runtime errors a template produces.

## Contents

1. [A template widget end to end](#1-a-template-widget-end-to-end)
2. [Blueprint essentials](#2-blueprint-essentials)
3. [Property bindings and expressions](#3-property-bindings-and-expressions)
4. [Signal handlers and template callbacks](#4-signal-handlers-and-template-callbacks)
5. [The Rust side in detail](#5-the-rust-side-in-detail)
6. [Alternatives to resource-loaded templates](#6-alternatives-to-resource-loaded-templates)
7. [Blueprint to XML cheat sheet](#7-blueprint-to-xml-cheat-sheet)
8. [Pitfalls](#pitfalls)

Examples use an app with id `org.example.Notes`, resource base path `/org/example/Notes` and type prefix `Notes`,
laid out like the scaffold: `.blp` files in `data/resources/ui/`, compiled by Meson's `blueprint-compiler
batch-compile`, bundled under the alias `ui/<name>.ui`.

## 1. A template widget end to end

```blp
// data/resources/ui/note-row.blp
using Gtk 4.0;
using Adw 1;

template $NotesNoteRow: Adw.ActionRow {
  title: bind template.note as <$NotesNote>.title;
  activatable: true;

  [suffix]
  Gtk.Button delete_button {
    icon-name: "user-trash-symbolic";
    tooltip-text: _("Delete Note");
    valign: center;
    clicked => $on_delete_clicked() swapped;

    styles [
      "flat",
    ]
  }
}
```

```rust
// src/note_row.rs
use adw::{prelude::*, subclass::prelude::*};
use gtk::glib;
use std::cell::RefCell;

use crate::note::Note;

mod imp {
    use super::*;

    #[derive(Debug, Default, gtk::CompositeTemplate, glib::Properties)]
    #[template(resource = "/org/example/Notes/ui/note-row.ui")]
    #[properties(wrapper_type = super::NoteRow)]
    pub struct NoteRow {
        #[template_child]
        pub delete_button: TemplateChild<gtk::Button>,
        #[property(get, set, nullable)]
        note: RefCell<Option<Note>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for NoteRow {
        const NAME: &'static str = "NotesNoteRow"; // == `template $NotesNoteRow`
        type Type = super::NoteRow;
        type ParentType = adw::ActionRow;            // == `: Adw.ActionRow`

        fn class_init(klass: &mut Self::Class) {
            klass.bind_template();
            klass.bind_template_callbacks();
        }

        fn instance_init(obj: &glib::subclass::InitializingObject<Self>) {
            obj.init_template();
        }
    }

    #[gtk::template_callbacks]
    impl NoteRow {
        #[template_callback]
        fn on_delete_clicked(&self) {
            let _ = self.obj().activate_action("note.delete", None);
        }
    }

    #[glib::derived_properties]
    impl ObjectImpl for NoteRow {}
    impl WidgetImpl for NoteRow {}
    impl ListBoxRowImpl for NoteRow {}
    impl PreferencesRowImpl for NoteRow {}
    impl ActionRowImpl for NoteRow {}
}

glib::wrapper! {
    pub struct NoteRow(ObjectSubclass<imp::NoteRow>)
        @extends gtk::Widget, gtk::ListBoxRow, adw::PreferencesRow, adw::ActionRow,
        @implements gtk::Accessible, gtk::Actionable, gtk::Buildable, gtk::ConstraintTarget;
}
```

Wiring it into the build (both lists live in the scaffold):

- add `'ui/note-row.blp'` to `blueprint_files` in `data/resources/meson.build`;
- add `<file compressed="true" preprocess="xml-stripblanks" alias="ui/note-row.ui">note-row.ui</file>` to
  `data/resources/resources.gresource.xml.in`;
- add the `.blp` to `po/POTFILES.in` if it has translatable strings.

Why this shape: the `.blp` describes structure and declarative state (bindings, styles, accessibility), the Rust
side holds behaviour. Bindings in the template replace most hand-written `connect_notify` glue.

Some apps keep `foo.blp` next to `foo.rs` instead (fractal, video-trimmer). That works well, but it needs a
Meson setup that handles nested directories (fractal: `build-aux/compile-blueprints.sh`). The scaffold's
`batch-compile` rule and its `fs.name()` output list assume a flat `data/resources/ui/` directory.

## 2. Blueprint essentials

```blp
using Gtk 4.0;
using Adw 1;

template $NotesWindow: Adw.ApplicationWindow {
  title: _("Notes");                   // translatable; property names use dashes
  width-request: 360;

  content: Adw.ToolbarView {          // object-valued property
    [top]                              // child slot ("child type")
    Adw.HeaderBar {
      [end]
      Gtk.MenuButton {
        primary: true;
        icon-name: "open-menu-symbolic";
        tooltip-text: _("Main Menu");
        menu-model: primary_menu;      // refers to the `menu` below by id
      }
    }

    content: $NotesNoteList note_list {}; // custom Rust type, with an id
  };
}

menu primary_menu {
  section {
    item {
      // Translators: Primary menu entry that opens the Keyboard Shortcuts dialog.
      label: _("_Keyboard Shortcuts");
      action: "app.shortcuts";
    }
  }
}
```

- **`template $Name: Parent`**: `$Name` must equal `ObjectSubclass::NAME`, and `Parent` must be the C type of
  `ParentType` (`Adw.ApplicationWindow` for `adw::ApplicationWindow`). The `$` prefix marks a type that is not
  in a GIR namespace, which covers every Rust-defined type, both for the template and for children
  (`$NotesNoteList note_list {}`).
- **Ids** (`note_list`) become `#[template_child]` field names. Objects without an id can't be reached from Rust,
  which is fine for pure layout.
- **Child slots**: `[top]`, `[bottom]`, `[start]`, `[end]`, `[title]`, `[overlay]`, `[prefix]`, `[suffix]`
  depend on the parent's `GtkBuildable` support. Containers with a single child use a property instead
  (`content:`, `child:`).
- **Namespaces**: `Gtk` is the default namespace, so `Gtk.` can be omitted. Writing it explicitly (fractal style)
  keeps files greppable and symmetrical with `Adw.`.
- **Styles**: `styles [ "boxed-list", "flat" ]` adds CSS classes. Use `dimmed`, not the old `dim-label`.
- **Translations**: `_("...")` marks a string, and `C_("context", "...")` adds a msgctxt (the scaffold's
  `shortcuts-dialog.blp` uses `C_("shortcut window", ...)`). Put translator comments on the line above.
  They are translated when the template is instantiated, using the text domain bound in `main.rs`.
- **Menus**: `menu id { section { item { label: ...; action: ...; target: ...; } } submenu { ... } }`, plus the
  short form `item (_("_Preferences"), "app.preferences")`. Menus sit next to the template at top level.
  Details are in `actions-and-shortcuts.md`.
- **Accessibility**: `accessibility { label: _("Delete"); labelled-by: [title_label]; }`. Icon-only buttons
  need one (see `accessibility-and-i18n.md`).
- **Extra top-level objects** (popovers, size groups, extra rows) can live in the same file next to the template
  and are reachable as template children by id.
- **String literals**: `"..."` and `'...'` are both accepted. GVariant properties such as `action-target` take
  GVariant text, so a string target is written `action-target: "'list'";`.

## 3. Property bindings and expressions

```blp
title: bind template.title;                                  // property of the template itself
sensitive: bind name_entry.text-length;                     // property of a sibling Gtk.Entry, by id
show-sidebar: bind sidebar_button.active bidirectional;     // two-way
visible: bind template.compact inverted;                   // boolean NOT
label: bind template.note as <$NotesNote>.title;            // lookup through an object property
visible: bind $not_empty(template.note as <$NotesNote>.title) as <bool>;  // closure
```

- `bind source.prop` with optional flags `bidirectional`, `inverted` and `no-sync-create` compiles to a
  `GBinding` (XML `bind-source`/`bind-property`/`bind-flags`). `sync-create` is the default in Blueprint, so
  only the opt-out exists. Older Blueprint versions had a separate `bind-property` keyword; 0.19 still accepts
  it with an upgrade warning, but `bind` covers both kinds. A binding with a single lookup (`source.prop`)
  always compiles to a `GBinding`; flags are only allowed on those.
- Anything with a lookup through an intermediate object (`as <$Type>.prop`) or a closure is a **GtkExpression**
  binding. Expressions are one-way, but unlike `GBinding` they **follow changes of the intermediate object**:
  when `template.note` is replaced, `title` re-evaluates. This is why expression bindings are the right tool for
  row widgets that get recycled (see `lists-and-models.md`).
- `as <$Type>` is required whenever the static type of the intermediate property is too general (`GObject`, or
  the property is nullable) for the compiler to know which property you mean. The type must be registered
  before the template is parsed (§5.3).
- A closure expression must end with `as <type>` for its return type (`as <bool>`, `as <string>`).

The closure functions are Rust template callbacks without `self`. A crate-wide bag of pure helpers (fractal:
`src/utils/template_callbacks.rs`) keeps them in one place:

```rust
// src/utils/template_callbacks.rs
pub struct TemplateCallbacks {}

#[gtk::template_callbacks(functions)]
impl TemplateCallbacks {
    #[template_callback]
    fn not_empty(text: Option<&str>) -> bool {
        text.is_some_and(|text| !text.is_empty())
    }

    #[template_callback]
    fn invert_boolean(value: bool) -> bool {
        !value
    }
}

// in every widget class that uses them:
fn class_init(klass: &mut Self::Class) {
    klass.bind_template();
    TemplateCallbacks::bind_template_callbacks(klass);
}
```

Why `functions`: closures in expressions receive the `this` object as an extra first value. `function` mode drops
it and disallows `self`, so the Rust signature matches the arguments written in the `.blp`.

Gotchas:

- Argument and return types are checked **at runtime**. A wrong type panics when the expression first
  evaluates, so exercise every new closure once. `Option<&str>` accepts a `NULL` string, `&str` does not.
- Expression bindings are owned by the template and torn down with it. You don't unbind them by hand.

## 4. Signal handlers and template callbacks

```blp
clicked => $on_save_clicked() swapped;
notify::selected => $on_selected_changed() swapped;
apply => $on_narrow_layout() swapped;       // Adw.Breakpoint signal
```

```rust
#[gtk::template_callbacks]
impl Window { // the imp struct
    #[template_callback]
    fn on_save_clicked(&self) {
        self.obj().save();
    }

    #[template_callback]
    fn on_selected_changed(&self) {
        tracing::debug!("selected {}", self.mode_dropdown.selected());
    }

    #[template_callback]
    async fn on_open_clicked(&self) {
        self.open_button.set_sensitive(false);
        let _ = self.obj().open_file().await;
        self.open_button.set_sensitive(true);
    }
}
```

- `swapped` makes the template instance the first argument. Every `&self` callback needs it; without it the
  first value is the emitting widget and the call panics with a type mismatch. With `swapped`, the remaining
  values are the signal arguments followed by the emitter; trailing ones can be left out (`on_save_clicked`).
- Where the callbacks live decides how they are registered in `class_init`:
  - on the **imp** struct: `klass.bind_template_callbacks()` (or `Self::bind_template_callbacks(klass)`);
  - on the **wrapper** type: `klass.bind_template_instance_callbacks()`;
  - on another type: `Other::bind_template_callbacks(klass)`.
- `#[template_callback(name = "save")]` renames the callback (`=> $save()`). `#[rest] values: &[glib::Value]`
  on the last argument collects the remaining values.
- **Async callbacks**: an `async fn` without a return value is spawned on the default main context. Each
  emission spawns another future, so guard against re-entrancy (disable the button, as above, or keep a
  `Cell<bool>` "busy" flag). fractal's `session_view/invite.rs` is a good example.
- For behaviour reachable from menus or shortcuts, prefer an action (`action-name: "win.save";`) over a
  `clicked` handler; see `actions-and-shortcuts.md`.

## 5. The Rust side in detail

### 5.1 The derive and the two init hooks

- `#[derive(gtk::CompositeTemplate)]` plus `#[template(resource = "/org/example/Notes/ui/window.ui")]`
  generates `bind_template` (sets the template and binds child fields) and a runtime type check of each child.
- `klass.bind_template()` in `class_init`, `obj.init_template()` in `instance_init`. Nothing else works without
  both. Register callbacks and actions in the same `class_init`.
- `#[template_child]` field name = object id. Use `#[template_child(id = "save-button")]` when the id isn't a
  valid Rust identifier or you want a different field name. `#[template_child(internal = true)]` exposes the
  child as an internal child to `.ui` files that subclass this widget.
- `TemplateChild<T>` derefs to `T` (`self.delete_button.set_visible(false)`), and `.get()` returns an owned
  clone for closures. The field type may be a parent class of the real object (`gtk::Widget` for a
  `gtk::Button`), but not an unrelated type: the check panics with "Template child with id `x` has incompatible
  type".

### 5.2 When children are available

Children exist after `init_template()`, so the earliest place to use them is `ObjectImpl::constructed`
(after `self.parent_constructed()`). GObject sets non-construct properties passed to
`glib::Object::builder().property(...)` *after* `constructed`, so a custom setter must not assume
`constructed` has not run yet, and `constructed` will not see those values. Keep setters to storing state
and notifying, and let template bindings react to the change.

### 5.3 When `ensure_type()` is needed

`TemplateChild<T>::default()` calls `T::static_type()` (gtk4 0.11.5, `src/subclass/widget.rs`), so any custom
type that has a `#[template_child]` field is registered before `init_template()` parses the template. A
`$Type` without such a field is not, and template parsing fails with "Invalid object type". Register it
explicitly:

```rust
fn class_init(klass: &mut Self::Class) {
    NotesSidebar::ensure_type();   // `$NotesSidebar {}` in the .blp, no field in Rust
    Note::ensure_type();           // used only in `as <$NotesNote>` casts
    klass.bind_template();
}
```

The same applies to types that appear only inside a `Gtk.BuilderListItemFactory` template and to the item types
named in `as <$T>`. A type that is already the value type of one of the widget's `#[property]` fields (like
`Note` in §1) gets registered when the class installs its properties, so the call is redundant there but
harmless. Some apps register every type once at startup instead; calling `ensure_type()` next to the
template that needs it is easier to keep correct.

### 5.4 Disposal

- Parent is a GTK/Adw container or window (`adw::Bin`, `gtk::Box`, `adw::ApplicationWindow`, rows, dialogs):
  nothing to do, the parent class unparents its children.
- Parent is `gtk::Widget` itself (custom layout): unparent the template children in `dispose`:

```rust
impl ObjectImpl for Timeline {
    fn dispose(&self) {
        self.dispose_template(); // GTK >= 4.8; from gtk::subclass::prelude
    }
}
```

- Disconnect handlers you attached to long-lived objects (singletons, models shared with other widgets) in
  `dispose` as well; templates don't do that for you (see `closures-and-lifetimes.md`).

## 6. Alternatives to resource-loaded templates

`#[template(...)]` accepts three sources (gtk4-macros 0.11.5):

| Source | Behaviour |
| --- | --- |
| `resource = "/path/x.ui"` | Loaded from the registered GResource when the class initialises. The only form that works with Meson-compiled Blueprint. **Recommended for apps.** |
| `file = "x.ui"` | Embedded with `include_bytes!`, so the path is relative to the `.rs` file. With the `xml_validation` cargo feature, child ids are checked at compile time. |
| `file = "x.blp"` | Needs the `blueprint` feature. Runs `blueprint-compiler compile -` at macro expansion time. In 0.11.5 the path is joined to `CARGO_MANIFEST_DIR`, i.e. relative to the **crate root**, not to the source file (upstream main may change this; check before relying on it). |
| `string = "..."` | Inline. Starts with `<` → XML. With the `blueprint` feature, a string starting with a letter (`using Gtk 4.0;`) is compiled as Blueprint. |

```toml
gtk = { package = "gtk4", version = "0.11", features = ["gnome_50", "blueprint"] }
```

The `blueprint` feature suits cargo-only prototypes, examples and tests. For a GNOME app built with Meson and
Flatpak it adds a build-time dependency on `blueprint-compiler` (and the GIR typelibs it needs to type-check
`using` imports) inside `cargo build`, and `resource =` doesn't work with it. Keep Meson + `resource =`.

## 7. Blueprint to XML cheat sheet

Apps such as pika-backup, warp and Shortwave use XML `.ui` files. Equivalents:

| Blueprint | XML |
| --- | --- |
| `template $NotesWindow: Adw.ApplicationWindow {` | `<template class="NotesWindow" parent="AdwApplicationWindow">` |
| `Gtk.Button save_button {` | `<object class="GtkButton" id="save_button">` |
| `label: _("Save");` | `<property name="label" translatable="yes">Save</property>` |
| `C_("ctx", "Save")` | `translatable="yes" context="ctx"` |
| `[end] Gtk.Button {}` | `<child type="end"><object class="GtkButton"/></child>` |
| `clicked => $on_save() swapped;` | `<signal name="clicked" handler="on_save" swapped="true"/>` |
| `title: bind template.title;` | `<property name="title" bind-source="NotesWindow" bind-property="title" bind-flags="sync-create"/>` |
| `bind x.active bidirectional inverted` | `bind-flags="sync-create\|bidirectional\|invert-boolean"` |
| `bind template.note as <$NotesNote>.title` | `<binding name="label"><lookup name="title" type="NotesNote"><lookup name="note">NotesWindow</lookup></lookup></binding>` |
| `bind $not_empty(...) as <bool>` | `<binding name="visible"><closure type="gboolean" function="not_empty">...</closure></binding>` |
| `styles ["flat"]` | `<style><class name="flat"/></style>` |

Convert a whole file with `blueprint-compiler compile file.blp`; the reverse direction is
`blueprint-compiler decompile file.ui` (it handles typical templates, menus and list factories; XML comments are
lost, and unusual XML may fail to convert).

## Pitfalls

- `Invalid object type 'NotesSidebar'` while building the template → a `$Type` is used without a
  `#[template_child]` field (or only in a cast or a list-item factory) → call `NotesSidebar::ensure_type()` at the
  top of `class_init`.
- Panic "Failed to retrieve template child. Please check that all fields of type `X` have been bound…" →
  the id in the `.blp` differs from the field name, the object has no id, or `bind_template()`/`init_template()`
  is missing → match the names (or use `#[template_child(id = ...)]`) and check both init hooks. GTK usually logs
  a critical about the missing child just before the panic.
- Critical "Unable to load resource for composite template" / "The resource at … does not exist" → the
  `#[template(resource)]` path doesn't match the gresource prefix + alias, the `.blp` isn't in
  `blueprint_files`, or the resource bundle was registered after the first widget was created → compare the path
  with `resources.gresource.xml.in` (prefix `/org/example/Notes` + alias `ui/x.ui`) and register resources in
  `main` before `Application::new()`, as the scaffold does.
- Error building the template that names the parent or class → `template $Name: Parent` disagrees with
  `const NAME` or `type ParentType` (common after renaming a type or switching `gtk::Window` to
  `adw::ApplicationWindow`) → keep the three in sync.
- Panic about argument types when a signal fires → `&self` callback without `swapped`, or a parameter type that
  doesn't match the signal → add `swapped`, check the `connect_*` signature of that signal.
- Closure in an expression panics or always yields the default → missing `functions`/`function`, so the
  arguments are shifted by the `this` object → mark the impl `#[gtk::template_callbacks(functions)]`.
- Callback "not found" when the template is built → the callbacks impl was never registered → call the matching
  `bind_template_callbacks` / `bind_template_instance_callbacks` in `class_init`.
- A binding stops updating after the bound object is replaced → plain `bind a.b` through an intermediate object
  → use an expression lookup (`bind template.note as <$NotesNote>.title`).
- New `.blp` compiles but the app shows the old UI → the Meson output list or the gresource XML wasn't
  updated, so the stale bundle is used → update both lists and rebuild with Meson, not just cargo.

## See also

- `gobject.md`: subclassing, `#[derive(Properties)]`, `explicit_notify` setters used by templates.
- `actions-and-shortcuts.md`: actions referenced from templates, menus, shortcuts dialog.
- `lists-and-models.md`: row widgets with an `item` property, `BuilderListItemFactory`.
- `libadwaita-ui.md`: which Adw widgets to put in templates.
- `project-layout-and-build.md`: Meson Blueprint rule and gresource bundle.
- `accessibility-and-i18n.md`: translatable strings, POTFILES, accessible labels.
- `compile-traps.md`: compile errors around templates and macros.
