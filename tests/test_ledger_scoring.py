from datetime import UTC, datetime

import pytest
from test_ledger_models import pillswood_claim

from gb_power_data.ledger.models import Claim, Resolution
from gb_power_data.ledger.scoring import interval_score, is_covered


def outcome(value: float, **overrides) -> Resolution:
    base = dict(
        claim_id="pillswood-rev-2025-01-08-001",
        resolution_version=1,
        realised_value=value,
        truth_data_hash="TODO",
    )
    return Resolution(**(base | overrides))


def test_spike_day_reproduces_the_hand_calculated_bill():
    claim = Claim(**pillswood_claim())
    res = outcome(232597, realised_class="wild")
    assert is_covered(claim, res) is False
    assert interval_score(claim, res) == pytest.approx(4_423_946)


def test_ordinary_day_pays_only_the_width_fee():
    claim = Claim(**pillswood_claim())
    res = outcome(8000)
    assert is_covered(claim, res) is True
    assert interval_score(claim, res) == pytest.approx(6_706)


def test_rejects_resolution_for_a_different_claim():
    claim = Claim(**pillswood_claim())
    with pytest.raises(ValueError, match="not pillswood"):
        interval_score(claim, outcome(8000, claim_id="other-claim"))


def test_rejects_resolution_before_the_target_period_ended():
    claim = Claim(**pillswood_claim())
    early = outcome(8000, resolved_at=datetime(2025, 1, 8, 12, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match="before the target period ended"):
        is_covered(claim, early)
