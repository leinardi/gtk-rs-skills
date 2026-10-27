# Document apps: open, save, close and recover

Read this when the app opens and saves user files (an editor, a viewer with edits, anything with a
"Save" action), when unsaved changes must survive Ctrl+W, Ctrl+Q, logout or a crash, or when
reviewing such code. The pattern is the whole lifecycle; skipping one part (the close re-entry
guard, the saved-content identity, the draft queue) loses data in a way tests rarely catch.

The code below is one working design for a text document, verified against gtk4 0.11 /
libadwaita 0.9 / glib 0.22. Text-specific parts (offsets, undo, search) are in `text-editing.md`.

## Contents

1. The model: revision vs saved identity
2. Opening files
3. Saving without races
4. Closing with unsaved changes
5. Quitting with several windows
6. Shutdown: hold, inhibit, session end
7. Crash recovery: drafts
8. Testing checklist
9. Pitfalls / See also

## 1. The model: revision vs saved identity

A `Document` GObject owns the content and two separate notions that are easy to conflate:

- **`revision`** is bumped on every edit. It answers one question: "is this snapshot still the
  current content?" Use it for stale results (search, background analysis), never for dirty state.
- **What is on disk** (`saved_text` here) is recorded by every successful load or save. **Dirty =
  the current content differs from it.** With a revision counter, "edit, then undo back" would stay
  dirty forever. With content identity it reads clean, as users expect.

Don't use `gtk::TextBuffer::is_modified()` as the source of truth either. In GTK 4.22 its flag
resets to "unmodified" when undo reaches the savepoint, but it also reads unmodified after undoing
*past* the savepoint, and when typing merged into one undo step across it. `set_modified(false)`
always marks the *current* state, which is wrong when a save raced an edit (§3).

```rust
use std::cell::{Cell, RefCell};

use adw::{prelude::*, subclass::prelude::*};
use gtk::{gio, glib};

use crate::drafts::Drafts;

/// What a save or an autosave writes, captured on the GTK thread.
#[derive(Clone, Debug)]
pub struct Snapshot {
    pub text: String,
    pub revision: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SaveOutcome {
    /// The snapshot reached disk. The document may still be modified if it was edited meanwhile.
    Saved,
    /// Another save to the same file was running; it saves again when it finishes.
    Queued,
    /// A save was running, so Save As did nothing (keep `win.save-as` disabled while saving).
    Busy,
}

mod imp {
    use super::*;

    #[derive(Debug, Default, glib::Properties)]
    #[properties(wrapper_type = super::Document)]
    pub struct Document {
        #[property(get)]
        pub buffer: gtk::TextBuffer,
        #[property(get, nullable)]
        pub file: RefCell<Option<gio::File>>,
        /// Content differs from what is on disk. Derived, never set directly.
        #[property(get)]
        pub modified: Cell<bool>,
        #[property(get)]
        pub saving: Cell<bool>,
        /// Bumped on every edit; only used to tell whether a snapshot is still current.
        pub revision: Cell<u64>,
        /// The text the last successful save (or load) put on disk.
        pub saved_text: RefCell<String>,
        pub saved_chars: Cell<usize>,
        pub etag: RefCell<Option<glib::GString>>,
        pub save_again: Cell<bool>,
        pub load_generation: Cell<u64>,
        pub drafts: Drafts,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for Document {
        const NAME: &'static str = "MyAppDocument";
        type Type = super::Document;
    }

    #[glib::derived_properties]
    impl ObjectImpl for Document {
        fn constructed(&self) {
            self.parent_constructed();
            let obj = self.obj();
            self.buffer.connect_changed(glib::clone!(
                #[weak]
                obj,
                move |_| {
                    let imp = obj.imp();
                    imp.revision.set(imp.revision.get() + 1);
                    obj.update_modified();
                    if obj.modified() {
                        imp.drafts.schedule(&obj); // §7
                    }
                }
            ));
        }
    }
}

glib::wrapper! {
    pub struct Document(ObjectSubclass<imp::Document>);
}

impl Default for Document {
    fn default() -> Self {
        glib::Object::new()
    }
}

impl Document {
    pub fn revision(&self) -> u64 {
        self.imp().revision.get()
    }

    pub fn snapshot(&self) -> Snapshot {
        let buffer = &self.imp().buffer;
        let (start, end) = buffer.bounds();
        Snapshot {
            text: buffer.text(&start, &end, true).into(),
            revision: self.revision(),
        }
    }

    pub fn drafts(&self) -> &Drafts {
        &self.imp().drafts
    }

    /// Dirty = the buffer differs from what is on disk. Comparing content (not a revision
    /// counter, not `TextBuffer::is_modified`) makes "edit, then undo back" read clean.
    fn update_modified(&self) {
        let imp = self.imp();
        let modified = {
            // Cheap check first: different lengths → modified, no copy of the text needed.
            usize::try_from(imp.buffer.char_count()).ok() != Some(imp.saved_chars.get())
                || self.snapshot().text != *imp.saved_text.borrow()
        };
        if imp.modified.replace(modified) != modified {
            if !modified {
                // Back to what is on disk (saved, or undone to it): the draft is obsolete.
                imp.drafts.clear();
            }
            self.notify_modified();
        }
    }

    fn set_saved_text(&self, text: String) {
        let imp = self.imp();
        imp.saved_chars.set(text.chars().count());
        imp.saved_text.replace(text);
    }

    fn set_saving(&self, saving: bool) {
        if self.imp().saving.replace(saving) != saving {
            self.notify_saving();
        }
    }
}
```

