# Action-fusion mechanism development pair — 2026-09-24

## Scope and assignment

The [protocol](../../experiments/action_fusion/protocol.json) and private-runner dependency hashes were frozen at [`31b518d`](https://github.com/DmitrL-dev/SoLCodex/commit/31b518d2c58675700230e98bbaab4a79b331d58b) before either model run. One exposed synthetic duration-parser task was run in fixed OFF → ON order with `gpt-6-luna`, low reasoning, code mode, isolated workspaces, and a 240-second limit per arm. OFF could fuse actions naturally. ON received one additional [instruction](../../experiments/action_fusion/on_instruction.txt) to combine a known deterministic edit and its narrow verifier while preserving separate authorization and status. Hooks, receipt packing, phase reset, and other skills were disabled. This is a mechanism development check, not an installed-plugin test.

The parent failed the public and external checks. A reference fix passed 4/4 public and 34/34 external cases. Eleven constructed wrong variants and one test-runner forgery were rejected by the external evaluator. These are targeted controls, and two share a whitespace witness; they do not satisfy a plausible-repair diversity gate. Their [source snapshots and witnesses](../../experiments/action_fusion/controls_manifest.json) are public. The model agents could not read the reference, evaluator, private logs, or host authentication. The trusted controller ran each candidate case in a separate network-denied child and compared results outside the candidate process.

## Recorded outcome

| Measure | OFF | ON |
| --- | ---: | ---: |
| External cases | 34/34 | 34/34 |
| Public suite | 4/4 | 4/4 |
| Completed model requests | 5 | 9 |
| Proxy-observed input tokens | 50,140 | 99,774 |
| Of those, cached input tokens | 34,560 | 64,256 |
| Proxy-observed output tokens | 748 | 1,342 |
| Input + output tokens | 50,888 | 101,116 |
| Agent wall time | 29.061 s | 53.307 s |
| Completed command items | 4 | 7 |
| Completed file-change items | 1 | 3 |
| Public verifier exits | `0` | `1, 1, 0` |

Both arms finished within the frozen time limit, left a source-only candidate snapshot, and passed the independent 34-case evaluation with clean process-group teardown. The proxy ledger has observed usage for every completed request; its totals match the CLI turn usage. ON used **50,228 more observed input/output tokens (98.7%)** and **24.246 s more agent time (83.4%)** in this pair. These are observed differences, not a causal effect estimate or a provider bill.

The OFF agent applied one patch and ran the public verifier successfully. ON first submitted a shell command containing a Python here-document edit followed by `python3 -m unittest discover -s tests -v` on a later line. The shell reported `can't create temp file for here document: operation not permitted`; the verifier still ran against the unedited parent and failed. ON later repaired the source and passed. A shared shell invocation is not evidence that edit and verifier kept separate authorization boundaries, and the first attempt did not gate the verifier on edit success. No qualified action fusion is attested by this trace. The attempted shortcut added a repair loop rather than reducing model turns.

The fixed order, one synthetic task, prompt overhead, cache differences, and divergent trajectories prevent attribution of the token difference to the instruction alone. The installed hook was not exercised. Provider-debited usage is unavailable, so these figures remain **proxy-observed**. This policy revision has no basis for expansion to a savings claim; the separate-permission and edit-success gate would need redesign and another frozen development test before a larger campaign.

## Reproduction and provenance

- [Fixed-field aggregate](data/2026-09-24-action-fusion-mechanism-dev.json) records candidate, trace, process, evaluator, and proxy-summary hashes, request usage, and acceptance without publishing raw traces or authentication.
- [Published OFF and ON source](../../experiments/action_fusion/results) exactly match the source-only snapshots pinned by the aggregate. The [reducer](../../experiments/action_fusion/reduce_pilot.py) checks the frozen protocol, private attempt ledger, CLI turn usage, independent evaluation, and source hashes when given the private run roots.
- [Public behavior replay](../../experiments/action_fusion/replay_behavior.py) uses a pinned Python image in read-only, network-disabled Linux containers. It checks all 34 cases for the parent, reference, OFF, and ON, plus one failing witness for each constructed wrong variant. Run `python3 -m experiments.action_fusion.replay_behavior` from the repository root on Linux x86-64 with Docker Engine. The [GitHub Actions workflow](../../.github/workflows/action-fusion-replay.yml) runs the same replay on `ubuntu-24.04`.

The public Docker replay reproduces behavior, not the original macOS agent isolation, token use, or private trace. The private-artifact hashes allow later audit of those measurements; they do not make private raw data independently observable. The result remains development evidence only and **general efficiency savings are NO-GO**.
