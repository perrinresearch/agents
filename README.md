# Perrin Research agents

Foundry workers and project provisioner. Polls `foundry.perrinresearch.com`.

## Setup

### Initial Setup (This Repo)

```bash
cd ~/perrinresearch/agents
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill credentials
```

Secrets stay in `.env` (never commit). Repo paths are centrally configured in `config/workers.json` with backward compatibility for existing configs.

On Foundry (WSL/local API), set `FOUNDRY_REPOS_ROOT=/home/scott/perrinresearch/repos` so claim payloads include `repo_cwd`.

### Foundry-Eval Setup (Separate Repo)

The foundry-eval worker runs from a separate repository. If not already set up:

```bash
# Clone if needed (unified manager expects it at ~/perrinresearch/repos/foundry-eval)
cd ~/perrinresearch/repos
git clone https://github.com/perrinresearch/foundry-eval.git
cd foundry-eval

# Set up its venv and dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Create its .env file
cp .env.example .env   # fill credentials (FOUNDRY_*, OLLAMA_HOST)
```

If your foundry-eval repo is in a different location, update `config/workers.json`:
```json
{
  "foundry_eval_path": "/path/to/your/foundry-eval"
}
```

Or set the environment variable:
```bash
export FOUNDRY_EVAL_PATH=/path/to/your/foundry-eval
```

### Ollama Setup

The foundry-eval worker requires Ollama:

```bash
# Install Ollama (if not already installed)
curl https://ollama.ai/install.sh | sh

# Start Ollama service (or ensure it's running)
ollama serve

# Pull the required model
ollama pull qwen3-coder:latest
```

Verify `OLLAMA_HOST` in both repos' `.env` files (typically `http://127.0.0.1:11434`).

## Quick Start: Unified Worker Management

All local workers can now be managed with a single command:

```bash
# Check configuration and dependencies
make check

# Start all workers
make up

# Check status
make status

# View logs
make logs-cursor      # Cursor worker logs
make logs-sync        # Project sync logs
make logs-eval        # Foundry-eval logs

# Stop all workers
make down

# Restart all workers
make restart
```

For individual worker control:
```bash
make start-cursor     # Start only Cursor worker
make stop-sync        # Stop only project_sync
```

### Auto-start with systemd (WSL)

To have workers start automatically and restart on failure:

```bash
# Install systemd service units
make install-services

# Enable and start all services
make enable-services

# Check service status
make systemd-status

# Disable all services
make disable-services

# Completely remove services
make uninstall-services
```

Services will auto-start on boot and restart on failure. Logs are available via:
- `journalctl --user -u foundry-cursor-worker.service -f`
- `journalctl --user -u foundry-project-sync.service -f`
- `journalctl --user -u foundry-eval-worker.service -f`

**Note**: The foundry-eval worker requires:
1. The `foundry-eval` repo cloned (default: `~/perrinresearch/repos/foundry-eval`)
2. A separate venv in that repo with foundry-eval dependencies
3. A `.env` file in the foundry-eval repo
4. Ollama running

Customize the foundry-eval path in `config/workers.json` if needed.

## Configuration

All repo paths are now centrally managed in `config/workers.json`:

```json
{
  "repos_root": "/home/scott/perrinresearch/repos",
  "foundry_eval_path": "/home/scott/perrinresearch/repos/foundry-eval",
  "workers": { ... }
}
```

Environment variables override config file values:
- `REPOS_ROOT` - Base directory for all project repos
- `FOUNDRY_EVAL_PATH` - Path to foundry-eval repo

The system maintains backward compatibility with:
- `config/sync.json` (project_sync legacy config)
- `config/project_repos.json` (project path mappings)
- `.env` environment variables

## Individual Worker Usage (Manual)

### Cursor worker (recommended for code changes)

**Via unified manager** (recommended):
```bash
make start-cursor     # Start in background
make logs-cursor      # View logs
make stop-cursor      # Stop
```

**Direct invocation** (for testing):
```bash
# Poll forever: claim → local Cursor agent → heartbeat → complete
python -m src.cursor_worker

# One claim then exit
python -m src.cursor_worker --once

# No Cursor call (writes a dry-run marker file under cwd)
CURSOR_DRY_RUN=1 python -m src.cursor_worker --once
```

Requires `CURSOR_API_KEY` unless `CURSOR_DRY_RUN=1`. Uses lease APIs (`/claim`, `/heartbeat`, `/complete`). Claim payload should include `repo_slug` / `repo_cwd`.

