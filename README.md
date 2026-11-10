# lazy-harness knowledge

The knowledge module of lazy-harness: it reads source documents, splits them into knowledge units and places each unit
under a DDD domain and subject so knowledge can be found and changed in exactly one place. Developed as its own
repository so each part is built, tested and released separately, and driven by issues and PRs (umbrella issue #1).

## Layers

| Path | Owns |
|---|---|
| `packages/core` | placement and judgement engine: domain/subject identity, judgement questions and criteria, store contracts, port definitions |
| `hosts/pi` | Pi model transport only (loads Pi `ModelRuntime`; never creates agent sessions, extensions or tools) |
| `packages/eval` | stage verification, probes and calibration tools shared by all checks |
| `migrations`, `deploy` | database schema and deployment |

## Boundaries

- **No pi-fabric dependency anywhere in this repository.** The module owns its port contracts (model call, judgement,
  worker/run) and conformance tests with fakes; the pi-fabric implementation and version pinning live in lazy-harness,
  the composition layer.
- Only source, tests and the fixtures those tests need live here. Design/decision/measurement records and measurement
  evidence stay in the lazy-harness repository under `.lazy-harness/`; see `MIGRATION.md`.
- Experiment result data (run outputs, raw model responses, database dumps, `.local/`) is never committed.

Language: Python for the core and evaluation; a small Node transport for the Pi host.

Tests: `./run-tests.sh` (each package in its own process).

Run state: `<repo>/.state` by default (gitignored; `LH_KNOWLEDGE_STATE_HOME` overrides). Experiment inputs are
not part of this repository; point `LH_KNOWLEDGE_INPUTS` at them.
