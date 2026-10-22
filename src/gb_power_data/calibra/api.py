"""Calibra over HTTPS: the API an optimiser's own software calls.

Every request carries the customer's key: `Authorization: Bearer cal_...`. Each customer has a
separate ledger. Interactive documentation is served at /docs (OpenAPI).

Run: uv run calibra serve        (admin: calibra customer create NAME prints a key once)
"""

import json
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from gb_power_data.calibra.engine import Calibra, Settings
from gb_power_data.calibra.mapping import Mapping, MappingError, draft, guess
from gb_power_data.calibra.schema import Forecast, Outcome
from gb_power_data.calibra.tenants import Tenants
from gb_power_data.ledger.store import Ledger

MAX_UPLOAD = 20 * 1024 * 1024  # 20 MB per file
WEB = Path(__file__).with_name("web")  # the browser app, served at /app
VERSION = "0.1.0"


class ForecastBatch(BaseModel):
    forecasts: list[Forecast] = Field(min_length=1, max_length=5000)
    reason: str | None = Field(None, description="why to go ahead if a forecast is out of scope")


class OutcomeBatch(BaseModel):
    outcomes: list[Outcome] = Field(min_length=1, max_length=5000)


class ScopeQuery(BaseModel):
    agent_id: str
    features: dict[str, float]
    as_of: datetime | None = None


