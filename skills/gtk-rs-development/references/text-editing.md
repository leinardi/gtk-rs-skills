# Text editing: TextView, offsets, undo and search

Read this when the app shows or edits multi-line text (`gtk::TextView` / `gtk::TextBuffer`): an
editor, a notes app, a chat composer, a log view. It covers positions and Unicode offsets,
selections, tags, undo grouping, search over large text, and when to switch to GtkSourceView.
Saving, dirty state and closing with unsaved changes are in `document-apps.md`.

## Contents

1. TextView and TextBuffer
2. Iters and marks
3. Selections and the built-in actions
4. Unicode offsets
5. Tags
6. Undo
7. Search
8. When to use GtkSourceView
9. Pitfalls / See also

## 1. TextView and TextBuffer

The view displays; the buffer holds the text, the undo history and the tags. Several views can
share one buffer. Declare both in the template:

```blueprint
content: ScrolledWindow {
  vexpand: true;

  child: TextView text_view {
    wrap-mode: word_char;
    top-margin: 12;
    bottom-margin: 12;
    left-margin: 12;
    right-margin: 12;

    buffer: TextBuffer buffer {
      enable-undo: true;
    };

    accessibility {
      label: _("Document");
    }
  };
};
```

- Always put a `TextView` in a `ScrolledWindow`; otherwise the window grows with the text.
- Set `monospace: true` for code or logs. For prose, `wrap-mode: word_char` wraps at words and
  breaks long words instead of overflowing.
- `buffer.text(&start, &end, include_hidden_chars)` returns the text. `slice` also includes
  `0xFFFC` placeholders for embedded images and widgets; use it only when offsets must line up with
  those anchors.
- `buffer.set_text()` **clears the undo history** (in GTK 4 it is an irreversible action). That
  is what you want when loading a file. To replace the text as one undoable step, delete and
  insert inside a user action (§6).

## 2. Iters and marks

A `gtk::TextIter` is a position valid only until the next change to the buffer. Any insert or
delete invalidates every iter, including ones you hold in a variable. Use a `gtk::TextMark` for a
position that must survive edits or an `.await`:

```rust
async fn insert_lookup_result(buffer: &gtk::TextBuffer) {
    // An iter dies with the next edit; a mark moves with the text.
    let mark = buffer.create_mark(None, &buffer.iter_at_mark(&buffer.get_insert()), true);
    let text = lookup().await; // the user keeps typing meanwhile
    let mut iter = buffer.iter_at_mark(&mark);
    buffer.insert(&mut iter, &text);
    buffer.delete_mark(&mark);
}
```

- `left_gravity: true` keeps the mark before text inserted exactly at its position; `false`
  moves it after.
- Anonymous marks (`None` name) must be deleted when done; named marks are looked up with
  `buffer.mark("name")`.
- `buffer.get_insert()` is the cursor and `buffer.selection_bound()` the other end of the
  selection. Both are marks.
- Never send an iter or a mark to another thread. `TextBuffer`, `TextIter` and `TextMark` are
  `!Send`; copy the text out (§7).

## 3. Selections and the built-in actions

```rust
buffer.select_range(&buffer.iter_at_offset(6), &buffer.iter_at_offset(11));
if let Some((start, end)) = buffer.selection_bounds() {
    let selected = buffer.text(&start, &end, false);
}
// Enable selection-dependent actions from the property, not by polling.
buffer.connect_has_selection_notify(glib::clone!(
    #[weak]
    window,
    move |buffer| window.action_set_enabled("win.share-selection", buffer.has_selection())
));
```

`TextView` already installs these actions on itself: `clipboard.cut`, `clipboard.copy`,
`clipboard.paste`, `selection.select-all`, `selection.delete`, `text.undo`, `text.redo`,
`text.clear`, `misc.insert-emoji` and `menu.popup`. The standard shortcuts (Ctrl+Z, Ctrl+Shift+Z,
Ctrl+C…) work without any code. These are widget actions: they resolve only from the view and
widgets *inside* it. A header-bar button is not inside the view, so `action-name: "text.undo"`
on it stays insensitive. Give the window its own `win.undo` / `win.redo` that call
`buffer.undo()` / `buffer.redo()`, and enable them from `buffer.can_undo()` / `can_redo()`
(`notify::can-undo`, `notify::can-redo`).