- The window binds its title to `modified` and `file` (`• name` or an `adw::WindowTitle`
  subtitle), and keeps `win.save-as` disabled while `saving`.
- Keeping a copy of the saved text doubles memory and costs a comparison per edit once lengths
  match. That is fine for documents people type. For very large files, compare a digest of the
  saved bytes, debounced, or use GtkSourceView's file handling (`text-editing.md`).
- **Non-text models** (a canvas, a project file) get the same separation from a branch-aware undo
  stack. Every state gets a unique id: a monotonic counter assigned when a command is applied, and
  undo/redo move to the stored id of that state. A new edit after undo gets a new id and drops the
  redo branch. A save records the id of the state it wrote, and dirty = `current_id != saved_id`.
  A cursor *index* is not identity: undo-then-edit reuses indexes for different content.

## 2. Opening files

```rust
impl Document {
    /// Loads `file`. Returns `Ok(false)` when a newer load started meanwhile (stale result).
    pub async fn load(&self, file: &gio::File) -> Result<bool, glib::Error> {
        let imp = self.imp();
        let generation = imp.load_generation.get() + 1;
        imp.load_generation.set(generation);

        let (bytes, etag) = file.load_contents_future().await?;
        if imp.load_generation.get() != generation {
            return Ok(false); // a newer open won
        }
        let text = String::from_utf8(bytes.to_vec()).map_err(|_| {
            glib::Error::new(gio::IOErrorEnum::InvalidData, "The file is not valid UTF-8")
        })?;

        self.set_saved_text(text.clone());
        imp.etag.replace(etag);
        // set_text() is irreversible: loading clears the undo history.
        imp.buffer.set_text(&text);
        imp.file.replace(Some(file.clone()));
        self.notify_file();
        self.update_modified();
        Ok(true)
    }
}
```

The window decides *where* a file opens. An empty, untouched document is reused; anything else gets
a new window, so opening never replaces unsaved work:

```rust
async fn open_document(&self) {
    let dialog = gtk::FileDialog::builder()
        .title(gettext("Open Document"))
        .modal(true)
        .build();
    match dialog.open_future(Some(self)).await {
        Ok(file) => self.open_file(&file).await,
        Err(err) if err.matches(gtk::DialogError::Dismissed)
            || err.matches(gtk::DialogError::Cancelled) => {}
        Err(err) => tracing::warn!("Open dialog failed: {err}"),
    }
}

/// Loads `file` here if this window holds an empty, untouched document, else in a new window.
pub async fn open_file(&self, file: &gio::File) {
    let document = self.document();
    let window = if document.file().is_none() && !document.modified() {
        self.clone()
    } else {
        let Some(app) = self.application().and_downcast::<Application>() else {
            return;
        };
        let window = Window::new(&app);
        window.present();
        window
    };
    match window.document().load(file).await {
        Ok(true) => {}
        Ok(false) => tracing::debug!("A newer open replaced this one"),
        Err(err) => window.show_error(&gettext("Could Not Open the File"), &err),
    }
}
```

