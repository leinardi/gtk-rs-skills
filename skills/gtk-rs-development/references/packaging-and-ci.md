# Packaging and CI (Flatpak, Flathub, GitHub/GitLab CI, releases)

Read this when writing or debugging a Flatpak manifest, preparing a Flathub submission or release tarball,
setting up CI (rustfmt, clippy, tests, lint tools), or cutting a release.

Contents: 1. Devel Flatpak manifest · 2. finish-args · 3. Offline builds for Flathub · 4. Metainfo quality ·
5. GitHub Actions · 6. GitLab CI · 7. Extra checks · 8. Release checklist · Pitfalls · See also

## 1. Devel Flatpak manifest

The template's `build-aux/<APP_ID>.Devel.json`:

```json
{
    "id": "com.example.MyApp.Devel",
    "runtime": "org.gnome.Platform",
    "runtime-version": "50",
    "sdk": "org.gnome.Sdk",
    "sdk-extensions": ["org.freedesktop.Sdk.Extension.rust-stable"],
    "command": "my-app",
    "desktop-file-name-suffix": " (Devel)",
    "finish-args": [
        "--share=ipc", "--socket=fallback-x11", "--socket=wayland", "--device=dri",
        "--env=RUST_LOG=my_app=debug", "--env=RUST_BACKTRACE=1"
    ],
    "build-options": {
        "append-path": "/usr/lib/sdk/rust-stable/bin",
        "build-args": ["--share=network"],
        "env": { "RUSTFLAGS": "-C force-frame-pointers=yes" }
    },
    "modules": [{
        "name": "my-app",
        "buildsystem": "meson",
        "builddir": true,
        "run-tests": true,
        "config-opts": ["-Dprofile=development"],
        "sources": [{ "type": "dir", "path": ".." }]
    }]
}
```

```sh
flatpak-builder --user --install --force-clean --install-deps-from=flathub _flatpak build-aux/com.example.MyApp.Devel.json
flatpak run com.example.MyApp.Devel
```

Why:

- **runtime-version** pins the GNOME release your crate features target (`gnome_50` ↔ `"50"`, see
  `project-layout-and-build.md`). Projects hosted on GNOME GitLab often use `"master"` with the nightly repo
  for Devel builds; then you also need a job that builds against the stable number before releasing (fractal
  `.gitlab-ci/build.yml`), because Flathub builds against stable.
- **rust-stable extension + `append-path`** is the toolchain; nobody ships `rust-toolchain.toml` for this.
  The extension also ships `mold` and `cargo-vendor-filterer`. Add `org.freedesktop.Sdk.Extension.llvmNN` only
  if you want clang + mold linking (identity, Authenticator).
- **`build-args: --share=network`** lets cargo fetch crates during a Devel build. Flathub rejects it; release
  builds must be offline (section 3).
- **`builddir: true`** builds in `_flatpak_build` inside the source copy. This also matters for offline
  builds: cargo looks up `.cargo/config.toml` from its working directory upwards, and the Meson build dir is
  then below the source root.
- **`run-tests: true`** runs `meson test` (cargo test + desktop/metainfo/schema validation) after the build.
  There is no display in that sandbox; GTK widget tests need `"test-args": ["--socket=x11", "--share=network"]`
  plus an X server on the host (Authenticator, fractal), or keep them out of `meson test`
  (`testing-and-debugging.md`). `test-args` is a **build-options** property (top-level or the module's
  `build-options`), like `build-args`; placed directly on the module, flatpak-builder only warns
  `Unknown property test-args for type BuilderModule` and ignores it. The template sets
  `"test-args": ["--share=network"]` in its top-level `build-options`.
- **`force-frame-pointers`** makes `sysprof` stacks usable; the GNOME runtime itself is built with frame
  pointers.
- `RUST_LOG`/`RUST_BACKTRACE` as `--env=` finish-args belong in the Devel manifest only.

The stable manifest normally lives in the Flathub repository, not in yours (helvum, pika-backup and
mission-center are exceptions). It differs in id (no `.Devel`), no network build-arg, no `RUST_LOG`, the
default profile, and an archive/git source instead of `"type": "dir"`.

## 2. finish-args

