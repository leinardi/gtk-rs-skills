# Security policy

## Supported versions

Only the latest release gets security fixes. A fix ships as a new version: release tags are
immutable, so an existing version is never re-tagged. Update the plugin to the latest release before
reporting.

## Reporting a vulnerability

Report it privately through GitHub's [private vulnerability
reporting](https://github.com/leinardi/gtk-rs-skills/security/advisories/new), not in a public
issue, a pull request or a discussion. Include the plugin version, the agent and its version, your
operating system, and the steps or the prompt that trigger the problem.

This is a project maintained in spare time, so reports are handled on a best-effort basis. You will
get an answer in the advisory, and the fix, once released, is credited there unless you prefer
otherwise.

## Scope

This repository ships instructions that coding agents follow and a project template that becomes
someone's code. In scope:

- guidance or template content that leads an agent to introduce a vulnerability into a user's
  project (for example over-broad Flatpak permissions presented as the default, unsafe handling of
  secrets, or disabling sandbox or validation steps);
- the generator (`skills/gtk-rs-new-app/scripts/new_app.py`) writing outside the requested
  destination, overwriting existing files, or executing content from the template;
- instructions that would make an agent run untrusted code or exfiltrate data.

Out of scope: vulnerabilities in GTK, libadwaita, gtk-rs or other upstream projects (report them
upstream), and generated projects after the user has modified them.
