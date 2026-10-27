# Contributing

Read [`AGENTS.md`](AGENTS.md) before changing anything: it holds the rules a change must not break.
It applies equally to human and AI-assisted contributions.

## Setup

Install [`pre-commit`](https://pre-commit.com/), then install the hooks once with
`make pre-commit-install`. It installs both the `pre-commit` and the `commit-msg` hooks, so commit
messages are checked when you commit, not when the release runs.

```console
make dev-deps         # the pinned development dependencies, once per checkout
make test             # the generator unit tests
make check            # pre-commit on every file
make check-stage      # pre-commit on the staged files only
make template-check   # generate an app from the template, lint it, build and test it with Meson
```

`make help` lists every target. `make check` only sees files git already knows about, so run
`git add -N` on a new file before it.

`make template-check` needs Rust ≥ 1.92, GTK ≥ 4.22, libadwaita ≥ 1.9, `blueprint-compiler`,
`meson`, `desktop-file-utils` and `appstream` on the host (Ubuntu 26.04 / Fedora 44 or newer). Without
them, use `make template-flatpak` (needs `flatpak-builder`; downloads the GNOME 50 SDK) or rely on
the `template-check` CI job.

## Changing guidance

- Verify every Rust and Blueprint snippet you add or change against the pinned crate versions
  (see "Verifying snippets" in `AGENTS.md`). Say in the pull request how you verified it.
- Cite a source for behaviour claims: docs.rs at the pinned version, the GTK/libadwaita docs, or a
  real app (by path).
- Keep a change to the template, the references describing it and the generator tests together in
  one pull request.

## Pull requests

Every change lands through a pull request against `main` with the required CI checks green.

## Commit messages

All commits must follow [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)
with a scope: `<type>(<scope>)[!]: <description>`. The `conventional-pre-commit` hook enforces this
on `commit-msg`, and the `conventional-commits` CI job checks it again on every pull request.
Release notes are not built from these messages: `gh release create --generate-notes` lists the
merged pull requests by title.

Use a lower-case, imperative description:

```text
fix(references): use the v1_8 gate for PreferencesGroup::bind_model
feat(template): add a preferences dialog to the generated app
ci(template-check): cache the flatpak-builder state
```

Mark breaking changes with `!` before the colon or a `BREAKING CHANGE: <description>` footer.

## Versioning

A release with no explicit version is derived from these types by commitizen: since the last
release, any `feat` makes the next release a minor, any `fix`, `perf` or `refactor` a patch, and `!`
or a `BREAKING CHANGE:` footer a major; `build`, `chore`, `ci`, `docs`, `revert`, `style` and `test`
bump nothing. See [`docs/release.md`](docs/release.md).

Pull requests are merged with merge commits; squash and rebase merging are disabled. Every commit in
a pull request therefore lands on `main` as it is and counts toward the version, so each commit
needs a correct type, not just the pull request as a whole.

commitizen reads every line of a message, not only the subject, and the merge commit's body is the
pull request's title. A line that opens with a word starting `feat`, `fix`, `refactor` or `perf`
followed by a colon (`fixtures:`, `performance:`) bumps as that type, and a line opening with
`word!:` cuts a major. Keep such lines out of commit bodies, and give the pull request a title whose
type matches what it ships.
