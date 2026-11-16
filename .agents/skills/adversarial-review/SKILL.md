---
name: adversarial-review
description: >
  Adversarial review of changes to gtk-rs-skills: working tree, staged diff,
  branch, commit range, or PR. Hunts for gtk-rs guidance that does not compile
  at the pinned crate versions, invented or outdated APIs, references that
  contradict the template or each other, template changes that break the
  generated app, generator bugs, weakened skill triggering, and CI/tooling gaps,
  then reports ranked findings. Use when the user asks to review changes, a
  diff, a PR, a branch or a commit, check work before committing, assess merge
  readiness, or poke holes in a change to this repository.
---

# Adversarial Review - gtk-rs-skills

Assume the change teaches an agent something wrong until proven otherwise. Find
the snippet that does not compile, the API that does not exist at the pinned
version, the claim no source supports, or the template edit that produces an app
which does not build, run or validate. Do not praise or restyle. A review with
no findings is credible only after real attempts to break the change. Read
`AGENTS.md` first: its rules are review checks. Then note the baseline versions
in `skills/gtk-rs-development/SKILL.md` and the template's `Cargo.toml`.

## Scope

When invoked directly: review the scope the user gives (`gh pr view <n>`, then
`gh pr diff <n>` for a PR); with none, the uncommitted work (`git diff HEAD`
plus every untracked file); on a clean tree, `git diff main...HEAD`. Never
review from memory or from the user's description alone: read the actual diff.

## Verify code claims

For every added or changed snippet, API name, signature, feature gate, version
number or behavioural claim:

- **Rust**: generate an app (`make template-generate`), copy it, put the
  snippet in a module with minimal scaffolding and run `cargo check` and
  `cargo clippy -- -W clippy::pedantic`. A snippet that only compiles after
  non-obvious changes is a finding.
- **Blueprint**: compile it with `blueprint-compiler compile` (wrap fragments
  in a minimal template).
- **API existence and gates**: grep the crate source under
  `~/.cargo/registry/src/*/` (`src/auto/` for generated bindings,
  `auto/mod.rs` for type-level `cfg(feature = ...)` gates, `*-macros/src/lib.rs`
  for macro syntax).
- **Runtime behaviour** (CSS classes, auto-loaded resources, warnings): check
  the installed libadwaita/GTK docs or stylesheet, or run the generated app
  under `xvfb-run`. Unverifiable claims must carry `(verify)`.

## Consistency passes

- **Template ↔ references**: any build, config, resource, schema, Flatpak or CI
  statement in `project-layout-and-build.md`, `packaging-and-ci.md`,
  `testing-and-debugging.md`, `settings-and-state.md` or the new-app `SKILL.md`
  matches the template as changed.
- **Template ↔ generator**: every `{{TOKEN}}` used in the template is produced
  by `build_tokens()`; new files/renames are covered by `tests/`; tokens never
  appear inside GitHub `${{ }}` expressions in a way the generator would touch.
- **References ↔ each other**: no two files give conflicting advice; sibling
  links use exact file names; the routing table in
  `gtk-rs-development/SKILL.md` lists every reference.
- **Deprecated APIs**: new text uses nothing from the left column of
  `deprecated-apis.md`, and a newly deprecated API is added there.
- **Skill metadata**: descriptions still say what and when, stay under 1024
  characters, and were not narrowed in a way that stops triggering.
- **Attribution**: adapted third-party material is in `THIRD_PARTY_NOTICES.md`.
- **Commit types**: `feat`/`fix` only for what ships (skills, references,
  template); tooling and CI never use `perf`/`refactor`.

## Gates

Do not re-run the repository's gates (`make check`, `make test`, `make
template-check`, `make template-flatpak`). The implementer is expected to run
them and CI runs them again, but do not assume either happened: report the
verification evidence you actually have (under adversarial-review-loop,
`verification.txt` when supplied, which is the implementer's claim and not
proof; when invoked directly, the CI status or gate output in the PR or session)
and what is missing, such as a change under `skills/gtk-rs-new-app/` with no
`make template-check` (or `make template-flatpak`) result, which a host may be
unable to run. Judge what is missing against `AGENTS.md`'s *Validation matrix*:
every changed path owes the checks its row names. Never say that a check you did
not see passed. Judge whether the change's tests prove it: a behaviour change
needs a test that fails on the old code. When invoked directly, one focused test
(one snippet through `cargo check`, or `blueprint-compiler compile`) may be run
to confirm a single suspected finding, never a full suite or gate. A candidate
that is neither reproduced nor traced end to end is dropped.

## Report

Rank findings most severe first:

```text
<path>:<line> — <title> [blocker|major|minor]
Evidence: <the compile error, grep result, doc quote or failing command>
Fix: <the change>
```

Blocker: guidance that does not compile or a template that breaks the generated
app. Major: wrong or unsupported claim, inconsistency with the template,
untested template path. Minor: unclear wording, missing cross-link. End with
a verdict and one line: `Verification evidence: seen … / missing …`.
