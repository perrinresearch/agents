"""Seed a Waypoint pilot AgentPrompt and optionally run the Cursor worker once.

Usage (against a reachable Foundry API with credentials in .env):

  python -m src.pilot_waypoint --dry-run
  python -m src.pilot_waypoint            # requires CURSOR_API_KEY
  python -m src.pilot_waypoint --create-only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from .foundry_client import FoundryClient

ROOT = Path(__file__).resolve().parents[1]

PILOT_TITLE = "Cursor pilot: footer version string"
PILOT_PROMPT = """\
In the Waypoint app, add a visible footer (or footer-adjacent) version string
that shows a short build/version label (e.g. "Waypoint v0.0.0-pilot" or the
package version if one already exists).

Constraints:
- Small UI-only change; keep styling consistent with the existing app.
- Create a feature branch named foundry/cursor-pilot-footer-version.
- Do not push to main/master.
- Do not open a PR unless asked; commit locally if you create commits.
- Stop when the version string is visible in the UI code path.
"""


def _find_waypoint_project(client: FoundryClient) -> dict[str, Any]:
    projects = client.list_projects(status=None)
    for p in projects:
        slug = (p.get("repo_slug") or "").strip()
        ref = (p.get("ref") or "").strip()
        name = (p.get("name") or "").lower()
        if slug == "waypoint" or ref.lower() in ("waypoint", "pr-waypoint") or "waypoint" in name:
            return p
    raise RuntimeError(
        "No Foundry project with repo_slug=waypoint (or Waypoint name). "
        "Set repo_slug on the Waypoint project first."
    )


def _ensure_repo_slug(client: FoundryClient, project: dict[str, Any]) -> dict[str, Any]:
    if (project.get("repo_slug") or "").strip() == "waypoint":
        return project
    r = client._client.patch(
        f"{client.base_url}/api/projects/{project['id']}",
        headers=client._headers(),
        json={"repo_slug": "waypoint"},
    )
    r.raise_for_status()
    return r.json()


def _create_task(client: FoundryClient, project_id: int) -> dict[str, Any]:
    r = client._client.post(
        f"{client.base_url}/api/projects/{project_id}/tasks",
        headers=client._headers(),
        json={"title": "Cursor pilot execution"},
    )
    r.raise_for_status()
    return r.json()


def _create_prompt(client: FoundryClient, task_id: int) -> dict[str, Any]:
    external_key = f"cursor-pilot-waypoint-{int(time.time())}"
    r = client._client.post(
        f"{client.base_url}/api/agent-prompts",
        headers=client._headers(),
        json={
            "task_id": task_id,
            "title": PILOT_TITLE,
            "prompt_text": PILOT_PROMPT,
            "external_key": external_key,
            "priority": 100,
        },
    )
    r.raise_for_status()
    return r.json()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Set CURSOR_DRY_RUN=1 for the worker (no Cursor SDK call)",
    )
    parser.add_argument(
        "--create-only",
        action="store_true",
        help="Create the Foundry prompt only; do not claim/run",
    )
    parser.add_argument(
        "--metrics-out",
        type=Path,
        default=ROOT / "var" / "cursor_pilot_waypoint.json",
        help="Write pilot metrics JSON here",
    )
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env")
    if args.dry_run:
        os.environ["CURSOR_DRY_RUN"] = "1"

    client = FoundryClient()
    started = time.monotonic()
    metrics: dict[str, Any] = {
        "pilot": "waypoint-footer-version",
        "dry_run": bool(args.dry_run) or os.environ.get("CURSOR_DRY_RUN") in ("1", "true", "yes"),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    try:
        project = _find_waypoint_project(client)
        project = _ensure_repo_slug(client, project)
        task = _create_task(client, int(project["id"]))
        prompt = _create_prompt(client, int(task["id"]))
        metrics.update(
            {
                "project_id": project["id"],
                "project_ref": project.get("ref"),
                "repo_slug": project.get("repo_slug"),
                "task_id": task["id"],
                "prompt_id": prompt["id"],
                "prompt_status": prompt.get("status"),
            }
        )
        print(json.dumps({"created": metrics}, indent=2), flush=True)

        if args.create_only:
            metrics["operator_minutes_estimate"] = round((time.monotonic() - started) / 60, 2)
            args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
            args.metrics_out.write_text(json.dumps(metrics, indent=2) + "\n")
            print(f"wrote {args.metrics_out}", flush=True)
            return 0

        from . import cursor_worker

        worker_id = os.environ.get("CURSOR_WORKER_ID", "cursor-pilot-1")
        lease_seconds = int(os.environ.get("LEASE_SECONDS", "900"))
        claimed = client.claim_next(worker_id, lease_seconds=lease_seconds)
        if not claimed:
            raise RuntimeError("Claim returned empty; expected pilot prompt at head of queue")
        if int(claimed["id"]) != int(prompt["id"]):
            raise RuntimeError(
                f"Claimed prompt {claimed['id']} instead of pilot {prompt['id']}; "
                "clear pending prompts or raise pilot priority"
            )

        repo_map = cursor_worker.load_repo_map()
        done = cursor_worker.process_claim(
            client, claimed, worker_id, repo_map, lease_seconds
        )
        metrics.update(
            {
                "claim_repo_cwd": claimed.get("repo_cwd"),
                "claim_repo_slug": claimed.get("repo_slug"),
                "completed_status": done.get("status"),
                "result_notes": (done.get("result_notes") or "")[:500],
                "artifact_refs": done.get("artifact_refs") or [],
                "runtime_seconds": done.get("runtime_seconds"),
                "cost_usd": done.get("cost_usd"),
                "error": done.get("error") or "",
            }
        )
    except httpx.HTTPError as exc:
        metrics["error"] = f"http: {exc}"
        print(metrics["error"], flush=True)
        return 1
    except Exception as exc:  # noqa: BLE001
        metrics["error"] = str(exc)
        print(metrics["error"], flush=True)
        return 1
    finally:
        metrics["wall_seconds"] = round(time.monotonic() - started, 2)
        metrics["operator_minutes_estimate"] = round(metrics["wall_seconds"] / 60, 2)
        metrics["human_review_required"] = True
        metrics["notes"] = (
            "Human reviews Diff/PR in Foundry (task → testing). "
            "No automatic stage advance."
        )
        args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
        args.metrics_out.write_text(json.dumps(metrics, indent=2) + "\n")
        print(f"wrote {args.metrics_out}", flush=True)

    print(json.dumps(metrics, indent=2), flush=True)
    return 0 if not metrics.get("error") and metrics.get("completed_status") == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
