# Benchmark source and quality risks for the next SoL Codex study

The exposed [quiet-diagnostic model screen](../measurements/2026-09-25-quiet-diagnostic-model-screen-development.md) had a token ratio of 0.693 but failed independent quality checks. Its two Click repairs produced the same source tree and both missed nested-scope behavior. A new study must select new lineages and establish repair acceptance before using token ratios as evidence of savings.

## What the benchmark literature supports

- [SWE-rebench V2](https://arxiv.org/abs/2602.23866) reports 32,079 executable tasks across 3,617 repositories and describes the collection as reinforcement-learning training environments. Its breadth is useful for drawing diverse lineages, but drawing from the public release cannot establish that a model has never seen a task.
- [SWE-bench Goes Live!](https://arxiv.org/abs/2505.23419) introduced a continuously updatable construction pipeline; its initial public release comprised 1,319 issues across 93 repositories with per-task Docker images. A later sample still needs a pinned dataset revision, issue dates and environment verification. The word “live” alone does not prove a sampled task was unseen in training.
- [SetUpAgent / SWEE-Bench and SWA-Bench](https://arxiv.org/abs/2503.07701) broadened repository coverage and found substantial distribution shifts, including up to 40% lower agent success rates than SWE-bench in the authors' comparisons. Its environment setup succeeds for only a subset of repositories, so an automated candidate list cannot substitute for a reproducible preflight on every admitted task. The paper also found a significant post-knowledge-cutoff performance drop for one model on SWA-Bench, which is evidence of a possible contamination effect, not a test of the model or tasks used here.

## Consequences for this campaign

The current [24-lineage proposal](2026-09-25-heldout-quiet-screen-proposal.md) remains a proposal. Its pinned SWE-rebench source, exclusions, two-curator eligibility review, complete ranked-prefix inspection, external behavior oracles and approval gates still apply. Adding SWE-bench-Live or SWEE-Bench would be a new sampling protocol, not an informal substitution after viewing outcomes.

For each future task, record the source and dataset revision, issue and merge dates, exact parent tree, environment image or dependency lock, full test inventory, and independent defect and preservation witnesses. Challenge the oracle with plausible wrong fixes and an alternate valid fix where possible. Freeze these checks before revealing the task to either arm. Report “held out from this project's development” unless stronger evidence supports a claim about model-training exposure.

These papers guide sample construction and interpretation. They do not measure SoL Codex token efficiency or prove quality preservation.
