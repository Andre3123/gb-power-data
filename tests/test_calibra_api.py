"""The hosted API: keys, separate customers, onboarding from the customer's own files."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from test_calibra_mapping import export

from gb_power_data.calibra.api import create_app


@pytest.fixture
def setup(tmp_path):
    app = create_app(tmp_path / "server", code_version="test")
    keys = {n: app.state.tenants.create(n) for n in ("acme", "rival")}
    return TestClient(app), keys, tmp_path


def auth(key):
    return {"Authorization": f"Bearer {key}"}


def onboard(client, key, folder):
    fpath, apath, mpath = export(folder)
    with fpath.open("rb") as f, apath.open("rb") as a:
        return client.post(
            "/v1/import",
            headers=auth(key),
            data={"mapping": mpath.read_text()},
            files={"forecasts": ("forecasts.csv", f), "outcomes": ("actuals.csv", a)},
        )


def test_no_key_no_entry(setup):
    client, _, _ = setup
    assert client.get("/health").json()["ok"] is True
    assert client.get("/v1/agents").status_code == 401
    assert client.get("/v1/agents", headers=auth("cal_wrong")).status_code == 401


def test_keys_are_stored_only_as_hashes(setup):
    _, keys, tmp = setup
    stored = (tmp / "server" / "tenants.json").read_text()
    assert keys["acme"] not in stored and "key_sha256" in stored


def test_onboarding_from_their_own_files(setup):
    client, keys, tmp = setup
    r = onboard(client, keys["acme"], tmp)
    assert r.status_code == 200, r.text
    assert r.json()["submitted"] == 120
    rep = client.get("/v1/agents/acme-optimiser/report", headers=auth(keys["acme"])).json()
    assert rep["calibra_range"]["coverage"] > 0.8
    v = client.get("/v1/ledger/verify", headers=auth(keys["acme"])).json()
    assert v["ok"] and v["entries"] == 240  # 120 forecasts + 120 outcomes


def test_customers_cannot_see_each_other(setup):
    client, keys, tmp = setup
    onboard(client, keys["acme"], tmp)
    assert client.get("/v1/agents", headers=auth(keys["rival"])).json()["agents"] == []
    r = client.get("/v1/agents/acme-optimiser/report", headers=auth(keys["rival"]))
    assert r.status_code == 409 and "no resolved forecasts" in r.json()["detail"]


def test_live_forecast_then_outcome_over_json(setup):
    client, keys, tmp = setup
    onboard(client, keys["acme"], tmp)
    start = (datetime.now(UTC) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    body = {
        "forecasts": [
            {
                "forecast_id": "live-1",
                "agent_id": "acme-optimiser",
                "target_start": start.isoformat(),
                "target_end": (start + timedelta(days=1)).isoformat(),
                "as_of": datetime.now(UTC).isoformat(),
                "point": 18000,
                "day_type": "calm",
                "features": {"wind_gw": 8.0, "spread": 61.0},
            }
        ]
    }
    r = client.post("/v1/forecasts", headers=auth(keys["acme"]), json=body)
    assert r.status_code == 200, r.text
    receipt = r.json()["receipts"][0]
    assert receipt["live"] and receipt["method"] == "mondrian" and receipt["in_scope"]
    again = client.post("/v1/forecasts", headers=auth(keys["acme"]), json=body).json()
    assert again["receipts"][0]["new"] is False  # retrying is safe
    early = client.post(
        "/v1/outcomes",
        headers=auth(keys["acme"]),
        json={"outcomes": [{"forecast_id": "live-1", "agent_id": "acme-optimiser", "realised": 1}]},
    )
    assert early.status_code == 409 and "before the target period ended" in early.json()["detail"]


def test_bad_input_says_what_is_wrong(setup):
    client, keys, _ = setup
    r = client.post(
        "/v1/forecasts",
        headers=auth(keys["acme"]),
        json={"forecasts": [{"forecast_id": "x", "agent_id": "a", "point": 1}]},
    )
    assert r.status_code == 422
    missing = {e["loc"][-1] for e in r.json()["detail"]}
    assert {"target_start", "target_end", "as_of"} <= missing
    m = client.post(
        "/v1/mapping/preview",
        headers=auth(keys["acme"]),
        data={"mapping": 'agent_id = "a"\n[columns]\npoint = "p"'},
        files={"forecasts": ("f.csv", b"p\n1\n")},
    )
    assert m.status_code == 422 and "target_start is required" in m.json()["detail"]


def test_draft_and_preview_before_import(setup):
    client, keys, tmp = setup
    fpath, _, mpath = export(tmp, days=10)
    with fpath.open("rb") as f:
        d = client.post(
            "/v1/mapping/draft", headers=auth(keys["acme"]), files={"forecasts": ("f.csv", f)}
        )
    assert d.status_code == 200 and 'point = "rev_forecast"' in d.text
    with fpath.open("rb") as f:
        p = client.post(
            "/v1/mapping/preview",
            headers=auth(keys["acme"]),
            data={"mapping": mpath.read_text()},
            files={"forecasts": ("f.csv", f)},
        )
    assert p.status_code == 200 and p.json()["rows"] == 10
    assert p.json()["preview"][0]["features"]["wind_gw"] > 0
    assert (
        client.get("/v1/agents", headers=auth(keys["acme"])).json()["agents"] == []
    )  # nothing written


def test_the_web_app_is_served(setup):
    client, _, _ = setup
    assert client.get("/", follow_redirects=False).headers["location"] == "/app/"
    page = client.get("/app/")
    assert page.status_code == 200 and "Sign in with your API key" in page.text
    assert client.get("/app/app.js").status_code == 200


def test_guess_then_import_with_a_json_mapping(setup):
    """What the web app does: guess, the user adjusts, then send the mapping as JSON."""
    import json

    client, keys, tmp = setup
    fpath, apath, _ = export(tmp, days=60)
    with fpath.open("rb") as f, apath.open("rb") as a:
        g = client.post(
            "/v1/mapping/guess",
            headers=auth(keys["acme"]),
            files={"forecasts": ("f.csv", f), "outcomes": ("a.csv", a)},
        ).json()
    cols = g["forecasts"]["columns"]
    assert cols["point"] == "rev_forecast" and g["outcomes"]["realised"] == "actual_revenue"
    assert g["forecasts"]["assets"] == {"soc_start_pct": "soc_start_pct"}
    mapping = {
        "agent_id": "acme-web",
        "timezone": "Europe/London",
        "period": "1D",
        "columns": cols,
        "features": g["forecasts"]["features"],
        "assets": {"soc_start": {"column": "soc_start_pct", "scale": 0.01}},
        "outcomes": {"realised": "actual_revenue", "target_start": cols["target_start"]},
    }
    with fpath.open("rb") as f, apath.open("rb") as a:
        r = client.post(
            "/v1/import",
            headers=auth(keys["acme"]),
            data={"mapping": json.dumps(mapping)},
            files={"forecasts": ("f.csv", f), "outcomes": ("a.csv", a)},
        )
    assert r.status_code == 200, r.text
    assert r.json()["resolved"] == 60


def test_mcp_in_hosted_mode_calls_the_api(setup):
    """calibra-mcp next to a customer's agent, records on the hosted service."""
    import asyncio

    from mcp import Client

    from gb_power_data.calibra.mcp import build_server
    from gb_power_data.calibra.remote import RemoteEngine

    client, keys, tmp = setup
    onboard(client, keys["acme"], tmp)
    remote = RemoteEngine("http://testserver", keys["acme"], client=client)
    start = (datetime.now(UTC) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    async def talk():
        async with Client(build_server(remote)) as c:
            ok = await c.call_tool(
                "submit_forecast",
                {
                    "agent_id": "acme-optimiser",
                    "forecast_id": "via-mcp",
                    "point": 18000,
                    "target_start": start.isoformat(),
                    "target_end": (start + timedelta(days=1)).isoformat(),
                    "day_type": "calm",
                    "features": {"wind_gw": 8.0, "spread": 61.0},
                },
            )
            bad = await c.call_tool(
                "submit_forecast",
                {
                    "agent_id": "acme-optimiser",
                    "forecast_id": "via-mcp",
                    "point": 1,
                    "target_start": start.isoformat(),
                    "target_end": (start + timedelta(days=1)).isoformat(),
                },
            )
            return ok, bad

    ok, bad = asyncio.run(talk())
    assert ok.structured_content["new"] is True and ok.structured_content["live"] is True
    assert bad.is_error and "different content" in bad.content[0].text
    stored = client.get("/v1/agents", headers=auth(keys["acme"])).json()["agents"][0]
    assert stored["forecasts"] == 121  # the hosted ledger has it