`cursor-position` (char offset of the cursor) has a notify signal for status bars ("Ln 3, Col 7":
`iter.line()` and `iter.line_offset()` of `buffer.iter_at_mark(&buffer.get_insert())`).

## 4. Unicode offsets

Three units meet in a text editor, and mixing them is the classic off-by-many bug with emoji and
non-Latin text:

| Unit | Who uses it |
| --- | --- |
| **Characters** (Unicode scalar values) | GTK: `offset()`, `line_offset()`, `iter_at_offset()`, `char_count()`, `cursor-position` |
| **Bytes** (UTF-8) | Rust `str` indexing, `match_indices`, regex; GTK `line_index()` / `iter_at_line_index()` |
| **Graphemes** (what the user sees as one character) | cursor movement and deletion in the view, which GTK handles itself |

In `"a😀b"`, `b` is at char offset 2 and byte index 5. In `"e\u{301}"` (é written as e plus a
combining accent) there are two chars but one grapheme. Convert explicitly at the boundary:

```rust
/// Byte index in `text` of the character at GTK char offset `char_offset`.
fn byte_index(text: &str, char_offset: usize) -> usize {
    text.char_indices()
        .nth(char_offset)
        .map_or(text.len(), |(index, _)| index)
}

/// GTK char offset of the byte index `byte_index` (must be on a char boundary).
fn char_offset(text: &str, byte_index: usize) -> usize {
    text[..byte_index].chars().count()
}
```

- GTK offsets are `i32`. Convert with `i32::try_from(..)` rather than `as`, which truncates.
- Both conversions are O(n). For many positions, convert per line (line start offset + offset
  within the line), as the search in §7 does.
- Byte offsets from a regex or a parser must be converted before `iter_at_offset`. Passing them
  directly highlights the wrong text as soon as the line contains a non-ASCII character.
- Count graphemes (the `unicode-segmentation` crate) only for user-facing limits such as "140
  characters left".

## 5. Tags

Tags style ranges (`weight`, `underline`, `background-rgba`, `foreground-rgba`, `invisible`,
`editable`…) and mark them for later lookup.

```rust
let tag = gtk::TextTag::new(Some("search-match"));
let mut color = adw::StyleManager::default().accent_color_rgba();
color.set_alpha(0.3);
tag.set_background_rgba(Some(&color));
buffer.tag_table().add(&tag);

buffer.apply_tag(&tag, &start, &end);
buffer.remove_tag(&tag, &buffer.start_iter(), &buffer.end_iter());
```

- `buffer.create_tag(Some("name"), &[("weight", &700)])` is the shorthand. Tag names are unique
  per tag table; creating the same name twice returns `None`.
- Tags take colors, not CSS. Derive them from libadwaita (`accent_color_rgba()`, adw feature
  `v1_6`) and
  update them on `notify::accent-color-rgba` and `notify::dark`. Hard-coded colors break in dark
  mode and high contrast.
- Applying or removing tags does not emit `changed`, so it does not bump the document revision
  or mark the document modified.

## 6. Undo

`TextBuffer` keeps its own undo history (`enable-undo`, on by default):

- Every edit from typing is undoable. Consecutive typing is merged into one step.
- **Group programmatic edits** with `begin_user_action()` / `end_user_action()`, so "Replace All"
  or "Format" undoes in one step:

  ```rust
  /// Replaces the whole text as one undoable step (unlike `set_text()`).
  pub fn replace_all_undoable(buffer: &gtk::TextBuffer, text: &str) {
      buffer.begin_user_action();
      let (mut start, mut end) = buffer.bounds();
      buffer.delete(&mut start, &mut end);
      buffer.insert(&mut start, text);
      buffer.end_user_action();
  }
  ```

- **Edits that must not be undone** (loading a file, applying a server state) go between
  `begin_irreversible_action()` and `end_irreversible_action()`, which also clears the history.
  `set_text()` already behaves this way. Don't call them inside a user action; GTK warns and
  ignores them.
- `max-undo-levels` bounds memory (0 = unlimited). `can-undo` / `can-redo` drive the sensitivity
  of window-level undo actions.

**Dirty state is not the buffer's `modified` flag.** In GTK 4.22 the flag resets to "unmodified"
when undo returns to the savepoint, but also when undoing *past* it, and when typing merged into
one step across it. `set_modified(false)` always marks the *current* state. Compare the content
with what the last successful save wrote instead (`document-apps.md` §1, §3).

