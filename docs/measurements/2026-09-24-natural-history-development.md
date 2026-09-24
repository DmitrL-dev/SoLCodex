# Natural historical repair development runs (2026-09-24)

These are development observations on exposed historical issues. They test whether a coding agent follows an opt-in receipt protocol during natural repository repair, then whether the complete repair passes checks outside its workspace. They do not estimate a general saving for SoL Codex. The installed hook was disabled; the treatment was a separate explicit command adapter plus instructions. Raw traces, patches, command artifacts, and private verifiers are not published.

The [Click and packaging aggregate](data/2026-09-24-natural-compliance-dev.json) contains provider-reported token categories, command counts, elapsed time, verifier outcomes, fixture hashes, tool hashes, and isolation canaries. `scripts/ab_trace.py` computed its trace fields. Both arms used `gpt-6-luna` at low effort, `codex-cli 0.155.0-alpha.16.3`, code mode, ephemeral sessions, disabled hooks/plugins, and Python 3.12.14 in the fixture toolchain. Each working copy began with a one-commit export of the historical parent; the evaluation applied only the agent's tracked patch to a separate parent copy. A local proxy allowed model API hosts, while the macOS Seatbelt profile denied direct network access and access to the live repository, hidden checks, gold canary, and other arm. All recorded canaries passed. This is developmental containment, not an absolute security boundary.

## Click and packaging: protocol was not followed

ON was instructed to capture commands expected to print more than 6 KiB with the explicit receipt adapter; OFF could run or filter commands directly. The adapter, search helper, prompts, package versions, and randomized arm order were frozen before these runs. The external parent and historical-fix matrices were rerun before the agents: [Click #2447](https://github.com/pallets/click/issues/2447) scored 3/9 → 9/9, and [packaging #928](https://github.com/pypa/packaging/issues/928) scored 8/14 → 14/14. Both agent repairs passed their respective full matrices and `git diff --check`.

| Task; execution order | OFF tokens | ON tokens | OFF seconds | ON seconds | External checks, OFF / ON | ON commands over 6 KiB; adapter calls |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| Click; ON → OFF | 205,269 | 312,910 | 61.11 | 79.54 | 9/9 / 9/9 | 3; 0 |
| packaging; OFF → ON | 182,670 | 226,228 | 39.88 | 47.68 | 14/14 / 14/14 | 2; 0 |
| **Total** | **387,939** | **539,138** | **100.99** | **127.22** | **2 / 2 pairs pass** | **5; 0** |

ON used 151,199 more provider-reported input/output tokens (+39.0%) and 26.23 more elapsed seconds (+26.0%) across these two pairs. Cached input was 339,968 OFF versus 478,976 ON; uncached input was 45,057 versus 56,260; output was 2,914 versus 3,902. The agents took different trajectories, and the ON agent never invoked the adapter despite five completed command outputs above 6 KiB. The 6 KiB rule was based on expected output, so observed output size alone cannot prove each command was predictable in advance. It does show ample missed opportunities and makes the result a negative test of instruction adherence, **not an estimate of receipt compression's effect**. The extra ON instructions and their cost remain part of this observed treatment.

Two earlier attempts are excluded. One failed the isolation/toolchain setup before usable agent traces; another had a broken fixture/tool path and an incomplete packaging ON repair. Neither is silently counted as a successful or failed treatment observation. Their raw traces remain private.

## SQLGlot traceback: applied treatment, incomplete cost telemetry

The [SQLGlot #7732](https://github.com/tobymao/sqlglot/issues/7732) development fixture used parent `f3ba8e4d20311c4e657f4f763c3e354fa533ba47` and historical fix `22844841f1de93643f17ae51077ceac79b0ec69a`. The parent scored 5/9 and the fix 9/9 on the external matrix before the frozen run. A `repro.py` was committed into both one-commit agent baselines and included in the fixture digest, so the evaluator could apply changes to it. OFF was told to run that reproducer directly as its first command; ON was told to run it through the adapter. The order was OFF → ON and the limit was 300 seconds per arm. The [aggregate JSON](data/2026-09-24-sqlglot-dev-v1.json) preserves nulls for missing provider usage.

| Arm | First command result | Agent time | External matrix | Provider-reported tokens |
| --- | ---: | ---: | --- | ---: |
| OFF | 233,616 bytes, direct traceback | 300.01 s, interrupted | 5/9 | Unknown: no completed-turn usage |
| ON | 894-byte receipt for a 232,616-byte exact artifact | 216.34 s, exit 0 | 9/9 | 1,006,886 input + output |

The ON first command used the adapter once. Its receipt reported a completed capture and child exit code 1; the artifact's byte count and SHA-256 matched the receipt. Both first commands followed their assigned protocol. ON also passed four post-hoc positive-path assertions that check the selected `F.VALUE AS V`, the source table, and the qualified lateral argument. The historical fix passes those four checks. One targeted lineage test passed. An optimizer test collection attempt failed because the experiment environment lacked `duckdb`; it supplies no regression evidence.

The frozen 9-check matrix was weaker than intended: a mutation that replaces a successful projection with `SELECT 999 AS v` can still score 9/9. The four additional checks were written after the result and must not be presented as a predeclared gate. The first evaluator also executed the agent's code outside the agent sandbox; no interference was observed, but this limits confidence in isolation. A second development series uses a stronger frozen matrix and a sandboxed evaluator. The OFF arm was still working when its timeout killed the process. Because `codex exec --json` emitted no `turn.completed` usage for it, the pair has **no measurable token or billing difference**. An ON win in the behavior matrix does not establish quality noninferiority or an economic gain.

## SQLGlot second series: complete telemetry, failed ON repair

The [second aggregate](data/2026-09-24-sqlglot-dev-v2.json) records a separate frozen series on the same exposed task. The order was ON → OFF, the per-arm timeout was 600 seconds, and the external matrix added the four projection/argument checks **before** either agent ran. Parent and historical fix scored 8/13 and 13/13. The evaluator replayed tracked changes and regular untracked files in a separate sandbox; a canary probe confirmed that it could read/write its evaluation copy but could not read gold or the live repository or connect directly to the network. The agent's first-command protocol was satisfied in both arms; ON's completed 232,616-byte capture again matched its receipt SHA-256. These controls reduce known leakage routes without making the local profile an absolute security boundary.

| Arm | External acceptance | Provider input + output tokens | Cached / uncached input | Agent time | First result |
| --- | --- | ---: | ---: | ---: | ---: |
| OFF | 13/13; accepted | 1,286,786 | 1,204,224 / 75,418 | 171.56 s | 233,616 bytes |
| ON | No patch; rejected | 389,507 | 349,952 / 37,655 | 62.36 s | 894-byte receipt |

The ON agent said it had not completed the repair, and its checkout had no tracked patch. A post-run check of that unchanged checkout still scored 8/13. OFF passed the frozen 13/13 matrix, `git diff --check`, and one targeted lineage regression test. Both CLI runs exited normally with one `turn.completed` usage record each, so token telemetry for this series is complete. ON used fewer tokens because it stopped without an accepted repair. Tokens per accepted ON task are undefined, not 389,507, and this is a **quality failure rather than an efficiency win**. Agent times exclude fixture preparation and external evaluation. This second attempt is not an independent SQLGlot task or a confirmatory replication.

These issue families were inspected before or during protocol development. They are not held out. One run per arm, differing trajectories, provider cache behavior, and no billing record prevent a causal cost claim. The [confirmation protocol](../research/2026-09-24-cost-frontier.md) requires independent frozen tasks, complete usage for failures, and an explicit adherence gate.
