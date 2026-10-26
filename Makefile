.PHONY: help up down status restart logs-cursor logs-sync logs-eval check list install-services uninstall-services

help:
	@echo "Foundry Worker Management"
	@echo "========================="
	@echo ""
	@echo "Quick commands:"
	@echo "  make up          - Start all workers"
	@echo "  make down        - Stop all workers"
	@echo "  make status      - Show worker status"
	@echo "  make restart     - Restart all workers"
	@echo "  make check       - Check configuration"
	@echo ""
	@echo "Logs:"
	@echo "  make logs-cursor       - Tail Cursor worker logs"
	@echo "  make logs-sync         - Tail project_sync logs"
	@echo "  make logs-eval         - Tail foundry-eval logs"
	@echo ""
	@echo "Systemd services:"
	@echo "  make install-services   - Install systemd user services"
	@echo "  make uninstall-services - Remove systemd user services"
	@echo "  make systemd-status     - Show systemd service status"
	@echo ""
	@echo "Individual workers:"
	@echo "  make start-cursor      - Start only Cursor worker"
	@echo "  make start-sync        - Start only project_sync"
	@echo "  make start-eval        - Start only foundry-eval"
	@echo "  make stop-cursor       - Stop only Cursor worker"
	@echo "  make stop-sync         - Stop only project_sync"
	@echo "  make stop-eval         - Stop only foundry-eval"

up:
	@./workers.py start

down:
	@./workers.py stop

status:
	@./workers.py status

restart:
	@./workers.py restart

check:
	@./workers.py check

list:
	@./workers.py list

logs-cursor:
	@./workers.py logs cursor

logs-sync:
	@./workers.py logs project_sync

logs-eval:
	@./workers.py logs foundry_eval

start-cursor:
	@./workers.py start cursor

start-sync:
	@./workers.py start project_sync

start-eval:
	@./workers.py start foundry_eval

stop-cursor:
	@./workers.py stop cursor

stop-sync:
	@./workers.py stop project_sync

stop-eval:
	@./workers.py stop foundry_eval

install-services:
	@echo "Installing systemd user services..."
	@mkdir -p ~/.config/systemd/user
	@cp deploy/foundry-cursor-worker.service ~/.config/systemd/user/
	@cp deploy/foundry-project-sync.service ~/.config/systemd/user/
	@cp deploy/foundry-eval-worker.service ~/.config/systemd/user/
	@systemctl --user daemon-reload
	@echo ""
	@echo "Services installed. To enable and start:"
	@echo "  systemctl --user enable --now foundry-cursor-worker.service"
	@echo "  systemctl --user enable --now foundry-project-sync.service"
	@echo "  systemctl --user enable --now foundry-eval-worker.service"
	@echo ""
	@echo "To enable all at once:"
	@echo "  make enable-services"

enable-services:
	@systemctl --user enable --now foundry-cursor-worker.service
	@systemctl --user enable --now foundry-project-sync.service
	@systemctl --user enable --now foundry-eval-worker.service
	@echo "All services enabled and started"

disable-services:
	@systemctl --user disable --now foundry-cursor-worker.service
	@systemctl --user disable --now foundry-project-sync.service
	@systemctl --user disable --now foundry-eval-worker.service
	@echo "All services disabled and stopped"

uninstall-services:
	@echo "Stopping and disabling services..."
	@systemctl --user disable --now foundry-cursor-worker.service 2>/dev/null || true
	@systemctl --user disable --now foundry-project-sync.service 2>/dev/null || true
	@systemctl --user disable --now foundry-eval-worker.service 2>/dev/null || true
	@rm -f ~/.config/systemd/user/foundry-cursor-worker.service
	@rm -f ~/.config/systemd/user/foundry-project-sync.service
	@rm -f ~/.config/systemd/user/foundry-eval-worker.service
	@systemctl --user daemon-reload
	@echo "Services uninstalled"

systemd-status:
	@echo "Systemd service status:"
	@echo ""
	@systemctl --user status foundry-cursor-worker.service --no-pager || true
	@echo ""
	@systemctl --user status foundry-project-sync.service --no-pager || true
	@echo ""
	@systemctl --user status foundry-eval-worker.service --no-pager || true
