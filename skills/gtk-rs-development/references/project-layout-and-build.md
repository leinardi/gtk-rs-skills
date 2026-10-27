# Project layout and build (Cargo + Meson + GResource + Blueprint)

Read this when creating or restructuring a gtk-rs app's tree, editing `Cargo.toml`/`meson.build`, wiring
resources, Blueprint, GSettings schemas, desktop/metainfo files or icons, or running the app uninstalled.

Contents: 1. Layout · 2. Cargo.toml · 3. Meson ↔ Cargo · 4. Build-time constants · 5. GResource ·
6. Cargo-only alternative · 7. Blueprint · 8. GSettings schema · 9. Desktop file and metainfo · 10. Icons ·
11. po/ · 12. Running uninstalled and the Devel profile · Pitfalls · See also

## 1. Layout

The recommended layout (what the `gtk-rs-new-app` scaffold generates; identity, Authenticator and contrast
use the same shape):

```
Cargo.toml  Cargo.lock  meson.build  meson.options  rustfmt.toml  .editorconfig
build-aux/   <APP_ID>.Devel.json   dist-vendor.sh
data/
  meson.build
  <APP_ID>.desktop.in.in   <APP_ID>.metainfo.xml.in.in   <APP_ID>.gschema.xml.in
  icons/      meson.build  <APP_ID>.svg  <APP_ID>.Devel.svg  <APP_ID>-symbolic.svg
  resources/  meson.build  resources.gresource.xml.in  style.css  ui/*.blp
po/          LINGUAS  POTFILES.in  meson.build
src/         meson.build  main.rs  application.rs  window.rs  config.rs  <feature modules>/
.github/workflows/ci.yml
```

Files under `data/` are named after the **base** app id; Meson renames them to the actual id (`.Devel` in
development builds). `config.rs` is a normal checked-in file, not generated (section 4).

**Colocated Blueprint (large apps).** Once you have dozens of widgets, putting `foo.blp` next to `foo.rs`
makes it obvious which template a widget uses (fractal: `src/session/view/...`, video-trimmer: `src/*.blp`).
The cost is Meson wiring: `custom_target` outputs cannot contain `/`, so nested `.blp` files must be compiled
to flattened names and restored with gresource `alias=` attributes (fractal lists them in
`src/ui-blueprint-resources.in` and generates the gresource XML; see section 7). Stay with
`data/resources/ui/` until that cost is worth paying.

Workspaces are rare: use one for a second binary or a GTK-free library (pika-backup, podcasts).

## 2. Cargo.toml

```toml
[package]
name = "my-app"            # = meson project name = binary name
version = "0.1.0"          # keep equal to project(version:) in meson.build
edition = "2024"
rust-version = "1.92"
publish = false

[dependencies]
gtk = { package = "gtk4", version = "0.11", features = ["gnome_50"] }
adw = { package = "libadwaita", version = "0.9", features = ["v1_9", "gtk_v4_22"] }
gettext-rs = { version = "0.8", features = ["gettext-system"] }
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }

[profile.release]
debug = true          # flatpak-builder splits debuginfo into the .Debug extension
lto = "thin"
codegen-units = 1

[profile.dev.package."*"]
opt-level = 3         # optimise dependencies so Devel builds stay responsive

[lints.rust]
unsafe_code = "deny"  # allow locally with a SAFETY comment (setlocale)

[lints.clippy]
pedantic = { level = "warn", priority = -1 }
new_without_default = "allow"        # GObject wrappers expose new() without Default
module_name_repetitions = "allow"
must_use_candidate = "allow"
```

**Renames.** `package = "gtk4"` → `gtk::`, `package = "libadwaita"` → `adw::`. Reach glib/gio/gdk/pango
through `gtk::{gio, glib, gdk, pango}` instead of separate dependencies; add `glib`/`gio` explicitly only to
enable extra features, and then keep them on the same 0.22 series as gtk4 0.11 and libadwaita 0.9 (mixing
series gives "two different versions of crate glib" type errors).

**Version features.** Bindings expose newer C API only behind features:

