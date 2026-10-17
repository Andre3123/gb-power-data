import hashlib
from datetime import date, timedelta

import pytest
from test_dayahead import DAY_SHAPE, SITE, price_table

from gb_power_data.claims.dayahead import backtest
from gb_power_data.ledger.store import Ledger
from gb_power_data.report import data
from gb_power_data.report.cli import main

# price level of each day, as a multiple of DAY_SHAPE (spread 110 GBP/MWh at 1.0)
LEVELS = [1, 1, 1, 1, 2, 1, 1, 0.5, 1, 1, 1.2, 1]
DAY1 = date(2025, 1, 1)
START, END = DAY1 + timedelta(days=2), DAY1 + timedelta(days=len(LEVELS) - 1)
LABELS = ("Naive (±40%)", "Split conformal", "Mondrian")

# Persistence forecasts day D with D-2's prices; the plan is the same at any price level, so
# realised / claimed point = LEVELS[D] / LEVELS[D-2]. Naive covers it if within 0.6..1.4:
#   Jan 3  1/1 in    Jan 4  1/1 in    Jan 5  2/1 out    Jan 6  1/1 in    Jan 7  1/2 out
#   Jan 8 .5/1 out   Jan 9  1/1 in    Jan 10 1/.5 out   Jan 11 1.2/1 in  Jan 12 1/1 in
# Day type comes from the forecast day's spread: 55 (level 0.5) is normal, 110+ is wild.
# Only Jan 10 is forecast from a level-0.5 day, so: wild 6 of 9 covered, normal 0 of 1, calm none.
SPLIT_HALF = 1e6  # wide enough to cover everything
MONDRIAN_HALF = {"calm": 1.0, "normal": 1e6, "wild": 1.0}  # only exact-ratio wild days + Jan 10


def build_ledger(path):
    prices = price_table({DAY1 + timedelta(days=i): DAY_SHAPE * k for i, k in enumerate(LEVELS)})
    ledger = Ledger(path)
    backtest(prices, START, END, SITE, ledger, "abc1234")
    backtest(prices, START, END, SITE, ledger, "abc1234", "persistence-cp", SPLIT_HALF)
    backtest(prices, START, END, SITE, ledger, "def5678", "persistence-mcp", MONDRIAN_HALF)
    ledger.close()
    return path


@pytest.fixture(scope="module")
def ledger_path(tmp_path_factory):
    return build_ledger(tmp_path_factory.mktemp("ledger") / "ledger.duckdb")


def test_coverage_matches_hand_count(ledger_path):
    df = data.load(ledger_path, START, END).claims
    head = data.headline(df)
    assert head["claims"].tolist() == [10, 10, 10]
    assert head.loc["Naive (±40%)", "coverage"] == pytest.approx(6 / 10)
    assert head.loc["Split conformal", "coverage"] == 1.0
    assert head.loc["Mondrian", "coverage"] == pytest.approx(6 / 10)

    by_type = data.coverage_by_day_type(df).loc["Naive (±40%)"]
    assert by_type["claims"].to_dict() == {"calm": 0, "normal": 1, "wild": 9}
    assert by_type.loc["wild", "coverage"] == pytest.approx(6 / 9)
    assert by_type.loc["normal", "coverage"] == 0.0


def test_interval_score_is_width_plus_twenty_times_the_miss(ledger_path):
    df = data.load(ledger_path, START, END).claims
    split = df[df["agent"] == "Split conformal"]
    assert split["score"].tolist() == pytest.approx([2 * SPLIT_HALF] * 10)  # all covered

    jan5 = df[(df["agent"] == "Naive (±40%)") & (df["day"] == date(2025, 1, 5))].iloc[0]
    miss = jan5["realised"] - jan5["upper"]
    assert miss > 0
    assert jan5["score"] == pytest.approx(jan5["width"] + 20 * miss)


def test_only_the_latest_resolution_version_counts(tmp_path):
    path = build_ledger(tmp_path / "ledger.duckdb")
    before = data.headline(data.load(path, START, END).claims)
    assert before.loc["Naive (±40%)", "coverage"] == pytest.approx(6 / 10)

    # corrected truth for Jan 5: version 2 lands on the point, so the claim is now covered
    ledger = Ledger(path)
    claim_id = "pillswood-persistence-2025-01-05"
    point = ledger.get_claim(claim_id).point
    assert ledger.resolve(claim_id, point, truth_data_hash="corrected").resolution_version == 2
    ledger.close()

    df = data.load(path, START, END).claims
    assert data.headline(df).loc["Naive (±40%)", "coverage"] == pytest.approx(7 / 10)
    row = df[df["claim_id"] == claim_id].iloc[0]
    assert row["resolution_version"] == 2
    assert bool(row["covered"])
    assert len(df) == 30  # still one row per claim


def test_date_filter_is_inclusive(ledger_path):
    df = data.load(ledger_path, date(2025, 1, 5), date(2025, 1, 6)).claims
    assert sorted(df["day"].unique()) == [date(2025, 1, 5), date(2025, 1, 6)]
    assert len(df) == 6


def test_html_is_written_with_all_agents(ledger_path, tmp_path, capsys):
    digest = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
    out = tmp_path / "reports" / "calibra_report.html"
    args = [str(START), str(END), "--ledger", str(ledger_path), "--out", str(out)]
    assert main(args) == 0
    assert "wrote" in capsys.readouterr().out
    html = out.read_text(encoding="utf-8")
    for label in LABELS:
        assert label in html
    assert "NOT money saved" in html
    assert "abc1234" in html and "def5678" in html
    assert "<script src" not in html  # plotly is inlined, nothing fetched from the network
    assert html.count("plotly.js v") == 1
    assert hashlib.sha256(ledger_path.read_bytes()).hexdigest() == digest  # read-only


@pytest.mark.parametrize(
    "start, end, message",
    [
        ("2025-13-01", "2025-01-12", "invalid date '2025-13-01'"),
        ("01/03/2025", "2025-01-12", "invalid date '01/03/2025'"),
        ("2025-01-03", "tomorrow", "invalid date 'tomorrow'"),
        ("2025-01-12", "2025-01-03", "START (2025-01-12) is after END (2025-01-03)"),
    ],
)
def test_bad_dates_give_a_clear_error(start, end, message, ledger_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main([start, end, "--ledger", str(ledger_path)])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert message in err
    assert "Traceback" not in err


def test_missing_ledger_gives_a_clear_error(tmp_path, capsys):
    missing = tmp_path / "nope.duckdb"
    out = tmp_path / "report.html"
    assert main(["2025-01-03", "2025-01-12", "--ledger", str(missing), "--out", str(out)]) == 1
    err = capsys.readouterr().err
    assert f"ledger file not found: {missing}" in err
    assert "Traceback" not in err
    assert not missing.exists() and not out.exists()


def test_no_claims_in_range_gives_a_clear_error(ledger_path, tmp_path, capsys):
    out = tmp_path / "report.html"
    assert main(["2024-01-01", "2024-01-31", "--ledger", str(ledger_path), "--out", str(out)]) == 1
    assert "no resolved claims between 2024-01-01 and 2024-01-31" in capsys.readouterr().err
    assert not out.exists()
