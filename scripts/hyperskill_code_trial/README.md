# Hyperskill code judging experiment

This is an isolated experiment runner, not the production website judge.

- Runtime inputs, public snapshots, generated programs, account receipts and reports belong in an explicitly supplied directory under `D:/Artifacts/GraspMemoEdu`.
- The first 100-question cohort is a development set. A subsequent cohort must exclude all its question IDs, be sampled before reference matching, and be frozen before official submission.
- Existing complete executable tests take priority. Public examples alone are not complete tests: preserve them and add source-derived branch, boundary and state tests.
- Test production sees the exercise and original template only. It does not see reference implementations, faulty variants or official results.
- Generated test code must compile. Harnesses use the shared assertions and must execute assertions; printing a success marker is not acceptance.
- Technical test failures are reported as test errors. They are not converted to incorrect learner answers.
- Official submissions are serialized per question, journaled before posting, and reconciled by exact identity after uncertainty. Only the authorized account is used.
- Primary evaluation is frozen before official outcomes. Do not tune tests or reference programs using the held-out cohort's verdicts and present the retuned result as an independent evaluation.

Run the source-only production regression and sandbox checks before selecting the new cohort. Keep each raw production attempt and failure. Repairs operate on the general prompt or runner and regenerate from original inputs; do not hand-edit produced test or answer code.

The workflow order is `init` → public `fetch_steps.mjs` → `prepare` → reference/test production → source-only review → `collect` → `compile` → `freeze` → `local` and official `run` → `summarize.py` and `audit.py`. Only compile diagnostics are inspected before freezing; neither local semantic decisions nor official outcomes may guide the held-out test construction. The official client requires `HYPERSKILL_TRIAL_ROOT` and rejects changed frozen input hashes. Native production uses the existing AI Production Supervisor runner. Java compilation/execution uses `/usr/bin/python3` in `Ubuntu-24.04-D`, the installed JDK 25, Bubblewrap and systemd resource limits.

The sampler records planned and actual quotas. It excludes all prior IDs and identical source content; an exhausted stratum is capped at its remaining population and unused places go to the largest remaining stratum. It never reuses an old question to fill a quota.

Compile-only failures can be sent through `technical_review_inputs.py` before freezing; it provides the original task, test draft and compiler diagnostics, without reference implementations or official feedback. Missing or identical mutants use `prepare_mutant_retries.py` and `mutant_prompt.txt`; collection refuses a replacement that changes the frozen reference. Every attempt remains in the artifact directory.

The report measures official agreement separately from local execution coverage and from positive-plus-negative discrimination. Mutations accepted by the official judge are not treated as known wrong programs. For native IO questions, the same runs also measure public-example-only decisions against supplemented decisions. Official agreement is a useful comparison, not proof that the official judge perfectly matches every stated contract.