- **Stale opens.** Two quick opens into the same document race. The load generation makes the
  older result a no-op, whatever order the reads finish in. `install_action_async` hands the
  future a strong window reference, so a load still finishes after its window closed; it only
  fills that window's document, which is then dropped.
- **Files from the command line and the file manager.** Set `ApplicationFlags::HANDLES_OPEN` and
  override `open`. The desktop file needs `MimeType=` and `Exec=… %U`.

  ```rust
  // Application::new(): .property("flags", gio::ApplicationFlags::HANDLES_OPEN)
  impl ApplicationImpl for Application {
      fn open(&self, files: &[gio::File], _hint: &str) {
          let app = self.obj();
          for file in files {
              let window = Window::new(&app);
              window.present();
              glib::spawn_future_local(glib::clone!(
                  #[strong]
                  file,
                  async move { window.open_file(&file).await }
              ));
          }
      }
  }
  ```

- In a Flatpak, files from the dialog, a drop or `open` arrive through the document portal. Keep
  the `gio::File` you were given; don't rebuild it from a path string (`platform-integration.md`).

## 3. Saving without races

```rust
impl Document {
    /// Saves to the current file. The caller asks for a file first (Save As) when there is none.
    pub async fn save(&self) -> Result<SaveOutcome, glib::Error> {
        let file = self.file().expect("save() needs a file; use save_as()");
        self.write(&file).await
    }

    /// Saves to `file` and makes it the document's file, but only once the write succeeded.
    pub async fn save_as(&self, file: &gio::File) -> Result<SaveOutcome, glib::Error> {
        let imp = self.imp();
        if imp.saving.get() {
            return Ok(SaveOutcome::Busy);
        }
        imp.etag.replace(None); // a different file: no etag to compare
        let outcome = self.write(file).await?;
        imp.file.replace(Some(file.clone()));
        self.notify_file();
        Ok(outcome)
    }

    /// After the user chose to overwrite a file that changed on disk.
    pub fn forget_etag(&self) {
        self.imp().etag.replace(None);
    }

    async fn write(&self, file: &gio::File) -> Result<SaveOutcome, glib::Error> {
        let imp = self.imp();
        if imp.saving.get() {
            imp.save_again.set(true);
            return Ok(SaveOutcome::Queued);
        }
        self.set_saving(true);
        // Keeps the process alive if the last window closes while the write is in flight.
        let _hold = gio::Application::default().map(|app| app.hold());

        let result = loop {
            let snapshot = self.snapshot();
            let etag = imp.etag.borrow().clone(); // no borrow held across the await
            let written = file
                .replace_contents_future(
                    snapshot.text.clone().into_bytes(),
                    etag.as_deref(),
                    false,
                    gio::FileCreateFlags::NONE,
                )
                .await;
            match written {
                Ok((_, new_etag)) => {
                    // Always record what reached disk, even if the buffer moved on meanwhile:
                    // undoing back to the old content must then read as modified.
                    self.set_saved_text(snapshot.text);
                    imp.etag.replace(new_etag);
                    self.update_modified();
                    if imp.save_again.take() && self.modified() {
                        continue;
                    }
                    break Ok(SaveOutcome::Saved);
                }
                Err((_, err)) => {
                    imp.save_again.set(false);
                    break Err(err);
                }
            }
        };
        self.set_saving(false);
        result
    }
}
```

Why it is shaped like this:

- **The snapshot is taken on the GTK thread, before the await.** The user can keep typing while
  the bytes are written.
- **A successful save always records what reached disk.** Take disk A, start saving B, edit to C
  while the write is in flight, then let B land. The document is still modified (C ≠ B). Undo to
  B reads clean, and undo further to A reads **modified**, because disk holds B. Recording only
  when the snapshot is still current would leave A as "saved" and report A as clean.
- **No overlapping saves.** A second save of the same file while one is running sets
  `save_again` instead of starting a parallel write. When the first one lands it saves again if
  the document is still modified. Save As while saving returns `Busy`; keep the action disabled.
