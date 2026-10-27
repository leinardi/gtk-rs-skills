# Clipboard and drag-and-drop

Read this when the app copies or pastes anything beyond what a text widget does by itself, drags
items out of a list, accepts files or text dropped onto a window, or when a paste or drop must be
cancelled. Text widgets (`gtk::Entry`, `gtk::TextView`) already handle Ctrl+C/Ctrl+V and text
drag-and-drop; this is for your own content.

## Contents

1. The clipboard
2. Content providers and formats
3. Dragging
4. Dropping with `DropTarget`
5. `DropTargetAsync`: reading the data yourself
6. File drops
7. Cancellation
8. Controllers in Blueprint
9. Pitfalls / See also

## 1. The clipboard

Get the clipboard from a widget (`widget.clipboard()`), so it belongs to the right display. On
Linux there is also `widget.primary_clipboard()`, the middle-click selection. Writing is
synchronous; reading is asynchronous, because the data may come from another process.

```rust
fn copy_text(widget: &impl IsA<gtk::Widget>, text: &str) {
    widget.clipboard().set_text(text);
}

fn copy_image(widget: &impl IsA<gtk::Widget>, texture: &gdk::Texture) {
    widget.clipboard().set_texture(texture);
}

async fn paste_text(widget: &impl IsA<gtk::Widget>) -> Option<String> {
    match widget.clipboard().read_text_future().await {
        Ok(text) => text.map(String::from), // None: the clipboard holds no text
        Err(err) => {
            tracing::warn!("Could not read the clipboard: {err}");
            None
        }
    }
}

async fn paste_image(widget: &impl IsA<gtk::Widget>) -> Option<gdk::Texture> {
    widget.clipboard().read_texture_future().await.ok().flatten()
}

async fn paste_files(widget: &impl IsA<gtk::Widget>) -> Vec<gio::File> {
    let value = widget
        .clipboard()
        .read_value_future(gdk::FileList::static_type(), glib::Priority::DEFAULT)
        .await;
    value
        .ok()
        .and_then(|value| value.get::<gdk::FileList>().ok())
        .map(|list| list.files())
        .unwrap_or_default()
}
```

Expose copy and paste as actions (`win.copy`, `win.paste`) so the menu, the shortcut and a
button share one path (`actions-and-shortcuts.md`). Keep Paste enabled only while there is
something to paste:

```rust
/// Keep `win.paste` enabled only while the clipboard has something we can paste.
fn track_paste_action(window: &gtk::ApplicationWindow) {
    let update = glib::clone!(
        #[weak]
        window,
        move |clipboard: &gdk::Clipboard| {
            let formats = clipboard.formats();
            let can_paste = formats.contains_type(glib::GString::static_type())
                || formats.contains_type(gdk::Texture::static_type());
            window.action_set_enabled("win.paste", can_paste);
        }
    );
    let clipboard = window.clipboard();
    update(&clipboard);
    clipboard.connect_changed(update);
}
```

The method names are inconsistent in gdk4 0.11: `ContentFormats::contains_type(gtype)` but
`contain_mime_type(mime)`.

## 2. Content providers and formats

A `gdk::ContentProvider` describes data that can be offered in one or more formats. The reader
picks the format it understands.

```rust
/// Offers rich and plain text at once; the paste target picks the best format it understands.
fn copy_rich(widget: &impl IsA<gtk::Widget>, html: &str, plain: &str) {
    let provider = gdk::ContentProvider::new_union(&[
        gdk::ContentProvider::for_bytes("text/html", &glib::Bytes::from(html.as_bytes())),
        gdk::ContentProvider::for_value(&plain.to_value()),
    ]);
    if let Err(err) = widget.clipboard().set_content(Some(&provider)) {
        tracing::warn!("Could not set the clipboard: {err}");
    }
}
```

