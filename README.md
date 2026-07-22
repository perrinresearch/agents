# Perrin Research agents

Foundry workers and project provisioner. Polls `foundry.perrinresearch.com`.

## Setup

```bash
cd ~/perrinresearch/agents
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill credentials
```

Secrets stay in `.env` (never commit). Repo paths live in `config/project_repos.json`.

On Foundry (WSL/local API), set `FOUNDRY_REPOS_ROOT=/home/scott/perrinresearch/repos` so claim payloads include `repo_cwd`.

## Loops

### Cursor worker (recommended for code changes)

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

```bash
# Dry-run first (default in config/sync.json)
python -m src.project_sync --once

# Live provision: set PROJECT_SYNC_DRY_RUN=0 or "dry_run": false in config/sync.json
PROJECT_SYNC_DRY_RUN=0 python -m src.project_sync
```

Polls `GET /api/projects`. When a project has a non-empty `repo_slug`, ensures a private GitHub repo under `perrinresearch/` and a clone at `~/perrinresearch/repos/<slug>`, then upserts `project_repos.json`.

Audit log: `var/project_sync.log` (gitignored).
