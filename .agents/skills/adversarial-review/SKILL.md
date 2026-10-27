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
no findings is credible only after real attempts to break the change.

Copy this checklist and tick items as you go:

```text
Review progress:
- [ ] 1. Diff and intent established (default scope if none given)
- [ ] 2. AGENTS.md read; pinned versions noted
- [ ] 3. Code claims verified (compile, crate source, docs)
- [ ] 4. Consistency passes run
- [ ] 5. Gates run
- [ ] 6. Report written
```

## 1. Establish the diff

Never review from memory or from the user's description alone. With no scope
given, review the uncommitted work; if the tree is clean, review the branch
against `main`.

| User intent | Command |
| --- | --- |
| uncommitted changes | `git status --short`, then `git diff HEAD`; read every untracked file too |
| staged changes only | `git diff --staged` |
| branch, "this PR", "ready to merge" | `git diff main...HEAD` |
| commit range | `git diff <base>..<head>` |
| GitHub PR number | `gh pr view <n>`, then `gh pr diff <n>` |

## 2. Load project authority

Read `AGENTS.md`: its rules are review checks. Note the baseline versions in
`skills/gtk-rs-development/SKILL.md` and the template's `Cargo.toml`.

## 3. Verify code claims

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

## 4. Consistency passes

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

## 5. Gates

Run `make check` and `make test`. If anything under
`skills/gtk-rs-new-app/` changed, run `make template-check` (or
`make template-flatpak`); say so explicitly when the host cannot run it.

## 6. Report

Rank findings most severe first:

```text
<path>:<line> — <title> [blocker|major|minor]
Evidence: <the compile error, grep result, doc quote or failing command>
Fix: <the change>
```

Blocker: guidance that does not compile or a template that breaks the generated
app. Major: wrong or unsupported claim, inconsistency with the template,
untested template path. Minor: unclear wording, missing cross-link. End with
what was verified and what could not be.
