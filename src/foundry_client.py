"""Foundry HTTPS client: login, projects, lease claim/heartbeat/complete."""

from __future__ import annotations

import os
from typing import Any

import httpx


class FoundryClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.environ["FOUNDRY_API_URL"]).rstrip("/")
        self._token: str | None = None
        self._client = httpx.Client(timeout=60.0)

    def login(self, email: str | None = None, password: str | None = None) -> str:
        email = email or os.environ["FOUNDRY_EMAIL"]
        password = password or os.environ["FOUNDRY_PASSWORD"]
        r = self._client.post(
            f"{self.base_url}/api/auth/login",
            json={"email": email, "password": password},
        )
        r.raise_for_status()
        data = r.json()
        self._token = data.get("access_token") or data.get("token")
        if not self._token:
            raise RuntimeError(f"login response missing token: {data!r}")
        return self._token

    def _headers(self) -> dict[str, str]:
        if not self._token:
            self.login()
        return {"Authorization": f"Bearer {self._token}"}

    def list_projects(self, status: str | None = "active") -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if status:
            params["status"] = status
        r = self._client.get(
            f"{self.base_url}/api/projects",
            headers=self._headers(),
            params=params,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data
        return data.get("items") or data.get("projects") or []

    def pending(self) -> list[dict[str, Any]]:
        r = self._client.get(
            f"{self.base_url}/api/agent-prompts/pending",
            headers=self._headers(),
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data
        return data.get("items") or data.get("prompts") or []

    def patch_prompt(self, prompt_id: str | int, payload: dict[str, Any]) -> dict[str, Any]:
        r = self._client.patch(
            f"{self.base_url}/api/agent-prompts/{prompt_id}",
            headers=self._headers(),
            json=payload,
        )
        r.raise_for_status()
        return r.json() if r.content else {}

    def claim_next(
        self, worker_id: str, lease_seconds: int = 900
    ) -> dict[str, Any] | None:
        """Lease the next pending/expired prompt. Returns None if queue empty."""
        r = self._client.post(
            f"{self.base_url}/api/agent-prompts/claim",
            headers=self._headers(),
            json={"worker_id": worker_id, "lease_seconds": lease_seconds},
        )
        r.raise_for_status()
        if r.status_code == 204 or not r.content:
            return None
        data = r.json()
        return data if data else None

    def heartbeat(
        self,
        prompt_id: int,
        worker_id: str,
        lease_token: str,
        lease_seconds: int = 900,
    ) -> dict[str, Any]:
        r = self._client.post(
            f"{self.base_url}/api/agent-prompts/{prompt_id}/heartbeat",
            headers=self._headers(),
            json={
                "worker_id": worker_id,
                "lease_token": lease_token,
                "lease_seconds": lease_seconds,
            },
        )
        r.raise_for_status()
        return r.json()

    def complete_lease(
        self,
        prompt_id: int,
        worker_id: str,
        lease_token: str,
        *,
        success: bool,
        result_notes: str = "",
        result: dict[str, Any] | None = None,
        error: str = "",
        runtime_seconds: float | None = None,
        cost_usd: float | None = None,
        artifact_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        r = self._client.post(
            f"{self.base_url}/api/agent-prompts/{prompt_id}/complete",
            headers=self._headers(),
            json={
                "worker_id": worker_id,
                "lease_token": lease_token,
                "success": success,
                "result": result or {},
                "result_notes": result_notes,
                "error": error,
                "runtime_seconds": runtime_seconds,
                "cost_usd": cost_usd,
                "artifact_refs": artifact_refs or [],
            },
        )
        r.raise_for_status()
        return r.json()

    # Legacy PATCH helpers (manual UI / old Ollama worker)
    def claim(self, prompt_id: str) -> dict[str, Any]:
        return self.patch_prompt(prompt_id, {"status": "in_progress"})

    def complete(self, prompt_id: str, result_notes: str) -> dict[str, Any]:
        return self.patch_prompt(
            prompt_id, {"status": "completed", "result_notes": result_notes}
        )

    def fail(self, prompt_id: str, result_notes: str) -> dict[str, Any]:
        return self.patch_prompt(
            prompt_id, {"status": "cancelled", "result_notes": result_notes}
        )
