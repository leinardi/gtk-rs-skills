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
import urllib.parse
from collections.abc import Callable
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "assets" / "template"

APP_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*){2,}$")
CRATE_RE = re.compile(r"^[a-z][a-z0-9-]*$")
DEVELOPER_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)+$")
LICENSE_RE = re.compile(r"^[A-Za-z0-9.+() -]+$")
MESON_VAR_RE = re.compile(r"@[-a-zA-Z0-9_]+@")

# Free-text values (name, summary, developer) may contain any printable character, and a homepage
# URL may contain `&` and the like in its query. They land in string literals of several languages,
# so each file kind gets its own escaping; every other token is validated to a character set that
# needs none.
FREE_TEXT_TOKENS = frozenset({"APP_NAME", "SUMMARY", "DEVELOPER"})
ESCAPED_TOKENS = FREE_TEXT_TOKENS | {"HOMEPAGE", "BUGTRACKER"}


def escape_double_quoted(value: str) -> str:
    """Rust, TOML basic strings and Blueprint strings share the backslash/double-quote escapes."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def escape_xml(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")


def escape_desktop_entry(value: str) -> str:
    # Desktop Entry spec: backslash starts an escape sequence in string values.
    return value.replace("\\", "\\\\")


def is_valid_homepage(url: str) -> bool:
    """An absolute http(s) URL with a host; query and fragment may hold anything printable (escaped per file kind)."""
    if any(char.isspace() or not char.isprintable() for char in url):
        return False
    try:
        parts = urllib.parse.urlsplit(url)
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.hostname)


def bugtracker_url(homepage: str) -> str:
    """`<homepage path>/issues`, without the homepage's query or fragment (the forge convention)."""
    try:
        parts = urllib.parse.urlsplit(homepage)
    except ValueError:
        return homepage  # validate() rejects it before anything is written
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + "/issues", "", ""))


def escaper_for(path: Path) -> Callable[[str], str]:
    name = path.name
    if name.endswith((".rs", ".toml", ".blp")):
        return escape_double_quoted
    if ".xml" in name:
        return escape_xml
    if ".desktop" in name:
        return escape_desktop_entry
    return lambda value: value


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
        "BUGTRACKER": bugtracker_url(args.homepage),
        "LICENSE": args.license,
        "DATE": datetime.datetime.now(datetime.UTC).date().isoformat(),
    }


def substitute(text: str, tokens: dict[str, str], escape: Callable[[str], str] | None = None) -> str:
    for key, value in tokens.items():
        replacement = escape(value) if escape is not None and key in ESCAPED_TOKENS else value
        text = text.replace("{{" + key + "}}", replacement)
    return text


def validate(args: argparse.Namespace, tokens: dict[str, str]) -> None:
    for key in FREE_TEXT_TOKENS:
        if not tokens[key].strip() or any(not char.isprintable() for char in tokens[key]):
            sys.exit(f"error: {key.lower().replace('_', '-')} must be non-empty printable text on one line")
        # Meson's configure_file() would replace it with a (missing) configuration variable.
        if MESON_VAR_RE.search(tokens[key]):
            sys.exit(f"error: {key.lower().replace('_', '-')} must not contain an @word@ sequence (Meson substitutes it)")
    if not DEVELOPER_ID_RE.match(tokens["DEVELOPER_ID"]):
        sys.exit(f"error: {tokens['DEVELOPER_ID']!r} is not a valid developer id (reverse DNS, e.g. io.github.alice)")
    if not is_valid_homepage(args.homepage):
        sys.exit(f"error: {args.homepage!r} is not a valid http(s) URL")
    if MESON_VAR_RE.search(args.homepage):
        sys.exit("error: homepage must not contain an @word@ sequence (Meson substitutes it)")
    if not LICENSE_RE.match(args.license):
        sys.exit(f"error: {args.license!r} is not a valid SPDX license expression")


def format_rust_sources(dest: Path) -> None:
    """Run rustfmt on the generated sources, so `cargo fmt --check` passes whatever the substituted values' lengths.

    Without rustfmt the project is still generated, and the first `cargo fmt` fixes the layout. A rustfmt
    failure means the generated Rust does not parse, which is a generator bug, so it fails the run.
    """
    rustfmt = shutil.which("rustfmt")
    sources = sorted(str(path.relative_to(dest)) for path in (dest / "src").glob("*.rs"))
    if rustfmt is None or not sources:
        print("note: rustfmt not found; run `cargo fmt` in the new project once", file=sys.stderr)
        return
    result = subprocess.run([rustfmt, "--edition", "2024", *sources], cwd=dest, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.exit(f"error: the generated Rust sources do not parse (rustfmt failed):\n{result.stderr}")


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
    validate(args, tokens)
    for source in sorted(TEMPLATE_DIR.rglob("*")):
        relative = source.relative_to(TEMPLATE_DIR)
        target = dest / substitute(str(relative), tokens)
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(substitute(source.read_text(encoding="utf-8"), tokens, escaper_for(source)), encoding="utf-8")
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