**Non-text models** (shapes on a canvas, rows in a project) need their own history. Give every
state an id, so "is this the saved state?" survives undo, redo and undo-then-edit:

```rust
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct StateId(u64);

/// Undo stack for a non-text model. Every state has its own id, so "is this the saved state?"
/// stays correct across undo, redo and undo-then-edit (which drops the redo branch).
#[derive(Debug)]
pub struct History<C> {
    done: Vec<(C, StateId)>, // each command with the id of the state it produced
    undone: Vec<(C, StateId)>,
    next_id: u64,
}

impl<C> History<C> {
    pub fn new() -> Self {
        Self {
            done: Vec::new(),
            undone: Vec::new(),
            next_id: 1,
        }
    }

    /// The id of the current state; `StateId(0)` is the state before any command.
    pub fn current(&self) -> StateId {
        self.done.last().map_or(StateId(0), |(_, id)| *id)
    }

    /// Record a command the caller has just applied to the model.
    pub fn record(&mut self, command: C) {
        self.done.push((command, StateId(self.next_id)));
        self.next_id += 1;
        self.undone.clear();
    }

    /// The command to revert, if any; the caller reverts it on the model.
    pub fn undo(&mut self) -> Option<&C> {
        let entry = self.done.pop()?;
        self.undone.push(entry);
        self.undone.last().map(|(command, _)| command)
    }

    /// The command to re-apply, if any.
    pub fn redo(&mut self) -> Option<&C> {
        let entry = self.undone.pop()?;
        self.done.push(entry);
        self.done.last().map(|(command, _)| command)
    }
}
```

A save records `history.current()`, and dirty is `history.current() != saved`. A stack *depth* is
not enough: record A, save, undo, record B, and the depth matches the saved state while the
content does not.

Keep the history next to the model it changes (a field of the document object), not in a
widget, and expose `can-undo` / `can-redo` as properties so actions bind to them.

## 7. Search

**Small text:** search on the GTK thread with the iter API.

```rust
let found = buffer
    .start_iter()
    .forward_search("world", gtk::TextSearchFlags::CASE_INSENSITIVE, None);
if let Some((start, end)) = found {
    buffer.select_range(&start, &end);
    text_view.scroll_to_iter(&mut start.clone(), 0.1, false, 0.0, 0.0);
}
```

`TextSearchFlags::VISIBLE_ONLY` and `TEXT_ONLY` skip invisible text and embedded objects.
`backward_search` gives "previous match".

**Large text:** scanning megabytes on every keystroke freezes the UI. Move the scan off the main
thread without moving GTK objects there:

1. **Snapshot on the GTK thread.** Copy the text into a `String`. `TextBuffer` and `TextIter` are
   `!Send` and never leave it.
2. **Search the plain string in `gio::spawn_blocking`** and return char offsets (GTK's unit; see
   §4), capped at a maximum number of matches.
3. **Cancel cooperatively.** Aborting or dropping the awaiting future does *not* stop a
   `spawn_blocking` closure, which keeps scanning to the end. Pass it an `Arc<AtomicBool>` and
   check it once per line or chunk.
4. **Check a generation before every batch.** Results come back after the user may have edited or
   typed a new query. Any edit or new query bumps a generation counter. Highlights are applied in
   batches with a yield in between, and **each** batch re-checks the generation, so an edit
   halfway through stops the rest.

