"""Tests for the gtk-rs-new-app generator (skills/gtk-rs-new-app/scripts/new_app.py)."""

#
# MIT License
#
# Copyright (c) 2026 Roberto Leinardi
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from xml.etree import ElementTree

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "skills" / "gtk-rs-new-app" / "scripts" / "new_app.py"
TEMPLATE = REPO_ROOT / "skills" / "gtk-rs-new-app" / "assets" / "template"
TOKEN_RE = re.compile(r"\{\{[A-Z_]+\}\}")


def load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("new_app", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_generator(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True, check=False)


@pytest.fixture
def generated(tmp_path: Path) -> Path:
    dest = tmp_path / "notes"
    result = run_generator(
        "--app-id",
        "io.github.alice.QuickNotes",
        "--name",
        "Quick Notes",
        "--dest",
        str(dest),
        "--developer",
        "Alice Doe",
        "--homepage",
        "https://github.com/alice/quick-notes",
    )
    assert result.returncode == 0, result.stderr
    return dest


def test_every_template_file_is_generated(generated: Path) -> None:
    template_files = {p.relative_to(TEMPLATE) for p in TEMPLATE.rglob("*") if p.is_file()}
    generated_files = {p.relative_to(generated) for p in generated.rglob("*") if p.is_file()}
    assert len(generated_files) == len(template_files)
    assert Path("data/icons/io.github.alice.QuickNotes.Devel.svg") in generated_files
    assert Path("build-aux/io.github.alice.QuickNotes.Devel.json") in generated_files


def test_no_placeholder_survives(generated: Path) -> None:
    for path in generated.rglob("*"):
        if path.is_file():
            assert not TOKEN_RE.search(path.read_text(encoding="utf-8")), path
            assert not TOKEN_RE.search(str(path.relative_to(generated))), path


def test_github_expressions_are_kept(generated: Path) -> None:
    workflow = (generated / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "${{ github.sha }}" in workflow


def test_derived_values(generated: Path) -> None:
    cargo = tomllib.loads((generated / "Cargo.toml").read_text(encoding="utf-8"))
    assert cargo["package"]["name"] == "quick-notes"
    manifest = json.loads((generated / "build-aux" / "io.github.alice.QuickNotes.Devel.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "io.github.alice.QuickNotes.Devel"
    assert manifest["command"] == "quick-notes"
    assert "--env=RUST_LOG=quick_notes=debug" in manifest["finish-args"]
    window = (generated / "src" / "window.rs").read_text(encoding="utf-8")
    assert '"QuickNotesWindow"' in window
    assert '#[template(resource = "/io/github/alice/QuickNotes/ui/window.ui")]' in window
    gresource = (generated / "data" / "resources" / "resources.gresource.xml.in").read_text(encoding="utf-8")
    assert 'prefix="/io/github/alice/QuickNotes"' in gresource


def test_executable_bit_is_preserved(generated: Path) -> None:
    assert (generated / "build-aux" / "dist-vendor.sh").stat().st_mode & 0o111


@pytest.mark.parametrize("app_id", ["notes", "com.example", "com.example.1Notes", "com..Notes", "com.example.My-App"])
def test_rejects_invalid_app_ids(tmp_path: Path, app_id: str) -> None:
    result = run_generator("--app-id", app_id, "--name", "Notes", "--dest", str(tmp_path / "out"))
    assert result.returncode != 0
    assert "not a valid application id" in result.stderr


def test_refuses_non_empty_destination(tmp_path: Path) -> None:
    (tmp_path / "existing.txt").write_text("keep me", encoding="utf-8")
    result = run_generator("--app-id", "io.github.alice.Notes", "--name", "Notes", "--dest", str(tmp_path))
    assert result.returncode != 0
    assert "is not empty" in result.stderr
    assert (tmp_path / "existing.txt").read_text(encoding="utf-8") == "keep me"


def test_rejects_invalid_crate_name(tmp_path: Path) -> None:
    result = run_generator("--app-id", "io.github.alice.Notes", "--name", "Notes", "--dest", str(tmp_path / "o"), "--crate", "Bad_Name")
    assert result.returncode != 0
    assert "invalid crate name" in result.stderr


@pytest.mark.parametrize(
    ("text", "expected"),
    [("QuickNotes", "quick-notes"), ("HTTPClient", "httpclient"), ("notes", "notes"), ("Notes2Go", "notes2-go")],
)
def test_kebab_case(text: str, expected: str) -> None:
    assert load_generator().kebab_case(text) == expected


@pytest.mark.parametrize(("text", "expected"), [("Quick Notes", "QuickNotes"), ("my-app", "MyApp"), ("hello gtk 4", "HelloGtk4")])
def test_camel_case(text: str, expected: str) -> None:
    assert load_generator().camel_case(text) == expected


@pytest.mark.skipif(shutil.which("rustfmt") is None, reason="rustfmt is not installed")
def test_generated_rust_is_rustfmt_clean_for_long_ids(tmp_path: Path) -> None:
    # Substituted values change line lengths; a long id must not leave the project failing `cargo fmt --check`.
    dest = tmp_path / "app"
    app_id = "io.github.a-very-long-organisation-name.SomeRatherLongApplicationName"
    result = run_generator("--app-id", app_id.replace("-", "_"), "--name", "Some Rather Long Application Name", "--dest", str(dest))
    assert result.returncode == 0, result.stderr
    sources = sorted(str(p.relative_to(dest)) for p in (dest / "src").glob("*.rs"))
    check = subprocess.run(["rustfmt", "--check", "--edition", "2024", *sources], cwd=dest, capture_output=True, text=True, check=False)
    assert check.returncode == 0, check.stdout + check.stderr


HOSTILE_NAME = 'Tom & Jerry <"Cat\\Mouse">'
HOSTILE_SUMMARY = 'Chase \'em & catch "them"'
HOSTILE_DEVELOPER = 'Jane "JJ" O\'Doe & Co'


@pytest.fixture
def hostile(tmp_path: Path) -> Path:
    dest = tmp_path / "hostile"
    result = run_generator(
        "--app-id",
        "io.github.x.TomJerry",
        "--name",
        HOSTILE_NAME,
        "--summary",
        HOSTILE_SUMMARY,
        "--developer",
        HOSTILE_DEVELOPER,
        "--dest",
        str(dest),
    )
    assert result.returncode == 0, result.stderr
    return dest


def test_special_characters_keep_toml_valid(hostile: Path) -> None:
    cargo = tomllib.loads((hostile / "Cargo.toml").read_text(encoding="utf-8"))
    assert cargo["package"]["authors"] == [HOSTILE_DEVELOPER]


def test_special_characters_keep_xml_valid(hostile: Path) -> None:
    metainfo = hostile / "data" / "io.github.x.TomJerry.metainfo.xml.in.in"
    root = ElementTree.parse(metainfo).getroot()
    assert root.findtext("name") == HOSTILE_NAME
    assert root.findtext("summary") == HOSTILE_SUMMARY
    assert root.findtext("developer/name") == HOSTILE_DEVELOPER


def test_special_characters_are_escaped_in_rust_and_blueprint(hostile: Path) -> None:
    def rust_literal(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    assert rust_literal(HOSTILE_DEVELOPER) in (hostile / "src" / "application.rs").read_text(encoding="utf-8")
    assert rust_literal(HOSTILE_NAME) in (hostile / "src" / "main.rs").read_text(encoding="utf-8")
    assert rust_literal(HOSTILE_NAME) in (hostile / "data" / "resources" / "ui" / "window.blp").read_text(encoding="utf-8")


def test_special_characters_are_escaped_in_desktop_entry(hostile: Path) -> None:
    desktop = (hostile / "data" / "io.github.x.TomJerry.desktop.in.in").read_text(encoding="utf-8")
    assert "Name=" + HOSTILE_NAME.replace("\\", "\\\\") + "\n" in desktop


@pytest.mark.parametrize(
    ("homepage", "bugtracker"),
    [
        ("https://example.com/project?lang=en&view=main", "https://example.com/project/issues"),
        ('https://example.com/"><x?a=1#frag', 'https://example.com/"><x/issues'),
        ("https://github.com/alice/notes/", "https://github.com/alice/notes/issues"),
    ],
)
def test_homepage_is_escaped_in_metainfo(tmp_path: Path, homepage: str, bugtracker: str) -> None:
    dest = tmp_path / "out"
    result = run_generator("--app-id", "io.github.x.Notes", "--name", "Notes", "--dest", str(dest), "--homepage", homepage)
    assert result.returncode == 0, result.stderr
    root = ElementTree.parse(dest / "data" / "io.github.x.Notes.metainfo.xml.in.in").getroot()
    urls = {url.get("type"): url.text for url in root.iter("url")}
    assert urls == {"homepage": homepage, "bugtracker": bugtracker, "vcs-browser": homepage}


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("--name", "Two\nLines", "printable text on one line"),
        ("--name", "Uses @VERSION@", "@word@"),
        ("--developer", "  ", "non-empty"),
        ("--homepage", "javascript:alert(1)", "not a valid http(s) URL"),
        ("--homepage", "https://", "not a valid http(s) URL"),
        ("--homepage", "https://exa mple.com", "not a valid http(s) URL"),
        ("--homepage", "https://example.com:99999", "not a valid http(s) URL"),
        ("--homepage", "https://example.com/@VERSION@", "@word@"),
        ("--license", "MIT'); run_command('x", "not a valid SPDX"),
        ("--developer-id", 'io.github.x"', "not a valid developer id"),
    ],
)
def test_rejects_unsafe_values(tmp_path: Path, option: str, value: str, message: str) -> None:
    args = ["--app-id", "io.github.x.Notes", "--name", "Notes", "--dest", str(tmp_path / "out")]
    if option == "--name":
        args[3] = value
    else:
        args += [option, value]
    result = run_generator(*args)
    assert result.returncode != 0
    assert message in result.stderr
    assert not (tmp_path / "out").exists()


TOOLCHAIN = ("meson", "ninja", "glib-compile-schemas", "gsettings", "blueprint-compiler", "pkg-config")


@pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in TOOLCHAIN)
    or subprocess.run(["pkg-config", "--exists", "gtk4 >= 4.22", "libadwaita-1 >= 1.9"], check=False).returncode != 0,
    reason="needs meson, blueprint-compiler, gsettings and GTK 4.22 / libadwaita 1.9 development files",
)
def test_devel_and_release_settings_are_isolated(generated: Path, tmp_path: Path) -> None:
    # dconf stores values by path, so the two profiles need different schema paths, not just different ids.
    schema_dirs = []
    for profile in ("default", "development"):
        build = tmp_path / f"build-{profile}"
        subprocess.run(["meson", "setup", str(build), f"-Dprofile={profile}"], cwd=generated, check=True, capture_output=True)
        subprocess.run(["ninja", "-C", str(build), "data/gschemas.compiled"], check=True, capture_output=True)
        schema_dirs.append(build / "data")
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    for directory in schema_dirs:
        for schema in directory.glob("*.gschema.xml"):
            shutil.copy(schema, schemas)
    subprocess.run(["glib-compile-schemas", "--strict", str(schemas)], check=True)
    env = {
        "PATH": os.environ["PATH"],
        "GSETTINGS_SCHEMA_DIR": str(schemas),
        "GSETTINGS_BACKEND": "keyfile",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }
    subprocess.run(["gsettings", "set", "io.github.alice.QuickNotes", "window-width", "1234"], env=env, check=True)
    devel = subprocess.run(
        ["gsettings", "get", "io.github.alice.QuickNotes.Devel", "window-width"], env=env, check=True, capture_output=True, text=True
    )
    assert devel.stdout.strip() == "800"


# Property names from flatpak-manifest(5) (flatpak-builder 1.4). flatpak-builder only *warns* about an unknown
# property and then ignores it, so a misplaced key (e.g. `test-args` on a module) silently does nothing.
FLATPAK_MODULE_KEYS = frozenset(
    [
        "build-commands",
        "builddir",
        "build-options",
        "buildsystem",
        "cleanup",
        "cleanup-platform",
        "cmake",
        "config-opts",
        "disabled",
        "ensure-writable",
        "install-rule",
        "license-files",
        "make-args",
        "make-install-args",
        "modules",
        "name",
        "no-autogen",
        "no-make-install",
        "no-parallel-make",
        "no-python-timestamp-fix",
        "only-arches",
        "post-install",
        "rm-configure",
        "run-tests",
        "secret-env",
        "secret-opts",
        "skip-arches",
        "sources",
        "subdir",
        "test-commands",
        "test-rule",
    ]
)
FLATPAK_BUILD_OPTIONS_KEYS = frozenset(
    [
        "append-ld-library-path",
        "append-path",
        "append-pkg-config-path",
        "arch",
        "build-args",
        "cflags",
        "cflags-override",
        "config-opts",
        "cppflags",
        "cppflags-override",
        "cxxflags",
        "cxxflags-override",
        "env",
        "ldflags",
        "ldflags-override",
        "libdir",
        "make-args",
        "make-install-args",
        "no-debuginfo",
        "no-debuginfo-compression",
        "prefix",
        "prepend-ld-library-path",
        "prepend-path",
        "prepend-pkg-config-path",
        "secret-env",
        "secret-opts",
        "strip",
        "test-args",
    ]
)


def test_flatpak_manifest_uses_known_properties(generated: Path) -> None:
    manifest = json.loads((generated / "build-aux" / "io.github.alice.QuickNotes.Devel.json").read_text(encoding="utf-8"))
    assert set(manifest["build-options"]) <= FLATPAK_BUILD_OPTIONS_KEYS
    for module in manifest["modules"]:
        assert set(module) <= FLATPAK_MODULE_KEYS, set(module) - FLATPAK_MODULE_KEYS
        assert set(module.get("build-options", {})) <= FLATPAK_BUILD_OPTIONS_KEYS
