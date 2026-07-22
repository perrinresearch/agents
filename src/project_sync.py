"""Poll Foundry projects and provision local GitHub repos when repo_slug is set.

Security defaults:
- Opt-in only (non-empty repo_slug)
- GitHub owner allowlisted (perrinresearch)
- Private repos by default
- Idempotent; never deletes
- Dry-run via config/env (PROJECT_SYNC_DRY_RUN=1)

Entry: python -m src.project_sync
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .foundry_client import FoundryClient

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SYNC_CONFIG = ROOT / "config" / "sync.json"
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,46}$")
ALLOWED_OWNER = "perrinresearch"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_sync_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or Path(os.environ.get("PROJECT_SYNC_CONFIG", str(DEFAULT_SYNC_CONFIG)))
    with cfg_path.open() as f:
        cfg = json.load(f)
    # Env overrides
    if "PROJECT_SYNC_DRY_RUN" in os.environ:
        cfg["dry_run"] = os.environ["PROJECT_SYNC_DRY_RUN"].strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    if "PROJECT_SYNC_POLL_SEC" in os.environ:
        cfg["poll_sec"] = float(os.environ["PROJECT_SYNC_POLL_SEC"])
    owner = cfg.get("github_owner") or ALLOWED_OWNER
    if owner != ALLOWED_OWNER:
        raise RuntimeError(f"github_owner must be {ALLOWED_OWNER!r}, got {owner!r}")
    cfg["github_owner"] = owner
    return cfg


def audit_log(var_dir: Path, message: str) -> None:
    var_dir.mkdir(parents=True, exist_ok=True)
    line = f"{utc_now()} {message}\n"
    print(message, flush=True)
    with (var_dir / "project_sync.log").open("a") as f:
        f.write(line)


def load_map(map_path: Path) -> dict[str, Any]:
    if not map_path.exists():
        return {"projects": {}}
    with map_path.open() as f:
        data = json.load(f)
    if "projects" not in data:
        data = {"projects": data}
    return data


def save_map(map_path: Path, data: dict[str, Any], dry_run: bool) -> None:
    if dry_run:
        return
    map_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = map_path.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    tmp.replace(map_path)


def load_state(var_dir: Path) -> dict[str, Any]:
    path = var_dir / "sync_state.json"
    if not path.exists():
        return {"known": {}, "last_success": None}
    with path.open() as f:
        return json.load(f)


def save_state(var_dir: Path, state: dict[str, Any], dry_run: bool) -> None:
    if dry_run:
        return
    var_dir.mkdir(parents=True, exist_ok=True)
    path = var_dir / "sync_state.json"
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
    tmp.replace(path)


def validate_slug(slug: str) -> bool:
    return bool(SLUG_RE.match(slug))


def map_has_ready(repo_map: dict[str, Any], ref: str, slug: str, path: Path) -> bool:
    projects = repo_map.get("projects") or {}
    entry = projects.get(ref) or projects.get(slug)
    if not entry:
        return False
    mapped = Path(entry["path"] if isinstance(entry, dict) else entry)
    return mapped.resolve() == path.resolve() and path.is_dir() and (path / ".git").exists()


def github_repo_exists(owner: str, slug: str) -> bool:
    r = subprocess.run(
        ["gh", "repo", "view", f"{owner}/{slug}", "--json", "name"],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


def scaffold_readme(path: Path, slug: str, ref: str, dry_run: bool) -> None:
    readme = path / "README.md"
    gitignore = path / ".gitignore"
    if dry_run:
        print(f"DRY-RUN: write {readme} and {gitignore}", flush=True)
        return
    if not readme.exists():
        readme.write_text(
            f"# {slug}\n\nProvisioned by local Foundry project_sync for `{ref}`.\n",
            encoding="utf-8",
        )
    if not gitignore.exists():
        gitignore.write_text(
            ".env\n.venv/\nnode_modules/\ndata/\n__pycache__/\n*.pyc\n",
            encoding="utf-8",
        )


def ensure_repo(
    *,
    owner: str,
    slug: str,
    ref: str,
    repos_root: Path,
    private: bool,
    dry_run: bool,
    var_dir: Path,
) -> Path:
    dest = repos_root / slug

    if dest.exists() and (dest / ".git").exists():
        audit_log(var_dir, f"OK exists local ref={ref} slug={slug} path={dest}")
        return dest

    if dest.exists() and not (dest / ".git").exists():
        raise RuntimeError(f"path exists but is not a git repo: {dest}")

    exists_remote = github_repo_exists(owner, slug)
    if exists_remote:
        audit_log(var_dir, f"CLONE ref={ref} slug={slug} -> {dest}")
        if dry_run:
            print(f"DRY-RUN: gh repo clone {owner}/{slug} {dest}", flush=True)
            return dest
        repos_root.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["gh", "repo", "clone", f"{owner}/{slug}", str(dest)],
            check=True,
        )
        return dest

    audit_log(var_dir, f"CREATE private={private} ref={ref} slug={slug}")
    create_args = ["gh", "repo", "create", f"{owner}/{slug}"]
    create_args.append("--private" if private else "--public")
    create_args.append("--clone")
    if dry_run:
        print(f"DRY-RUN: {' '.join(create_args)} (cwd={repos_root})", flush=True)
        return dest

    repos_root.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise RuntimeError(f"destination unexpectedly exists: {dest}")
    subprocess.run(create_args, check=True, cwd=str(repos_root))
    if not dest.exists():
        raise RuntimeError(f"gh --clone did not create {dest}")
    scaffold_readme(dest, slug, ref, dry_run=False)
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(dest),
        capture_output=True,
        text=True,
        check=True,
    )
    if status.stdout.strip():
        subprocess.run(
            ["git", "add", "README.md", ".gitignore"], cwd=str(dest), check=True
        )
        subprocess.run(
            ["git", "commit", "-m", f"Initial scaffold for Foundry {ref}"],
            cwd=str(dest),
            check=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", "HEAD"], cwd=str(dest), check=True
        )
    return dest


def upsert_map_entries(
    repo_map: dict[str, Any],
    *,
    ref: str,
    slug: str,
    path: Path,
    github: str,
) -> None:
    projects = repo_map.setdefault("projects", {})
    entry = {
        "path": str(path),
        "github": github,
        "notes": f"Provisioned by project_sync for Foundry {ref}",
    }
    projects[ref] = entry
    projects[slug] = dict(entry)
    # Convenience alias used by existing maps
    projects[f"PR-{slug}"] = dict(entry)


def reconcile_once(
    client: FoundryClient,
    cfg: dict[str, Any],
    var_dir: Path,
) -> None:
    dry_run = bool(cfg.get("dry_run", True))
    repos_root = Path(cfg["repos_root"])
    map_path = Path(cfg["map_path"])
    owner = cfg["github_owner"]
    private = bool(cfg.get("private", True))

    repo_map = load_map(map_path)
    state = load_state(var_dir)
    known: dict[str, str] = state.setdefault("known", {})

    projects = client.list_projects(status="active")
    for project in projects:
        ref = str(project.get("ref") or "")
        slug = str(project.get("repo_slug") or "").strip()
        status = str(project.get("status") or "active")
        if status == "archived" or not slug:
            continue
        if not validate_slug(slug):
            audit_log(var_dir, f"SKIP invalid_slug ref={ref} slug={slug!r}")
            continue

        dest = repos_root / slug
        github = f"https://github.com/{owner}/{slug}.git"

        if map_has_ready(repo_map, ref, slug, dest):
            known[ref] = slug
            continue

        try:
            if dry_run:
                action = "exists_remote" if github_repo_exists(owner, slug) else "would_create"
                audit_log(
                    var_dir,
                    f"DRY-RUN {action} ref={ref} slug={slug} path={dest}",
                )
                # Still show map upsert intent
                audit_log(var_dir, f"DRY-RUN upsert_map ref={ref} slug={slug}")
            else:
                path = ensure_repo(
                    owner=owner,
                    slug=slug,
                    ref=ref,
                    repos_root=repos_root,
                    private=private,
                    dry_run=False,
                    var_dir=var_dir,
                )
                upsert_map_entries(
                    repo_map, ref=ref, slug=slug, path=path, github=github
                )
                save_map(map_path, repo_map, dry_run=False)
                known[ref] = slug
                audit_log(var_dir, f"MAPPED ref={ref} slug={slug} path={path}")
        except Exception as exc:  # noqa: BLE001
            audit_log(var_dir, f"ERROR ref={ref} slug={slug}: {exc}")
            continue

    state["last_success"] = utc_now()
    state["known"] = known
    save_state(var_dir, state, dry_run=dry_run)


def main() -> int:
    load_dotenv(ROOT / ".env")
    cfg = load_sync_config()
    var_dir = ROOT / "var"
    poll_s = float(cfg.get("poll_sec", 60))
    dry = bool(cfg.get("dry_run", True))
    once = "--once" in sys.argv
    audit_log(
        var_dir,
        f"project_sync starting dry_run={dry} poll={poll_s}s once={once} map={cfg['map_path']}",
    )
    client = FoundryClient()
    while True:
        try:
            reconcile_once(client, cfg, var_dir)
        except Exception as exc:  # noqa: BLE001
            audit_log(var_dir, f"POLL_ERROR {exc}")
            if once:
                return 1
        if once:
            return 0
        time.sleep(poll_s)


if __name__ == "__main__":
    sys.exit(main())
