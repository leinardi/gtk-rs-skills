# Accessibility and internationalization

Read this when making widgets usable with a screen reader or keyboard, or when adding, extracting or formatting
translatable text (gettext-rs, Blueprint `_()`, `po/`).

Target: GTK 4.22 (`gtk` 0.11), libadwaita 1.9 (`adw` 0.9 with `v1_9` and `gtk_v4_22`), gettext-rs 0.8 with the
`gettext-system` feature. File paths match the scaffold (`data/resources/ui/*.blp`, `src/*.rs`, `po/`).

## Contents

- [Accessibility](#accessibility)
  - [Roles for custom widgets](#roles-for-custom-widgets)
  - [Labels and descriptions](#labels-and-descriptions)
  - [Relations](#relations)
  - [States and announcements](#states-and-announcements)
  - [Keyboard and focus](#keyboard-and-focus)
  - [Testing](#testing)
- [Internationalization](#internationalization)
  - [Initialization](#initialization)
  - [Marking strings in Rust](#marking-strings-in-rust)
  - [Named placeholders: gettext_f](#named-placeholders-gettext_f)
  - [Blueprint strings](#blueprint-strings)
  - [Translator comments](#translator-comments)
  - [po/ layout and workflow](#po-layout-and-workflow)
  - [Dates, numbers and sizes](#dates-numbers-and-sizes)
  - [Right-to-left languages](#right-to-left-languages)
- [Pitfalls](#pitfalls)

## Accessibility

GTK builds the accessibility tree from widgets. Standard widgets with visible text (buttons with labels, rows with
titles, labels) are already accessible. The work is in icon-only controls, custom widgets, dynamic state, and
keyboard reachability. fractal is the most thorough exemplar in the corpus.

Feature note: in libadwaita-rs 0.9.2, adw widgets only implement `gtk::Accessible` when an adw `gtk_v4_*` feature
of at least `gtk_v4_10` is enabled. Without it, `update_property` and the other calls below don't exist on adw
types. The scaffold enables `gtk_v4_22`.

### Roles for custom widgets

```rust
fn class_init(klass: &mut Self::Class) {
    klass.bind_template();
    klass.set_css_name("app-rating");
    klass.set_accessible_role(gtk::AccessibleRole::Group);
}
```

```blp
$Avatar avatar { accessible-role: presentation; }  // decorative: hidden from screen readers
```

- Set the role once in `class_init`. It can't change after the widget is created. A custom widget derived
  directly from `gtk::Widget` defaults to `generic`, which screen readers mostly skip.
- Pick the role by behavior: `Button`, `ToggleButton`, `Checkbox`, `Slider`, `Meter`, `ProgressBar`, `Img`,
  `List`/`ListItem`, `Group` for a container of related controls, `Presentation` for decoration.
- If the widget behaves like an existing GTK widget, subclass that widget instead. You inherit its role,
  states and keyboard handling for free.

### Labels and descriptions

```blp
Gtk.Button {
  icon-name: "edit-delete-symbolic";
  tooltip-text: _("Delete Track");
  accessibility {
    label: _("Delete Track");
  }
}

Gtk.Image status_icon {
  icon-name: "network-offline-symbolic";
  accessibility {
    label: _("Offline");
    description: _("Changes will sync when you reconnect");
  }
}
```

```rust
// Computed content: update the label whenever the data changes.
let label = gettext_f("Rated {stars} out of 5", &[("stars", &stars.to_string())]);
self.obj().update_property(&[gtk::accessible::Property::Label(&label)]);
```

- Every icon-only button needs a `tooltip-text` for sighted mouse users. GTK falls back to the tooltip when no
  accessible label is set, but an explicit `accessibility { label: ...; }` states the intent and survives
  later tooltip edits (fractal: `src/window.blp`).
- The label names the thing ("Delete Track"). The description adds secondary context. Don't repeat the role
  in the label ("Delete button"), because the screen reader already announces the role.
- Keep labels in sync with dynamic content (timestamps, counters, avatars). fractal updates them in property
  setters (`src/session_view/room_history/event_timestamp.rs`).

### Relations

```blp
Gtk.Label topic_label { label: _("Topic"); }
Gtk.TextView topic_view {
  accessibility {
    labelled-by: [topic_label];
  }
}
```

```rust
entry.update_relation(&[gtk::accessible::Relation::LabelledBy(&[label.upcast_ref()])]);
toggle.update_relation(&[gtk::accessible::Relation::Controls(&[revealer.upcast_ref()])]);
entry.update_relation(&[gtk::accessible::Relation::DescribedBy(&[hint_label.upcast_ref()])]);
```

Use `labelled-by` when a visible label already names a control, so the text isn't duplicated. A
`Gtk.Label { mnemonic-widget: entry; }` sets this up automatically, and libadwaita rows label their own
activatable widget. Use `described-by` for hints and error messages, and `controls` for expanders and toggles.

### States and announcements

```rust
// Keep states in sync with the visuals.
button.update_state(&[gtk::accessible::State::Expanded(Some(is_expanded))]);
entry.update_state(&[gtk::accessible::State::Invalid(gtk::AccessibleInvalidState::True)]);
entry.add_css_class("error"); // never signal errors with color alone; also show error text

// Status changes that don't move focus (GTK 4.14).
status_label.announce(&gettext("Sync complete"), gtk::AccessibleAnnouncementPriority::Medium);
```

`announce` is for events like "download finished" or "connection lost", where nothing on screen gets focus
(podcasts: `widgets/sync_preferences.rs`). Use `High` only for things that must interrupt.

### Keyboard and focus

```rust
fn class_init(klass: &mut Self::Class) {
    klass.set_accessible_role(gtk::AccessibleRole::Button);
    klass.add_binding_signal(gdk::Key::space, gdk::ModifierType::empty(), "activate");
    klass.add_binding_signal(gdk::Key::Return, gdk::ModifierType::empty(), "activate");
    klass.add_binding_signal(gdk::Key::KP_Enter, gdk::ModifierType::empty(), "activate");
}
fn constructed(&self) {
    self.parent_constructed();
    let obj = self.obj();
    obj.set_focusable(true);
    obj.set_focus_on_click(true);
}
```

```css
app-rating:focus-visible { outline: 2px solid var(--accent-color); outline-offset: 2px; }
```

- Every action reachable with the mouse must be reachable with the keyboard: Tab to it, then activate with
  Space/Enter. Prefer real `Gtk.Button`s over gestures on plain widgets.
- Focus visuals use `:focus-visible`, which only shows after keyboard navigation. Don't remove outlines from
  libadwaita widgets in CSS. Give custom focusable widgets a visible outline.
- Move focus deliberately when content changes: override `WidgetImpl::grab_focus` on composite widgets to
  forward to the main control (fractal: `src/session_view/invite.rs`), and focus the first field when a
  `NavigationPage` is `shown` or a dialog opens (`Adw.Dialog`'s `focus-widget`).
- Widget-local shortcuts: `klass.add_binding_action(...)`. Application shortcuts belong in
  `actions-and-shortcuts.md` and must also appear in the shortcuts dialog.
- Every interactive custom widget is a candidate for the `Button`/`ToggleButton` role plus the bindings above.

### Testing

- **Orca:** enable with `Super+Alt+S`, close your eyes, and do the main task with keyboard only. Every control
  should be announced with a sensible name, role and state.
- **GTK inspector** (`Ctrl+Shift+D`, or `GTK_DEBUG=interactive`): select a widget, then open the
  "Accessibility" page of its properties to see role, name, description, states and relations. That's the
  quickest check for missing labels.
- **Accerciser:** browses the AT-SPI tree of the running app. Useful to confirm what assistive tech actually
  receives, including inside Flatpak.
- Check with large text and high contrast (Settings → Accessibility), and at the 360×294 minimum size.

## Internationalization

### Initialization

```rust
// src/main.rs, before creating the Application or loading any UI.
fn init_i18n() {
    // SAFETY: called before any other thread is spawned, so nothing can race the
    // process-global locale.
    #[allow(unsafe_code)]
    unsafe {
        gettextrs::setlocale(gettextrs::LocaleCategory::LcAll, "");
    }
    if let Err(err) = gettextrs::bindtextdomain(config::GETTEXT_PACKAGE, config::LOCALEDIR) {
        tracing::warn!("Unable to bind the text domain: {err}");
    }
    if let Err(err) = gettextrs::textdomain(config::GETTEXT_PACKAGE) {
        tracing::warn!("Unable to switch to the text domain: {err}");
    }
}
```

```toml
gettext-rs = { version = "0.8", features = ["gettext-system"] }
```

Why:

- `setlocale` is `unsafe` in gettext-rs 0.8 because it mutates process-global state that other threads may be
  reading. Calling it first thing in `main` is sound. The scaffold denies `unsafe_code` crate-wide and allows it
  only here, with the SAFETY comment.
- `textdomain` sets the default domain that GtkBuilder uses for Blueprint `_()` strings, so it must run before
  any template is instantiated. `GETTEXT_PACKAGE` and `LOCALEDIR` come from `config.rs` (meson exports them,
  with defaults so `cargo check` works).
- `gettext-system` uses the C library's libintl instead of building GNU gettext from source.

### Marking strings in Rust

```rust
use gettextrs::{gettext, ngettext, npgettext, pgettext};

let title = gettext("Library");
let summary = ngettext("{n} song", "{n} songs", count).replace("{n}", &count.to_string());
// Context separates identical English strings with different meanings.
let verb = pgettext("verb", "Open");
let state = pgettext("file state", "Open");
let items = npgettext("selection", "{n} item selected", "{n} items selected", n);
```

- The `msgid` must be a string literal at the call site. xgettext extracts strings by reading the source, so
  `gettext(&format!(...))` or `gettext(variable)` produces nothing to translate.
- Plurals always go through `ngettext`/`npgettext`, never `if n == 1`. Many languages have more than two
  plural forms. The count parameter is `u32`; convert with `u32::try_from(len).unwrap_or(u32::MAX)`.
- Keep whole sentences together. Don't concatenate translated fragments, because word order differs between
  languages:

  ```rust
  // Wrong: translators can't reorder, and "of" may need a different case.
  let s = gettext("Track") + " " + &n + " " + &gettext("of") + " " + &total;
  // Right: one sentence with named placeholders.
  let s = gettext_f("Track {n} of {total}", &[("n", &n), ("total", &total)]);
  ```

- Translate at display time, not in `static`s initialized before `init_i18n` runs. Strings created too early
  stay in English.

### Named placeholders: gettext_f

```rust
// src/i18n.rs (pattern from fractal: src/i18n.rs)
use gettextrs::{gettext, ngettext};

/// Like `gettext`, but replaces `{name}` placeholders with the given values.
pub fn gettext_f(msgid: &str, args: &[(&str, &str)]) -> String {
    freplace(gettext(msgid), args)
}

/// Like `ngettext`, but replaces `{name}` placeholders with the given values.
pub fn ngettext_f(msgid: &str, msgid_plural: &str, n: u32, args: &[(&str, &str)]) -> String {
    freplace(ngettext(msgid, msgid_plural, n), args)
}

fn freplace(mut s: String, args: &[(&str, &str)]) -> String {
    for (key, value) in args {
        s = s.replace(&format!("{{{key}}}"), value);
    }
    s
}

// Usage
let text = ngettext_f(
    "{user} added {n} song",
    "{user} added {n} songs",
    n,
    &[("user", &name), ("n", &n.to_string())],
);
```

```meson
# po/meson.build: tell xgettext about the wrappers, or their strings are never extracted.
i18n.gettext(
  gettext_package,
  args: ['--keyword=gettext_f', '--keyword=ngettext_f:1,2'],
  preset: 'glib',
)
```

- Named placeholders (`{user}`) can be reordered by translators and explain themselves. Positional `{}` can't
  be reordered.
- Every wrapper needs an xgettext `--keyword`. `:1,2` marks singular and plural arguments. For context wrappers
  use `:1c,2` (`pgettext_f`) or `:1c,2,3`.
- Unit-test the helpers (fractal tests `gettext_f` in `src/i18n.rs`). List `src/i18n.rs` in `po/POTFILES.skip`
  if its doc comments contain keyword-looking examples.

### Blueprint strings

```blp
Gtk.Label { label: _("No Results"); }
Adw.ShortcutsSection { title: C_("shortcut window", "General"); }
Gtk.Button {
  // Translators: Button that removes the selected songs from the queue.
  label: _("_Remove");
  use-underline: true;
}
```

- `_()` marks a translatable string. `C_("context", "...")` adds a msgctxt, which mirrors `pgettext` in Rust.
- GtkBuilder translates these at load time using the default text domain, so nothing else is needed at
  runtime.
- Long strings may span lines inside `_()`. Non-translatable values (icon names, action names, CSS classes)
  must not be wrapped.

### Translator comments

```rust
// Translators: {size} is a file size like "4.2 MB". Shown under the download button.
let label = gettext_f("Download ({size})", &[("size", &size)]);
```

- Put the comment on the line directly above the line containing the call, starting with `Translators:`. Meson's
  `i18n.gettext` passes `--add-comments`, so xgettext copies it into the `.pot` as `#.`. If rustfmt moves the
  call onto a new line, move the comment with it.
- Explain placeholders, context (a button? a title?), and length limits. Ambiguous one-word strings ("Open",
  "Empty") need a comment or a context.
- Give the About dialog `gettext("translator-credits")` with a comment telling translators to put their names
  there (the scaffold's `application.rs` does this).

### po/ layout and workflow

```text
po/
├── LINGUAS          # one language code per line: de, fr, pt_BR ...
├── POTFILES.in      # every source file with translatable strings, sorted
├── POTFILES.skip    # optional: files that look translatable but aren't
└── meson.build      # i18n.gettext(gettext_package, args: [...], preset: 'glib')
```

```text
# po/POTFILES.in (scaffold layout)
data/com.example.App.desktop.in.in
data/com.example.App.gschema.xml.in
data/com.example.App.metainfo.xml.in.in
data/resources/ui/shortcuts-dialog.blp
data/resources/ui/window.blp
src/application.rs
src/i18n.rs
src/main.rs
src/window.rs
```

```sh
meson compile -C _build <gettext_package>-pot        # regenerate po/<gettext_package>.pot
meson compile -C _build <gettext_package>-update-po  # merge the .pot into every .po
LANGUAGE=de meson devenv -C _build <app-binary>       # try a translation
```

- `POTFILES.in` must list `.blp`, `.rs`, the `.desktop.in.in`, the metainfo and the gschema. A new file with
  strings that's missing from the list produces no error, just untranslated text. Add the file in the same
  commit that adds the strings.
- xgettext doesn't know `.blp`, and older versions don't know `.rs`. It falls back to C-style parsing, which
  handles `_()`, `C_()`, `gettext()` and the declared keywords. Read the `-pot` output for warnings after
  adding wrappers. Avoid raw strings (`r"..."`) in translatable calls, because the C parser misreads them.
- The `glib` preset adds `_`, `N_`, `C_:1c,2`, `NC_` and `--from-code=UTF-8`. If `pgettext`/`npgettext` strings
  are missing from the `.pot`, add `--keyword=pgettext:1c,2` and `--keyword=npgettext:1c,2,3` to `args`.
- Desktop and metainfo files are translated at build time by `i18n.merge_file` (see
  `project-layout-and-build.md`). xgettext extracts plain `Name=`, `Comment=` and `Keywords=` keys from the
  desktop file, so no intltool-style `_Name=` prefix is needed (the scaffold's `.desktop.in.in` uses plain keys
  with `# Translators:` comments).
- Adding a language: add the code to `LINGUAS`, then run `-update-po` (or `msginit`) to create `<lang>.po`.
  GNOME apps usually take translations through Damned Lies (l10n.gnome.org). Link "Contribute Translations"
  from the About dialog with `add_link`.
- Don't hand-edit the `.pot`. Commit it or not according to your translation platform's needs.

### Dates, numbers and sizes

```rust
let now = glib::DateTime::now_local()?;
// Locale's preferred formats: %x date, %X time, %c date and time.
let short = now.format("%x")?;

// Better for UI: let translators choose the pattern.
// Translators: Date format for the "last played" label, see
// https://docs.gtk.org/glib/method.DateTime.format.html
let label = now.format(&gettext("%B %-e, %Y"))?;

let size = glib::format_size(bytes); // "4.2 MB" with the locale's decimal separator
```

- `glib::DateTime::format` uses the current locale for month and day names. Translatable format strings let
  each language pick its own order. Add a translator comment linking the format codes.
- Rust's `format!` and `to_string()` always use `.` as decimal separator and no digit grouping. That's fine for
  small integers. For sizes, use `glib::format_size`/`format_size_full`. For durations, build strings with
  `ngettext_f` ("{n} minute"/"{n} minutes"). For general locale-aware number formatting, the standard library
  has nothing, so evaluate a crate before adding one.
- Never parse user-visible, formatted strings back into data. Keep the raw value alongside.

### Right-to-left languages

- GTK mirrors horizontal layouts automatically in RTL locales: `Gtk.Box` order, `start`/`end` packing, header
  bar slots, `halign: start`. Use `start`/`end` (and `[start]`/`[end]` slots), not left/right thinking. Check
  `widget.direction() == gtk::TextDirection::Rtl` only in custom `snapshot`/`size_allocate` code that positions
  things manually.
- Directional icons: the icon theme looks up a `-rtl` variant first in RTL (`go-next-symbolic` →
  `go-next-symbolic-rtl`). Ship `-rtl` versions of your own directional symbolic icons (arrows, "back",
  progress), and don't mirror icons that represent real objects.
- CSS `margin-left`/`padding-right` are physical. Mirror them with the `:dir(rtl)` pseudo-class, or use
  symmetric values.
- Don't build sentences by placing labels side by side. RTL reverses the order, and the result is nonsense.
- Test with an RTL locale (`LANGUAGE=ar`, `he`) when a translation exists, or force direction with
  `GTK_DEBUG=invert-text-dir` (listed by `GTK_DEBUG=help` in GTK 4.22).

## Pitfalls

- A Blueprint or Rust string stays English even though the `.po` is translated → its file isn't in
  `POTFILES.in`, or the string was created before `init_i18n` → add the file, and translate at display time.
- Strings from `gettext_f(...)` are missing in the `.pot` → no `--keyword=gettext_f` in `po/meson.build` → add
  the keyword args.
- A translation renders `{user}` literally → the translator changed the placeholder name, or the code passes a
  different key → keep keys stable and explain them in a `Translators:` comment.
- `error[E0133]: call to unsafe function setlocale` → gettext-rs 0.8 made it `unsafe` → wrap in `unsafe {}` with
  a SAFETY comment and `#[allow(unsafe_code)]` (the scaffold denies `unsafe_code`).
- Screen reader says only "button" → an icon-only button without a tooltip or accessible label → add both.
- `update_property` not found on an adw widget → adw built without a `gtk_v4_*` feature, so no `gtk::Accessible`
  impl → enable `gtk_v4_10` or later.
- A custom widget can't be reached with Tab → not focusable or no key bindings → `set_focusable(true)`,
  binding signals for Space/Enter, a role, and a `:focus-visible` style.
- An error is shown only by turning a field red → color alone isn't accessible → add error text, the
  `Invalid` state and `described-by`.

## See also

- `libadwaita-ui.md`: header bar tooltips, dialogs, HIG capitalization.
- `css-drawing-animation.md`: focus outlines, custom widget drawing.
- `templates-and-blueprint.md`: `accessibility {}` and `styles []` syntax.
- `actions-and-shortcuts.md`: keyboard shortcuts and the shortcuts dialog.
- `project-layout-and-build.md`: `i18n.merge_file`, `config.rs`, the meson layout.
- `packaging-and-ci.md`: translations in Flatpak and CI checks.
