"""claim ledger records. Domain-agnostic: nothing in this module knows about batteries
A claim may only contain what was known at the meoent it was made"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


def _now() -> datetime:
    return datetime.now(UTC)


class Claim(BaseModel):
    """One forcast : a guess, a range, a promise about how often the range holds"""

    model_config = ConfigDict(frozen=True, extra="forbid")
    claim_id: str
    agent_id: str
    model_version: str
    code_version: str

    as_of: AwareDatetime  # the moment the agent stands at when it makes the claim
    data_cutoff: AwareDatetime  # the latest data the agent used
    recorded_at: AwareDatetime = Field(default_factory=_now)  # set by the clock

    target_name: str
    unit: str
    target_start: AwareDatetime
    target_end: AwareDatetime

    point: float
    lower: float
    upper: float
    coverage: float = Field(gt=0, lt=1)

    reference_class: str | None = None  # must be decidable at as_of
    resolution_rule: str
    input_data_hash: str
    context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> Claim:
        if not self.lower <= self.point <= self.upper:
            raise ValueError("range must satisfy lower <= point <= upper")
        if self.data_cutoff > self.as_of:
            raise ValueError("data_cutoff is after as _of : the claim uses data from the future")
        if self.as_of > self.target_start:
            raise ValueError("as_of is after target_start: the claim forecasts the past")
        if self.target_end <= self.target_start:
            raise ValueError("target_end must be after target_start")
        return self

    @property
    def is_live(self) -> bool:
        """true if the row was written before the target began ; false for backtest"""
        return self.recorded_at <= self.target_start


class Resolution(BaseModel):
    """what actually happened"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: str
    resolution_version: int = Field(ge=1)
    resolved_at: AwareDatetime = Field(default_factory=_now)
    realised_value: float
    realised_class: str | None = None  # labels that depend on the outcome live here
    truth_data_hash: str
