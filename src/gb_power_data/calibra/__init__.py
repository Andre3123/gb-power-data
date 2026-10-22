"""Calibra for any forecaster: bring your own forecasts, get honest ranges and a record.

from gb_power_data.calibra import Calibra, Forecast, Outcome
engine = Calibra("data/calibra/ledger.duckdb")
engine.submit(Forecast(...))   # before the target period
engine.resolve(Outcome(...))   # after it
engine.report("my-optimiser")
"""

from gb_power_data.calibra.engine import Calibra, Settings
from gb_power_data.calibra.schema import Forecast, Outcome

__all__ = ["Calibra", "Forecast", "Outcome", "Settings"]