- **`replace_contents` is atomic** (temporary file + rename) when it can write the directory.
  If the directory is read-only but the file is writable, GIO writes the file in place.
- **The etag detects external changes.** A `gio::IOErrorEnum::WrongEtag` error means another
  program changed the file since it was loaded or saved. Ask, then `forget_etag()` and retry to
  overwrite. `make_backup: true` keeps a `file~` copy if the app wants one.
- **Save As changes `file` only after success.** A failed or cancelled Save As leaves the file,
  the etag and the saved text untouched.

The window's save helper shows errors and stays silent on cancellation:

```rust
/// Saves, asking for a file first when the document has none. Returns whether the content
/// reached disk. Cancelling the file dialog returns `false` silently; errors are shown.
async fn save_document(&self) -> bool {
    if self.document().file().is_none() {
        return match self.choose_save_file().await {
            Some(file) => self.save_document_as(&file).await,
            None => false,
        };
    }
    loop {
        match self.document().save().await {
            Ok(outcome) => return outcome == SaveOutcome::Saved,
            Err(err) if err.matches(gio::IOErrorEnum::WrongEtag) => {
                if !self.confirm_overwrite().await {
                    return false;
                }
                self.document().forget_etag();
            }
            Err(err) => {
                self.show_error(&gettext("Could Not Save the Document"), &err);
                return false;
            }
        }
    }
}

async fn save_document_as(&self, file: &gio::File) -> bool {
    match self.document().save_as(file).await {
        Ok(outcome) => outcome == SaveOutcome::Saved,
        Err(err) => {
            self.show_error(&gettext("Could Not Save the Document"), &err);
            false
        }
    }
}

async fn choose_save_file(&self) -> Option<gio::File> {
    let dialog = gtk::FileDialog::builder()
        .title(gettext("Save Document"))
        .modal(true)
        .initial_name(gettext("Untitled Document.txt"))
        .build();
    if let Some(file) = self.document().file() {
        dialog.set_initial_file(Some(&file));
    }
    match dialog.save_future(Some(self)).await {
        Ok(file) => Some(file),
        Err(err) => {
            if !err.matches(gtk::DialogError::Dismissed) && !err.matches(gtk::DialogError::Cancelled) {
                tracing::warn!("Save dialog failed: {err}");
            }
            None
        }
    }
}

fn show_error(&self, heading: &str, err: &glib::Error) {
    let dialog = adw::AlertDialog::new(Some(heading), Some(err.message()));
    dialog.add_response("close", &gettext("_Close"));
    dialog.present(Some(self));
}
```

Register the actions with `klass.install_action_async("win.save", None, |window, _, _| async move
{ window.save_document().await; })` (likewise `win.open` and `win.save-as`) and disable
`win.save-as` while saving: `document.connect_saving_notify(… window.action_set_enabled("win.save-as",
!document.saving()))`.

## 4. Closing with unsaved changes

`close_request` must answer synchronously, but the question ("Save changes?") is asynchronous. So
it returns `Stop`, runs the dialog in a future, and calls `close()` again once the answer allows it.
Without a guard, a second Ctrl+W (or Ctrl+Q, or a click on the close button) while the dialog is up
opens a second dialog and can start a second save. Track one close flow per window:

```rust
/// One close flow per window: a second Ctrl+W while the dialog, a save or the draft cleanup is
/// running must not start another one.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum CloseFlow {
    #[default]
    Idle,
    Confirming,
    Saving,
    CleaningUp,
    Approved,
}

// In mod imp: `pub document: Document, pub close_flow: Cell<CloseFlow>` on the window struct.
impl WindowImpl for Window {
    fn close_request(&self) -> glib::Propagation {
        match self.close_flow.get() {
            CloseFlow::Approved => {}
            CloseFlow::Idle if !self.document.modified() => {}
            CloseFlow::Idle => {
                self.close_flow.set(CloseFlow::Confirming);
                let obj = self.obj();
                glib::spawn_future_local(glib::clone!(
                    #[weak]
                    obj,
                    async move { obj.confirm_close().await }
                ));
                return glib::Propagation::Stop;
            }
            // A dialog, a save or the draft cleanup is already running.
            CloseFlow::Confirming | CloseFlow::Saving | CloseFlow::CleaningUp => {
                return glib::Propagation::Stop;
            }
        }
        if let Err(err) = self.obj().save_window_state() {
            tracing::warn!("Failed to save the window state: {err}");
        }
        self.parent_close_request()
    }
}
```