```rust
use std::{
    cell::{Cell, RefCell},
    rc::Rc,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};

use gtk::{gio, glib, prelude::*};

const MAX_MATCHES: usize = 10_000;
const BATCH: usize = 500;

/// Runs on a worker thread over a plain copy of the text. Returns match start offsets in chars
/// (GTK's unit), or `None` when cancelled.
fn find_matches(text: &str, query: &str, cancel: &AtomicBool) -> Option<Vec<usize>> {
    let mut matches = Vec::new();
    let mut line_start = 0; // chars before the current line
    for line in text.split_inclusive('\n') {
        // Aborting the awaiting future does not stop this closure: check the flag regularly.
        if cancel.load(Ordering::Relaxed) {
            return None;
        }
        let (mut chars, mut last) = (0, 0);
        for (byte, _) in line.match_indices(query) {
            chars += line[last..byte].chars().count();
            last = byte;
            matches.push(line_start + chars);
            if matches.len() == MAX_MATCHES {
                return Some(matches);
            }
        }
        line_start += line.chars().count();
    }
    Some(matches)
}

/// Highlights all matches of a query. Any edit or a new query makes running work stale.
#[derive(Debug)]
pub struct SearchHighlighter {
    buffer: gtk::TextBuffer,
    tag: gtk::TextTag,
    generation: Cell<u64>,
    cancel: RefCell<Arc<AtomicBool>>,
}

impl SearchHighlighter {
    pub fn new(buffer: &gtk::TextBuffer) -> Rc<Self> {
        let tag = gtk::TextTag::new(Some("search-match"));
        let mut color = adw::StyleManager::default().accent_color_rgba();
        color.set_alpha(0.3);
        tag.set_background_rgba(Some(&color));
        buffer.tag_table().add(&tag);

        let this = Rc::new(Self {
            buffer: buffer.clone(),
            tag,
            generation: Cell::new(0),
            cancel: RefCell::new(Arc::new(AtomicBool::new(false))),
        });
        buffer.connect_changed(glib::clone!(
            #[weak]
            this,
            move |_| {
                this.invalidate();
            }
        ));
        this
    }

    /// Stops the running worker, makes pending batches stale and removes old highlights.
    fn invalidate(&self) -> u64 {
        self.cancel.borrow().store(true, Ordering::Relaxed);
        let generation = self.generation.get() + 1;
        self.generation.set(generation);
        let (start, end) = self.buffer.bounds();
        self.buffer.remove_tag(&self.tag, &start, &end);
        generation
    }

    pub fn search(self: &Rc<Self>, query: &str) {
        let generation = self.invalidate();
        if query.is_empty() {
            return;
        }
        let cancel = Arc::new(AtomicBool::new(false));
        self.cancel.replace(cancel.clone());
        // Snapshot on the GTK thread: TextBuffer and TextIter never leave it.
        let (start, end) = self.buffer.bounds();
        let text = self.buffer.text(&start, &end, true).to_string();
        let query = query.to_owned();
        let Ok(query_chars) = i32::try_from(query.chars().count()) else {
            return;
        };
        let this = Rc::downgrade(self);
        glib::spawn_future_local(async move {
            let found = gio::spawn_blocking(move || find_matches(&text, &query, &cancel)).await;
            let Ok(Some(matches)) = found else {
                return; // cancelled, or the worker panicked
            };
            for batch in matches.chunks(BATCH) {
                let Some(this) = this.upgrade() else {
                    return;
                };
                // Checked before every batch, not only the first one.
                if this.generation.get() != generation {
                    return;
                }
                for &offset in batch {
                    let Ok(offset) = i32::try_from(offset) else {
                        return;
                    };
                    let start = this.buffer.iter_at_offset(offset);
                    let end = this.buffer.iter_at_offset(offset + query_chars);
                    this.buffer.apply_tag(&this.tag, &start, &end);
                }
                drop(this);
                // Let input and drawing run between batches.
                glib::timeout_future(Duration::ZERO).await;
            }
        });
    }
}
```

- This version matches case-sensitively. For case-insensitive or regex search, run the `regex`
  crate in the worker; it returns byte ranges, so convert per line as above.
- When the search is tied to a document revision (`document-apps.md`), the generation can be the
  pair (query generation, document revision). The rule is the same: a stale result never touches
  the buffer.
- Show "10,000+ matches" when the cap is hit, rather than spending seconds tagging a million
  ranges.

**Search UI:** `gtk::SearchBar` with a `SearchEntry` child, `key-capture-widget` set to the
text view, and `search-mode-enabled` bound to a `win.find` toggle (Ctrl+F). Connect the entry's
`search-changed` (emitted after a short delay, `search-delay`), `activate` (Enter),
`next-match` / `previous-match` (Ctrl+G / Ctrl+Shift+G) and `stop-search` (Escape: close the bar
and return focus to the view).

## 8. When to use GtkSourceView

Plain `TextView` is right for notes, chat composers, comments and forms. Switch to GtkSourceView 5
(the `sourceview5` crate) when the app needs any of:

- syntax highlighting, bracket matching, auto-indent, line numbers, a right margin;
- regex search with match counting (`SearchContext`) done for you;
- style schemes that follow the light/dark style;
- encoding detection, line-ending handling and compressed files (`FileLoader` / `FileSaver`);
- completion, snippets, a minimap;
- good performance on very large files.

