"""What a customer sends Calibra: forecasts from their own optimiser or agent, then outcomes.

Calibra does not make forecasts. It records the customer's forecast before the target period,
gives it an honest range learned from that agent's own past errors, checks whether the inputs
look like the history the range was learned on, and scores it once the outcome is known.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

ID_PATTERN = r"^[A-Za-z0-9._\-]+$"  # safe in claim ids, file names and URLs


class Forecast(BaseModel):
    """One forecast, as made by the customer's agent before the target period."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    forecast_id: str = Field(pattern=ID_PATTERN, max_length=120)
    agent_id: str = Field(pattern=ID_PATTERN, max_length=80)
    model_version: str = "unknown"

    target_start: AwareDatetime
    target_end: AwareDatetime
    as_of: AwareDatetime  # when the forecast was made
    data_cutoff: AwareDatetime | None = None  # latest data used; defaults to as_of

    target_name: str = "value"
    unit: str = "unit"
    point: float

    # the agent's own range, if it gives one: recorded and scored next to Calibra's
    lower: float | None = None
    upper: float | None = None

    day_type: str | None = None  # the customer's own class, decidable at as_of
    features: dict[str, float] = Field(default_factory=dict)  # inputs checked for scope
    asset: dict[str, float | str] = Field(default_factory=dict)  # physical assumptions used
    asset_source: dict[str, str] = Field(default_factory=dict)  # where each assumption is from

    @model_validator(mode="after")
    def _check(self) -> Forecast:
        if self.target_end <= self.target_start:
            raise ValueError("target_end must be after target_start")
        if self.as_of > self.target_start:
            raise ValueError("as_of is after target_start: a forecast must be made beforehand")
        if self.data_cutoff is not None and self.data_cutoff > self.as_of:
            raise ValueError("data_cutoff is after as_of: the forecast used data from the future")
        if (self.lower is None) != (self.upper is None):
            raise ValueError("give both lower and upper, or neither")
        if self.lower is not None and not self.lower <= self.point <= self.upper:
            raise ValueError("the agent's range must satisfy lower <= point <= upper")
        if set(self.asset_source) - set(self.asset):
            raise ValueError("asset_source names an assumption that is not in asset")
        return self

    @property
    def claim_id(self) -> str:
        return f"{self.agent_id}:{self.forecast_id}"

    def input_hash(self) -> str:
        """Fingerprint of everything the forecast says it used, so it cannot be swapped later."""
        body = self.model_dump(mode="json", include={"point", "features", "asset", "day_type"})
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]


class Outcome(BaseModel):
    """What really happened, from the customer's meter or settlement data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    forecast_id: str = Field(pattern=ID_PATTERN, max_length=120)
    agent_id: str = Field(pattern=ID_PATTERN, max_length=80)
    realised: float
    truth_hash: str | None = None  # fingerprint of the source data; computed if absent
    realised_class: str | None = None

    @property
    def claim_id(self) -> str:
        return f"{self.agent_id}:{self.forecast_id}"

    def fingerprint(self) -> str:
        if self.truth_hash:
            return self.truth_hash
        return hashlib.sha256(f"{self.claim_id}|{self.realised!r}".encode()).hexdigest()[:16]


def now() -> datetime:
    return datetime.now(UTC)
