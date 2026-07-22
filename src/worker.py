"""Poll Foundry → run Ollama → report. Entry: python -m src.worker"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from .foundry_client import FoundryClient
from .ollama_runner import run_prompt

ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "config" / "project_repos.json"


def load_repo_map() -> dict:
    with MAP_PATH.open() as f:
        return json.load(f)


def resolve_cwd(project_ref: str | None, repo_map: dict) -> Path:
    default = Path(
        os.environ.get(
            "DEFAULT_REPO_CWD",
            str(Path.home() / "perrinresearch" / "repos" / "waypoint"),
        )
    )
    if not project_ref:
        return default
    mapping = repo_map.get("projects") or repo_map
    entry = mapping.get(project_ref) or mapping.get(project_ref.lower())
    if isinstance(entry, str):
        return Path(entry)
    if isinstance(entry, dict) and entry.get("path"):
        return Path(entry["path"])
    return default


def main() -> int:
    load_dotenv(ROOT / ".env")
    repo_map = load_repo_map()
    client = FoundryClient()
    poll_s = float(os.environ.get("POLL_INTERVAL_SEC", "30"))
    print(f"worker starting; poll={poll_s}s map={MAP_PATH}", flush=True)
    while True:
        try:
            prompts = client.pending()
        except Exception as exc:  # noqa: BLE001
            print(f"pending failed: {exc}", flush=True)
            time.sleep(poll_s)
            continue
        if not prompts:
            time.sleep(poll_s)
            continue
        for prompt in prompts:
            pid = str(prompt.get("id") or prompt.get("prompt_id"))
            text = prompt.get("prompt") or prompt.get("body") or prompt.get("text") or ""
            pref = prompt.get("project_ref") or prompt.get("project") or ""
            cwd = resolve_cwd(str(pref) if pref else None, repo_map)
            print(f"claim {pid} project_ref={pref!r} cwd={cwd}", flush=True)
            try:
                client.claim(pid)
                notes = run_prompt(text, cwd)
                client.complete(pid, notes[:8000])
                print(f"completed {pid}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"fail {pid}: {exc}", flush=True)
                try:
                    client.fail(pid, f"worker error: {exc}")
                except Exception as fail_exc:  # noqa: BLE001
                    print(f"fail report error: {fail_exc}", flush=True)
        time.sleep(poll_s)


if __name__ == "__main__":
    sys.exit(main())