```rust
async fn confirm_close(&self) {
    let imp = self.imp();
    let next = match self.ask_save_changes().await.as_str() {
        "save" => {
            imp.close_flow.set(CloseFlow::Saving);
            // Close only if the save went through and nothing was typed meanwhile.
            if self.save_document().await && !self.document().modified() {
                imp.close_flow.set(CloseFlow::CleaningUp);
                self.document().drafts().discard().await;
                CloseFlow::Approved
            } else {
                CloseFlow::Idle
            }
        }
        "discard" => {
            imp.close_flow.set(CloseFlow::CleaningUp);
            self.document().drafts().discard().await;
            CloseFlow::Approved
        }
        _ => CloseFlow::Idle, // Cancel or Escape
    };
    imp.close_flow.set(next);
    if next == CloseFlow::Approved {
        self.close();
    }
}

async fn ask_save_changes(&self) -> glib::GString {
    let dialog = adw::AlertDialog::builder()
        .heading(gettext("Save Changes?"))
        .body(gettext("Unsaved changes will be lost permanently if you don’t save them."))
        .close_response("cancel")
        .default_response("save")
        .build();
    dialog.add_responses(&[
        ("cancel", &gettext("_Cancel")),
        ("discard", &gettext("_Discard")),
        ("save", &gettext("_Save")),
    ]);
    dialog.set_response_appearance("discard", adw::ResponseAppearance::Destructive);
    dialog.set_response_appearance("save", adw::ResponseAppearance::Suggested);
    dialog.choose_future(Some(self)).await
}
```

Every branch ends in a defined state:

| Answer | Result |
| --- | --- |
| Cancel / Escape | `Idle`, window stays open |
| Discard | draft deleted and **awaited**, then `Approved` and `close()` |
| Save, success, document clean | draft deleted and awaited, then `close()` |
| Save, failure | error shown, `Idle`, window open, draft kept |
| Save, Save As cancelled | silent, `Idle`, window open |
| Save, edited while saving | `Idle`, window open (still modified) |

For in-window dialogs with unsaved input (`adw::Dialog`), the equivalent is `can-close: false`
plus the `close-attempt` signal and `force_close()` once confirmed (`libadwaita-ui.md`).

## 5. Quitting with several windows

`app.quit` closes every window and does **not** call `app.quit()` (the template does this). Each
window runs its own guarded close flow, and the app exits when the last window is gone. A window
that vetoes keeps the app alive. Calling `quit()` after closing the windows would end the process
while the "Save changes?" dialog is still up (`architecture.md` §4).

An app that prefers one dialog ("3 documents have unsaved changes") runs the same idea at app
level: an app-wide `Cell<bool>` guard so a second Ctrl+Q does nothing while it is open, then each
window's save or discard, then `close()` on the windows that approved.

## 6. Shutdown: hold, inhibit, session end

- **Hold the application while I/O is pending.** `gio::Application::hold()` returns an
  `ApplicationHoldGuard`; the app cannot exit until it is dropped. §3 holds it for the whole save,
  and the draft queue (§7) holds it per queued operation. Otherwise closing the last window ends
  the process mid-write.
- **Delay logout while modified.** `gtk::Application::inhibit(Some(window),
  gtk::ApplicationInhibitFlags::LOGOUT, Some(&reason))` returns a cookie; call `uninhibit(cookie)`
  when the document is clean again. The session may still force the logout, so this complements
  drafts and doesn't replace them.

  ```rust
  fn update_inhibit(&self) {
      let Some(app) = self.application() else {
          return;
      };
      let imp = self.imp();
      match (self.document().modified(), imp.inhibit_cookie.get()) {
          (true, None) => {
              let cookie = app.inhibit(
                  Some(self),
                  gtk::ApplicationInhibitFlags::LOGOUT,
                  Some(&gettext("There are unsaved changes")),
              );
              imp.inhibit_cookie.set(Some(cookie));
          }
          (false, Some(cookie)) => {
              app.uninhibit(cookie);
              imp.inhibit_cookie.set(None);
          }
          _ => {}
      }
  }
  ```

