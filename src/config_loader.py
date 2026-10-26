"""Unified configuration loader with backward compatibility.

Consolidates overlapping repo path settings from:
- config/workers.json (new unified config)
- config/sync.json (project_sync legacy)
- config/project_repos.json (project map)
- .env (environment variables)

Priority (highest first):
1. Environment variables (REPOS_ROOT, FOUNDRY_EVAL_PATH, etc.)
2. config/workers.json (new unified config)
3. config/sync.json (legacy fallback)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORKERS_CONFIG = ROOT / "config" / "workers.json"
SYNC_CONFIG = ROOT / "config" / "sync.json"
PROJECT_MAP = ROOT / "config" / "project_repos.json"


def load_unified_config() -> dict[str, Any]:
    """Load unified worker configuration with backward compatibility."""
    config: dict[str, Any] = {
        "repos_root": None,
        "foundry_eval_path": None,
        "workers": {},
    }
    
    # 1. Load workers.json if it exists
    if WORKERS_CONFIG.exists():
        with WORKERS_CONFIG.open() as f:
            config.update(json.load(f))
    
    # 2. Fallback to sync.json for repos_root
    if not config["repos_root"] and SYNC_CONFIG.exists():
        with SYNC_CONFIG.open() as f:
            sync_cfg = json.load(f)
            config["repos_root"] = sync_cfg.get("repos_root")
    
    # 3. Environment variable overrides (highest priority)
    if os.environ.get("REPOS_ROOT"):
        config["repos_root"] = os.environ["REPOS_ROOT"]
    
    if os.environ.get("FOUNDRY_EVAL_PATH"):
        config["foundry_eval_path"] = os.environ["FOUNDRY_EVAL_PATH"]
    
    # 4. Set defaults if still None
    if not config["repos_root"]:
        config["repos_root"] = str(Path.home() / "perrinresearch" / "repos")
    
    if not config["foundry_eval_path"]:
        repos_root = Path(config["repos_root"])
        config["foundry_eval_path"] = str(repos_root / "foundry-eval")
    
    # Resolve paths
    config["repos_root"] = str(Path(config["repos_root"]).expanduser().resolve())
    config["foundry_eval_path"] = str(Path(config["foundry_eval_path"]).expanduser().resolve())
    
    return config


def get_repos_root() -> Path:
    """Get the repos root directory with backward compatibility."""
    # Priority: ENV > workers.json > sync.json > default
    if os.environ.get("REPOS_ROOT"):
        return Path(os.environ["REPOS_ROOT"]).expanduser().resolve()
    
    config = load_unified_config()
    return Path(config["repos_root"])


def get_foundry_eval_path() -> Path:
    """Get the foundry-eval repository path."""
    config = load_unified_config()
    return Path(config["foundry_eval_path"])


if __name__ == "__main__":
    import sys
    config = load_unified_config()
    json.dump(config, sys.stdout, indent=2)
    print()