Start from the four baseline permissions and add only what a feature needs, with a comment per line
(pika-backup's manifest is a good example).

| Permission | Add when | Notes |
| --- | --- | --- |
| `--share=ipc` `--socket=fallback-x11` `--socket=wayland` `--device=dri` | always (GUI app) | `fallback-x11` only grants X11 when Wayland is unavailable |
| `--share=network` | app talks to the network | fractal, Shortwave, warp |
| `--socket=pulseaudio` | audio playback/recording | also covers PipeWire's Pulse shim |
| `--filesystem=xdg-music:ro` etc. | app manages a library in a known dir | prefer portals for user-picked files |
| `--talk-name=org.freedesktop.secrets` | libsecret/oo7 without the portal | Authenticator |
| `--talk-name=org.gtk.vfs.*` `--filesystem=xdg-run/gvfsd` | browsing GVfs mounts (smb, mtp) |  |
| `--filesystem=host`, `--device=all` | backup tools, system monitors | expect Flathub review questions |

Not needed thanks to portals: file open/save (`gtk::FileDialog`), opening URIs (`gtk::UriLauncher`),
notifications (`gio::Notification`), background/autostart, screenshots and camera (ashpd). Avoid
`--filesystem=home`; it is the most common reason for Flathub review pushback.

## 3. Offline builds for Flathub

Flathub builds without network, so every crate must be a declared source. Two established ways:

**(a) `flatpak-cargo-generator` → `cargo-sources.json`** (Flathub's documented way, works from git tags):

```sh
# from github.com/flatpak/flatpak-builder-tools, cargo/ directory
python3 flatpak-cargo-generator.py Cargo.lock -o cargo-sources.json
```

```json
"sources": [
    { "type": "git", "url": "https://github.com/example/my-app.git", "tag": "v1.2.0", "commit": "…" },
    "cargo-sources.json",
    { "type": "inline", "dest": ".cargo", "dest-filename": "config.toml",
      "contents": "[source.vendored-sources]\ndirectory = \"cargo/vendor\"\n\n[source.crates-io]\nreplace-with = \"vendored-sources\"\n" }
]
```

Each crate becomes an archive source plus an inline `.cargo-checksum.json` (mission-center
`flatpak/cargo-sources.json`). Regenerate `cargo-sources.json` whenever `Cargo.lock` changes; git
dependencies need an extra `[source."git+…"]` entry, which the generator also writes.

**(b) `meson dist` with vendored crates** (most GNOME-hosted apps; the template is set up for it):

```sh
#!/bin/sh
# build-aux/dist-vendor.sh, registered with meson.add_dist_script(<dist dir>, <source root>)
set -eu
export DIST="$1"
export SOURCE_ROOT="$2"
cd "$SOURCE_ROOT"
mkdir "$DIST"/.cargo
cargo vendor-filterer --platform=x86_64-unknown-linux-gnu --platform=aarch64-unknown-linux-gnu > "$DIST"/.cargo/config.toml
sed -i 's/^directory = ".*"/directory = "vendor"/g' "$DIST"/.cargo/config.toml
mv vendor "$DIST"
```

`meson dist -C _build` produces `meson-dist/my-app-1.2.0.tar.xz` with `vendor/` and `.cargo/config.toml`
inside (and builds/tests the tarball by default). The Flathub manifest then uses one
`{ "type": "archive", "url": ".../my-app-1.2.0.tar.xz", "sha256": "…" }` source. Upload the tarball as a
release asset.

|  | cargo-sources.json | vendored dist tarball |
| --- | --- | --- |
| Release artifact | git tag | tarball you must host |
| Flathub repo churn | large JSON per `Cargo.lock` change | one URL + sha256 |
| Tools needed locally | Python generator | `cargo-vendor-filterer` (`cargo install cargo-vendor-filterer`) |
| Size | only listed crates | filtered to Linux x86_64/aarch64 |

Write `.cargo/config.toml`, not the deprecated `.cargo/config` that older scripts (amberol, identity) still
produce. `vendor-filterer` drops Windows/macOS-only crates, which makes the tarball much smaller.

## 4. Metainfo quality for Flathub

`appstreamcli validate --no-net` in `meson test` catches syntax; Flathub additionally runs
`flatpak-builder-lint` (fractal runs it in CI with an exceptions file). Content Flathub and GNOME Software
expect:

```xml
<developer id="com.example"><name translate="no">Jane Doe</name></developer>
<launchable type="desktop-id">@app-id@.desktop</launchable>
<url type="homepage">…</url> <url type="bugtracker">…</url> <url type="vcs-browser">…</url>
<url type="translate">…</url> <url type="donation">…</url>
<content_rating type="oars-1.1" />
<branding>
  <color type="primary" scheme_preference="light">#99c1f1</color>
  <color type="primary" scheme_preference="dark">#1a5fb4</color>
</branding>
<screenshots>
  <screenshot type="default">
    <image>https://raw.githubusercontent.com/example/my-app/v1.2.0/data/screenshots/main.png</image>
    <caption>Main window</caption>
  </screenshot>
</screenshots>
<releases>
  <release version="1.2.0" date="2026-10-01">
    <description><p>Added export to PDF.</p></description>
  </release>
</releases>
```

- **Screenshots**: required by Flathub; real window captures at default size, light style, with captions.
  Point at a tagged URL so they don't change under a release. Store sources in `data/screenshots/`.
- **Releases**: newest first, with dates; the release description is shown in GNOME Software and in
  `adw::AboutDialog` "What's New" (via `from_appdata`). `translate="no"` on release notes keeps translators
  from chasing every release.
- **Content rating**: fill the OARS questionnaire (e.g. `<content_attribute id="social-chat">intense</content_attribute>`
  for a chat app); an empty `oars-1.1` element means "no such content".
- **Branding**: provide both light and dark primary colors; they color the app's banner in Software/Flathub.
- **Summary**: short, no trailing period, does not repeat the name. **Developer id** is reverse DNS.
- `<requires><display_length compare="ge">360</display_length></requires>` plus `<supports>` controls signal
  mobile/adaptive support.

## 5. GitHub Actions

```yaml
jobs:
  rustfmt:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: rustup component add rustfmt
      - run: cargo fmt --all -- --check

  flatpak:
    runs-on: ubuntu-latest
    container:
      image: ghcr.io/flathub-infra/flatpak-github-actions:gnome-50
      options: --privileged
    steps:
      - uses: actions/checkout@v4
      - uses: flatpak/flatpak-github-actions/flatpak-builder@v6
        with:
          bundle: my-app.flatpak
          manifest-path: build-aux/com.example.MyApp.Devel.json
          cache-key: flatpak-builder-${{ github.sha }}

  clippy:
    runs-on: ubuntu-latest
    container:
      image: ghcr.io/flathub-infra/flatpak-github-actions:gnome-50
      options: --privileged
    steps:
      - uses: actions/checkout@v4
      - run: |
          flatpak-builder --user --disable-rofiles-fuse --install-deps-from=flathub --keep-build-dirs \
            --stop-at=my-app flatpak_app build-aux/com.example.MyApp.Devel.json
          flatpak build-finish --share=network flatpak_app
          echo "cd .. && cargo clippy --all-targets -- -D warnings" | \
            flatpak-builder --user --disable-rofiles-fuse \
            --build-shell=my-app flatpak_app build-aux/com.example.MyApp.Devel.json
```

- The action is `flatpak/flatpak-github-actions/flatpak-builder@v6`; the `flathub-infra/flatpak-github-actions`
  fork was archived in 2025. The container images moved to `ghcr.io/flathub-infra/flatpak-github-actions:<runtime>`
  (`gnome-49`, `gnome-50`, …) and only preconfigure the flathub/gnome-nightly remotes.
- The action's own `run-tests` input enables network and X11 under `xvfb-run` for the test step, which is
  the easiest way to run `#[gtk::test]` tests in CI.
- **Why clippy runs in the SDK**: runner images ship older GTK/libadwaita than your features need, so
  `gtk4-sys` fails its pkg-config check. `--stop-at=<module>` builds all dependencies, `build-finish
  --share=network` lets cargo fetch crates in the shell (Authenticator, warp and pika-backup do the same),
  and `--build-shell` drops into the module's build dir (`_flatpak_build`, hence `cd ..`). The `option_env!`
  defaults in `config.rs` let clippy run without Meson.
- rustfmt needs no system libraries, so it runs on the bare runner.
- Add an `aarch64` matrix entry with `arch: aarch64` on `ubuntu-24.04-arm` if you publish for ARM.

## 6. GitLab CI (GNOME citemplates)

```yaml
include:
  - project: "GNOME/citemplates"
    file: "templates/default-rules.yml"
  - project: "GNOME/citemplates"
    file: "flatpak/flatpak_ci_initiative.yml"

.vars-devel:
  variables:
    MANIFEST_PATH: "build-aux/com.example.MyApp.Devel.json"
    FLATPAK_MODULE: "my-app"
    APP_ID: "com.example.MyApp.Devel"
    RUNTIME_REPO: "https://nightly.gnome.org/gnome-nightly.flatpakrepo"
    BUNDLE: "my-app-nightly.flatpak"

flatpak@x86_64:
  extends: [".flatpak@x86_64", ".vars-devel"]
flatpak@aarch64:
  extends: [".flatpak@aarch64", ".vars-devel"]

cargo-clippy:
  # same --stop-at / build-finish / --build-shell trick as on GitHub
```

The `.flatpak@*` jobs build, run tests and export a bundle; `.publish_nightly` publishes Devel builds to
GNOME's nightly repo (World/ and GNOME/ projects). Authenticator's `.gitlab-ci.yml` is the compact
exemplar, fractal's `.gitlab-ci/` the thorough one (stable-runtime build, flatpak-builder-lint, code quality
report from clippy, headless Rust tests under mutter).

## 7. Extra checks

| Tool | Command | Config | Notes |
| --- | --- | --- | --- |
| cargo-deny | `cargo deny check` | `deny.toml` | licenses allow-list, advisories, `unknown-git = "deny"`; often `allow_failure` for advisories (warp, pika-backup) |
| typos | `typos` | `.typos.toml` | exclude `po/*`, `LICENSES/`; whitelist domain words (fractal, Shortwave) |
| REUSE | `reuse lint` | `REUSE.toml` + `LICENSES/` | per-file license metadata (amberol, fractal, warp) |
| Blueprint format | `blueprint-compiler format data/resources/ui/*.blp` | none | non-zero exit when unformatted; `--fix` rewrites |
| Meson format | `meson format -q -r` | optional `meson.format` | `-q` is check-only, `-i` rewrites; Meson ≥ 1.5 |
| desktop/metainfo/schema | `meson test -C _build --suite lint` | in `data/meson.build` | already in the template |
| Flathub lint | `flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest <file>` | exceptions JSON | also `repo` mode on the built repo |
| POTFILES completeness | script |  | every `.rs`/`.blp` with `gettext`/`_()` listed and sorted (fractal `hooks/checks`) |

Run Blueprint and Meson format checks inside the SDK build shell (as fractal does) so the tool versions
match the runtime. A pre-commit hook running `cargo fmt --check` is common (identity, Authenticator); keep
it optional rather than installed by Meson.

## 8. Release checklist

1. Bump the version in **`Cargo.toml`** and **`meson.build` `project(version:)`**; run `cargo build` so
   `Cargo.lock` picks it up. (Or read the version from `Cargo.toml` in Meson, warp style, to keep one
   source of truth.)
2. Add a `<release version="X.Y.Z" date="YYYY-MM-DD">` with a description at the top of
   `<releases>` in the metainfo. `adw::AboutDialog::from_appdata` uses it for release notes.
3. Update screenshots if the UI changed; keep URLs pinned to the new tag.
4. `meson test -C _build` and `flatpak-builder-lint` pass; build once against the stable runtime number
   if Devel uses `master`.
5. Tag (`vX.Y.Z`), then either `meson dist -C _build` and upload the tarball, or regenerate
   `cargo-sources.json` for the tag.
6. Open a PR on the Flathub repo updating the source URL/tag + `sha256`/commit (and `cargo-sources.json`).
7. Raise the runtime only together with the crate features and Meson `dependency()` minimums.

### Tested vs required versions

Keep two things apart, and write both down in the README:

- **Tested:** what CI builds and runs on, which is the Flatpak `runtime-version` (and the SDK that clippy runs
  in, §5). Nothing older is exercised.
- **Required:** the oldest versions the project claims to build against. Three places state it, and they must
  agree: the Cargo version features (`gnome_50`, `v1_9`), the Meson `dependency(…, version: '>= …')` checks, and
  the runtime. A feature above the Meson minimum compiles against APIs the minimum lacks; a Meson minimum above the
  feature turns away distributions that would work.

The scaffold ships with required = tested = GNOME 50. Lowering the required versions (for a distribution with an
older GTK) means lowering all three together and adding a CI job on that older runtime, since otherwise the claim
is untested. When working on an existing project, its required versions are the constraint: don't raise them as
a side effect of a change (`deprecated-apis.md`, "Projects on older versions").

## Pitfalls

- Flathub build fails "network access not allowed" or cargo "failed to download" → `--share=network` build
  arg or missing vendored sources → use section 3.
- Offline build ignores `vendor/` → `.cargo/config.toml` not on cargo's cwd ancestry (Meson build dir outside
  the source tree) or written as the deprecated `.cargo/config` → keep `builddir: true` and the `.toml` name.
