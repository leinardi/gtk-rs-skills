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

ifndef MK_LOCAL_TEST_INCLUDED
MK_LOCAL_TEST_INCLUDED := 1

PYTHON ?= python3

# Where template-check generates the sample app. Kept outside the repository so the generated
# project never shows up in `git status` or in pre-commit's file list.
TEMPLATE_CHECK_DIR ?= $(or $(TMPDIR),/tmp)/gtk-rs-skills-template-check
TEMPLATE_CHECK_APP_ID ?= io.github.example.TemplateCheck
TEMPLATE_CHECK_BIN := template-check
NEW_APP := $(REPO_ROOT)/skills/gtk-rs-new-app/scripts/new_app.py

# uv runs the tests when it is installed: it resolves requirements-dev.txt into a cached
# environment with no virtualenv to manage, and it works where pip refuses to touch the system
# interpreter (PEP 668). An explicit PYTHON= on the command line wins.
UV := $(shell command -v uv 2>/dev/null)
ifeq ($(origin PYTHON),command line)
UV :=
endif

UV_RUN = $(UV) run --no-project --with-requirements $(REPO_ROOT)/requirements-dev.txt python

.PHONY: dev-deps
dev-deps: ## Install the pinned Python development dependencies (pytest, commitizen)
ifneq ($(strip $(UV)),)
	@$(UV_RUN) -c 'import pytest'
	@printf 'dev dependencies ready in uv'"'"'s cache; make test uses them automatically.\n'
else
	@$(PYTHON) -m pip install -r $(REPO_ROOT)/requirements-dev.txt
endif

.PHONY: test
test: test-unit ## Run the generator unit tests (fast, no GTK needed)

.PHONY: test-unit
test-unit: ## Run the Python unit tests for the new-app generator
ifneq ($(strip $(UV)),)
	@$(UV_RUN) -m pytest
else
	@$(PYTHON) -m pytest
endif

.PHONY: template-generate
template-generate: ## Generate the sample app from the template into TEMPLATE_CHECK_DIR
	@rm -rf "$(TEMPLATE_CHECK_DIR)"
	@$(PYTHON) -I "$(NEW_APP)" --app-id "$(TEMPLATE_CHECK_APP_ID)" --name "Template Check" \
	  --crate "$(TEMPLATE_CHECK_BIN)" --dest "$(TEMPLATE_CHECK_DIR)"

.PHONY: template-check
template-check: template-generate ## Generate the sample app, lint it and build/test it with Meson (needs GTK 4.22, libadwaita 1.9)
	@cd "$(TEMPLATE_CHECK_DIR)" && cargo fmt --all -- --check
	@cd "$(TEMPLATE_CHECK_DIR)" && cargo clippy --all-targets -- -D warnings
	@cd "$(TEMPLATE_CHECK_DIR)" && meson setup _build -Dprofile=development
	@cd "$(TEMPLATE_CHECK_DIR)" && meson compile -C _build
	@cd "$(TEMPLATE_CHECK_DIR)" && meson test -C _build --print-errorlogs

.PHONY: template-flatpak
template-flatpak: template-generate ## Generate the sample app and build/test it with flatpak-builder (GNOME 50 SDK)
	@cd "$(TEMPLATE_CHECK_DIR)" && flatpak-builder --user --install-deps-from=flathub --force-clean \
	  _flatpak build-aux/$(TEMPLATE_CHECK_APP_ID).Devel.json > _flatpak.log 2>&1 \
	  || { tail -n 50 _flatpak.log >&2; exit 1; }
	@# flatpak-builder only warns about unknown manifest properties and then ignores them.
	@if grep -q 'Unknown property' "$(TEMPLATE_CHECK_DIR)/_flatpak.log"; then \
	  grep 'Unknown property' "$(TEMPLATE_CHECK_DIR)/_flatpak.log" >&2; exit 1; \
	fi

endif  # MK_LOCAL_TEST_INCLUDED
