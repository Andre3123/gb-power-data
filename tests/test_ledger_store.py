from datetime import UTC, datetime

import pytest
from test_ledger_models import pillswood_claim

from gb_power_data.ledger.models import Claim
from gb_power_data.ledger.store import Ledger

CLAIM_ID = "pillswood-rev-2025-01-08-001"


@pytest.fixture
def ledger():
    led = Ledger()  # in memory
    led.add_claim(Claim(**pillswood_claim()))
    yield led
    led.close()


def test_claim_survives_a_restart_unchanged(tmp_path):
    original = Claim(**pillswood_claim())
    path = tmp_path / "ledger.duckdb"
    led = Ledger(path)
    led.add_claim(original)
    led.close()

    reopened = Ledger(path)
    assert reopened.get_claim(CLAIM_ID) == original  # every field, times and context included
    reopened.close()


def test_a_claim_id_can_only_be_written_once(ledger):
    with pytest.raises(ValueError, match="written once"):
        ledger.add_claim(Claim(**pillswood_claim(point=9000)))


def test_resolving_twice_from_the_same_data_adds_one_row(ledger):
    first = ledger.resolve(CLAIM_ID, 232597, truth_data_hash="abc")
    second = ledger.resolve(CLAIM_ID, 232597, truth_data_hash="abc")
    assert first == second
    assert ledger.count_resolutions(CLAIM_ID) == 1


def test_corrected_data_adds_version_two_and_keeps_version_one(ledger):
    ledger.resolve(CLAIM_ID, 232597, truth_data_hash="abc")
    v2 = ledger.resolve(CLAIM_ID, 231000, truth_data_hash="revised")
    assert v2.resolution_version == 2
    assert ledger.count_resolutions(CLAIM_ID) == 2


def test_same_data_giving_a_different_value_is_refused(ledger):
    ledger.resolve(CLAIM_ID, 232597, truth_data_hash="abc")
    with pytest.raises(ValueError, match="not repeatable"):
        ledger.resolve(CLAIM_ID, 1, truth_data_hash="abc")


def test_cannot_resolve_an_unknown_claim(ledger):
    with pytest.raises(KeyError):
        ledger.resolve("no-such-claim", 1.0, truth_data_hash="abc")


def test_cannot_resolve_before_the_target_period_ends():
    led = Ledger()
    future = Claim(
        **pillswood_claim(
            claim_id="future-claim",
            as_of=datetime(2099, 1, 1, tzinfo=UTC),
            data_cutoff=datetime(2099, 1, 1, tzinfo=UTC),
            target_start=datetime(2099, 1, 1, tzinfo=UTC),
            target_end=datetime(2099, 1, 2, tzinfo=UTC),
        )
    )
    led.add_claim(future)
    with pytest.raises(ValueError, match="before the target period ended"):
        led.resolve("future-claim", 1.0, truth_data_hash="abc")
    led.close()
