# Everything runs in the Nix dev shell by default. CI already has the venv, so
# it overrides:  make audit-check RUN="uv run"
RUN ?= nix develop --command uv run

.PHONY: audit audit-check

# Regenerate docs/status.md from a fresh measurement.
audit:
	$(RUN) python scripts/audit.py --write

# Fail if the committed docs/status.md no longer matches the repo.
audit-check: audit
	git diff --exit-code -- docs/status.md
