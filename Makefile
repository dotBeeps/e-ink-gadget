# ── Variables ──────────────────────────────────────────────────────────────
PI_HOST  ?= raspberrypi.local
PI_USER  ?= pi
PI_DIR   ?= /opt/e-ink-gadget

# ── Deploy ────────────────────────────────────────────────────────────────
.PHONY: deploy-rpi
deploy-rpi:
	rsync -avz --delete \
		--exclude '.git' \
		--exclude '__pycache__' \
		--exclude 'tests' \
		pi/ "$(PI_USER)@$(PI_HOST):$(PI_DIR)/pi/"

# ── Install service ───────────────────────────────────────────────────────
.PHONY: install-service
install-service: deploy-rpi
	ssh "$(PI_USER)@$(PI_HOST)" "\
		sudo cp $(PI_DIR)/pi/setup/eink-gadget.service /etc/systemd/system/ && \
		sudo systemctl daemon-reload && \
		sudo systemctl enable --now eink-gadget \
	"

# ── Test ──────────────────────────────────────────────────────────────────
.PHONY: test
test:
	python3 -m pytest tests/ -v

# ── Clean ─────────────────────────────────────────────────────────────────
.PHONY: clean
clean:
	find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete
	rm -rf .pytest_cache