- **Session end.** `gtk::Application` emits `query-end` (`connect_query_end`) when the session is
  about to end; inhibiting there gives the app time to flush state. In GTK 4.22 the
  `register-session` property is deprecated and ignored: GTK always registers with the session
  manager.

## 7. Crash recovery: drafts

**Durability rule:** an unsaved draft is the user's work. Store it under
`glib::user_data_dir()/<app-id>/drafts/`, never under the cache dir, which may be wiped, or the
state dir. Disposable state (scroll position, last folder) goes to GSettings or
`glib::user_state_dir()`.

Every draft write and delete for a document goes through **one queue**:

- **Order.** A delete queued after a write runs after that write finished, so a write already in
  flight cannot recreate a discarded draft.
- **Epoch.** Discard and a successful save bump the epoch and cancel the pending debounce. A write
  queued before that is dropped before it touches disk.
- **Drain before exit.** Each queued operation holds the application, and `discard()` awaits an
  acknowledgement. The close flow approves only after the draft is gone, and closing the last
  window cannot end the process with a delete still queued.
- **Retention.** Delete a draft when the document becomes clean (a successful save, or undo back
  to the saved content: `clear()` in `update_modified`, §1) or on an explicit Discard. A failed or
  cancelled save keeps it.

```rust
use std::{
    cell::{Cell, OnceCell, RefCell},
    path::PathBuf,
    rc::Rc,
    time::Duration,
};

use gtk::{gio, glib, prelude::*};

use crate::{config, document::Document};

const AUTOSAVE_DELAY: Duration = Duration::from_secs(2);

enum Op {
    Write {
        epoch: u64,
        uri: Option<glib::GString>,
        text: String,
    },
    Delete {
        reply: Option<async_channel::Sender<()>>,
    },
}

struct Queued {
    op: Op,
    // Each queued operation holds the application, so closing the last window cannot end
    // the process before the queue has drained.
    _hold: Option<gio::ApplicationHoldGuard>,
}

/// Crash-recovery drafts for one document. Every write and delete goes through one queue, so
/// they run in order, and a write queued before a discard or a save is dropped (`epoch`).
#[derive(Debug, Default)]
pub struct Drafts {
    file: OnceCell<gio::File>,
    epoch: Rc<Cell<u64>>,
    debounce: RefCell<Option<glib::SourceId>>,
    sender: OnceCell<async_channel::Sender<Queued>>,
}

/// Unsaved work is user data: keep it in the data dir, never in the cache or state dirs.
pub fn drafts_dir() -> PathBuf {
    glib::user_data_dir().join(config::APP_ID).join("drafts")
}

impl Drafts {
    /// Continue with a draft found at startup, so saving or discarding removes it.
    pub fn adopt(&self, file: gio::File) {
        let _ = self.file.set(file);
    }

    fn file(&self) -> &gio::File {
        self.file.get_or_init(|| {
            gio::File::for_path(drafts_dir().join(format!("{}.draft", glib::uuid_string_random())))
        })
    }

    /// Writes a draft once the user pauses typing.
    pub fn schedule(&self, document: &Document) {
        self.cancel_pending();
        let id = glib::timeout_add_local_once(
            AUTOSAVE_DELAY,
            glib::clone!(
                #[weak]
                document,
                move || {
                    let drafts = document.drafts();
                    // The source has fired; removing it again would panic.
                    drafts.debounce.take();
                    drafts.enqueue(Op::Write {
                        epoch: drafts.epoch.get(),
                        uri: document.file().map(|file| file.uri()),
                        text: document.snapshot().text,
                    });
                }
            ),
        );
        self.debounce.replace(Some(id));
    }

    /// The document was saved: drop the draft. The queue's hold keeps the app alive meanwhile.
    pub fn clear(&self) {
        self.invalidate();
        self.enqueue(Op::Delete { reply: None });
    }

    /// The user discarded the changes: drop the draft and wait until it is gone from disk.
    pub async fn discard(&self) {
        self.invalidate();
        let (reply, done) = async_channel::bounded(1);
        self.enqueue(Op::Delete { reply: Some(reply) });
        let _ = done.recv().await;
    }

    fn cancel_pending(&self) {
        if let Some(id) = self.debounce.take() {
            id.remove();
        }
    }

    /// Cancels the pending debounce and makes every queued write stale.
    fn invalidate(&self) {
        self.cancel_pending();
        self.epoch.set(self.epoch.get() + 1);
    }

    fn enqueue(&self, op: Op) {
        let hold = gio::Application::default().map(|app| app.hold());
        let queued = Queued { op, _hold: hold };
        // Unbounded and the receiver lives as long as the sender: this cannot fail.
        let _ = self.sender().try_send(queued);
    }

    fn sender(&self) -> &async_channel::Sender<Queued> {
        self.sender.get_or_init(|| {
            let (sender, receiver) = async_channel::unbounded::<Queued>();
            let epoch = self.epoch.clone();
            let file = self.file().clone();
            glib::spawn_future_local(async move {
                while let Ok(Queued { op, _hold }) = receiver.recv().await {
                    match op {
                        // Stale: discarded or saved after this write was queued.
                        Op::Write { epoch: queued, .. } if queued != epoch.get() => {}
                        Op::Write { uri, text, .. } => {
                            if let Err(err) = write_draft(&file, uri.as_deref(), &text).await {
                                tracing::warn!("Failed to write a recovery draft: {err}");
                            }
                        }
                        Op::Delete { reply } => {
                            match file.delete_future(glib::Priority::DEFAULT).await {
                                Err(err) if !err.matches(gio::IOErrorEnum::NotFound) => {
                                    tracing::warn!("Failed to delete a recovery draft: {err}");
                                }
                                _ => {}
                            }
                            if let Some(reply) = reply {
                                let _ = reply.send(()).await;
                            }
                        }
                    }
                    // `_hold` is dropped here, after the operation finished.
                }
            });
            sender
        })
    }
}

async fn write_draft(file: &gio::File, uri: Option<&str>, text: &str) -> Result<(), glib::Error> {
    let dir = drafts_dir();
    gio::spawn_blocking(move || std::fs::create_dir_all(dir))
        .await
        .expect("create_dir_all does not panic")
        .map_err(|err| glib::Error::new(gio::IOErrorEnum::Failed, &err.to_string()))?;
    let key_file = glib::KeyFile::new();
    key_file.set_string("draft", "uri", uri.unwrap_or_default());
    key_file.set_string("draft", "text", text);
    // replace_contents writes a temporary file and renames it: a crash never leaves half a draft.
    file.replace_contents_future(
        key_file.to_data().as_bytes().to_vec(),
        None,
        false,
        gio::FileCreateFlags::PRIVATE,
    )
    .await
    .map_err(|(_, err)| err)?;
    Ok(())
}
```