- CI clippy: "Package gtk4 was not found" or version too old → running on the runner's system libraries →
  run it inside `flatpak-builder --build-shell`.
- `flatpak-builder` in a custom CI step: "runtime/extension not installed" → no `--install-deps-from=flathub`
  (the action passes it for you; manual steps must too).
- Clippy in the build shell cannot download crates → shell lacks network → `flatpak build-finish
  --share=network flatpak_app` first.
- `meson test` fails only in Flatpak with "cannot open display" → GTK tests in the no-display sandbox →
  `build-options.test-args` with X11 + xvfb, the action's `run-tests`, or a separate headless test job.
- `cargo test` in the Flatpak test step cannot fetch a new dev-dependency → `cargo build` never downloaded
  dev-dependencies and the test sandbox has no network → add `"--share=network"` to `build-options.test-args` in Devel.
- Test permissions seem ignored and the build log says `Unknown property test-args for type BuilderModule` →
  `test-args` placed on the module itself → move it into `build-options`.
- About dialog shows no release notes → version passed to `from_appdata` has no matching `<release>` →
  strip build suffixes and add the release entry.
- Flathub review asks to drop permissions → broad `--filesystem` → use portals.

## See also

`project-layout-and-build.md`, `testing-and-debugging.md`, `platform-integration.md`,
`accessibility-and-i18n.md`.