- `for_value` offers a `glib::Value`. GTK converts the common types to MIME types for other
  processes: a string becomes `text/plain;charset=utf-8` and `text/plain`, a `gdk::Texture`
  becomes `image/png` (and other image types), a `gdk::FileList` becomes `text/uri-list` plus
  `application/vnd.portal.filetransfer` (how files cross a sandbox boundary).
- `for_bytes(mime, bytes)` offers raw bytes under a MIME type of your choice, such as
  `text/html` or an app-specific `application/x-myapp-items`.
- `new_union` combines providers; list the richest format first.
- Custom GObject types in a `for_value` provider only work *inside* your process. For pasting into
  another instance or app, serialize to bytes under a MIME type.
- In a Flatpak, the clipboard works without extra permissions. Files are a special case: a
  `FileList` from another sandboxed app arrives through the document portal (§6).

## 3. Dragging

Attach a `gtk::DragSource` to the widget that can be dragged. Build the content in `prepare`, when
the drag actually starts, not up front.

```rust
fn make_draggable(row: &gtk::Widget, file: &gio::File) {
    let source = gtk::DragSource::builder()
        .actions(gdk::DragAction::COPY | gdk::DragAction::MOVE)
        .build();
    source.connect_prepare(glib::clone!(
        #[strong]
        file,
        move |_, _x, _y| {
            let list = gdk::FileList::from_array(std::slice::from_ref(&file));
            Some(gdk::ContentProvider::for_value(&list.to_value()))
        }
    ));
    source.connect_drag_begin(|source, _drag| {
        if let Some(widget) = source.widget() {
            // A snapshot of the row follows the pointer.
            let paintable = gtk::WidgetPaintable::new(Some(&widget));
            source.set_icon(Some(&paintable), 0, 0);
        }
    });
    source.connect_drag_end(|_, _drag, delete_data| {
        if delete_data {
            // MOVE finished: remove the item from our model here.
        }
    });
    source.connect_drag_cancel(|_, _drag, reason| {
        tracing::debug!("Drag cancelled: {reason:?}");
        false // not handled: GTK shows the default "snap back" animation
    });
    row.add_controller(source);
}
```

- `prepare` returning `None` refuses the drag at that position, for example on a header row.
- With `MOVE`, the source deletes its copy only in `drag-end` when `delete_data` is true. Never
  delete in `prepare` or on `drag-begin`.
- In a `ListView`, add the `DragSource` once per row widget in the factory's `setup` and read the
  current item in `prepare` (`lists-and-models.md`). Adding it in `bind` stacks one controller per
  rebind.
- `drag-cancel` fires for Escape, a failed drop or no target. Restore any visual state the drag
  changed.

## 4. Dropping with `DropTarget`

`gtk::DropTarget` receives a value of one of the types it accepts, already read and converted:

```rust
fn accept_drops(widget: &gtk::Widget) {
    let target = gtk::DropTarget::new(glib::Type::INVALID, gdk::DragAction::COPY);
    target.set_types(&[gdk::FileList::static_type(), glib::GString::static_type()]);
    target.connect_drop(|target, value, _x, _y| {
        if let Ok(list) = value.get::<gdk::FileList>() {
            let files = list.files();
            if files.is_empty() {
                return false;
            }
            open_files(target.widget().as_ref(), &files);
            return true;
        }
        if let Ok(text) = value.get::<glib::GString>() {
            insert_text(&text);
            return true;
        }
        false // tells the source the drop failed
    });
    widget.add_controller(target);
}
```

- `DropTarget::new(type, actions)` takes one type; `glib::Type::INVALID` plus `set_types` takes
  several, in order of preference.
- Return `true` from `drop` only when the data was used. `false` reports a failed drop to the
  source, which then won't delete anything on MOVE.
- While a drag hovers, GTK sets the `:drop(active)` state on the widget. Style the highlight in CSS
  (`.my-drop-area:drop(active) { … }`) rather than by hand.
- `enter` / `motion` return the action the drop *would* perform at that point (or
  `DragAction::empty()` to refuse there); `leave` resets per-position state.

