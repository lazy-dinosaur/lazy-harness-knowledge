# Migration from lazy-harness

Source: the lazy-harness worktree `lazy-harness.v2-g16`, folder `experiments/v2-knowledge-module-runner-01/` (the
`scale-04/` code there is gitignored, so this repository becomes the first git history of that code).
Records stay in lazy-harness: `.lazy-harness/planning/v2-knowledge-module-extraction.md` (plan),
`.lazy-harness/planning/v2-stagewise-verification-plan.md` (execution log), `.lazy-harness/domain/v2-knowledge-forms.md`
(placement rules), `.lazy-harness/spec/v2-knowledge-ddd-structure.md` (structure contract),
`.lazy-harness/ssot/paid-run-cash-caps.md` (paid-run limits), main `.lazy-harness/ssot/project-identity.md` (module boundary).

Order (each step keeps its own tests green before the next):

1. **P0 in progress** — layout and ignore rules (this PR). Inventory: Python import closure of `scale-04/place_write.py`,
   `stagewise.py`, `active_worker.py`, `pipeline.py` and the 8 green offline test bundles = 113 modules (about 1.5 MB):
   runner root 72, `scale-04/` 38, `subject-criteria-01/` 2, `fragmenter/` 1. Before copying: classify referenced JSON
   files as fixtures or results (`scale-04/plan.json`, `capture-01/s1b-claims-flat.json`, `scale-01/records.json`,
   `read-01/questions.json`, `subject-criteria-01/results2.json`, …), decide whether history-specific modules
   (`task314_*` … `task318_*`) are needed by the product path, and classify the pre-existing stale offline failures.
2. `packages/core` — the closure's engine/criteria/store modules with their offline tests; the 8 bundles stay green
   (`test_umbrella`, `test_place_write`, `test_input_extract_domain`, `test_active_worker`, `test_numbered_domain`,
   `test_stagewise`, `test_domain_membership`, `test_placement_decisions`).
3. `hosts/pi` — `writer_host.mjs`, `active_worker_host.mjs`, `active_worker_transport.py`, `writer_transport.py` and their
   offline tests.
4. `packages/eval` — stage runner and probes, code only; run outputs stay in lazy-harness `.local`.
5. Port contracts + fake conformance tests (issue #1 P4); the pi-fabric implementation is lazy-harness work.
6. lazy-harness keeps a thin consumer only.