| Crate | Features | Meaning |
| --- | --- | --- |
| gtk4 0.11 | `v4_2` … `v4_24` | GTK API added in 4.x (cumulative) |
| gtk4 0.11 | `gnome_42` … `gnome_50` | Matches a GNOME SDK: `gnome_50` = `v4_22` + `gio/v2_88` (+ pango etc.) |
| libadwaita 0.9 | `v1_1` … `v1_9` | libadwaita API added in 1.x |
| libadwaita 0.9 | `gtk_v4_2` … `gtk_v4_24` | Forwards a GTK level to adw's own gtk dependency |

The adw `gtk_v4_*` feature is easy to forget and matters: from `gtk_v4_10` up, adw widget wrappers implement
`gtk::Accessible` (the wrapper in `libadwaita/src/auto/*` is cfg-gated on it). Without it, a11y methods on adw
widgets are missing and upcasts to `gtk::Accessible` fail to compile. Set it to the same GTK level as gtk's
feature (`gtk_v4_22` with `gnome_50`).

Three things must agree; the template comments all three places:

| Where | GNOME 50 values |
| --- | --- |
| `Cargo.toml` features | `gnome_50`, `v1_9`, `gtk_v4_22` |
| `meson.build` checks | `dependency('gtk4', version: '>= 4.22')`, `dependency('libadwaita-1', version: '>= 1.9')`, `dependency('glib-2.0', version: '>= 2.88')` |
| Flatpak manifest | `"runtime-version": "50"` |

A feature above the library you run against compiles and then fails at link or load time with missing
symbols, so never raise a feature without raising the runtime. Meson's `dependency()` calls are presence and
version checks only; cargo links the libraries itself through the `-sys` crates' pkg-config probes.

**gettext-system** links the system/Flatpak libintl instead of building the bundled gettext sources.

**Profiles.** `debug = true` in release costs nothing in Flatpak (symbols go to `<id>.Debug`) and makes
backtraces and `sysprof` useful. **Lints** in `[lints]` apply to cargo, the Meson `cargo-clippy` target and
CI alike; workspaces use `[workspace.lints]` + `[lints] workspace = true` (pika-backup). fractal also allows
`struct_field_names` and `cast_possible_truncation`.

## 3. Meson ↔ Cargo

Meson owns configuration, data files, translations and installation; cargo builds the binary. The template's
`src/meson.build`:

```meson
cargo_target_dir = meson.project_build_root() / 'cargo-target'
cargo_vars = {
  'CARGO_HOME': meson.project_build_root() / 'cargo-home',
  'CARGO_TARGET_DIR': cargo_target_dir,
  'APP_ID': application_id,   # + PROFILE, VERSION, GETTEXT_PACKAGE, LOCALEDIR, PKGDATADIR
}
cargo_env = environment(cargo_vars)

cargo_options = ['--manifest-path', meson.project_source_root() / 'Cargo.toml']
if get_option('profile') == 'default'
  cargo_options += ['--release']
  rust_target = 'release'
else
  rust_target = 'debug'
endif

custom_target('cargo-build',
  build_by_default: true,
  build_always_stale: true,      # Meson cannot see Rust deps; cargo decides what is stale
  output: meson.project_name(),
  console: true,                 # stream cargo output
  install: true, install_dir: bindir,
  depends: resources,            # the gresource bundle exists before the binary runs
  env: cargo_env,
  command: [cargo, 'build', cargo_options,
            '&&', 'cp', cargo_target_dir / rust_target / meson.project_name(), '@OUTPUT@'],
)

run_target('cargo-clippy', env: cargo_env,
  command: [cargo, 'clippy', cargo_options, '--all-targets', '--', '-D', 'warnings'])

test('cargo-test', cargo, args: ['test', cargo_options],
  # Tests use the build-dir schema and never touch the user's real settings.
  env: cargo_vars + {
    'GSETTINGS_SCHEMA_DIR': meson.project_build_root() / 'data',
    'GSETTINGS_BACKEND': 'memory',
  },
  timeout: 600, is_parallel: false)

meson.add_devenv(cargo_env)
```

Why each piece:

- `CARGO_HOME` in the build dir keeps downloads and vendored config per build, which is what Flatpak needs
  (the sandbox has no usable `~/.cargo`) and keeps `meson dist`/CI reproducible.