**Reject before the drop.** With `preload`, the value is read while hovering, so you can refuse
unsuitable content before the user lets go:

```rust
/// Only image files are accepted: decide on hover, before the data is transferred.
fn accept_images_only(target: &gtk::DropTarget) {
    target.set_preload(true); // read the value during hover so `value()` is available
    target.connect_value_notify(|target| {
        let Some(value) = target.value() else {
            return;
        };
        let images = value.get::<gdk::FileList>().is_ok_and(|list| {
            list.files().iter().all(|file| {
                file.basename()
                    .and_then(|name| name.extension().map(|ext| ext.eq_ignore_ascii_case("png")))
                    .unwrap_or(false)
            })
        });
        if !images {
            target.reject();
        }
    });
}
```

Preload transfers the data for every hover, so keep it for small values such as file lists or
text, not large images.

**Hover to switch.** To open a tab, page or folder while something is dragged over it, use
`gtk::DropControllerMotion`. It reports enter/leave for any drag without accepting anything:

```rust
fn hover_switch(stack_page_button: &gtk::Widget) {
    let motion = gtk::DropControllerMotion::new();
    motion.connect_enter(|motion, _x, _y| {
        if let Some(widget) = motion.widget() {
            widget.activate(); // e.g. switch to the tab under the pointer
        }
    });
    stack_page_button.add_controller(motion);
}
```

## 5. `DropTargetAsync`: reading the data yourself

Use `gtk::DropTargetAsync` when you read the data yourself, for example a format GTK does not
convert, or a large read you want to run asynchronously:

```rust
fn drop_async(widget: &gtk::Widget) {
    let formats = gdk::ContentFormats::for_type(gdk::Texture::static_type());
    let target = gtk::DropTargetAsync::new(Some(formats), gdk::DragAction::COPY);
    target.connect_drop(|_, drop, _x, _y| {
        glib::spawn_future_local(glib::clone!(
            #[strong]
            drop,
            async move {
                let read = drop
                    .read_value_future(gdk::Texture::static_type(), glib::Priority::DEFAULT)
                    .await;
                match read.ok().and_then(|value| value.get::<gdk::Texture>().ok()) {
                    Some(texture) => {
                        tracing::debug!("Dropped a {}x{} image", texture.width(), texture.height());
                        drop.finish(gdk::DragAction::COPY);
                    }
                    // finish() on every path, or the source waits until it times out.
                    None => drop.finish(gdk::DragAction::empty()),
                }
            }
        ));
        true // we will call finish()
    });
    widget.add_controller(target);
}
```

Returning `true` from `drop` is a promise to call `drop.finish(action)` exactly once, on success
(the action performed), on failure and on cancellation (`DragAction::empty()`). A missed `finish`
leaves the source app waiting. `accept` decides whether to take part in the drag at all (default:
the formats match).

## 6. File drops

- Files arrive as a `gdk::FileList` (`.files()` gives `Vec<gio::File>`), both from Files and from
  other apps.
- In a Flatpak, files dropped from outside the sandbox come through the **document portal**, as
  paths under `/run/user/<uid>/doc/…`. The `gio::File` you receive is readable (and writable if the
  user could write it). Use it as is: don't rebuild it from a path string or a parent directory,
  which the app has no access to.
- Route dropped files through the same open path as the Open dialog (`document-apps.md` §2), so
  unsaved work isn't replaced and stale loads are handled.
- Reject unsupported files up front (§4 preload, or by MIME type via
  `file.query_info_future(gio::FILE_ATTRIBUTE_STANDARD_CONTENT_TYPE, …)` after the drop), and show
  a toast for files that were skipped.

## 7. Cancellation

The future-returning reads (`read_text_future`, `read_texture_future`, `read_value_future`, and
`Drop::read_value_future`) take **no cancellable parameter**; they create one internally and
cancel it when the future is dropped. There are three ways to cancel:

