# Lists and models

Read this when you show a collection: picking `ListBox` or `ListView`/`GridView`/`ColumnView`, building the model
chain (store, filter, sort, selection), writing factories that survive row recycling, and implementing a custom
`gio::ListModel`.

## Contents

1. [Choosing the widget](#1-choosing-the-widget)
2. [Models: store, filter, sort, selection](#2-models-store-filter-sort-selection)
3. [Factories and recycling](#3-factories-and-recycling)
4. [A row widget with an `item` property](#4-a-row-widget-with-an-item-property)
5. [`BuilderListItemFactory` in Blueprint](#5-builderlistitemfactory-in-blueprint)
6. [Boxed lists: `ListBox` and `PreferencesGroup` with `bind_model`](#6-boxed-lists-listbox-and-preferencesgroup-with-bind_model)
7. [Custom `ListModel`](#7-custom-listmodel)
8. [Trees and tables](#8-trees-and-tables)
9. [Performance](#9-performance)
10. [Pitfalls](#pitfalls)

## 1. Choosing the widget

| Situation | Widget | Notes |
| --- | --- | --- |
| Settings, accounts, devices; up to a few hundred rows; rows with switches/entries | `Gtk.ListBox` + `styles ["boxed-list"]` + `bind_model`, or `Adw.PreferencesGroup` | One widget per item, never recycled. Adw rows (`ActionRow`, `SwitchRow`, `EntryRow`, `ComboRow`) fit directly. |
| Sidebars, feeds, search results, anything unbounded | `Gtk.ListView` | Only visible rows exist; widgets are recycled. |
| Thumbnails, icons, cards | `Gtk.GridView` | Same model and factory as `ListView`. |
| Sortable multi-column data | `Gtk.ColumnView` | One factory per column, per-column sorters. |
| Hierarchies | `TreeListModel` + `TreeExpander` in any of the above | Replaces `GtkTreeView`. |

`GtkTreeView`, `gtk::ListStore`, `gtk::TreeStore` and `EntryCompletion` are deprecated; see `deprecated-apis.md`.
A `ListView`/`GridView`/`ColumnView` must be the direct child of a `Gtk.ScrolledWindow`; otherwise it can't
scroll itself and ends up creating a widget for every item.

## 2. Models: store, filter, sort, selection

Items are GObjects. Wrap plain data in a small `#[derive(Properties)]` type (see `gobject.md`); use
`gtk::StringList` when the items really are just strings.

```rust
let store = gio::ListStore::new::<Note>();
store.extend_from_slice(&notes); // one items-changed for the whole batch

let search_filter = gtk::StringFilter::builder()
    .expression(gtk::PropertyExpression::new(Note::static_type(), gtk::Expression::NONE, "title"))
    .match_mode(gtk::StringFilterMatchMode::Substring)
    .ignore_case(true)
    .build();
// The search entry drives the filter declaratively.
self.search_entry.property_expression("text").bind(&search_filter, "search", gtk::Widget::NONE);

let archived_filter = gtk::CustomFilter::new(|obj| {
    obj.downcast_ref::<Note>().is_some_and(|note| !note.archived())
});
let filters = gtk::EveryFilter::new();
filters.append(search_filter);
filters.append(archived_filter.clone());

let filtered = gtk::FilterListModel::new(Some(store.clone()), Some(filters));
let sorter = gtk::CustomSorter::new(|a, b| {
    let (a, b) = (a.downcast_ref::<Note>().unwrap(), b.downcast_ref::<Note>().unwrap());
    b.modified().cmp(&a.modified()).into() // std::cmp::Ordering -> gtk::Ordering
});
let sort_model = gtk::SortListModel::new(Some(filtered), Some(sorter.clone()));
let selection = gtk::SingleSelection::new(Some(sort_model));
self.note_list.set_model(Some(&selection));
```

- Keep the original store as the single source of truth and filter/sort with wrapper models. Rebuilding a store to
  filter destroys selection, scroll position and row widgets.
- **Filters and sorters do not see item property changes on their own**, expression-based ones included: a
  `BoolFilter`/`StringFilter` re-evaluates an item only when told to. After an input changes (an item's
  `archived` flag, a search term, a sort setting), invalidate explicitly:
  - filters: `filter.changed(gtk::FilterChange::Different)` (or `MoreStrict`/`LessStrict` when you know the
      direction, so GTK re-checks only part of the items). Since GTK 4.20 (gtk4 feature `v4_20`, included in
      `gnome_50`) `filtered.set_watch_items(true)` makes a `FilterListModel` watch the properties its filter's
      expressions read, at the cost of a notify handler per item.
  - `CustomSorter`: `sorter.changed(gtk::SorterChange::Different)`.
  - key-based sorters (`NumericSorter`, `StringSorter`, and `MultiSorter`s built from them) cache a sort key per
      item, so `sorter.changed()` does **not** pick up a changed item property. Re-emit the item instead:
      `store.items_changed(pos, 1, 1)` recomputes its key (it also re-runs filters for that item). `SortListModel`
      has no `watch-items` at gtk4 0.11.
  Don't create new filter or sorter objects to refresh.
- Combine with `gtk::EveryFilter`/`AnyFilter` and `gtk::MultiSorter` instead of writing one large closure.
- Selection models: `NoSelection` for activate-only lists (`single-click-activate: true` + the `activate`
  signal; fractal uses it in most lists), `SingleSelection` for pickers and master-detail, `MultiSelection` when
  users select several rows. `SingleSelection` has `autoselect` (default true) and `can-unselect` (default false)
  properties; turn autoselect off when an empty selection is meaningful.
- Read the selection through properties: `selection.selected_item().and_downcast::<Note>()` and
  `connect_selected_item_notify`, or bind `selected-item` in Blueprint.
- Empty state: bind a `Gtk.Stack`'s `visible-child-name` or an `Adw.StatusPage`'s `visible` to the model's
  `n-items` property (available on `gio::ListStore` and GTK's wrapper models) instead of counting by hand.

## 3. Factories and recycling

`ListView` creates a handful of row widgets and reuses them for different items as the user scrolls. A
`SignalListItemFactory` has four signals:

| Signal | Runs | Do here |
| --- | --- | --- |
| `setup` | once per row widget | create the child widget, set up bindings that follow `ListItem:item` |
| `bind` | every time the row gets an item | per-item work that can't be expressed as a binding |
| `unbind` | before the row gets a different item | undo **everything** `bind` did |
| `teardown` | when the row widget is discarded | rarely needed |

The recommended pattern does all the work in `setup` and makes `bind`/`unbind` unnecessary:

```rust
let factory = gtk::SignalListItemFactory::new();
factory.connect_setup(|_, obj| {
    let Some(list_item) = obj.downcast_ref::<gtk::ListItem>() else { return };
    let row = NoteListRow::default();
    list_item.set_child(Some(&row));
    // Follows whatever item this row currently shows; no unbind needed.
    list_item.property_expression("item").bind(&row, "item", gtk::Widget::NONE);
});
self.note_list.set_factory(Some(&factory));
```

Why it works: the expression watches `ListItem:item`, so when GTK recycles the row for another item, the row's
`item` property is updated, and the row's own template bindings (`bind template.item as <$NotesNote>.title`,
see §4) follow. fractal does the same with `list_item.bind_property("item", &row, "item").build()`
(`session_view/sidebar/mod.rs`); amberol chains expressions to individual properties
(`list_item.property_expression("item").chain_property::<Song>("title").bind(&row, "song-title", ...)`).

For a quick list without a custom row type, chain directly to a `gtk::Label`:

```rust
list_item
    .property_expression("item")
    .chain_property::<Note>("title")
    .bind(&label, "label", gtk::Widget::NONE);
```

### When you do need `bind`/`unbind`

Bidirectional bindings and signal handlers can't be expressed as expressions. Whatever `bind` creates,
`unbind` must remove, because a recycled row would otherwise keep writing to (and keep alive) the previous
item. warp shows the leak (`src/ui/camera.rs`): `connect_bind` connects a new
`selection.connect_selected_item_notify` handler on every bind and never disconnects it, so handlers pile up for
as long as the popover lives. Two fixes:

- Put the state in the row instead: `list_item.property_expression("selected").bind(&row, "selected", ...)`
  in `setup` replaces the per-row selection handler entirely (`ListItem` has `selected`, `position` and `item`
  properties).
- Or let the row own the per-item connections and reset them when `item` changes (§4), so the factory still
  only needs `setup`.

If you keep the logic in the factory, store the `glib::Binding`s / `SignalHandlerId`s on the row widget
(`RefCell<Vec<glib::Binding>>`) and drain them in `connect_unbind`. Avoid `set_data`/`steal_data`: they are
`unsafe`, and the scaffold denies `unsafe_code`.

## 4. A row widget with an `item` property

```blp
// data/resources/ui/note-list-row.blp
using Gtk 4.0;

template $NotesNoteListRow: Gtk.Box {
  orientation: vertical;
  spacing: 3;

  Gtk.Label {
    xalign: 0;
    ellipsize: end;
    label: bind template.item as <$NotesNote>.title;
  }

  Gtk.Label {
    xalign: 0;
    label: bind template.item as <$NotesNote>.summary;

    styles [
      "caption",
      "dimmed",
    ]
  }
}
```

```rust
#[derive(Debug, Default, gtk::CompositeTemplate, glib::Properties)]
#[template(resource = "/org/example/Notes/ui/note-list-row.ui")]
#[properties(wrapper_type = super::NoteListRow)]
pub struct NoteListRow {
    #[property(get, set = Self::set_item, explicit_notify, nullable)]
    item: RefCell<Option<Note>>,
    // Only needed for two-way bindings or signal handlers on the item.
    bindings: RefCell<Vec<glib::Binding>>,
    #[template_child]
    done_check: TemplateChild<gtk::CheckButton>,
}

impl NoteListRow {
    fn set_item(&self, item: Option<Note>) {
        if *self.item.borrow() == item {
            return;
        }
        for binding in self.bindings.borrow_mut().drain(..) {
            binding.unbind();
        }
        if let Some(note) = &item {
            let binding = note
                .bind_property("done", &*self.done_check, "active")
                .bidirectional()
                .sync_create()
                .build();
            self.bindings.borrow_mut().push(binding);
        }
        self.item.replace(item);
        self.obj().notify_item();
    }
}
```

- One-way display state goes in the template as expression bindings; they follow `item` without any code.
- Two-way bindings and handlers are reset in the setter, which runs on every recycle. The early return keeps
  recycling cheap and avoids redundant notifications.
- Parent on `gtk::Box` or `adw::Bin` (single child). Register the item type before the template parses the
  `as <$NotesNote>` casts; the `item` property already does that here (see `templates-and-blueprint.md` §5.3).

## 5. `BuilderListItemFactory` in Blueprint

```blp
Gtk.ScrolledWindow {
  child: Gtk.ListView note_list {
    single-click-activate: true;
    activate => $on_note_activated() swapped;

    factory: Gtk.BuilderListItemFactory {
      template Gtk.ListItem {
        child: $NotesNoteListRow {
          item: bind template.item;
        };
      }
    };

    styles [
      "navigation-sidebar",
    ]
  };
}
```

```rust
// In the window's class_init: the row type has no #[template_child] field, so register it.
NoteListRow::ensure_type();

#[template_callback]
fn on_note_activated(&self, position: u32) {
    let note = self.note_list.model().and_then(|m| m.item(position)).and_downcast::<Note>();
    if let Some(note) = note {
        self.obj().open_note(&note);
    }
}
```

- Inside the factory, `template` means the `Gtk.ListItem`, so `template.item` is the model object. You can also
  bind `ListItem` properties (`activatable: bind template.item as <$NotesNote>.enabled;`).
- Signal handlers with `swapped` inside the factory template don't reach your window; handle activation on the
  view (above) or put behaviour in the row widget.
- The model is still set from Rust (`set_model`) unless the whole chain is declared in Blueprint, which is
  possible (`model: Gtk.NoSelection { model: Gtk.FilterListModel { ... }; };`) but harder to wire to Rust data.
- Exemplar: fractal `session_view/room_details/invite_subpage/mod.blp`.

## 6. Boxed lists: `ListBox` and `PreferencesGroup` with `bind_model`

```blp
Adw.PreferencesGroup {
  title: _("Devices");

  Gtk.ListBox device_list {
    selection-mode: none;

    styles [
      "boxed-list",
    ]
  }
}
```

```rust
self.device_list.bind_model(Some(&devices), |obj| {
    let device = obj.downcast_ref::<Device>().expect("model contains Device items");
    DeviceRow::new(device).upcast()
});
self.device_list.set_placeholder(Some(&adw::ActionRow::builder().title(gettext("No Devices")).build()));
```

- Rows are created per item and destroyed on removal, never recycled, so bindings made in the row's constructor
  need no unbind (Shortwave `src/ui/device_dialog.rs`, Authenticator `widgets/providers/dialog.rs`).
- With libadwaita 1.8+ (`adw` feature `v1_8`; the scaffold enables `v1_9`), `adw::PreferencesGroup` can bind
  a model itself, without an inner `ListBox`: `group.bind_model(Some(&devices), |obj| ...)`, from
  `PreferencesGroupExt`. Use it in preference pages; the group adds the boxed-list styling.
- `ListBox` filters and sorts with `set_filter_func`/`set_sort_func`, but with a model, filter the model
  (`FilterListModel`) instead; the functions don't apply to bound models.
- Past a few hundred rows, or when rows are expensive, switch to `ListView`. Its usual styles are
  `navigation-sidebar` and `rich-list`. `boxed-list` doesn't style a `ListView`: libadwaita 1.9's stylesheet only
  matches `list.boxed-list` (the `ListBox` node), not `listview`.

## 7. Custom `ListModel`

Implement `gio::ListModel` when items come from your own data structure (a paginated API, a merged view). The
minimal clean example is fractal `src/session/room/typing_list.rs`:

```rust
#[glib::object_subclass]
impl ObjectSubclass for TypingList {
    const NAME: &'static str = "NotesTypingList";
    type Type = super::TypingList;
    type Interfaces = (gio::ListModel,);
}

impl ListModelImpl for TypingList {
    fn item_type(&self) -> glib::Type {
        Member::static_type()
    }
    fn n_items(&self) -> u32 {
        u32::try_from(self.members.borrow().len()).unwrap_or(u32::MAX)
    }
    fn item(&self, position: u32) -> Option<glib::Object> {
        self.members.borrow().get(position as usize).map(|m| m.clone().upcast())
    }
}

impl TypingList {
    fn replace_all(&self, new_members: Vec<Member>) {
        let (removed, added) = {
            let mut members = self.members.borrow_mut();
            let removed = u32::try_from(members.len()).unwrap_or(u32::MAX);
            let added = u32::try_from(new_members.len()).unwrap_or(u32::MAX);
            *members = new_members;
            (removed, added)
        }; // borrow dropped here
        self.obj().items_changed(0, removed, added);
    }
}
```

- **Drop every `RefCell` borrow before `items_changed`.** Views and wrapper models call `n_items()`/`item()`
  synchronously from the signal; a live `borrow_mut` panics with "already mutably borrowed".
- The `(position, removed, added)` triple must describe the change exactly; a wrong count corrupts views
  silently or triggers GTK criticals. When in doubt, report a full replacement (`0, old_len, new_len`).
- `item()` must return the same object for the same position until `items_changed` says otherwise. Creating a
  new wrapper GObject on each call breaks selection and bindings; cache them.
- Before writing one, check the built-ins: `FlattenListModel` (sections from a model of models; fractal's
  sidebar), `MapListModel`, `SliceListModel`, `gtk::SingleSelection` wrapping, or a plain `gio::ListStore`
  updated with `splice`.

## 8. Trees and tables

```rust
let tree = gtk::TreeListModel::new(root_store, false, false, |item| {
    item.downcast_ref::<Folder>().map(|folder| folder.children().upcast::<gio::ListModel>())
});
// setup: a TreeExpander wraps the row and draws the disclosure triangle.
factory.connect_setup(|_, obj| {
    let Some(list_item) = obj.downcast_ref::<gtk::ListItem>() else { return };
    let expander = gtk::TreeExpander::new();
    let row = FolderRow::default();
    expander.set_child(Some(&row));
    list_item.set_child(Some(&expander));
    list_item.property_expression("item").bind(&expander, "list-row", gtk::Widget::NONE);
    list_item
        .property_expression("item")
        .chain_property::<gtk::TreeListRow>("item")
        .bind(&row, "item", gtk::Widget::NONE);
});
```

- With `passthrough = false` the model's items are `gtk::TreeListRow`s; their `item` property is your object.
  Return `None` from the closure for leaves. Wrap sorters in `gtk::TreeListRowSorter` so children sort under
  their parents (mission-center `src/table_view/mod.rs`).
- `ColumnView`: one `ColumnViewColumn` per column, each with its own factory and optional sorter
  (`column.set_sorter(Some(&sorter))`); feed `column_view.sorter()` to the `SortListModel` so header clicks
  re-sort. Persist the sort column/order from `column_view.sorter().connect_changed` if users expect it to stick
  (mission-center).

## 9. Performance

- Batch store updates: `store.splice(pos, n_removed, &new_items)` or `extend_from_slice` emit one
  `items-changed`; N `append`/`remove` calls emit N, and each one re-runs filters, sorters and view updates.
  `store.retain(|obj| ...)` emits one signal per contiguous removed run.
- Avoid per-item notify storms. Updating 10 000 items that each notify a property watched by an expression,
  filter or sorter wakes all of them. Change only items whose value actually differs (`explicit_notify` setters
  with an early return), use `obj.freeze_notify()` (a guard) when one item changes several properties, and for
  bulk refreshes replace the items with `splice` instead of mutating them in place.
- Call `filter.changed()` / `sorter.changed()` once after a batch, not per item.
- Large sorts: `SortListModel::set_incremental(true)` and `FilterListModel::set_incremental(true)` spread the
  work over several frames; the `pending` property tells you when it's done (show a spinner).
- Keep `bind`/setup cheap: no file I/O or image decoding in row setup. Load thumbnails asynchronously and set
  them on the item, so the row binding picks them up (see `async-and-threads.md`).
- Don't call `n_items()`/`item()` in a loop over a `FilterListModel` to find something; keep an index in your
  own data or use `store.find(&item)` / `find_with_equal_func`.

## Pitfalls

- Rows show the wrong item after scrolling, or two rows change together → bindings or handlers made in `bind`
  are never undone → bind `ListItem:item` once in `setup` with an expression, or unbind in `unbind`/the row's
  `item` setter.
- Memory and CPU grow while scrolling → per-bind `connect_*` handlers accumulating (warp `camera.rs`) → move the
  state to a `ListItem` property binding or disconnect in `unbind`.
- Panic "already mutably borrowed" from inside `items_changed` → custom model emits while holding a `RefCell`
  borrow → compute counts in a block, drop the borrow, then emit.
- Filter or sort order goes stale after an item changes → no filter or sorter watches item properties by itself,
  expression-based ones included → `filter.changed(...)` (or `FilterListModel::set_watch_items(true)`, GTK ≥ 4.20);
  for `NumericSorter`/`StringSorter`, whose per-item keys `changed()` does not refresh, `store.items_changed(pos, 1, 1)`;
  for a `CustomSorter`, `changed(...)`.
- `ListView` is slow with many items and every row is realised → it isn't the direct child of a
  `ScrolledWindow` (e.g. wrapped in a `Box`) → make it the direct child.
- `Invalid object type 'NotesNoteListRow'` when the list appears → row type used only in a
  `BuilderListItemFactory` template → `NoteListRow::ensure_type()` in the owning widget's `class_init`.
- `downcast` panics in factory callbacks → the model holds a different type than expected (e.g. `TreeListRow`
  from a `TreeListModel`, or `StringObject` from a `StringList`) → downcast to the wrapper first.
- Selection jumps to the first row after a refresh → the store was cleared and refilled, and `SingleSelection`
  autoselects → splice changed ranges only, or turn off `autoselect`.

## See also

- `gobject.md`: item types with `#[derive(Properties)]`, `explicit_notify`.
- `templates-and-blueprint.md`: expression bindings, `ensure_type`, template callbacks.
- `closures-and-lifetimes.md`: handler and binding lifetimes, weak references.
- `actions-and-shortcuts.md`: per-row actions and context menus.
- `libadwaita-ui.md`: boxed lists, preference rows, status pages for empty states.
- `async-and-threads.md`: loading items and thumbnails without blocking the UI.
- `deprecated-apis.md`: `TreeView`, `gtk::ListStore` and their replacements.
- `clipboard-and-dnd.md`: dragging rows (`DragSource` in `setup`) and dropping onto lists.
