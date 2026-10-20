import pytest
from test_ledger_models import pillswood_claim

from gb_power_data.ledger.chain import GENESIS
from gb_power_data.ledger.models import Claim
from gb_power_data.ledger.store import Ledger
from gb_power_data.ledger.verify_cli import main as verify_main

CLAIM_ID = "pillswood-rev-2025-01-08-001"


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.duckdb")
    led.add_claim(Claim(**pillswood_claim()))
    led.resolve(CLAIM_ID, 5000.0, truth_data_hash="abc")
    yield led
    led.close()


def test_honest_ledger_verifies(ledger):
    report = ledger.verify()
    assert report.ok, report.problems
    assert report.entries == 2 and report.backfilled == 0
    assert report.head != GENESIS


def test_widening_a_range_afterwards_is_caught(ledger):
    ledger.con.execute("UPDATE claims SET upper = upper * 2 WHERE claim_id = ?", [CLAIM_ID])
    assert any("changed after it was written" in p for p in ledger.verify().problems)


def test_changing_the_outcome_is_caught(ledger):
    ledger.con.execute("UPDATE resolutions SET realised_value = 9999")
    assert any("resolution" in p and "changed" in p for p in ledger.verify().problems)


def test_deleting_a_resolution_is_caught(ledger):
    ledger.con.execute("DELETE FROM resolutions")
    assert any("missing" in p for p in ledger.verify().problems)


def test_a_row_slipped_in_without_the_ledger_is_caught(ledger):
    ledger.con.execute("INSERT INTO claims SELECT * REPLACE ('sneaky' AS claim_id) FROM claims")
    assert any("no chain entry" in p for p in ledger.verify().problems)


def test_rewriting_the_chain_breaks_the_published_head(ledger):
    published = ledger.head()
    # an attacker edits a claim and recomputes the whole chain so every link looks fine
    ledger.con.execute("UPDATE claims SET upper = upper * 2")
    ledger.con.execute("DELETE FROM chain")
    ledger._backfill_once()
    assert ledger.verify().ok  # on its own, a rewritten chain looks consistent...
    assert not ledger.verify(expected_head=published).ok  # ...the published head exposes it


def test_growth_after_publishing_is_fine(ledger):
    published = ledger.head()
    ledger.add_claim(Claim(**pillswood_claim(claim_id="pillswood-rev-2025-01-09-001")))
    report = ledger.verify(expected_head=published)
    assert report.ok and report.head != published


def test_an_old_ledger_is_chained_once_and_marked(tmp_path, ledger):
    ledger.con.execute("DELETE FROM chain")  # as if made before the chain existed
    ledger.close()
    reopened = Ledger(tmp_path / "ledger.duckdb")
    report = reopened.verify()
    assert report.ok and report.backfilled == 2
    reopened.close()


def test_a_refused_duplicate_leaves_no_chain_entry(ledger):
    with pytest.raises(ValueError, match="written once"):
        ledger.add_claim(Claim(**pillswood_claim()))
    assert ledger.verify().entries == 2


def test_cli_exit_code(tmp_path, ledger, capsys):
    ledger.close()
    path = str(tmp_path / "ledger.duckdb")
    assert verify_main(["--ledger", path]) == 0
    assert "OK" in capsys.readouterr().out
    assert verify_main(["--ledger", path, "--head", "f" * 64]) == 1