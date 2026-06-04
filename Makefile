# ── Variables ──────────────────────────────────────────────────────────────
# This Makefile is intended to be run locally on the Raspberry Pi.
APP_DIR ?= /opt/e-ink-gadget
ETC_DIR ?= /etc/e-ink-gadget
STATE_DIR ?= /var/lib/e-ink-gadget
PYTHON  ?= python3
SUDO    ?= sudo

# ── Help ───────────────────────────────────────────────────────────────────
.PHONY: help
help:
	@printf '%s\n' 'Targets:'
	@printf '%s\n' '  make install-code     Copy this checkout into APP_DIR locally (no ssh/rsync)'
	@printf '%s\n' '  make install-service  Install/update systemd units and start services'
	@printf '%s\n' '  make restart          Restart the display daemon'
	@printf '%s\n' '  make status           Show service status'
	@printf '%s\n' '  make logs             Follow display daemon logs'
	@printf '%s\n' '  make test             Run tests'
	@printf '%s\n' '  make clean            Remove local Python/test cache artifacts'

# ── Local install ──────────────────────────────────────────────────────────
.PHONY: install-code
install-code:
	@case "$(APP_DIR)" in /*) ;; *) printf 'APP_DIR must be absolute: %s\n' "$(APP_DIR)" >&2; exit 2;; esac
	@if [ "$$(pwd -P)" = "$$(cd "$(APP_DIR)" 2>/dev/null && pwd -P)" ]; then \
		printf 'Already running from %s; skipping code copy\n' "$(APP_DIR)"; \
	else \
		printf 'Copying checkout to %s locally\n' "$(APP_DIR)"; \
		$(SUDO) mkdir -p "$(APP_DIR)"; \
		tar \
			--exclude='.git' \
			--exclude='.venv' \
			--exclude='.pytest_cache' \
			--exclude='__pycache__' \
			--exclude='*.pyc' \
			-cf - . | $(SUDO) tar -C "$(APP_DIR)" -xf -; \
	fi

# Backwards-compatible alias for old docs/users. It is now local-only.
.PHONY: deploy-rpi
deploy-rpi: install-code

# ── Install service ───────────────────────────────────────────────────────
.PHONY: install-service
install-service: install-code
	@case "$(APP_DIR)" in /*) ;; *) printf 'APP_DIR must be absolute: %s\n' "$(APP_DIR)" >&2; exit 2;; esac
	$(SUDO) install -d -m 0755 "$(ETC_DIR)" "$(STATE_DIR)/gallery"
	@if [ ! -f "$(ETC_DIR)/eink-gadget.env" ]; then \
		$(SUDO) install -m 0644 "$(APP_DIR)/pi/setup/eink-gadget.env.example" "$(ETC_DIR)/eink-gadget.env"; \
		printf 'Installed default %s\n' "$(ETC_DIR)/eink-gadget.env"; \
	else \
		printf 'Preserving existing %s\n' "$(ETC_DIR)/eink-gadget.env"; \
	fi
	$(SUDO) chmod 0755 "$(APP_DIR)/pi/setup/gadget-usb.sh"
	@tmpdir="$$(mktemp -d)"; \
	trap 'rm -rf "$$tmpdir"' EXIT; \
	$(PYTHON) "$(APP_DIR)/tools/render_systemd_units.py" --app-dir "$(APP_DIR)" --template-dir "$(APP_DIR)/pi/setup" --output-dir "$$tmpdir" >/dev/null; \
	if command -v systemd-analyze >/dev/null 2>&1; then systemd-analyze verify "$$tmpdir"/*.service; fi; \
	$(SUDO) install -m 0644 "$$tmpdir/eink-gadget.service" /etc/systemd/system/eink-gadget.service; \
	$(SUDO) install -m 0644 "$$tmpdir/eink-gadget-setup.service" /etc/systemd/system/eink-gadget-setup.service
	$(SUDO) systemctl daemon-reload
	$(SUDO) systemctl enable --now eink-gadget-setup
	$(SUDO) systemctl enable --now eink-gadget

# ── Service helpers ────────────────────────────────────────────────────────
.PHONY: restart
restart:
	$(SUDO) systemctl daemon-reload
	$(SUDO) systemctl restart eink-gadget

.PHONY: status
status:
	$(SUDO) systemctl status eink-gadget-setup --no-pager
	$(SUDO) systemctl status eink-gadget --no-pager

.PHONY: logs
logs:
	journalctl -u eink-gadget -f

# ── Test ──────────────────────────────────────────────────────────────────
.PHONY: test
test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m pytest tests/ -v

# ── Clean ─────────────────────────────────────────────────────────────────
.PHONY: clean
clean:
	find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete
	rm -rf .pytest_cache
