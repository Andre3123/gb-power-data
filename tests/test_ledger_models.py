from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from gb_power_data.ledger.models import Claim


def pillswood_claim(**overrides) -> dict:
    """Persistence claim for 8 Jan 2025, made at the end of 7 Jan (GMT, so UTC = UK time)."""
    base = dict(
        claim_id="pillswood-rev-2025-01-08-001",
        agent_id="persistence-baseline",
        model_version="0.1",
        code_version="test",
        as_of=datetime(2025, 1, 8, 0, 0, tzinfo=UTC),
        data_cutoff=datetime(2025, 1, 8, 0, 0, tzinfo=UTC),
        target_name="daily_trading_revenue",
        unit="GBP",
        target_start=datetime(2025, 1, 8, 0, 0, tzinfo=UTC),
        target_end=datetime(2025, 1, 9, 0, 0, tzinfo=UTC),
        point=8382,
        lower=5029,
        upper=11735,
        coverage=0.90,
        resolution_rule="crude one-cycle revenue on realised APX prices, 196 MWh, eff 0.85",
        input_data_hash="TODO",
        context={"site": "Pillswood", "energy_mwh": 196},
    )
    return base | overrides


def test_backtest_claim_is_accepted_and_marked_as_backtest():
    claim = Claim(**pillswood_claim())
    assert claim.is_live is False  # written in 2026 about 2025: a replay, weaker evidence


def test_rejects_data_from_after_the_claim_moment():
    # the 18:00 / 23:00 mistake from the design exercise
    with pytest.raises(ValidationError, match="data from the future"):
        Claim(
            **pillswood_claim(
                as_of=datetime(2025, 1, 7, 18, 0, tzinfo=UTC),
                data_cutoff=datetime(2025, 1, 7, 23, 0, tzinfo=UTC),
            )
        )


def test_rejects_a_range_that_does_not_contain_the_point():
    with pytest.raises(ValidationError, match="lower <= point <= upper"):
        Claim(**pillswood_claim(lower=9000))


def test_rejects_times_without_a_timezone():
    with pytest.raises(ValidationError):
        Claim(**pillswood_claim(as_of=datetime(2025, 1, 8, 0, 0)))  # noqa: DTZ001 - deliberately naive


def test_claim_cannot_be_edited_after_creation():
    claim = Claim(**pillswood_claim())
    with pytest.raises(ValidationError):
        claim.point = 23259
