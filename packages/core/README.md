# packages/core

The knowledge placement core, moved from `experiments/v2-knowledge-module-runner-01/` (runner root + `scale-04/`) with
its offline tests. Modules keep their original flat names for now (`src/*.py`, imported via `tests/conftest.py`);
splitting out `hosts/pi` (`writer_host.mjs`, `active_worker_host.mjs`, transports) and `packages/eval` (stage runner,
prepare, calibration) follows in later steps.

- `migrations/`: database schema (applied/draft hash files are lazy-harness database evidence and stay there).
- Run: `python3 -m pytest -q packages/core/tests`.
- Not shipped: experiment inputs/results, host-derived gold sets and catalogs, the host-phrase leak guard (it lists host
  phrases, so it stays in lazy-harness and runs over this checkout there).
