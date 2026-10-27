# Libadwaita UI

Read this when building or reviewing the visible structure of a libadwaita app: the window skeleton, adaptive
layouts, dialogs, preferences, toasts, empty and loading states, and style management.

Target: libadwaita 1.9 through the `adw` crate 0.9 (`features = ["v1_9", "gtk_v4_22"]` in the scaffold). The
minimum libadwaita version is noted next to each widget that is newer than 1.4. Each `v1_x` Cargo feature turns on
that version and everything before it.

## Contents

- [Crate features](#crate-features)
- [Window skeleton](#window-skeleton)
- [Adaptive layout](#adaptive-layout)
- [Dialogs](#dialogs)
- [Preferences](#preferences)
- [Toasts](#toasts)
- [Empty, loading and status UI](#empty-loading-and-status-ui)
- [Segmented controls and small containers](#segmented-controls-and-small-containers)
- [StyleManager: color scheme and accent](#stylemanager-color-scheme-and-accent)
- [HIG pointers](#hig-pointers)
- [Pitfalls](#pitfalls)

## Crate features

```toml
adw = { package = "libadwaita", version = "0.9", features = ["v1_9", "gtk_v4_22"] }
```

- `v1_x` gates libadwaita API by version: `adw::Dialog`/`AlertDialog`/`AboutDialog`/`PreferencesDialog` need
  `v1_5`, `ShortcutsDialog` needs `v1_8`, and `Sidebar`/`ViewSwitcherSidebar` need `v1_9`.
- `gtk_v4_*` matters too. In libadwaita 0.9.2 most widget wrappers only `@implements gtk::Accessible` when
  `gtk_v4_10` (or a later `gtk_v4_*`) is enabled. Without it, `update_property`, `announce` and upcasts to
  `gtk::Accessible` are missing on adw widgets, and the compiler errors are confusing. Match it to the GTK
  version of your runtime, as the scaffold does.

## Window skeleton

```blp
template $AppWindow: Adw.ApplicationWindow {
  width-request: 360;
  height-request: 294;
  content: Adw.ToastOverlay toast_overlay {
    child: Adw.ToolbarView {
      [top]
      Adw.HeaderBar {
        [end]
        MenuButton { primary: true; icon-name: "open-menu-symbolic"; tooltip-text: _("Main Menu"); menu-model: primary_menu; }
      }
      content: Adw.StatusPage { /* ... */ };
    };
  };
}
```

Rust side: `type ParentType = adw::ApplicationWindow;` plus `impl AdwApplicationWindowImpl for Window {}`. The
scaffold's `data/resources/ui/window.blp` and `src/window.rs` are the complete version.

Why:

- `Adw.ApplicationWindow` has no titlebar slot. Its single `content` child draws everything, so each page owns
  its header bar. That's what lets split views give the sidebar and the content separate header bars.
- `Adw.ToolbarView` (1.4) stacks `[top]`/`[bottom]` bars around `content`, handles the "raised" undershoot
  shadow when content scrolls (`top-bar-style: raised;` for always-on), and can hide bars with
  `reveal-top-bars`. Use it instead of a vertical `Gtk.Box` with a header bar.
- One `Adw.ToastOverlay` around the whole content makes toasts reachable from anywhere in the window.
- `width-request: 360; height-request: 294;` is the GNOME minimum (the smallest phone-sized layout). Breakpoints
  need an explicit minimum size. Without one, libadwaita warns and the window can't shrink into the breakpoint.
  Verify every layout at 360×294.

Gotcha: use `Adw.HeaderBar`, not `Gtk.HeaderBar`, because only the former adapts its buttons to split views and
dialogs. For a subtitle, set `title-widget: Adw.WindowTitle { title: ...; subtitle: ...; };`.

## Adaptive layout

### Breakpoints

```blp
template $AppWindow: Adw.ApplicationWindow {
  // ...
  Adw.Breakpoint {
    condition ("max-width: 600sp")
    setters {
      split_view.collapsed: true;
      template.compact: true;
    }
  }

  Adw.Breakpoint {
    condition ("max-width: 550sp")
    setters {
      switcher_bar.reveal: true;
      header_bar.title-widget: null;
    }
  }
}
```

```rust
// Rust equivalent, e.g. when the threshold comes from data. Also: BreakpointCondition::new_length(
// adw::BreakpointConditionLengthType::MaxWidth, 600.0, adw::LengthUnit::Sp).
let condition = adw::BreakpointCondition::parse("max-width: 600sp")?;
let breakpoint = adw::Breakpoint::new(condition);
breakpoint.add_setters(&[(&*imp.split_view, "collapsed", true)]);
breakpoint.connect_apply(|_| tracing::debug!("narrow layout"));
window.add_breakpoint(breakpoint);
```

Why:

- Breakpoints (1.4) are declarative. Setters apply when the condition matches and revert automatically, so you
  never write "restore wide layout" code.
- Use `sp` units: they scale with text size, so large-text users get the narrow layout earlier. Conditions
  combine with `and`/`or` and parentheses: `"max-width: 500sp or max-aspect-ratio: 4/3"`.
- `apply`/`unapply` signals (`apply => $narrow() swapped;`) are for changes setters can't express.
- Breakpoints belong to `Adw.ApplicationWindow`, `Adw.Window`, `Adw.Dialog` or an `Adw.BreakpointBin` (for a
  widget that should react to its own width rather than the window's).

### Propagate a `compact` property instead of many breakpoints

```rust
// window.rs (imp): a plain #[property(get, set)] works; explicit_notify avoids redundant notifications.
#[property(get, set = Self::set_compact, explicit_notify)]
compact: Cell<bool>,

fn set_compact(&self, compact: bool) {
    if self.compact.replace(compact) != compact { self.obj().notify_compact(); }
}
```

```blp
// A page deep in the tree binds to the window's property.
template $RoomList: Adw.Bin {
  child: Adw.NavigationSplitView { collapsed: bind template.root as <$AppWindow>.compact; };
}
```

The window's breakpoint sets `template.compact: true`, and any descendant binds to it through `template.root`.
One breakpoint drives the whole tree (fractal: `src/window.blp`, `src/session_view/mod.blp`). Pages that
are reused inside dialogs can instead expose their own `compact` property and let the owner bind it.

### Choosing a container

| Need | Widget | Since |
| --- | --- | --- |
| Sidebar list + content, collapsing into a push/pop stack | `Adw.NavigationSplitView` (children must be `Adw.NavigationPage`) | 1.4 |
| Utility sidebar that slides over the content when narrow | `Adw.OverlaySplitView` (`show-sidebar`, `sidebar-position`) | 1.4 |
| Drill-down hierarchy | `Adw.NavigationView` + `Adw.NavigationPage` | 1.4 |
| A few peer views (tabs-like) | `Adw.ViewStack` + `Adw.ViewSwitcher` / `Adw.ViewSwitcherBar` | 1.0 (stack pages model 1.4) |
| Views inside a card, sidebar or popover | `Adw.InlineViewSwitcher` | 1.7 |
| Persistent sheet that peeks from the bottom (mini player) | `Adw.BottomSheet` | 1.6 |
| Completely different layouts per size | `Adw.MultiLayoutView` + `Adw.Layout`/`Adw.LayoutSlot` | 1.6 |
| Sectioned sidebar with built-in selection, or sidebar driven by a ViewStack | `Adw.Sidebar` / `Adw.ViewSwitcherSidebar` | 1.9 |

```blp
Adw.NavigationSplitView split_view {
  sidebar: Adw.NavigationPage {
    title: _("Library");
    child: Adw.ToolbarView {
      [top] Adw.HeaderBar {}
      content: Gtk.ScrolledWindow { child: Gtk.ListBox { styles ["navigation-sidebar"] }; };
    };
  };
  content: Adw.NavigationPage {
    title: _("Details");
    child: Adw.ToolbarView { [top] Adw.HeaderBar {} content: /* ... */; };
  };
}
```

- Collapsed `NavigationSplitView`: call `split_view.set_show_content(true)` after a selection, or the user stays
  on the sidebar. Header bars get a back button automatically.
- `NavigationView`: `nav.push(&page)`, `nav.push_by_tag("details")`, `nav.pop()`, `nav.pop_to_tag(tag)`. From
  Blueprint, any actionable widget can use the built-in actions: `action-name: "navigation.push";
  action-target: "'details'";` (note the GVariant quoting) and `navigation.pop`. Subclass `adw::NavigationPage`
  (`NavigationPageImpl`) for real pages, and use `shown => $focus_first() swapped;` to move focus when a page
  appears (fractal: `src/login/`).
- `ViewStack` + switchers: put `Adw.ViewSwitcher { policy: wide; stack: stack; }` in the header bar's
  `title-widget` and `Adw.ViewSwitcherBar switcher_bar { stack: stack; }` in the `ToolbarView`'s `[bottom]`.
  The breakpoint above reveals the bar and removes the title widget (warp: `src/ui/window.ui`). Each
  `Adw.ViewStackPage` needs `title` and `icon-name`, and `needs-attention`/`badge-number` show indicators.
- `Adw.ViewSwitcherTitle`, `Adw.Leaflet`, `Adw.Flap` and `Adw.Squeezer` are deprecated (1.4). Use the table above.

## Dialogs

All dialogs derive from `adw::Dialog` (1.5). They're widgets, not windows. They're presented over a parent
widget, become bottom sheets on narrow windows, and need no `transient-for`.

### Custom dialog

```rust
mod imp {
    use super::*;

    #[derive(Debug, Default, gtk::CompositeTemplate)]
    #[template(resource = "/com/example/App/ui/rename-dialog.ui")]
    pub struct RenameDialog {
        #[template_child]
        pub entry: TemplateChild<adw::EntryRow>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for RenameDialog {
        const NAME: &'static str = "AppRenameDialog";
        type Type = super::RenameDialog;
        type ParentType = adw::Dialog;
        // class_init / instance_init: bind_template / init_template as for any template widget
    }
    impl ObjectImpl for RenameDialog {}
    impl WidgetImpl for RenameDialog {}
    impl AdwDialogImpl for RenameDialog {}
}

glib::wrapper! {
    pub struct RenameDialog(ObjectSubclass<imp::RenameDialog>)
        @extends adw::Dialog, gtk::Widget,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::ShortcutManager;
}

// Any widget in the window works as the parent.
RenameDialog::new().present(Some(&button));
```

The template usually looks like `template $AppRenameDialog: Adw.Dialog { title: _("Rename"); content-width:
360; child: Adw.ToolbarView { [top] Adw.HeaderBar {} content: ...; }; }`. Use `can-close: false` plus the
`close-attempt` signal to confirm discarding unsaved input, and call `force_close()` once confirmed.

### Alert dialog

```rust
let dialog = adw::AlertDialog::builder()
    .heading(gettext("Delete Project?"))
    .body(gettext("The project and its files will be permanently deleted."))
    .close_response("cancel")
    .default_response("cancel")
    .build();
dialog.add_responses(&[("cancel", &gettext("_Cancel")), ("delete", &gettext("_Delete"))]);
dialog.set_response_appearance("delete", adw::ResponseAppearance::Destructive);

if dialog.choose_future(Some(&*self.obj())).await == "delete" {
    self.delete_project().await;
}
```

```blp
// Declarative alternative (also works as `template $ConfirmDialog: Adw.AlertDialog`):
Adw.AlertDialog delete_dialog {
  heading: _("Delete Project?");
  close-response: "cancel";
  responses [ cancel: _("_Cancel"), delete: _("_Delete") destructive, ]
}
```

- `choose_future` (from `adw::prelude::AlertDialogExtManual`) consumes the dialog and resolves to the response
  id. Escape and the close button resolve to `close-response`, so set it to the harmless choice.
- `ResponseAppearance::Suggested` for the primary safe action, `Destructive` for irreversible ones, `Default`
  otherwise. Only one response should be styled.
- Use `extra_child` for an entry or a list. Toggle `set_response_enabled("create", valid)` while the user types,
  and set `activates-default` on the entry together with `default_response`.
- Don't confuse it with `gtk::AlertDialog`, a plain GTK type. `adw::MessageDialog` is deprecated (1.6).

### About dialog

```rust
let dialog = adw::AboutDialog::from_appdata(
    &format!("{}/metainfo.xml", config::RESOURCE_BASE_PATH),
    Some(release_version), // shows the release notes of this <release> entry
);
dialog.set_version(config::VERSION);
// Translators: Replace "translator-credits" with your names, one name per line.
dialog.set_translator_credits(&gettext("translator-credits"));
dialog.present(app.active_window().as_ref());
```

`from_appdata` reads name, developer, links, license and release notes from the metainfo bundled into the
gresource (the scaffold aliases it to `metainfo.xml`), so they live in one place. Add extras with `add_link`,
`add_credit_section`, `set_developers`. `adw::AboutWindow` is deprecated (1.6).

## Preferences

```blp
template $AppPreferences: Adw.PreferencesDialog {
  search-enabled: true;

  Adw.PreferencesPage {
    title: _("General");
    icon-name: "preferences-system-symbolic";

    Adw.PreferencesGroup {
      title: _("Behavior");
      Adw.SwitchRow run_in_background { title: _("Run in Background"); subtitle: _("Keep running after the window is closed"); }
      Adw.ComboRow units_row { title: _("Units"); model: Gtk.StringList { strings [_("Metric"), _("Imperial")] }; }
      Adw.SpinRow interval_row {
        title: _("Refresh Interval");
        adjustment: Gtk.Adjustment { lower: 1; upper: 60; step-increment: 1; };
      }
      Adw.EntryRow name_row { title: _("Device Name"); show-apply-button: true; }
      Adw.ExpanderRow {
        title: _("Advanced");
        Adw.ActionRow {
          title: _("Cache Location");
          activatable-widget: open_cache_button;
          [suffix] Gtk.Button open_cache_button { icon-name: "folder-open-symbolic"; valign: center; tooltip-text: _("Open Folder"); styles ["flat"] }
        }
      }
    }

    Adw.PreferencesGroup {
      Adw.ButtonRow { title: _("_Reset All Settings"); use-underline: true; action-name: "prefs.reset"; styles ["destructive-action"] }
    }
  }
}
```

```rust
// constructed(): bind rows straight to GSettings; no handlers needed.
let settings = gio::Settings::new(config::APP_ID);
settings.bind("run-in-background", &*self.run_in_background, "active").build();
settings.bind("refresh-interval", &*self.interval_row, "value").build();
settings.bind("units", &*self.units_row, "selected").build(); // needs a uint/enum mapping, see settings-and-state.md
```

- Row choice: `SwitchRow` (1.4) for booleans, `ComboRow` for a choice from a list, `SpinRow` (1.4) for numbers,
  `EntryRow` (1.2) for short text (`apply` signal with `show-apply-button`), `PasswordEntryRow` (1.2),
  `ActionRow` for navigation or custom suffixes, `ExpanderRow` for grouped details, `ButtonRow` (1.6) for
  standalone actions inside a boxed list.
- Changes apply immediately. GNOME preferences have no OK/Apply buttons.
- Subpages: `dialog.push_subpage(&navigation_page)`. `PreferencesDialog` has its own `add_toast`.
- `PreferencesGroup::bind_model` (1.8) binds a list model directly. `adw::PreferencesWindow` is deprecated (1.6).

## Toasts

```rust
let toast = adw::Toast::builder()
    .title(gettext("Project deleted"))
    .button_label(gettext("_Undo"))
    .action_name("win.undo-delete")
    .build();
self.toast_overlay.add_toast(toast);
```

- Put one `Adw.ToastOverlay` at the window root (the scaffold does this). `add_toast` takes the toast by value.
- `adw::Dialog` has no toast overlay. Toasts sent to the window's overlay appear behind the dialog.
  `PreferencesDialog` is the exception: it has `add_toast`. For other dialogs that need toasts, wrap their
  content in their own overlay (fractal: `src/components/dialogs/toastable.blp`, `template $ToastableDialog:
  Adw.Dialog { child: Adw.ToastOverlay toast_overlay {}; }`).
- A helper that walks `widget.ancestor(...)` to the nearest toast host (dialog overlay, then
  `PreferencesDialog`, then the window) keeps call sites short (fractal: `src/utils/toast.rs`).
- Toasts confirm something the user did. Errors that need action belong in an `Adw.Banner` or a dialog.

## Empty, loading and status UI

```blp
Gtk.Stack stack {
  transition-type: crossfade;

  Gtk.StackPage { name: "loading"; child: Adw.Spinner { width-request: 32; height-request: 32; halign: center; valign: center; }; }
  Gtk.StackPage {
    name: "empty";
    child: Adw.StatusPage {
      icon-name: "folder-music-symbolic";
      title: _("No Music");
      description: _("Add a folder to start listening");
      child: Gtk.Button { label: _("_Add Folder…"); use-underline: true; halign: center; action-name: "win.add-folder"; styles ["pill", "suggested-action"] };
    };
  }
  Gtk.StackPage { name: "list"; child: Gtk.ScrolledWindow { /* ... */ }; }
}
```

- `Adw.StatusPage` is the standard empty, error and "no results" state. Add `styles ["compact"]` inside
  sidebars and popovers. Switch pages from the model's `items-changed` or bind `visible-child-name` through
  an expression (see `lists-and-models.md`).
- `Adw.Spinner` (1.6) replaces `Gtk.Spinner`. It is always animating while visible, so hide it rather than
  "stopping" it. `Adw.SpinnerPaintable::new(Some(&widget))` (1.6) puts a spinner in a `StatusPage`'s
  `paintable` for full-page loading screens, or in a `Gtk.Image`'s `paintable` where a widget won't fit
  (pika-backup: `navigation/spinner_page.ui`).
- `Adw.Banner` (1.3) shows persistent state ("Offline", "Read-only") in the `ToolbarView`'s `[top]`, below the
  header bar. Use `revealed`, `button-label` and `connect_button_clicked`.

## Segmented controls and small containers

```blp
Adw.ToggleGroup view_mode {
  active-name: "grid";
  Adw.Toggle { name: "grid"; icon-name: "view-grid-symbolic"; tooltip: _("Grid"); }
  Adw.Toggle { name: "list"; icon-name: "view-list-symbolic"; tooltip: _("List"); }
}
```

- `Adw.ToggleGroup` (1.7) replaces linked `Gtk.ToggleButton`s. Read and watch `active-name` (bind it to a
  GSettings key or an enum property). Add `styles ["round"]` or `["flat"]` to vary the look (both are in the
  libadwaita 1.9 stylesheet as `toggle-group.round` and `toggle-group.flat`).
- `Adw.InlineViewSwitcher` (1.7) is a ToggleGroup bound to an `Adw.ViewStack`, for switching inside content.
- `Adw.WrapBox` (1.7) flows children onto new lines, which is useful for tag chips and button rows that must fit
  at 360 px (fractal, pika-backup).
- `Adw.Clamp` limits content width (`maximum-size: 600; tightening-threshold: 400;`). Wrap boxed lists and
  forms in it so they don't stretch across a maximized window. `Adw.ClampScrollable` does the same for
  scrollables like `Gtk.ListView`. `Adw.PreferencesPage` and `Adw.StatusPage` clamp already.

## StyleManager: color scheme and accent

```rust
// Read-only use: adapt something CSS can't reach (e.g. a GtkSourceView scheme).
let style = adw::StyleManager::default();
let handler = style.connect_dark_notify(clone!(#[weak(rename_to = this)] self, move |style| {
    this.set_source_scheme(style.is_dark());
}));
// Store `handler` and disconnect it in dispose(): StyleManager outlives every widget.
```

- Follow the system by default. libadwaita applies the user's dark preference, accent color (1.6) and high
  contrast with no code. Don't force `ColorScheme::ForceDark`/`PreferDark`.
- An in-app "Follow System / Light / Dark" choice is mainly for platforms where
  `!style.system_supports_color_schemes()` (icon-library: `src/application.rs`). Store it in GSettings and map
  it to `set_color_scheme(adw::ColorScheme::Default | ForceLight | ForceDark)`.
- Queries: `is_dark()`, `is_high_contrast()`, `accent_color()`/`accent_color_rgba()` (1.6),
  `is_system_supports_accent_colors()` (1.6), with matching `connect_*_notify`. For drawing, prefer
  `widget.color()` and CSS variables (`css-drawing-animation.md`). They follow dark mode, high contrast and accent
  without any signal handling.

## HIG pointers

- **Header bar:** at most a few icon buttons. Put the primary menu (`open-menu-symbolic`, `primary: true` so F10
  opens it) at the end. Put "add/new" style actions at the start, and the view switcher in the title. Every
  icon-only button needs a `tooltip-text` (which also serves as its accessible label fallback).
- **Primary menu contents, in order:** optional "New Window", then a section with *Preferences*, *Keyboard
  Shortcuts* and *About <App Name>*. Leave out "Quit" and "Help" unless you have real docs. `app.shortcuts` is
  provided by `AdwApplication` (1.8) when `shortcuts-dialog.ui` sits at the resource base path.
- **Capitalization:** header capitalization for titles, buttons, menu items, row titles and toggle labels
  ("Keyboard Shortcuts", "Add Folder…"). Sentence capitalization for descriptions, subtitles, toasts and body
  text. Labels that open a further dialog requiring input end with an ellipsis (`…`, U+2026). Add mnemonics
  (`_Delete`) on dialog responses and menu items.
- **Dialogs:** confirmations name the action ("Delete", not "OK"/"Yes"), and the safe option is the default.
- **Size:** every view must work at 360×294. Resize to the minimum to test, with large text enabled too.
- Reference: <https://developer.gnome.org/hig/>.

## Pitfalls

- Compile error "no method `update_property`" or "`Accessible` is not implemented" on an adw widget → the adw
  `gtk_v4_*` feature is missing → enable `gtk_v4_10` or later (the scaffold uses `gtk_v4_22`).
- libadwaita warns about a missing minimum size, or the narrow layout never applies → no
  `width-request`/`height-request` on the window → set 360×294.
- Collapsed split view keeps showing the sidebar after a click → `show-content` never set → call
  `set_show_content(true)` on selection.
- Toast never appears from a dialog → it went to the window overlay behind the dialog → give the dialog its own
  `Adw.ToastOverlay` (or use `PreferencesDialog::add_toast`).
- Runtime "Invalid object type 'AppRenameDialog'" → a custom type appears only in Blueprint → call
  `RenameDialog::ensure_type()` in `class_init` (see `templates-and-blueprint.md`).
- StyleManager handler fires after the widget is gone, or leaks → the singleton holds the closure → use
  `#[weak]` in `clone!` and disconnect in `dispose`.
- Deprecated types in new code (`PreferencesWindow`, `AboutWindow`, `MessageDialog`, `Leaflet`, `Flap`,
  `ViewSwitcherTitle`, `Gtk.Spinner`) → see `deprecated-apis.md` for the replacements.

## See also

- `templates-and-blueprint.md`: template syntax, `ensure_type`, template callbacks.
- `actions-and-shortcuts.md`: `win.*`/`app.*` actions, the shortcuts dialog.
- `lists-and-models.md`: boxed lists, `ListView`, empty-state switching.
- `settings-and-state.md`: binding rows to GSettings, enum mappings.
- `css-drawing-animation.md`: style classes, CSS variables, animations.
- `accessibility-and-i18n.md`: accessible labels, translatable strings.
- `deprecated-apis.md`: old widgets and their replacements.
- `document-apps.md`: the "Save changes?" `AlertDialog` and close confirmation for windows.
- `text-editing.md`: `TextView`, GtkSourceView and schemes that follow the style manager.