```toml
# sourceview5 0.11 pairs with gtk4 0.11. GNOME 50 ships GtkSourceView 5.20; v5_18 is the newest
# feature it satisfies (there is no v5_20 feature).
sourceview5 = { version = "0.11", features = ["v5_18"] }
```

```rust
use gtk::{glib, prelude::*};
use sourceview5::prelude::*;

/// A code view whose colors follow the light/dark style.
fn code_view() -> sourceview5::View {
    let buffer = sourceview5::Buffer::new(None);
    buffer.set_language(sourceview5::LanguageManager::default().language("rust").as_ref());
    buffer.set_highlight_matching_brackets(true);

    let style_manager = adw::StyleManager::default();
    let update_scheme = glib::clone!(
        #[weak]
        buffer,
        move |style_manager: &adw::StyleManager| {
            let id = if style_manager.is_dark() { "Adwaita-dark" } else { "Adwaita" };
            buffer.set_style_scheme(sourceview5::StyleSchemeManager::default().scheme(id).as_ref());
        }
    );
    update_scheme(&style_manager);
    style_manager.connect_dark_notify(update_scheme);

    let view = sourceview5::View::with_buffer(&buffer);
    view.set_show_line_numbers(true);
    view.set_monospace(true);
    view.set_auto_indent(true);
    view
}

/// Regex search with match counting and highlighting done by `GtkSourceView`.
fn search(buffer: &sourceview5::Buffer, pattern: &str) -> sourceview5::SearchContext {
    let settings = sourceview5::SearchSettings::new();
    settings.set_regex_enabled(true);
    settings.set_case_sensitive(false);
    settings.set_search_text(Some(pattern));
    let context = sourceview5::SearchContext::new(buffer, Some(&settings));
    context.set_highlight(true);
    context
}
```

- Call `sourceview5::init()` after GTK is initialized (`startup`) and before a template that uses
  `GtkSource.View` is built, so the types are registered.
- `sourceview5::Buffer` is a `gtk::TextBuffer`. Everything in §1–§7 still applies, including
  "don't trust `modified`".
- Flatpak: the GNOME runtime already ships GtkSourceView 5 (5.20 in GNOME 50), so no extra
  module is needed. Host builds need the `gtksourceview-5` development package (and a
  `dependency('gtksourceview-5')` in Meson).
- The snippets here type-check against the GNOME 50 SDK's `gtksourceview-5` (5.20) with
  sourceview5 0.11.2; behaviour was not run on a host build (verify in your app).

## Pitfalls

- **Highlight lands a few characters off on lines with emoji or accents** → byte offsets from
  `match_indices`/regex passed to `iter_at_offset` → convert bytes to chars per line.
- **"Invalid text buffer iterator" warning or a crash after an edit** → an iter kept across an
  edit or an `.await` → use a `TextMark`.
- **Undo history disappears after loading or "Replace All"** → `set_text()` is irreversible →
  delete + insert inside `begin_user_action()` when the change should be undoable.
- **One Ctrl+Z undoes only half of a programmatic change** → edits not grouped → wrap them in
  `begin_user_action()` / `end_user_action()`.
- **"Cannot begin irreversible action while in user action"** → `set_text()` or
  `begin_irreversible_action()` inside a user action → do irreversible work outside it.
- **Document shows as unsaved after undoing to the saved text, or as saved when it isn't** →
  dirty state taken from `TextBuffer::is_modified()` → compare with what was saved
  (`document-apps.md`).
- **Custom undo reports "clean" after undo-then-edit** → savepoint stored as a stack depth → use
  per-state ids.
- **UI freezes while searching a large file** → iter search on the main thread over megabytes →
  snapshot + `spawn_blocking` + batched highlighting.
- **CPU stays busy after typing a new query** → the old worker was not told to stop → an
  `AtomicBool` checked inside the scan; aborting the future is not enough.
- **Old highlights reappear after an edit** → generation checked only before the first batch →
  check it before every batch.
- **Search highlight invisible in dark mode** → hard-coded tag color → derive from
  `accent_color_rgba()` and update on style changes.
- **TextView grows the window instead of scrolling** → not inside a `ScrolledWindow`.

## See also

`document-apps.md`, `clipboard-and-dnd.md`, `async-and-threads.md`, `libadwaita-ui.md`,
`accessibility-and-i18n.md`, `actions-and-shortcuts.md`, `css-drawing-animation.md`
