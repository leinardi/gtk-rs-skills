---
name: gtk-rs-review
description: Review checklist for gtk-rs (gtk4-rs + libadwaita) Rust code — finds main-thread blocking, reference cycles and leaked signal handlers, RefCell borrows across callbacks/await, wrong clone! usage, broken GObject subclassing, list-factory recycling bugs, deprecated GTK/libadwaita APIs, missing translations/accessibility, and Meson/Flatpak/GResource/GSettings misconfiguration. Use when the user asks to review, audit, check, modernise or find bugs/leaks in a GTK 4 Rust app, a diff or PR touching gtk-rs code, or wants to know whether gtk-rs code follows current best practices.
---

# gtk-rs review

Review gtk-rs code against the conventions of current GNOME Rust apps. Findings must be concrete: file and line,
what goes wrong at runtime (or why it is outdated), and the fix. Do not report style nits that `rustfmt`/`clippy`
already enforce, and do not flag a deliberate house style (e.g. `.ui` XML instead of Blueprint) as a defect.

## How to run the review

1. Establish scope: the diff (`git diff <base>...HEAD`) or the files/modules the user named. Read `Cargo.toml`
   for crate versions and features — a finding about a "missing" API is only valid if the enabled feature has it.
2. Walk the checklist below over the scope. For each hit, confirm it by reading the surrounding code (e.g. a
   strong capture is fine when the closure's owner is the captured object's child).
3. For fixes, follow the `gtk-rs-development` skill's references (paths below are relative to that skill). When
   a finding depends on an API detail, verify it in the crate source under `~/.cargo/registry/src/` rather than
   from memory.
4. If you can build, run `cargo clippy --all-targets -- -D warnings -W clippy::await_holding_refcell_ref` and
   include relevant lints; run `meson test` when build files changed.

## Checklist

Severity guide: **bug** = crash, freeze, leak, data loss or wrong behaviour; **risk** = breaks under plausible
conditions (Flatpak, Devel build, slow disk, re-entrancy); **modernise** = outdated but working.

### Main loop and threads (`references/async-and-threads.md`)

- Blocking calls on the main thread: sync file/network I/O (`std::fs`, `gio::File::*` without `_future` on user
  files, `reqwest::blocking`), `block_on`, `thread::sleep`, heavy loops in signal handlers. → bug
- GTK objects moved to other threads, or `unsafe impl Send` around them. → bug
- `#[tokio::main]` owning the main thread; tokio futures awaited on glib without a runtime context. → bug
- Spawned futures never cancelled when their widget goes away (no stored handle aborted in `dispose`/`unbind`/close);
  results applied to a widget that has since changed (no re-check after `.await`). → risk
- Timer `SourceId` removed after the source already finished (panic), or timers that never stop
  (`ControlFlow::Continue` after a failed upgrade). → bug

### Memory and lifetimes (`references/closures-and-lifetimes.md`)

- Closures connected to a longer-lived object (app, settings, model, `StyleManager`) that capture the widget
  strongly → cycle/leak; or that are never disconnected → handler runs on a disposed widget. → bug
