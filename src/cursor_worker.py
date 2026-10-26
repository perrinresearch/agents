"""Foundry ↔ Cursor SDK local worker.

Claim a leased AgentPrompt → resolve repo cwd → run Cursor local agent → complete.

Entry: python -m src.cursor_worker
Once:  python -m src.cursor_worker --once
Dry:   CURSOR_DRY_RUN=1 python -m src.cursor_worker --once
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .config_loader import get_repos_root
from .foundry_client import FoundryClient

ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "config" / "project_repos.json"

PROMPT_GUARDRAILS = """
---
Worker constraints (mandatory):
- Work only inside this repository checkout.
- Create or use a feature branch; never push to main/master.
- Do not force-push; do not rewrite shared history.
- Prefer a small, reviewable diff; stop when the request is done.
- Leave a short summary of files changed.
"""


def load_repo_map() -> dict:
    if not MAP_PATH.is_file():
        return {"projects": {}}
    with MAP_PATH.open() as f:
        return json.load(f)


def resolve_cwd(claim: dict[str, Any], repo_map: dict) -> Path:
    """Prefer claim.repo_cwd, then repos_root/slug, then project_repos map."""
    hinted = (claim.get("repo_cwd") or "").strip()
    if hinted:
        path = Path(hinted).expanduser()
        if path.is_dir():
            return path.resolve()

    slug = (claim.get("repo_slug") or "").strip()
    repos_root = get_repos_root()
    if slug:
        candidate = repos_root / slug
        if candidate.is_dir():
            return candidate.resolve()

    project_ref = claim.get("project_ref") or ""
    mapping = repo_map.get("projects") or repo_map
    entry = mapping.get(project_ref) or mapping.get(str(project_ref).lower())
    if slug:
        entry = entry or mapping.get(slug)
    if isinstance(entry, str):
        return Path(entry).expanduser().resolve()
    if isinstance(entry, dict) and entry.get("path"):
        return Path(entry["path"]).expanduser().resolve()

    default = Path(
        os.environ.get(
            "DEFAULT_REPO_CWD",
            str(repos_root / "waypoint"),
        )
    ).expanduser()
    return default.resolve()


def _git_snapshot(cwd: Path) -> dict[str, str]:
    def run(args: list[str]) -> str:
        try:
            out = subprocess.check_output(
                ["git", *args],
                cwd=cwd,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            return out.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return ""

    return {
        "branch": run(["rev-parse", "--abbrev-ref", "HEAD"]),
        "commit": run(["rev-parse", "HEAD"]),
        "status": run(["status", "--porcelain"]),
    }


def _heartbeat_loop(
    client: FoundryClient,
    prompt_id: int,
    worker_id: str,
    lease_token: str,
    lease_seconds: int,
    stop: threading.Event,
    interval: float,
) -> None:
    while not stop.wait(interval):
        try:
            client.heartbeat(prompt_id, worker_id, lease_token, lease_seconds)
        except Exception as exc:  # noqa: BLE001
            print(f"heartbeat failed: {exc}", flush=True)


def run_cursor_agent(prompt_text: str, cwd: Path) -> dict[str, Any]:
    """Run Cursor SDK local agent; returns result payload for Foundry complete."""
    dry = os.environ.get("CURSOR_DRY_RUN", "").strip() in ("1", "true", "yes")
    if dry:
        marker = cwd / ".foundry-cursor-dry-run"
        marker.write_text(
            f"dry-run at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
            f"prompt_chars={len(prompt_text)}\n",
            encoding="utf-8",
        )
        return {
            "success": True,
            "result_notes": (
                "CURSOR_DRY_RUN=1: skipped Cursor SDK; wrote .foundry-cursor-dry-run"
            ),
            "result": {"dry_run": True, "marker": str(marker)},
            "cost_usd": 0.0,
        }

    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "CURSOR_API_KEY is required (or set CURSOR_DRY_RUN=1 for a no-op pilot)"
        )

    from cursor_sdk import Agent, CursorAgentError, LocalAgentOptions

    model = os.environ.get("CURSOR_MODEL", "composer-2.5").strip() or "composer-2.5"
    wrapped = prompt_text.strip() + "\n" + PROMPT_GUARDRAILS

    try:
        with Agent.create(
            model=model,
            api_key=api_key,
            local=LocalAgentOptions(cwd=str(cwd)),
        ) as agent:
            run = agent.send(wrapped)
            result = run.wait()
    except CursorAgentError as err:
        return {
            "success": False,
            "result_notes": "",
            "error": f"Cursor startup failed: {err}",
            "result": {"retryable": bool(getattr(err, "is_retryable", False))},
        }

    status = getattr(result, "status", None) or ""
    text = ""
    if hasattr(result, "result") and result.result is not None:
        text = str(result.result)
    elif hasattr(run, "text"):
        try:
            text = str(run.text())
        except Exception:  # noqa: BLE001
            text = ""

    ok = status in ("finished", "success", "completed", "")
    if status == "error":
        ok = False

    cost = None
    for attr in ("cost_usd", "total_cost_usd", "cost"):
        if hasattr(result, attr):
            try:
                cost = float(getattr(result, attr))
            except (TypeError, ValueError):
                pass
            break

    return {
        "success": ok,
        "result_notes": (text or f"Cursor run status={status!r}")[:8000],
        "error": "" if ok else f"Cursor run status={status!r}",
        "result": {
            "status": status,
            "run_id": getattr(result, "id", None),
            "model": model,
        },
        "cost_usd": cost,
    }


def enrich_claim_repo(client: FoundryClient, claim: dict[str, Any]) -> dict[str, Any]:
    """Fill repo_slug from project list when Foundry claim payload is older."""
    if (claim.get("repo_slug") or "").strip():
        return claim
    project_ref = (claim.get("project_ref") or "").strip()
    if not project_ref:
        return claim
    try:
        projects = client.list_projects(status=None)
    except Exception:  # noqa: BLE001
        return claim
    for project in projects:
        if (project.get("ref") or "").strip() != project_ref:
            continue
        slug = (project.get("repo_slug") or "").strip()
        if slug:
            claim = {**claim, "repo_slug": slug}
        break
    return claim


def process_claim(
    client: FoundryClient,
    claim: dict[str, Any],
    worker_id: str,
    repo_map: dict,
    lease_seconds: int,
) -> dict[str, Any]:
    claim = enrich_claim_repo(client, claim)
    prompt_id = int(claim["id"])
    lease_token = claim["lease_token"]
    text = claim.get("prompt_text") or claim.get("prompt") or ""
    cwd = resolve_cwd(claim, repo_map)
    if not cwd.is_dir():
        return client.complete_lease(
            prompt_id,
            worker_id,
            lease_token,
            success=False,
            error=f"repo cwd not found: {cwd}",
            result_notes="",
        )

    before = _git_snapshot(cwd)
    print(
        f"claimed {prompt_id} slug={claim.get('repo_slug')!r} "
        f"ref={claim.get('project_ref')!r} cwd={cwd}",
        flush=True,
    )

    stop = threading.Event()
    hb_interval = float(os.environ.get("HEARTBEAT_INTERVAL_SEC", "60"))
    hb = threading.Thread(
        target=_heartbeat_loop,
        args=(
            client,
            prompt_id,
            worker_id,
            lease_token,
            lease_seconds,
            stop,
            hb_interval,
        ),
        daemon=True,
    )
    hb.start()
    started = time.monotonic()
    try:
        outcome = run_cursor_agent(text, cwd)
    finally:
        stop.set()
        hb.join(timeout=2)

    runtime = time.monotonic() - started
    after = _git_snapshot(cwd)
    artifacts: list[str] = []
    if after.get("branch"):
        artifacts.append(f"branch:{after['branch']}")
    if after.get("commit"):
        artifacts.append(f"commit:{after['commit']}")
    if before.get("commit") and after.get("commit") and before["commit"] != after["commit"]:
        artifacts.append(f"commit_delta:{before['commit'][:8]}..{after['commit'][:8]}")

    return client.complete_lease(
        prompt_id,
        worker_id,
        lease_token,
        success=bool(outcome.get("success")),
        result_notes=str(outcome.get("result_notes") or ""),
        result=outcome.get("result") or {},
        error=str(outcome.get("error") or ""),
        runtime_seconds=runtime,
        cost_usd=outcome.get("cost_usd"),
        artifact_refs=artifacts,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Foundry Cursor local worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Claim at most one prompt then exit",
    )
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env")
    repo_map = load_repo_map()
    client = FoundryClient()
    worker_id = os.environ.get("CURSOR_WORKER_ID", "cursor-wsl-1")
    lease_seconds = int(os.environ.get("LEASE_SECONDS", "900"))
    poll_s = float(os.environ.get("POLL_INTERVAL_SEC", "30"))

    print(
        f"cursor worker starting worker_id={worker_id} poll={poll_s}s once={args.once}",
        flush=True,
    )

    while True:
        try:
            claim = client.claim_next(worker_id, lease_seconds=lease_seconds)
        except Exception as exc:  # noqa: BLE001
            print(f"claim failed: {exc}", flush=True)
            if args.once:
                return 1
            time.sleep(poll_s)
            continue

        if not claim:
            if args.once:
                print("queue empty", flush=True)
                return 0
            time.sleep(poll_s)
            continue

        try:
            done = process_claim(
                client, claim, worker_id, repo_map, lease_seconds
            )
            print(
                f"finished {claim['id']} status={done.get('status')} "
                f"artifacts={done.get('artifact_refs')}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"process failed: {exc}", flush=True)
            try:
                client.complete_lease(
                    int(claim["id"]),
                    worker_id,
                    claim["lease_token"],
                    success=False,
                    error=f"worker error: {exc}",
                )
            except Exception as fail_exc:  # noqa: BLE001
                print(f"complete after error failed: {fail_exc}", flush=True)
            if args.once:
                return 1

        if args.once:
            return 0
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
