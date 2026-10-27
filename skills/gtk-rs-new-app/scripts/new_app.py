#!/usr/bin/env python3
"""Generate a new gtk-rs (GTK 4 + libadwaita + Blueprint + Meson + Flatpak) app from the template.

Usage:
    python3 -I new_app.py --app-id com.example.MyApp --name "My App" --dest ~/Projects/my-app \
        [--summary "Do something useful"] [--developer "Jane Doe"] [--developer-id com.example] \
        [--homepage https://github.com/jane/my-app] [--license GPL-3.0-or-later] [--crate my-app]

Only the exact {{TOKEN}} placeholders listed in build_tokens() are replaced, in file contents and
in file names, so unrelated `{{ ... }}` syntax (e.g. GitHub Actions expressions) survives.
"""

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

import argparse
import datetime
import re
import shutil
import subprocess
import sys
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "assets" / "template"

APP_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*){2,}$")
CRATE_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def camel_case(text: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in re.split(r"[^A-Za-z0-9]+", text) if part)


def kebab_case(text: str) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", text)
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def build_tokens(args: argparse.Namespace) -> dict[str, str]:
    app_id = args.app_id
    last_segment = app_id.rsplit(".", 1)[1]
    crate = args.crate or kebab_case(last_segment)
    if not CRATE_RE.match(crate):
        sys.exit(f"error: invalid crate name {crate!r}; pass --crate explicitly")
    type_prefix = camel_case(args.name) or camel_case(last_segment)
    if not type_prefix[:1].isalpha():
        type_prefix = "App" + type_prefix
    developer_id = args.developer_id or app_id.rsplit(".", 1)[0]
    return {
        "APP_ID": app_id,
        "APP_PATH": "/" + app_id.replace(".", "/"),
        "APP_NAME": args.name,
        "SUMMARY": args.summary,
        "CRATE": crate,
        "CRATE_IDENT": crate.replace("-", "_"),
        "TYPE_PREFIX": type_prefix,
        "DEVELOPER": args.developer,
        "DEVELOPER_ID": developer_id,
        "HOMEPAGE": args.homepage,
        "LICENSE": args.license,
        "DATE": datetime.datetime.now(datetime.UTC).date().isoformat(),
    }


def substitute(text: str, tokens: dict[str, str]) -> str:
    for key, value in tokens.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def format_rust_sources(dest: Path) -> None:
    """Run rustfmt on the generated sources, so `cargo fmt --check` passes whatever the substituted values' lengths.

    Best effort: without rustfmt the project is still generated, and the first `cargo fmt` fixes the layout.
    """
    rustfmt = shutil.which("rustfmt")
    sources = sorted(str(path.relative_to(dest)) for path in (dest / "src").glob("*.rs"))
    if rustfmt is None or not sources:
        print("note: rustfmt not found; run `cargo fmt` in the new project once", file=sys.stderr)
        return
    result = subprocess.run([rustfmt, "--edition", "2024", *sources], cwd=dest, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"warning: rustfmt failed, run `cargo fmt` in the new project:\n{result.stderr}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--app-id", required=True, help="Reverse-DNS application id, e.g. io.github.user.MyApp")
    parser.add_argument("--name", required=True, help='Human readable name, e.g. "My App"')
    parser.add_argument("--dest", required=True, type=Path, help="Destination directory (must not exist or be empty)")
    parser.add_argument("--summary", default="A GTK 4 application written in Rust")
    parser.add_argument("--developer", default="The Developers")
    parser.add_argument("--developer-id", help="AppStream developer id (default: app id without the last segment)")
    parser.add_argument("--homepage", default="https://example.com")
    parser.add_argument("--license", default="GPL-3.0-or-later", help="SPDX license identifier")
    parser.add_argument("--crate", help="Cargo package/binary name (default: kebab-case of the last app id segment)")
    args = parser.parse_args()

    if not APP_ID_RE.match(args.app_id):
        sys.exit(f"error: {args.app_id!r} is not a valid application id (need at least 3 dot-separated segments)")
    dest: Path = args.dest.expanduser().resolve()
    if dest.exists() and any(dest.iterdir()):
        sys.exit(f"error: {dest} exists and is not empty")

    tokens = build_tokens(args)
    for source in sorted(TEMPLATE_DIR.rglob("*")):
        relative = source.relative_to(TEMPLATE_DIR)
        target = dest / substitute(str(relative), tokens)
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(substitute(source.read_text(encoding="utf-8"), tokens), encoding="utf-8")
        shutil.copymode(source, target)

    leftovers = [
        str(path.relative_to(dest)) for path in dest.rglob("*") if path.is_file() and re.search(r"\{\{[A-Z_]+\}\}", path.read_text(encoding="utf-8"))
    ]
    if leftovers:
        sys.exit(f"error: unreplaced placeholders in: {', '.join(leftovers)}")

    format_rust_sources(dest)

    print(f"Created {tokens['APP_NAME']} ({tokens['APP_ID']}) in {dest}")
    for key in ("CRATE", "TYPE_PREFIX", "APP_PATH"):
        print(f"  {key.lower()}: {tokens[key]}")


if __name__ == "__main__":
    main()