def create_app(root: str | Path, code_version: str = "unknown") -> FastAPI:
    tenants = Tenants(root)
    app = FastAPI(
        title="Calibra API",
        version=VERSION,
        description=(
            "Record forecasts before the event, get honest ranges learned from your own past "
            "errors, and a tamper-evident track record. Authenticate with "
            "`Authorization: Bearer <your key>`."
        ),
    )
    app.state.tenants = tenants

    def customer(authorization: Annotated[str | None, Header()] = None) -> str:
        key = authorization.removeprefix("Bearer ").strip() if authorization else None
        name = tenants.who(key)
        if name is None:
            raise HTTPException(401, "missing or unknown API key", {"WWW-Authenticate": "Bearer"})
        return name

    Customer = Annotated[str, Depends(customer)]

    def run(name: str, work: Callable[[Calibra], Any]) -> Any:
        engine = Calibra(tenants.ledger(name), Settings(code_version=code_version))
        with tenants.lock(name):
            try:
                return work(engine)
            except (MappingError, ValidationError) as e:
                raise HTTPException(422, str(e)) from e
            except KeyError as e:
                raise HTTPException(404, str(e).strip("'\"")) from e
            except ValueError as e:
                raise HTTPException(409, str(e)) from e

    @app.get("/health", tags=["service"])
    def health() -> dict:
        return {"ok": True, "version": VERSION}

    @app.get("/v1/me", tags=["service"])
    def me(name: Customer) -> dict:
        """Who this key belongs to."""
        return {"customer": name}

    @app.post("/v1/forecasts", tags=["forecasts"])
    def submit(batch: ForecastBatch, name: Customer) -> dict:
        """Record forecasts before their target period. Each gets Calibra's range and a scope
        verdict. Sending the same forecast again is safe: it returns the stored record."""
        return run(
            name, lambda e: {"receipts": [e.submit(f, batch.reason) for f in batch.forecasts]}
        )

    @app.post("/v1/outcomes", tags=["forecasts"])
    def resolve(batch: OutcomeBatch, name: Customer) -> dict:
        """Score recorded forecasts against what happened. Same outcome twice changes nothing;
        a corrected value adds a new version and keeps the old one."""
        return run(name, lambda e: {"results": [e.resolve(o) for o in batch.outcomes]})

    @app.post("/v1/scope-check", tags=["forecasts"])
    def scope(q: ScopeQuery, name: Customer) -> dict:
        """Are these inputs like the ones the agent's past forecasts were made from?
        Nothing is recorded."""
        when = q.as_of or datetime.now(UTC)
        return run(name, lambda e: e.check_scope(q.agent_id, when, q.features))

    @app.get("/v1/agents", tags=["track record"])
    def agents(name: Customer) -> dict:
        return run(name, lambda e: {"agents": e.agents()})

    @app.get("/v1/agents/{agent_id}/status", tags=["track record"])
    def status(agent_id: str, name: Customer, day_type: str | None = None) -> dict:
        """How Calibra would range this agent's next forecast, and its scope bands."""
        return run(name, lambda e: e.status(agent_id, datetime.now(UTC), day_type))

    @app.get("/v1/agents/{agent_id}/report", tags=["track record"])
    def report(agent_id: str, name: Customer) -> dict:
        """Track record: how often Calibra's range and the agent's own range held, and the money
        missing below the lower bound, in scope vs flagged."""
        return run(name, lambda e: e.report(agent_id))

    @app.get("/v1/agents/{agent_id}/forecasts", tags=["track record"])
    def forecasts(agent_id: str, name: Customer) -> dict:
        """Every scored forecast of this agent, for charts and audits."""

        def work(e: Calibra) -> dict:
            df = e.frame(agent_id)
            if not df.empty:
                df["target_start"] = df["target_start"].astype(str)
            return {"rows": [] if df.empty else df.to_dict("records")}

        return run(name, work)

    @app.get("/v1/ledger/verify", tags=["track record"])
    def verify(name: Customer) -> dict:
        """Check the hash chain of this customer's ledger: any edit after writing shows here."""

        def work(e: Calibra) -> dict:
            if not e.ledger_path.exists():
                return {"ok": True, "entries": 0, "head": None, "problems": []}
            led = Ledger(e.ledger_path)
            try:
                rep = led.verify()
            finally:
                led.close()
            return {"ok": rep.ok, "entries": rep.entries, "head": rep.head,
                    "problems": rep.problems[:50]}  # fmt: skip

        return run(name, work)

    # ---- onboarding: the customer's own files ------------------------------------------

    async def _text(upload: UploadFile) -> str:
        data = await upload.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, f"{upload.filename} is larger than 20 MB")
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HTTPException(422, f"{upload.filename} is not UTF-8 text (CSV)") from None

    def _rows(text: str) -> list[dict[str, str]]:
        import csv
        import io

        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            raise HTTPException(422, "the file has a header but no rows")
        return rows

    def _mapping(text: str) -> Mapping:
        """A mapping as calibra.toml text, or as JSON (what the web app sends)."""
        try:
            data = json.loads(text) if text.lstrip().startswith("{") else tomllib.loads(text)
            return Mapping.from_dict(data)
        except (tomllib.TOMLDecodeError, json.JSONDecodeError) as e:
            raise HTTPException(422, f"mapping is not valid TOML or JSON: {e}") from e
        except MappingError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/v1/mapping/guess", tags=["onboarding"])
    async def mapping_guess(
        name: Customer,
        forecasts: Annotated[UploadFile, File()],
        outcomes: Annotated[UploadFile | None, File()] = None,
    ) -> dict:
        """Your columns, a few sample values each, and Calibra's guess of which is which."""
        f_rows = _rows(await _text(forecasts))
        out = {"forecasts": {**guess(f_rows), "rows": len(f_rows)}}
        if outcomes is not None:
            o_rows = _rows(await _text(outcomes))
            g = guess(o_rows)
            out["outcomes"] = {"header": g["header"], "samples": g["samples"],
                               "realised": g["realised"], "rows": len(o_rows)}  # fmt: skip
        return out

    @app.post("/v1/mapping/draft", tags=["onboarding"], response_class=PlainTextResponse)
    async def mapping_draft(
        name: Customer,
        forecasts: Annotated[UploadFile, File()],
        agent_id: Annotated[str, Form()] = "my-optimiser",
    ) -> str:
        """A first calibra.toml guessed from your forecast file's column names."""
        return draft(_rows(await _text(forecasts)), agent_id)

    @app.post("/v1/mapping/preview", tags=["onboarding"])
    async def mapping_preview(
        name: Customer, mapping: Annotated[str, Form()], forecasts: Annotated[UploadFile, File()]
    ) -> dict:
        """Read the first rows of your file through the mapping, without writing anything."""
        m = _mapping(mapping)
        rows = _rows(await _text(forecasts))
        try:
            parsed = m.forecasts(rows[:5])
        except MappingError as e:
            raise HTTPException(422, str(e)) from e
        return {
            "columns": list(rows[0]),
            "rows": len(rows),
            "preview": [f.model_dump(mode="json") for f in parsed],
        }

    @app.post("/v1/import", tags=["onboarding"])
    async def import_history(
        name: Customer,
        mapping: Annotated[str, Form()],
        forecasts: Annotated[UploadFile, File()],
        outcomes: Annotated[UploadFile | None, File()] = None,
    ) -> dict:
        """Onboard a past track record: your files, read through your mapping, replayed in time
        order as if live. With no outcomes file, the forecasts are recorded and await outcomes."""
        m = _mapping(mapping)
        f_rows = _rows(await _text(forecasts))
        o_rows = _rows(await _text(outcomes)) if outcomes is not None else []

        def work(e: Calibra) -> dict:
            fs = m.forecasts(f_rows)
            os_ = m.outcomes_from(o_rows) if o_rows else []
            return {"agent_id": m.agent_id, **e.replay(fs, os_)}

        return await run_in_threadpool(run, name, work)  # the replay may take a while

    if WEB.is_dir():
        app.mount("/app", StaticFiles(directory=WEB, html=True), name="app")

        @app.get("/", include_in_schema=False)
        def home() -> RedirectResponse:
            return RedirectResponse("/app/")

    return app


def default_app() -> FastAPI:
    """For `uvicorn gb_power_data.calibra.api:default_app --factory`."""
    import os

    from gb_power_data.calibra.cli import _code_version

    return create_app(os.environ.get("CALIBRA_HOME", "data/calibra_server"), _code_version())
