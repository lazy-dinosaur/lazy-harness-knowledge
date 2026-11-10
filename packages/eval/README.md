# packages/eval

Stage runner, experiment preparation, calibration and measurement tools (`pipeline`, `prepare`, `reembed`,
`calibrate`, `calibration_evidence`, `report`, `area_report`, `fresh`, `run_paid_pilot`, `local_db`). Depends on
`packages/core` (and on `hosts/pi` for real model calls); the core never imports from here.

Experiment inputs and results are never committed: inputs come from outside the repository and run state goes to an
ignored state directory (issue #6 step 5).

Run: `python3 -m pytest -q packages/eval/tests`.
