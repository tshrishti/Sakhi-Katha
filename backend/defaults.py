"""Default rate card used for pricing and task allocation. TODO: per-group, group-approved rate cards stored in the database."""
STAGES = [{"name": "cutting", "rate": 5, "workers": 1}, {"name": "stitching", "rate": 16, "workers": 2},
          {"name": "finishing", "rate": 8, "workers": 2}, {"name": "packing", "rate": 4, "workers": 1}]
COST = {"material": 38, "minutes": 40, "hourly": 50, "overhead_pct": 8, "margin_pct": 10}
