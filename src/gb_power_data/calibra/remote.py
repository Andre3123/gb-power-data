"""Use a hosted Calibra through its API, with the same methods as the local engine.

This is what lets calibra-mcp run next to a customer's AI agent while the records live on the
hosted service: set CALIBRA_API_URL and CALIBRA_KEY and the MCP tools call the API instead.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from gb_power_data.calibra.schema import Forecast


class RemoteError(ValueError):
    """The hosted Calibra refused the request; the message says why."""


class RemoteEngine:
    def __init__(self, base_url: str, key: str, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=60)
        self.headers = {"Authorization": f"Bearer {key}"}

    def _call(self, method: str, path: str, **kw) -> dict:
        r = self.client.request(method, path, headers=self.headers, **kw)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            if isinstance(detail, list):  # field-by-field validation errors
                detail = "; ".join(
                    f"{'.'.join(str(x) for x in d.get('loc', [])[1:])}: {d.get('msg')}"
                    for d in detail
                )
            raise RemoteError(f"Calibra API {r.status_code}: {detail}")
        return r.json()

    def check_scope(self, agent_id: str, as_of: datetime, features: dict[str, float]) -> dict:
        body = {"agent_id": agent_id, "features": features, "as_of": as_of.isoformat()}
        return self._call("POST", "/v1/scope-check", json=body)

    def submit(self, f: Forecast, reason: str | None = None) -> dict:
        body = {"forecasts": [f.model_dump(mode="json")], "reason": reason}
        return self._call("POST", "/v1/forecasts", json=body)["receipts"][0]

    def status(self, agent_id: str, as_of: datetime, day_type: str | None = None) -> dict:
        params = {"day_type": day_type} if day_type else None
        return self._call("GET", f"/v1/agents/{agent_id}/status", params=params)

    def report(self, agent_id: str) -> dict:
        return self._call("GET", f"/v1/agents/{agent_id}/report")