### Waypoint pilot

```bash
# Creates a synthetic Waypoint prompt and runs the Cursor worker once (dry)
python -m src.pilot_waypoint --dry-run

# Live Cursor SDK run
python -m src.pilot_waypoint
```

Metrics land in `var/cursor_pilot_waypoint.json`. Human review remains mandatory (task → `testing`).

### Ollama prompt worker

```bash
python -m src.worker
```

Polls pending agent prompts → Ollama → claim/complete (legacy PATCH helpers).

### Project sync (repo initiation)

**Via unified manager** (recommended):
```bash
make start-sync       # Start in background
make logs-sync        # View logs
make stop-sync        # Stop
```

**Direct invocation** (for testing):
```bash
# Dry-run first (default in config/sync.json)
python -m src.project_sync --once

# Live provision: set PROJECT_SYNC_DRY_RUN=0 or "dry_run": false in config/sync.json
PROJECT_SYNC_DRY_RUN=0 python -m src.project_sync
```

Polls `GET /api/projects`. When a project has a non-empty `repo_slug`, ensures a private GitHub repo under `perrinresearch/` and a clone at `~/perrinresearch/repos/<slug>`, then upserts `project_repos.json`.

Audit log: `var/project_sync.log` (gitignored).

### Foundry-eval worker

**Via unified manager** (recommended):
```bash
make start-eval       # Start in background
make logs-eval        # View logs
make stop-eval        # Stop
```

**Note**: This worker runs from the `foundry-eval` repository, not this one. The unified manager references it via `config/workers.json`. Direct invocation requires:

```bash
cd ~/perrinresearch/repos/foundry-eval
source .venv/bin/activate
python -m foundry_eval.worker
```

Requires Ollama running (`OLLAMA_HOST` in `.env`).

## Troubleshooting

### Workers not starting

1. Check configuration: `make check`
2. Verify all required files exist (`.env`, `.venv`, etc.)
3. Check logs: `make logs-cursor` (or `logs-sync`, `logs-eval`)
4. Test manually: `source .venv/bin/activate && python -m src.cursor_worker --once`

### Systemd services not working

```bash
# Check service status
make systemd-status

# View detailed logs
journalctl --user -u foundry-cursor-worker.service -f

# Reload systemd if you edited service files
systemctl --user daemon-reload

# Restart a specific service
systemctl --user restart foundry-cursor-worker.service
```

### Foundry-eval worker issues

Common issues:
- **Ollama not running**: Start with `ollama serve` or check system service
- **Wrong path**: Update `config/workers.json` or set `FOUNDRY_EVAL_PATH` environment variable
- **Missing .env**: Foundry-eval needs its own `.env` in its repo directory
- **Model not downloaded**: Run `ollama pull qwen3-coder:latest`

### Path configuration issues

The unified system consolidates path settings with this priority (highest first):
1. Environment variables (`REPOS_ROOT`, `FOUNDRY_EVAL_PATH`)
2. `config/workers.json`
3. `config/sync.json` (backward compatibility)
4. Defaults

To check current configuration: `python3 -m src.config_loader`

### Migration from manual startup

If you were previously starting workers manually:

1. Stop all manual processes
2. Run `make check` to verify configuration
3. Update `config/workers.json` if paths differ from defaults
4. Test with `make up` and `make status`
5. Once working, install systemd services: `make install-services && make enable-services`

The eval worker should now check in regularly if configured correctly.

## Architecture

### Workers

- **cursor**: Claims agent prompts from Foundry, runs Cursor SDK local agent, completes with results
- **project_sync**: Monitors Foundry projects, auto-creates GitHub repos and local clones when `repo_slug` is set
- **foundry_eval**: Claims evaluation tasks from Foundry, runs Ollama inference, returns results

### Configuration Consolidation

Previously, repo paths were scattered across:
- `REPOS_ROOT` env var
- `config/sync.json` for project_sync
- `config/project_repos.json` for project mappings
- Hardcoded defaults

Now they're unified in `config/workers.json` with backward compatibility preserved via `src/config_loader.py`.

### Process Management

Two modes available:

1. **Manual (via Makefile/workers.py)**: 
   - Start/stop on demand
   - Logs in `var/logs/`
   - PIDs in `var/pids/`
   - Good for development

2. **Systemd (via services)**:
   - Auto-start on boot
   - Auto-restart on failure
   - System-level monitoring
   - Logs via `journalctl`
   - Good for production/always-on

Both use the same unified configuration.