- `clone!` with `#[strong]` capturing the signal's own emitter or an ancestor. → bug
- `#[weak]` in a long-lived `async` loop (upgraded once, held for the loop's life). → risk
- `RefCell` borrow alive while emitting a signal, calling `items_changed`, `notify`, a callback, or across
  `.await`. → bug (BorrowMutError panic)
- Strong references to windows stored in the application or in models. → risk
- `ExpressionWatch`/`Binding`/`SignalHandlerId` dropped on the floor when the source can change. → risk

### GObject structure (`references/gobject.md`)

- Overrides that do not chain up (`parent_constructed`, `parent_startup`, `parent_close_request`, ...). → bug
  `dispose` is the exception: glib chains it to the parent itself and there is no `parent_dispose()`, so do not
  report its absence (and flag any attempt to add one).
- `glib::wrapper!` missing ancestors/interfaces. → bug
- Hand-written property boilerplate, `set_data`, `once_cell`/`lazy_static`, `RefCell<Option<T>>` for set-once
  fields. → modernise
- Custom setters without equality check + `explicit_notify` (notify storms, binding loops). → risk
- `impl Default for Application` that *creates* an app instead of downcasting `gio::Application::default()`. → bug

### Templates, actions and lists

- Template child names/ids out of sync with the Blueprint; `$Types` used only in templates without `ensure_type()`;
  `&self` callbacks without `swapped`. → bug (`references/templates-and-blueprint.md`)
- App behaviour reachable only through a button handler (no action) so shortcuts/menus cannot share it; manual key
  dispatch instead of accelerators. → modernise (`references/actions-and-shortcuts.md`)
- `connect_*` inside `SignalListItemFactory::connect_bind` without disconnect in `unbind`; per-row state stored on
  recycled widgets; `ListBox` with thousands of rows; mutating items without `filter.changed()`/`sorter.changed()`;
  `items_changed` emitted while a borrow is held. → bug/risk (`references/lists-and-models.md`)

### Documents, text, clipboard (`references/document-apps.md`, `references/text-editing.md`, `references/clipboard-and-dnd.md`)

- `app.quit` calls `quit()` (after closing the windows or instead of it) in an app with unsaved state; a window's
  unsaved-changes veto is overridden. → bug (data loss)
- `close_request` without a re-entry guard: repeated Ctrl+W/Ctrl+Q opens several dialogs or starts several saves.
  → bug
- Dirty state from `TextBuffer::is_modified()`/`set_modified(false)` or from a revision counter; "saved" not
  recorded when a save raced an edit; a custom undo savepoint stored as a stack depth. → bug
- Overlapping saves of one document; `file` or the etag changed before a Save As succeeded (a failed Save As then
  loses conflict detection on the original); a cancelled file dialog reported as an error, or a failed save not
  shown. → bug
- A load applied after the user typed during the read (only another open is checked, not the revision). → bug
- Recovery drafts in the cache dir; draft writes and deletes not serialized; Discard or the last window closing
  without awaiting the draft delete or holding the app until it ran; a failed delete acknowledged as success; the
  close approved without re-checking for edits made while the cleanup was awaited. → bug
- `TextBuffer`/`TextIter` captured into a worker; iters kept across edits or `.await`; GTK char offsets mixed with
  Rust byte indices. → bug
- Large-text search on the main thread, or in `spawn_blocking` without a cancel flag checked inside the scan, or
  highlights applied without a generation check before each batch. → risk
- `DropTargetAsync` drop handler that does not call `drop.finish()` on every path; MOVE source deleting data before
  `drag-end` confirms it. → bug
- An aborted `glib::JoinHandle` that is awaited (never resolves) or kept alive (the future isn't dropped). → bug

### UI, style, a11y, i18n

- Deprecated widgets (`TreeView`, `gtk::Dialog`, `MessageDialog`, `FileChooser*`, `PreferencesWindow`,
  `AboutWindow`, `ShortcutsWindow`, `gtk::Spinner`, `Leaflet`...). → modernise (`references/deprecated-apis.md`)
- Hard-coded colors, `@define-color`/named colors, `style-dark.css`, `.dim-label`; forced dark mode. → modernise
  (`references/css-drawing-animation.md`)
- Windows/dialogs with a minimum size above 360×294 or no breakpoints for narrow widths. → risk
  (`references/libadwaita-ui.md`)
- User-visible strings not wrapped in `gettext`/`_()`, string concatenation of translated fragments, files with
  strings missing from `POTFILES.in`. → bug for translators (`references/accessibility-and-i18n.md`)
- Icon-only buttons without tooltip/accessible label; custom widgets without an accessible role; non-focusable
  interactive custom widgets. → bug for accessibility
- Errors shown as `unwrap`/`expect` panics or only logged; cancelled dialogs reported as errors. → risk

### Build, packaging, config (`references/project-layout-and-build.md`, `references/packaging-and-ci.md`)

- gresource prefix ≠ `resource-base-path` (style.css/shortcuts dialog/icons silently not loaded), especially with a
  `.Devel` app id. → bug
- Schema id or schema path not tied to the app id (dconf keys values by path, so Devel and release share
  settings), schema keys used but not declared. → bug
- Blueprint `custom_target(output: '.')` (ninja race); new `.blp` not listed in Meson/gresource. → risk
- Crate version features newer than the Flatpak runtime / Meson `dependency()` checks, or out of sync with each other. → bug
- Over-broad Flatpak permissions (`--filesystem=host`, `--device=all`, `--socket=session-bus`) without a comment
  justifying them; `--share=network` build-arg in a release manifest. → risk
- Missing desktop/metainfo/schema validation tests; metainfo without releases/screenshots before a release. → modernise

## Output format

Group findings by severity (bug, risk, modernise), most important first. For each:

```
<path>:<line> — <short title> [bug|risk|modernise]
What happens: <concrete runtime consequence or why it is outdated>
Fix: <the change, with a short snippet when it is not obvious>
```

End with a one-paragraph summary: overall health and the two or three changes with the best payoff. If the scope is
clean, say so plainly instead of inventing findings.
