#claim ledger design 

## hypothesis
one claim record works unchanged across domains. Validated if battery revenue claim and a heat-pump COP claim both fit with no added or force fields 

## test 1: Pillswood daily revenue, 8 Jan 2025 (persistence baseline)

Numbers from 'scratch/day_claim.py'(crude revenue is heuristic: eff 0.85 placeholder, ignores charge discharges order)

-2025-01-07: ___(paste the output line)
-2025-01-08: ___(paste the output line)

### Claim 
```json
{
  "claim_id": "pillswood-rev-2025-01-08-001",
  "agent_id": "persistence-baseline",
  "model_version": "0.1",
  "code_version": "___",
  "recorded_at": "2025-01-07T18:00:00Z",
  "data_cutoff": "2025-01-07T23:00:00Z",
  "target_name": "daily_trading_revenue",
  "unit": "GBP",
  "target_period": "2025-01-08",
  "point": ___,
  "lower": ___,
  "upper": ___,
  "coverage": 0.90,
  "reference_class": "___",
  "resolution_rule": "crude one-cycle revenue on realised APX prices, 196 MWh, eff 0.85",
  "input_data_hash": "TODO",
  "context": {"site": "Pillswood", "energy_mwh": 196, "soc_start": 0.5}
}
```

### Resolution

```json
{
  "claim_id": "pillswood-rev-2025-01-08-001",
  "resolution_version": 1,
  "resolved_at": "2025-01-09T12:00:00Z",
  "realised_value": ___,
  "truth_data_hash": "TODO"
}
```

- Inside the interval? ___
- Which field breaks a rule? ___

## Test 2: heat pump weekly COP (made-up)

### Claim

```json
{
  "claim_id": "___",
  "agent_id": "___",
  "model_version": "___",
  "code_version": "___",
  "recorded_at": "___",
  "data_cutoff": "___",
  "target_name": "___",
  "unit": "___",
  "target_period": "___",
  "point": ___,
  "lower": ___,
  "upper": ___,
  "coverage": ___,
  "reference_class": "___",
  "resolution_rule": "___",
  "input_data_hash": "TODO",
  "context": {___}
}
```

## Result
Fields I had to force or add: ___