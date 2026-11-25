# Legacy: the pre-fix backtest

These are the original versions, kept for the record. Don't use them for results.

| File | What |
|---|---|
| `as_pre_fix.py` | `as.py` before the October 2026 fixes |
| `backtest_daily_results_pre_fix.csv`, `backtest_summary_pre_fix.csv` | Results that an earlier version of that code produced for a single session (2025-02-03) |
| `README_pre_fix.md` | The original README and its claims |

The old results can't be reproduced, even with `as_pre_fix.py`: the CSVs came from an older version still. For example, they report +5,194 pt for the mid at cap 100, while `as_pre_fix.py` gives −1,238 pt on the same day.

What was wrong, and how much each problem mattered, is described in the main [README](../README.md#what-i-got-wrong-first) and in [`diagnostics/`](../diagnostics/).
