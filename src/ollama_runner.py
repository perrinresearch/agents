"""Call local Ollama against a repo cwd."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx


def run_prompt(prompt: str, cwd: str | Path, model: str | None = None) -> str:
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    model = model or os.environ.get("OLLAMA_MODEL", "llama3.2:latest")
    cwd = Path(cwd).resolve()
    system = (
        f"You are a coding agent working in repository {cwd}. "
        "Describe the concrete files and edits you would make. "
        "Do not invent secrets or credentials."
    )
    payload: dict[str, Any] = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    with httpx.Client(timeout=600.0) as client:
        r = client.post(f"{host}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
    message = data.get("message") or {}
    return message.get("content") or data.get("response") or str(data)
