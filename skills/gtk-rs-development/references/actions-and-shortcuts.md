# Actions, menus and keyboard shortcuts

Read this when you add a command (menu entry, button, keyboard shortcut, context menu) or the shortcuts dialog:
where the action should live, how to give it state or a parameter, and how to attach accelerators.

## Contents

1. [Where an action lives](#1-where-an-action-lives)
2. [Application actions](#2-application-actions)
3. [Window and widget actions in `class_init`](#3-window-and-widget-actions-in-class_init)
4. [Stateful and parameterized actions](#4-stateful-and-parameterized-actions)
5. [Enabling and disabling](#5-enabling-and-disabling)
6. [Accelerators and widget-scoped shortcuts](#6-accelerators-and-widget-scoped-shortcuts)
7. [Menus](#7-menus)
8. [The shortcuts dialog](#8-the-shortcuts-dialog)
9. [Activating actions from code](#9-activating-actions-from-code)
10. [Naming conventions](#10-naming-conventions)
11. [Pitfalls](#pitfalls)

## 1. Where an action lives

| Scope | Mechanism | Typical actions |
| --- | --- | --- |
| `app.*` | `gio::ActionEntry` + `add_action_entries` in `ApplicationImpl::startup` | quit, about, preferences, new-window |
| `win.*` | `klass.install_action[_async]` in the window's `class_init` | open, save, toggle-sidebar, search |
| `<widget>.*` (`note.*`, `sidebar.*`) | `klass.install_action[_async]` in that widget's `class_init` | delete-row, copy-link, context-menu entries |
| Property-backed toggles/radios | `klass.install_property_action` | `win.show-sidebar`, `win.view-mode` |
| GSettings-backed toggles/radios | `settings.create_action("key")` added to an action map | `app.spell-check`, `win.sort-order` |

Recommendation: `ActionEntry` for `app.*` (the scaffold's `setup_actions()`), class actions for everything
attached to a widget type. Class actions get a typed `&Self` (no `clone!`, no weak references), exist for every
instance automatically, and are visible to all descendants of that widget, including popovers parented to it.

Avoid hand-rolled key dispatch (`EventControllerKey` + a `HashMap<Key, fn>`, as mission-center does): actions
plus accelerators give you menus, the shortcuts dialog, and accessibility for free.

## 2. Application actions

The scaffold registers them in `startup`, which runs once per process (not on every `activate`):

```rust
// src/application.rs
fn setup_actions(&self) {
    let quit = gio::ActionEntry::builder("quit")
        .activate(|app: &Self, _, _| {
            for window in app.windows() {
                window.close(); // lets windows save state in close_request
            }
            app.quit();
        })
        .build();
    let preferences = gio::ActionEntry::builder("preferences")
        .activate(|app: &Self, _, _| app.show_preferences())
        .build();
    self.add_action_entries([quit, preferences]);

    self.set_accels_for_action("app.quit", &["<Control>q"]);
    self.set_accels_for_action("app.preferences", &["<Control>comma"]);
    self.set_accels_for_action("window.close", &["<Control>w"]);
}
```

- The closure's first argument has the type of the action map the entries are added to (`&Self` here), which
  is why `add_action_entries` must be called on that same type.
- `window.close` is a built-in GTK action on every `gtk::Window`; don't define your own `win.close`.
- `app.shortcuts` is provided by libadwaita (§8); `app.about` is defined by the scaffold.

`win.*` actions can also be added with `ActionEntry` to a `gtk::ApplicationWindow` in `constructed`
(amberol, warp). That works, but the closure gets the window type only, and you repeat it for every instance.
Prefer class actions for new code.

## 3. Window and widget actions in `class_init`

```rust
fn class_init(klass: &mut Self::Class) {
    klass.bind_template();

    klass.install_action("win.toggle-search", None, |win, _, _| {
        let bar = &win.imp().search_bar;
        bar.set_search_mode(!bar.is_search_mode());
    });

    klass.install_action_async("win.open", None, |win, _, _| async move {
        if let Err(err) = win.open_file().await {
            tracing::warn!("Could not open the file: {err}");
        }
    });

    klass.install_action("win.open-note", Some(glib::VariantTy::STRING), |win, _, param| {
        let Some(id) = param.and_then(glib::Variant::get::<String>) else { return };
        win.open_note(&id);
    });

    // Boolean property -> toggle; other types -> parameter of the property's type.
    klass.install_property_action("win.show-sidebar", "show-sidebar");
}
```

- Signatures (gtk4 0.11.5): `install_action(name, Option<&VariantTy>, Fn(&Type, &str, Option<&Variant>))`;
  `install_action_async(name, Option<&VariantTy>, Fn(Type, String, Option<Variant>) -> Fut)`. The async form
  receives owned values and spawns the future on the default main context. Each activation spawns another
  future, so disable the action while it runs if re-entry is a problem (§5).
- The action name is always detailed with a group prefix. The prefix is just a string; `win.` on the window class
  is a convention, and `note.delete` on a row class works the same way.
- Class actions are not part of any `gio::ActionMap`: `window.lookup_action("open")` won't find them, they
  aren't exported on D-Bus, and they can't be stateful except through `install_property_action`.
- `install_property_action` needs a property that the macro-generated `#[property(get, set)]` notifies.
  Booleans become toggles; strings, numbers and enums become radio-style actions whose target is the value
  (identity uses an enum `display-mode` with targets `'tabbed'`/`'row'`). Use `explicit_notify` setters that
  only notify on change.
- Exemplars: identity `src/window.rs` (many window actions plus binding shortcuts), fractal
  `session_view/room_history/mod.rs` (widget-prefixed async actions).

## 4. Stateful and parameterized actions

### Boolean toggle and string radio with `ActionEntry`

```rust
let spell_check = gio::ActionEntry::builder("spell-check")
    .state(true.to_variant())
    .change_state(|app: &Application, action, value| {
        let Some(value) = value else { return };
        action.set_state(value); // a change_state handler must set the state itself
        app.set_spell_check(value.get::<bool>().unwrap_or_default());
    })
    .build();

let view_mode = gio::ActionEntry::builder("view-mode")
    .parameter_type(Some(glib::VariantTy::STRING))
    .state("list".to_variant())
    .change_state(|_app: &Application, action, value| {
        if let Some(value) = value {
            action.set_state(value);
        }
    })
    .build();
```

GTK's conventions decide how menus render them: no parameter + boolean state is a check item; a parameter
whose type equals the state type is a radio item, checked when `target == state`. With only `state` and
`change_state` set, `gio::SimpleAction`'s default `activate` toggles the boolean or forwards the parameter to
`change_state`, so you rarely need an `activate` callback for stateful actions.

### Detailed names

A detailed action name carries the target: `win.view-mode('list')` (GVariant text) or the shorthand
`win.view-mode::list` for strings. Use them in accelerators and shortcuts-dialog items:

```rust
app.set_accels_for_action("win.view-mode('list')", &["<Control>1"]);
app.set_accels_for_action("win.view-mode('grid')", &["<Control>2"]);
```

In Blueprint, a button sets the target separately and as GVariant text: `action-name: "win.view-mode";
action-target: "'grid'";` (note the inner quotes). Menu items use `target: "grid";` (a plain string is
assumed).

### `PropertyAction` and `Settings::create_action`

```rust
// In Window::constructed: a stateful win.* action that mirrors a widget property.
let action = gio::PropertyAction::new("show-sidebar", &*self.split_view, "show-sidebar");
self.obj().add_action(&action);

// GSettings-backed: state follows the key, activation writes the key.
let settings = gio::Settings::new(config::APP_ID);
self.obj().add_action(&settings.create_action("sort-order"));
```

`create_action` produces a toggle for boolean keys and a radio action for string/enum keys; bind the key to the
UI with `settings.bind(...)` instead of reacting to `activate`. Prefer it to hand-written stateful entries that
duplicate the setting (amberol's `background-play` does this). See `settings-and-state.md` for the settings
object's lifetime.

## 5. Enabling and disabling

```rust
// Class action (installed with install_action): set on the widget instance.
self.obj().action_set_enabled("win.save", has_changes);

// gio::SimpleAction (ActionEntry, PropertyAction is not a SimpleAction): look it up first.
if let Some(action) = app.lookup_action("quit").and_downcast::<gio::SimpleAction>() {
    action.set_enabled(false);
}
```

- Disabled actions make every widget bound to them insensitive, including menu items. In menus, add
  `hidden-when: "action-disabled";` to hide instead of grey out (fractal `media_viewer.blp`); useful for
  context menus whose entries depend on the item.
- For async actions, disable at the start and re-enable at the end so the user can't start a second run.

## 6. Accelerators and widget-scoped shortcuts

```rust
// Application-wide (works for app.*, win.* and class actions alike), in startup:
app.set_accels_for_action("win.toggle-search", &["<Control>f"]);

// Widget-scoped, in class_init: only active while focus is inside this widget.
use gtk::gdk::{Key, ModifierType};
klass.add_binding_action(Key::Delete, ModifierType::empty(), "note.delete");
klass.add_shortcut(&gtk::Shortcut::with_arguments(
    Some(gtk::KeyvalTrigger::new(Key::_1, ModifierType::ALT_MASK)),
    Some(gtk::NamedAction::new("win.view-mode")),
    &"list".to_variant(),
));
```

- `set_accels_for_action` accels are global to the window and are handled before focused widgets see the key.
  A bare letter or `Delete` there would fire while the user types in an entry. Single keys belong in
  `add_binding_action` on the widget that owns the context (identity: "Add these here instead of
  set_accels_for_action so that they don't override typing").
- Accelerator strings use GTK syntax: `<Control>`, `<Shift>`, `<Alt>`, `<Primary>`; key names as in
  `gdk::Key` (`comma`, `question`, `F10`, `Page_Down`).
- Blueprint equivalent of a widget-scoped shortcut (fractal `session_view/mod.blp`):

```blp
Gtk.ShortcutController {
  scope: managed;
  Gtk.Shortcut {
    trigger: "<Control>Page_Down";
    action: "action(sidebar.select-next)";
  }
}
```

## 7. Menus

### Primary menu

The scaffold's `window.blp` defines `menu primary_menu` next to the template and attaches it to a
`MenuButton` with `primary: true` (F10 opens it). GNOME HIG order: New Window, then a section with
Preferences, Keyboard Shortcuts and About <App>; no Quit entry.

```blp
menu primary_menu {
  section {
    item {
      label: _("_List");
      action: "win.view-mode";
      target: "list";             // radio item, since view-mode has string state
    }
    item {
      label: _("_Grid");
      action: "win.view-mode";
      target: "grid";
    }
  }
  section {
    item (_("_Preferences"), "app.preferences")
    item (_("_Keyboard Shortcuts"), "app.shortcuts")
    item (_("_About Notes"), "app.about")
  }
}
```

For a widget inside a menu (segmented control, slider), use `item { custom: "view-switcher"; }` and add the
widget from Rust with `popover_menu.add_child(&widget, "view-switcher")` (identity, mission-center).

### Context menu

```blp
// note-row.blp, next to the template
menu context_menu {
  section {
    item (_("_Rename"), "note.rename")
    item (_("_Delete"), "note.delete")
  }
}
```

```rust
// NoteRow imp: `#[template_child] context_menu: TemplateChild<gio::MenuModel>`,
// `popover: OnceCell<gtk::PopoverMenu>`; called from constructed().
fn setup_context_menu(&self) {
    let obj = self.obj();
    let popover = gtk::PopoverMenu::from_model(Some(&*self.context_menu));
    popover.set_parent(&*obj); // parented to the row, so note.* actions resolve
    popover.set_has_arrow(false);
    popover.set_halign(gtk::Align::Start);

    let click = gtk::GestureClick::builder().button(gtk::gdk::BUTTON_SECONDARY).build();
    click.connect_pressed(glib::clone!(
        #[weak]
        popover,
        move |gesture, _, x, y| {
            gesture.set_state(gtk::EventSequenceState::Claimed);
            #[expect(clippy::cast_possible_truncation)] // pointer coordinates fit in i32
            let rect = gtk::gdk::Rectangle::new(x as i32, y as i32, 1, 1);
            popover.set_pointing_to(Some(&rect));
            popover.popup();
        }
    ));
    obj.add_controller(click);
    let _ = self.popover.set(popover);
}

// ObjectImpl::dispose: a manually parented popover must be unparented.
fn dispose(&self) {
    if let Some(popover) = self.popover.get() {
        popover.unparent();
    }
}
```

- Add a `gtk::GestureLongPress` with the same handler for touch, and a keyboard path
  (`klass.add_binding_action(Key::F10, ModifierType::SHIFT_MASK, "note.context-menu")` plus `Key::Menu`) so
  the menu is reachable without a pointer.
- In list views, create one popover per row widget, as above, or share one popover at the list level and
  remember which item was clicked (fractal `session_view/sidebar/mod.rs` keeps a shared `room_row_popover`).

## 8. The shortcuts dialog

libadwaita 1.8+ loads `shortcuts-dialog.ui` from the application's **resource base path** and installs the
`app.shortcuts` action with `<Control>question` itself. You write no Rust for it. The scaffold already has
`data/resources/ui/shortcuts-dialog.blp`, bundled with `alias="shortcuts-dialog.ui"` at the root of the prefix:

```blp
using Gtk 4.0;
using Adw 1;

Adw.ShortcutsDialog shortcuts_dialog {
  Adw.ShortcutsSection {
    title: C_("shortcut window", "General");

    Adw.ShortcutsItem {
      title: C_("shortcut window", "Search");
      action-name: "win.toggle-search";   // shows the accel set with set_accels_for_action
    }

    Adw.ShortcutsItem {
      title: C_("shortcut window", "Delete Note");
      accelerator: "Delete";              // widget-scoped binding: spell it out
    }
  }
}
```

- The object id must be `shortcuts_dialog`, and the file must sit at `<resource-base-path>/shortcuts-dialog.ui`,
  not under `ui/`. The scaffold sets `resource-base-path` explicitly so `.Devel` app ids still find it.
- `action-name` items show what is registered with `set_accels_for_action`; shortcuts added with
  `add_binding_action`/`add_shortcut` aren't visible to the dialog, so use `accelerator:` for those. If an
  `action-name` item shows no keys, check that the accel is registered on the application with
  `set_accels_for_action` (setting both `action-name` and `accelerator` makes the latter a fallback).
- `GtkShortcutsWindow`, `gtk/help-overlay.ui` and `win.show-help-overlay` are deprecated (GTK 4.18); see
  `deprecated-apis.md`.

## 9. Activating actions from code

```rust
// From any widget: resolves "group.name" up the widget tree, then the app.
let _ = gtk::prelude::WidgetExt::activate_action(&*row, "note.delete", None);

// Directly on an action group, with the unprefixed name.
gio::prelude::ActionGroupExt::activate_action(&app, "quit", None);
gio::prelude::ActionGroupExt::change_action_state(&app, "spell-check", &false.to_variant());
```

- **E0034 "multiple applicable items in scope"**: windows implement both `gtk::Widget` and `gio::ActionGroup`,
  so `window.activate_action(...)` is ambiguous once the preludes are imported. Call the trait method fully
  qualified as above. `WidgetExt::activate_action` returns `Result<(), BoolError>` (error = no such action);
  `ActionGroupExt::activate_action` returns `()`.
- Prefer binding a widget to an action (`action-name`, menu `action:`) over calling `activate_action`; that
  keeps sensitivity in sync automatically.

## 10. Naming conventions

- Lowercase, dash-separated verbs: `win.toggle-search`, `note.move-to-trash`, `app.new-window`.
- Prefixes: `app.` for application scope, `win.` for window scope, a short widget name for class actions on other
  widgets (`note.`, `sidebar.`, `player.`). Avoid `app`/`win` prefixes on non-window widgets; readers will look
  for those actions on the application or window, and a same-named action would shadow the window's.
- Stateful toggles are named after the state (`win.show-sidebar`), not the verb (`win.toggle-sidebar`), because
  menus render them as check items.
- Built-in names worth knowing: `window.close`, `window.toggle-maximized`, `app.shortcuts` (libadwaita),
  `navigation.push`/`navigation.pop` (`Adw.NavigationView`), `menu.popup`, `listitem.activate`.

## Pitfalls

- Menu item or button stays greyed out → the action name is wrong, lacks its prefix, or isn't reachable from that
  widget (a class action on a sibling widget is invisible) → check the detailed name and that the widget is a
  descendant of the widget class that installs it.
- Radio menu items render as plain items → the state type differs from the parameter type (e.g. `i32` state, `s`
  parameter) → give both the same type.
- A single-letter shortcut fires while typing in an entry → it's registered with `set_accels_for_action` → move it
  to `klass.add_binding_action` on the widget that owns the context.
- Toggle action's state never changes → `change_state` handler doesn't call `action.set_state(value)` → set it
  there, or use `install_property_action`/`create_action`, which manage state for you.
- "Keyboard Shortcuts" entry does nothing → `shortcuts-dialog.ui` not at the root of the resource base path, wrong
  object id, or `resource-base-path` doesn't match the gresource prefix → follow the scaffold's alias and
  `Application::new()`.
- Async action runs twice when double-clicked → each activation spawns a future → disable the action while it
  runs.
- E0034 on `activate_action` / E0599 on `add_action_entries` with the wrong closure type → see §9 and make the
  `ActionEntry` closure's first argument the type you call `add_action_entries` on.
- Context menu popover leaks or warns "Finalizing … but it still has children left" → manually parented popover
  not unparented → unparent it in `dispose`.

## See also

- `templates-and-blueprint.md`: Blueprint menus, `action-name`, template callbacks.
- `settings-and-state.md`: `gio::Settings`, `create_action`, binding keys to widgets.
- `async-and-threads.md`: what to do inside `install_action_async`.
- `libadwaita-ui.md`: primary menu placement, `Adw.NavigationView` built-in actions.
- `accessibility-and-i18n.md`: keyboard reachability, translator comments for menu labels.
- `compile-traps.md`: E0034 and other trait-ambiguity errors.
- `deprecated-apis.md`: `GtkShortcutsWindow` and other replaced APIs.
