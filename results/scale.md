# Scale

One check of each kind over 28 days of data, 7 days for the reconcile window; median over 5 runs with the range in brackets. Python's sqlite3 on one laptop, synthetic data, loading not included.

| rooftops | recordings | quiet check (s) | reconcile (s) |
| --- | --- | --- | --- |
| 80 | 92490 | 0.1 (0.095-0.128) | 0.311 (0.285-0.442) |
| 1000 | 1040238 | 0.88 (0.844-1.083) | 10.069 (9.548-10.336) |