- **Abort the task and drop its handle.** `glib::JoinHandle::abort()` alone keeps the future
  alive while the handle exists, and awaiting an aborted handle never resolves. Abort, then drop
  the handle (`async-and-threads.md`, Cancellation).

  ```rust
  /// `read_*_future()` makes its own cancellable: abort the task to cancel the read.
  fn paste_abortable(widget: &gtk::Widget) -> glib::JoinHandle<Option<String>> {
      let widget = widget.clone();
      glib::spawn_future_local(async move { paste_text(&widget).await })
  }
  // Later: handle.abort(); drop(handle);
  ```

- **One `gio::Cancellable` for several operations.** Wrap each future with
  `gio::CancellableFuture`; it resolves to `Err(gio::Cancelled)` once the cancellable fires.

  ```rust
  /// One cancellable for several operations: wrap the futures.
  async fn paste_with(widget: &gtk::Widget, cancellable: &gio::Cancellable) -> Option<String> {
      let read = gio::CancellableFuture::new(widget.clipboard().read_text_future(), cancellable.clone());
      match read.await {
          Ok(Ok(text)) => text.map(String::from),
          Ok(Err(err)) => {
              tracing::warn!("Could not read the clipboard: {err}");
              None
          }
          Err(gio::Cancelled) => None,
      }
  }
  ```

- **Callback style** takes the cancellable directly:
  `clipboard.read_text_async(Some(&cancellable), |result| …)`. A cancelled read reports an error
  matching `gio::IOErrorEnum::Cancelled`, which is not something to show the user.

Cancel pending reads when the window closes or the target widget is unmapped. A cancelled
`DropTargetAsync` read still owes `drop.finish(gdk::DragAction::empty())`. Never hold a `RefCell`
borrow across any of these `.await`s.

## 8. Controllers in Blueprint

Drag sources and drop targets can be declared as children of the widget; GtkBuilder adds them as
controllers:

```blueprint
Box drop_area {
  DropTarget drop_target {
    actions: copy;
  }

  DragSource {
    actions: copy;
  }
}
```

The accepted types are not a property, so set them in code: bind `drop_target` as a
`#[template_child]` and call `drop_target.set_types(&[gdk::FileList::static_type()])` and
`connect_drop` in `constructed`. Signal handlers can also be template callbacks.

## Pitfalls

- **A drop seems to succeed but nothing happens, and the source hangs for a while** → a
  `DropTargetAsync` handler returned `true` but some path never called `drop.finish()` → finish on
  every path.
- **Item disappears from the source list although the drop failed** → deleted in `prepare` or on
  `drag-begin` → delete only in `drag-end` with `delete_data == true`.
- **`drop` returns `true` for data that wasn't used** → the source deletes the original on MOVE →
  return `false` when nothing happened.
- **Dragging a list row starts several drags or leaks controllers** → `DragSource` added in
  `bind` → add it once in `setup`.
- **Paste stays enabled with an empty clipboard** → computed once at startup → update on
  `clipboard.connect_changed`.
- **Pasted content is a custom object in one instance and nothing in another** → `for_value` with
  an app-only GType → also offer bytes under a MIME type.
- **Code after an aborted read never runs, or the read keeps going** → aborted `JoinHandle`
  awaited or kept → abort, then drop it.
- **"no method `contain_gtype`"** → the gtk-rs name is `contains_type` (but `contain_mime_type`).
- **Dropped file opens fine on the host but fails in Flatpak** → the app rebuilt the path or opened
  the parent directory → use the `gio::File` from the drop.
- **Drop highlight never shows, or stays on** → highlight managed by hand in `enter`/`leave` →
  style `:drop(active)`.

## See also

`document-apps.md`, `text-editing.md`, `async-and-threads.md`, `platform-integration.md`,
`lists-and-models.md`, `actions-and-shortcuts.md`, `css-drawing-animation.md`
