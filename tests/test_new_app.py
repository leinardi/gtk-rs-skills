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
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

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