`async-channel` (`async-channel = "2"`) is the channel `async-and-threads.md` already uses. The
`timeout_add_local_once` closure takes its own `SourceId` out of the cell, because
`SourceId::remove()` panics on a source that has already fired.

**Restoring at startup.** List the drafts off the main thread, then for each one open the original
file (if any) in a window, `adopt` the draft file and put the text in as an undoable edit, so the
document is modified and Save or Discard removes the draft:

```rust
pub struct FoundDraft {
    pub path: PathBuf,
    pub uri: Option<String>,
    pub text: String,
}

/// Drafts left behind by a crash. Call at startup and offer to restore them.
pub async fn find_drafts() -> Vec<FoundDraft> {
    gio::spawn_blocking(|| {
        let Ok(entries) = std::fs::read_dir(drafts_dir()) else {
            return Vec::new(); // no drafts directory: nothing to restore
        };
        entries
            .flatten()
            .filter_map(|entry| {
                let key_file = glib::KeyFile::new();
                key_file
                    .load_from_file(entry.path(), glib::KeyFileFlags::NONE)
                    .ok()?;
                let uri = key_file.string("draft", "uri").ok().filter(|uri| !uri.is_empty());
                Some(FoundDraft {
                    path: entry.path(),
                    uri: uri.map(String::from),
                    text: key_file.string("draft", "text").ok()?.into(),
                })
            })
            .collect()
    })
    .await
    .unwrap_or_default()
}

impl Document {
    /// Puts recovered draft text into the buffer as one undoable edit (unlike `set_text()`).
    pub fn restore_text(&self, text: &str) {
        let buffer = &self.imp().buffer;
        buffer.begin_user_action();
        let (mut start, mut end) = buffer.bounds();
        buffer.delete(&mut start, &mut end);
        buffer.insert(&mut start, text);
        buffer.end_user_action();
    }
}
```

