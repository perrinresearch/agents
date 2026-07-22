"""Unit tests for Cursor worker helpers (no live Foundry/Cursor required)."""

from __future__ import annotations

import os
from pathlib import Path

from src.cursor_worker import resolve_cwd, run_cursor_agent


def test_resolve_cwd_prefers_claim_repo_cwd(tmp_path, monkeypatch):
    cwd = tmp_path / "checkout"
    cwd.mkdir()
    monkeypatch.delenv("REPOS_ROOT", raising=False)
    path = resolve_cwd(
        {"repo_cwd": str(cwd), "repo_slug": "other", "project_ref": "PR-x"},
        {"projects": {}},
    )
    assert path == cwd.resolve()


def test_resolve_cwd_uses_repos_root_slug(tmp_path, monkeypatch):
    root = tmp_path / "repos"
    slug_dir = root / "waypoint"
    slug_dir.mkdir(parents=True)
    monkeypatch.setenv("REPOS_ROOT", str(root))
    path = resolve_cwd(
        {"repo_cwd": "", "repo_slug": "waypoint", "project_ref": "PR-x"},
        {"projects": {}},
    )
    assert path == slug_dir.resolve()


def test_run_cursor_agent_dry_run(tmp_path, monkeypatch):
    monkeypatch.setenv("CURSOR_DRY_RUN", "1")
    out = run_cursor_agent("add a footer version string", tmp_path)
    assert out["success"] is True
    assert (tmp_path / ".foundry-cursor-dry-run").is_file()
    assert out["cost_usd"] == 0.0