- `CARGO_TARGET_DIR` per Meson build dir keeps release and Devel builds from thrashing each other.
- `cp` instead of `--artifact-dir`: that cargo flag is still unstable.
- The `env:` kwarg (fractal style) is cleaner than an `env VAR=… cargo` command prefix. Keeping the values
  in a dict (`cargo_vars`) lets the build, clippy, `add_devenv` and the test share identical constants and
  caches, while the test alone adds `GSETTINGS_SCHEMA_DIR` + `GSETTINGS_BACKEND=memory`. Those two must not
  go into `cargo_env`: `meson devenv` exports it, and the running app would stop persisting settings.
- `meson test -C _build` runs `cargo test` plus the `lint` suite (desktop/metainfo/schema validation);
  `ninja -C _build cargo-clippy` lints with the same environment.
- `meson.add_devenv(cargo_env)` means `meson devenv -C _build` gives you a shell where plain `cargo check`
  uses the same caches and constants.

Root `meson.build` order: `subdir('data')` before `subdir('src')`, because `src/` references `resources`.
Declare the project as `'rust'` even though Meson never compiles Rust itself.

## 4. Build-time constants

Template style: Meson exports environment variables, `src/config.rs` reads them with `option_env!` and falls
back to Devel defaults.

```rust
pub const APP_ID: &str = default_env(option_env!("APP_ID"), "com.example.MyApp.Devel");
pub const PROFILE: &str = default_env(option_env!("PROFILE"), "Devel");
pub const VERSION: &str = default_env(option_env!("VERSION"), env!("CARGO_PKG_VERSION"));
pub const PKGDATADIR: &str = default_env(option_env!("PKGDATADIR"), "/usr/share/my-app");
pub const RESOURCE_BASE_PATH: &str = "/com/example/MyApp";   // never has .Devel

pub const fn is_devel() -> bool {
    matches!(PROFILE.as_bytes(), b"Devel")
}

const fn default_env(value: Option<&'static str>, default: &'static str) -> &'static str {
    match value {
        Some(value) => value,
        None => default,
    }
}
```

