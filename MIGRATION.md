# Migration from lazy-harness

Source: the lazy-harness worktree `lazy-harness.v2-g16`, folder `experiments/v2-knowledge-module-runner-01/` (the
`scale-04/` code there is gitignored, so this repository becomes the first git history of that code).
Records stay in lazy-harness: `.lazy-harness/planning/v2-knowledge-module-extraction.md` (plan),
`.lazy-harness/planning/v2-stagewise-verification-plan.md` (execution log), `.lazy-harness/domain/v2-knowledge-forms.md`
(placement rules), `.lazy-harness/spec/v2-knowledge-ddd-structure.md` (structure contract),
`.lazy-harness/ssot/paid-run-cash-caps.md` (paid-run limits), main `.lazy-harness/ssot/project-identity.md` (module boundary).

Public repository (user decision 2026-11-07): no host-derived content is committed. The test source documents used in
lazy-harness (S002, S003, R01) come from a downstream host and stay out; every fixture copied here must be neutral or
synthetic (the same neutral-example rule the judgement prompts already follow). Measurement against real host documents
stays a lazy-harness activity.

Order (each step keeps its own tests green before the next):

1. **P0 in progress** — layout and ignore rules (this PR). Inventory: Python import closure of `scale-04/place_write.py`,
   `stagewise.py`, `active_worker.py`, `pipeline.py` and the 8 green offline test bundles = 113 modules (about 1.5 MB):
   runner root 72, `scale-04/` 38, `subject-criteria-01/` 2, `fragmenter/` 1. Before copying: classify referenced JSON
   files as fixtures or results (`scale-04/plan.json`, `capture-01/s1b-claims-flat.json`, `scale-01/records.json`,
   `read-01/questions.json`, `subject-criteria-01/results2.json`, …), decide whether history-specific modules
   (`task314_*` … `task318_*`) are needed by the product path, and classify the pre-existing stale offline failures.
   **P0 findings (read-only, 2026-11-07):**
   - History chain: `source_role_contract.py` imports `ExecutionReceipt` from `task314_execution.py`, which pulls 8 history
     recovery modules (`task314_*`, `task316_*`, `task317_*`, `task318_*`) into the product closure through
     `pipeline → active_worker → writer_worker → source_context_policy → source_anchor_proof → source_role_contract`.
     `task314_execution.py` also hard-codes a lazy-harness-local execution directory. Plan: move `ExecutionReceipt` into a
     neutral core module in this repository and leave the history modules in lazy-harness.
   - Host names: host-specific host/container ids in `local_db.py`, `prepare.py`, `pipeline.py` and
     `test_placement_decisions_offline.py`; an absolute user home path in `pipeline.py`. Plan: neutral configurable
     defaults; no host name or user path in this repository.
   - Data files: product code reads only the run state `state/plan.json` at runtime; the asset `scale-04/plan.json`
     (records, questions, caps, estimates) and `prepare.py`'s inputs (`capture-01/s1b-claims-flat.json`,
     `scale-01/records.json`, `read-01/questions.json`, `baselines-64.json`, `restore-report.json`, `fragmenter/prompt-v3.md`)
     are experiment inputs/results built from host documents — none is copied. `subject-criteria-01/results2.json` (read by
     `test_placement_decisions_offline.py`) is historical judgement output — replaced by a synthetic fixture.
   - Offline test baseline in the source folder (47 files): 41 green; failing — `test_feature_subjects` 1,
     `test_hierarchy` 1, `test_jev_state_choice` 3 (`('jev','selection') != ('jev','placement')` wire label), `test_pilot_repairs` 1,
     `test_split_ground` 1; `test_extraction_provenance` is a standalone (`env -i`) test that does not collect under pytest.
     These are carried as known failures, each to be fixed or dropped with a reason, not copied as silently failing.

2. **done in PR `core-migration`** — 95 source modules + 10 test files + `migrations/` under `packages/core`; history modules
   (`task314_*` … `task318_*`) left in lazy-harness (only `source_role_contract`'s history dispatch removed); host ids
   neutralized (`knowledge-local`), user paths removed (worker-root guard is opt-in via `LH_KNOWLEDGE_WORKER_ROOT`),
   host-derived catalog/gold/phrases replaced by neutral examples or kept in lazy-harness; result: 150 passed,
   2 skipped (need experiment inputs → `packages/eval` with synthetic fixtures), 1 expected failure (known source
   failure). Original text of this step:
   `packages/core` — the closure's engine/criteria/store modules with their offline tests; the 8 bundles stay green
   (`test_umbrella`, `test_place_write`, `test_input_extract_domain`, `test_active_worker`, `test_numbered_domain`,
   `test_stagewise`, `test_domain_membership`, `test_placement_decisions`).
3. **done in PR `hosts-pi`** — `writer_host.mjs`, `active_worker_host.mjs`, `writer_transport.py`, `active_worker_transport.py`
   moved to `hosts/pi/src`; the core reaches them only at runtime by module name (`host_defaults.py`, explicit
   `HostUnavailable` error) or through injected factories; `hosts/pi` may import the core, never the reverse. The Node
   replay test runs against the installed Pi runtime (skipped without Pi); the writer replay test is skipped because its
   capture producer was not found in the source folder. `run-tests.sh` runs each package in its own process. Original:
   `hosts/pi` — `writer_host.mjs`, `active_worker_host.mjs`, `active_worker_transport.py`, `writer_transport.py` and their
   offline tests.
4. `packages/eval` — stage runner and probes, code only; run outputs stay in lazy-harness `.local`.
5. Port contracts + fake conformance tests (issue #1 P4); the pi-fabric implementation is lazy-harness work.
6. lazy-harness keeps a thin consumer only.