The caller does `document.load(&gio::File::for_uri(uri)).await` when the draft has a URI, then
`document.drafts().adopt(gio::File::for_path(&draft.path))` and `document.restore_text(&draft.text)`.
Offer the restore (a banner or a dialog) rather than restoring silently. To notice changes made by
other programs while the document is open, watch the file with `gio::File::monitor_file` and
compare etags.

## 8. Testing checklist

Drive these through the real window. A scripted run under `xvfb-run` can answer the `AlertDialog`
by emitting its `response` signal (`dialog.emit_by_name::<()>("response", &[&"discard"])`) and can
add an artificial delay before the write to widen races.

- Close with changes → Cancel keeps the window; Discard closes; Save closes after writing.
- Ctrl+W pressed three times while the dialog is up shows **one** dialog.
- Save to a read-only file fails: the error is shown, the window stays open, the flow is back to
  `Idle`, and the draft is still on disk.
- Save As cancelled from the close dialog: the window stays open and nothing is shown.
- Edit, then undo back to the saved content: clean (no `•`, no close prompt).
- Save race: disk A, slow save of B, edit to C, B lands → modified; undo to B → clean; undo to A →
  **modified**.
- Two saves started together: one writes, the other returns `Queued`, and the final file has the
  latest text.
- Two quick opens into one document apply only the second.
- Ctrl+Q with a modified window: the app keeps running until the dialog is answered.
- Discard while an autosave write is in flight and the last window closes: the process stays
  alive until the delete is acknowledged, and the next start finds no draft.
- Kill the app after an autosave: the next start finds the draft and restores it.
- Autosave, then undo back to the saved content: the draft is deleted (no restore offer for a
  clean document on the next start).

## Pitfalls

- **Unsaved changes lost on Ctrl+Q** → `app.quit` calls `quit()` after closing the windows → only
  close the windows; the app exits when the last one goes.
- **Two "Save changes?" dialogs, or two saves, from repeated Ctrl+W** → no re-entry guard → one
  `CloseFlow` per window; `close_request` returns `Stop` while a flow runs.
- **Document reads clean although the file on disk differs** → dirty derived from
  `TextBuffer::is_modified()` or `set_modified(false)` after a save that raced an edit → compare
  content with what the last successful save wrote.
- **Edit, then undo back, still shows `•`** → dirty derived from a revision counter → compare
  content (or an undo state id), and use the revision only for stale I/O.
- **Typing during a save is silently marked saved** → the save clears `modified` by assignment →
  recompute from the saved snapshot.
- **The file is overwritten with older content** → overlapping saves of one document → coalesce
  with a `save_again` flag.
- **Save As error leaves the title showing the new name** → `file` set before the write →
  set it after success.
- **A discarded draft reappears after restart** → delete raced an in-flight autosave, or the
  process exited before the delete ran → one queue per document, an epoch, an acknowledged
  delete, and a hold guard per queued operation.
- **Restore offered for a document that was closed clean** → the draft was deleted only on save,
  not when undo returned to the saved content → clear the draft whenever the document becomes
  clean.
- **Drafts vanish after a cache cleanup** → drafts stored under the cache dir → use
  `user_data_dir()`.
- **Opening a file replaces unsaved work** → open into the current window unconditionally →
  reuse only an empty, untouched document.
- **Loading a file adds an undo step back to the empty document** → text inserted with
  `insert` → `set_text()` (irreversible) or `begin_irreversible_action()`.
- **"Failed to remove source" panic** → `SourceId::remove()` on a timeout that already fired →
  take the id out of its cell inside the callback.
- **`RefCell` already borrowed panic during save** → borrow of the etag or text held across
  `.await` → clone out first.

## See also

`text-editing.md`, `async-and-threads.md`, `architecture.md`, `platform-integration.md`,
`settings-and-state.md`, `libadwaita-ui.md`, `actions-and-shortcuts.md`, `clipboard-and-dnd.md`