The alternative is `configure_file(input: 'config.rs.in', …)` with `set_quoted()` values, copied into
`src/` at configure time (Authenticator, contrast, identity; fractal's typed variant makes `PROFILE` an enum).

|  | `option_env!` + defaults (template, Solanum, Loupe) | `config.rs.in` (fractal, Authenticator) |
| --- | --- | --- |
| `cargo check`/rust-analyzer before Meson ran | works | fails: file missing |
| Generated file in the source tree | none | must be gitignored, `#[rustfmt::skip] mod config;`, CI rustfmt job needs a stub `src/config.rs` |
| Rebuild when a value changes | cargo tracks `option_env!` | file timestamp changes |
| Typed values (enums, `concat!`) | strings only (derive helpers like `is_devel()`) | anything Rust accepts |
| Risk | a key forgotten in `cargo_vars` silently uses the default | none |

`option_env!` has one trap: if you add a constant in Rust but not in `cargo_vars`, Meson builds quietly get
the default. Grep both files when adding one.

## 5. GResource

The template builds one bundle with Meson, installs it to `pkgdatadir`, and loads it at runtime:

```meson
# data/resources/meson.build
gresource_conf = configuration_data()
gresource_conf.set('metainfo-file', fs.name(metainfo_file.full_path()))
resources = gnome.compile_resources('resources',
  configure_file(input: 'resources.gresource.xml.in', output: '@BASENAME@',
                 configuration: gresource_conf),
  gresource_bundle: true,
  source_dir: [meson.current_build_dir(), meson.current_source_dir()],
  dependencies: [blueprints, metainfo_file],
  install: true, install_dir: pkgdatadir,
)
```

```xml
<gresource prefix="/com/example/MyApp">
  <file compressed="true" preprocess="xml-stripblanks" alias="ui/window.ui">window.ui</file>
  <file compressed="true" preprocess="xml-stripblanks" alias="shortcuts-dialog.ui">shortcuts-dialog.ui</file>
  <file compressed="true">style.css</file>
  <file compressed="true" preprocess="xml-stripblanks" alias="metainfo.xml">@metainfo-file@</file>
</gresource>
```

```rust
// main.rs (template's load_resources(), abridged), before Application::new()
let path = if std::env::var_os("MESON_DEVENV").is_some() {
    // <build>/src/my-app -> <build>/data/resources/resources.gresource
    std::env::current_exe().unwrap().parent().and_then(|p| p.parent()).unwrap()
        .join("data/resources/resources.gresource")
} else {
    std::path::Path::new(config::PKGDATADIR).join("resources.gresource")
};
gio::resources_register(&gio::Resource::load(&path).expect("Could not load resources"));
```

Rules that make this work:

- **Prefix = base path = `resource-base-path`.** The prefix is the base id as a path, without `.Devel`. The
  application sets `.property("resource-base-path", config::RESOURCE_BASE_PATH)` to the same value. GApplication
  otherwise derives the base path from the application id, so a Devel build would look under
  `/com/example/MyApp/Devel` and libadwaita would silently skip `style.css`, `icons/` and
  `shortcuts-dialog.ui`.
- **Automatic resources** under the base path: `style.css` (and `style-dark.css`, `style-hc.css`),
  `icons/scalable/actions/*.svg` (added to the icon theme search path), and `shortcuts-dialog.ui` at the root
  (libadwaita wires `app.shortcuts` and Ctrl+? to it).
- **Aliases** decouple the on-disk name from the resource path: Blueprint output lands flat in the build dir,
  `alias="ui/window.ui"` gives it the path `#[template(resource = "/com/example/MyApp/ui/window.ui")]` uses.
- **`source_dir` lists the build dir first** so generated files (`.ui`, merged metainfo) are found before
  source files.
- **Metainfo in the bundle** lets `adw::AboutDialog::from_appdata(&format!("{}/metainfo.xml", RESOURCE_BASE_PATH), Some(version))`
  fill name, developer, license, website and release notes from the same file Flathub reads. Pass a version
  that exists as a `<release>`; the template strips the `-<git hash>` suffix for that argument and then calls
  `set_version()` with the full string.
- **`MESON_DEVENV`** is set by `meson devenv`; the uninstalled bundle is in the build tree. Register the
  bundle before any templated widget type is first used: the template is loaded in `class_init`.
- Use `preprocess="xml-stripblanks"` on `.ui`/`.xml` (needs `xmllint` at build time; it is in the GNOME SDK),
  not on CSS or SVG-as-icon files.

Alternatives: `include_bytes!` of the Meson-built bundle (podcasts), or `gvdb-macros` (pure Rust; warp,
pika-backup), which cannot run Blueprint or `xml-stripblanks`.

## 6. Cargo-only alternative (gtk-rs book route)

For a small tool that never needs installed data files, skip Meson:

```rust
// build.rs  ([build-dependencies] glib-build-tools = "0.22")
fn main() {
    glib_build_tools::compile_resources(
        &["resources"],                         // source dirs
        "resources/resources.gresource.xml",
        "app.gresource",                        // relative to OUT_DIR
    );
}
// main.rs, before creating the Application
gio::resources_register_include!("app.gresource").expect("Failed to register resources.");
```

`compile_resources` shells out to `glib-compile-resources`, emits `rerun-if-changed` for the XML and every
file it references, and `resources_register_include!` embeds the bytes from `OUT_DIR`. Blueprint then has to
happen in `build.rs` too: run `blueprint-compiler batch-compile $OUT_DIR/ui resources/ui resources/ui/*.blp`
via `std::process::Command`, print `cargo:rerun-if-changed=` for each `.blp`, and pass `$OUT_DIR/ui` as an
extra source dir. (gtk4's `blueprint` feature can also compile `#[template(file = "x.blp")]` or inline
`string = "..."` templates at macro-expansion time, but not `resource = ...`.)

What you lose: GSettings schemas, the desktop file, icons and `.mo` translations still need install rules,
so `cargo run` only works after `glib-compile-schemas data/ && GSETTINGS_SCHEMA_DIR=data/`. No real GNOME
app in the survey uses this route; move to Meson as soon as you ship.

## 7. Blueprint wiring

```meson
blueprint_files = ['ui/shortcuts-dialog.blp', 'ui/window.blp']
ui_files = []
foreach blp : blueprint_files
  ui_files += fs.replace_suffix(fs.name(blp), '.ui')
endforeach

blueprints = custom_target('blueprints',
  input: files(blueprint_files),
  output: ui_files,
  command: [blueprint_compiler, 'batch-compile', '@OUTDIR@', '@CURRENT_SOURCE_DIR@/ui', '@INPUT@'],
)
```

Why explicit outputs: the commonly copied `output: '.'` hides the real outputs from ninja, which may then
compile the gresource before every `.ui` exists (fractal documents the race in
`build-aux/compile-blueprints.sh`; Solanum papers over it with `build_always_stale`). Listing outputs also
makes ninja rebuild only when a `.blp` changes. Add `dependencies: [blueprints]` to `compile_resources`.

`find_program('blueprint-compiler', version: '>= 0.16')` — the GNOME SDK ships it, no Flatpak module needed.
For nested colocated files, compile to flattened names (`session-view-foo.ui`) and alias them back
(`alias="session/view/foo.ui"`), generating the gresource XML from one sorted list (fractal
`src/meson.build`). Every `.blp` with translatable strings goes into `po/POTFILES.in`.

## 8. GSettings schema

```xml
<!-- data/<base id>.gschema.xml.in -->
<schemalist gettext-domain="@gettext-package@">
  <schema id="@app-id@" path="/com/example/MyApp/">
    <key name="window-width" type="i"><default>800</default><summary>Window width</summary></key>
  </schema>
</schemalist>
```

```meson
gschema_file = configure_file(input: '@0@.gschema.xml.in'.format(base_id),
  output: '@0@.gschema.xml'.format(application_id),
  configuration: gschema_conf, install: true, install_dir: datadir / 'glib-2.0' / 'schemas')
# Compile the *configured* schema into the build dir for `meson devenv`.
custom_target('gschemas-compiled',
  input: gschema_file,
  output: 'gschemas.compiled',
  command: [glib_compile_schemas, '--strict', '--targetdir', '@OUTDIR@', '@OUTDIR@'],
  build_by_default: true)
test('validate-schema', glib_compile_schemas,
  args: ['--strict', '--dry-run', meson.current_build_dir()], suite: ['lint'])
devenv = environment()
devenv.set('GSETTINGS_SCHEMA_DIR', meson.current_build_dir())
meson.add_devenv(devenv)
```

Schema id = application id, so `gio::Settings::new(config::APP_ID)` gives Devel and stable builds separate
settings. The path deliberately stays the base path; change it too if you want fully separate dconf storage.
Installed builds compile schemas through `gnome.post_install(glib_compile_schemas: true)`.

Why a `custom_target` and not `gnome.compile_schemas()`: that helper compiles the **source** directory.
With a static `.gschema.xml` (amberol, identity) that works, but a templated schema only exists as
`.gschema.xml.in` there, so no `gschemas.compiled` appears in the build dir and the uninstalled app aborts
with "Settings schema … is not installed". Compiling the build dir, with the configured file as `input`,
also makes ninja recompile whenever the schema changes.

## 9. Desktop file and metainfo

Both are `.in.in`: `configure_file` substitutes `@app-id@`/`@bin@`, then `i18n.merge_file` merges
translations from `po/`.

```meson
desktop_file = i18n.merge_file(
  input: configure_file(input: '@0@.desktop.in.in'.format(base_id), output: '@BASENAME@',
                        configuration: desktop_conf),
  output: '@0@.desktop'.format(application_id), type: 'desktop', po_dir: podir,
  install: true, install_dir: datadir / 'applications')
test('validate-desktop', desktop_file_validate, args: [desktop_file], suite: ['lint'])  # if found()
# metainfo: same shape (no type:), install_dir: datadir / 'metainfo', validated with
#   appstreamcli validate --no-net --explain
```

`find_program(..., required: false)` keeps builds working where validators are missing; in the Flatpak build
they exist and run through `"run-tests": true`. Install metainfo to `share/metainfo`, not the legacy
`appdata`. The desktop file uses `Icon=@app-id@` with a translator comment telling translators not to touch
it. Add `DBusActivatable=true` plus an installed `<app-id>.service` only if you need D-Bus activation
(Authenticator, amberol). Flathub-oriented metainfo content is in `packaging-and-ci.md`.

## 10. Icons

```meson
icon_source = profile == 'Devel' ? '@0@.Devel.svg'.format(base_id) : '@0@.svg'.format(base_id)
install_data(icon_source, install_dir: iconsdir / 'hicolor' / 'scalable' / 'apps',
             rename: '@0@.svg'.format(application_id))
install_data('@0@-symbolic.svg'.format(base_id), install_dir: iconsdir / 'hicolor' / 'symbolic' / 'apps',
             rename: '@0@-symbolic.svg'.format(application_id))
```

`<id>.svg` is the full-color icon, `<id>.Devel.svg` the striped nightly variant (installed under the
`.Devel` id), `<id>-symbolic.svg` the monochrome one (also handy for an `Adw.StatusPage`). At runtime call
`gtk::Window::set_default_icon_name(config::APP_ID)` in `startup()`. Private UI icons go in the gresource
under `<base path>/icons/scalable/actions/` and are found automatically.

## 11. po/

`po/meson.build` is one line: `i18n.gettext(gettext_package, preset: 'glib')`. Add
`args: ['--keyword=gettext_f', '--keyword=ngettext_f:1,2']` if you wrap gettext in formatting helpers
(fractal). `POTFILES.in` lists every file with translatable strings (desktop, metainfo, gschema, `.blp`,
`.rs`), sorted; `LINGUAS` lists language codes. Regenerate the template with
`meson compile -C _build <project>-pot`, update `.po` files with `<project>-update-po`. Details in
`accessibility-and-i18n.md`.

## 12. Running uninstalled and the Devel profile

```sh
meson setup _build -Dprofile=development
meson compile -C _build
meson devenv -C _build ./src/my-app      # GSETTINGS_SCHEMA_DIR + MESON_DEVENV are set
meson test -C _build                     # cargo test + lint suite
ninja -C _build cargo-clippy
```

`meson devenv` exports everything registered with `meson.add_devenv()`, so resources, schemas and cargo
caches resolve from the build tree. Translations are not found uninstalled (LOCALEDIR points at the prefix);
install to a prefix or use Flatpak to test them.

Devel conventions (`-Dprofile=development`): app id `<base>.Devel`; version suffix `-<git short hash>`
(`-devel` from a tarball; guard `git` with `fs.is_dir('.git')` and `check: false`); debug cargo build; the
window adds the `devel` CSS class (striped header bar) when `config::is_devel()`; striped icon;
`desktop-file-name-suffix: " (Devel)"` and `RUST_LOG=<crate>=debug` in the Devel manifest; separate settings
through the templated schema id.

## Pitfalls

- Styles, private icons or the shortcuts dialog work in release but not in Devel → `resource-base-path` not
  set, so it was derived from the `.Devel` id → set it to the gresource prefix.
- `gtk::Accessible` methods missing on adw widgets, or `IsA<gtk::Accessible>` not satisfied → libadwaita
  built without a `gtk_v4_*` feature ≥ `gtk_v4_10` → add `gtk_v4_22` (matching gtk's feature).
- Undefined symbol / "could not find symbol" at startup → crate feature newer than the runtime → lower the
  feature or raise `runtime-version` and the Meson `dependency()` minimum together.
- Intermittent "Failed to locate 'window.ui'" from `glib-compile-resources` → Blueprint target with
  `output: '.'` → list outputs explicitly.
- Meson build uses Devel defaults in a release build → a constant read via `option_env!` is missing from
  `cargo_vars` → add the key there.
- Devel and stable builds overwrite each other's settings → schema id not templated → use `@app-id@`.
- "Settings schema … is not installed" under `meson devenv` with a templated schema →
  `gnome.compile_schemas()` compiled the source dir, which only has the `.in` → compile the build dir with a
  `custom_target` (section 8).
- App panics "Could not load resources from …/share/my-app/resources.gresource" when run from `_build` →
  started outside `meson devenv` → run through `meson devenv -C _build`.

## See also

`packaging-and-ci.md`, `testing-and-debugging.md`, `templates-and-blueprint.md`, `settings-and-state.md`,
`accessibility-and-i18n.md`, `architecture.md`, `libadwaita-ui.md`.
