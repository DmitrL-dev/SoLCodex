# Paired acceptance precision sensitivity (2026-09-24)

The proposed 360 pairs and five-percentage-point noninferiority margin do not by themselves make the quality gate attainable. The paired uncertainty depends on **discordant** tasks: ON-only successes minus OFF-only successes. Concordant success/success and failure/failure pairs contribute zero to that difference.

[`scripts/paired_acceptance_precision.py`](../../scripts/paired_acceptance_precision.py) calculates the exact 2.5th percentile of the *conditional paired-bootstrap distribution* for hypothetical counts. It draws paired differences `+1`, `-1`, or `0` from the specified empirical proportions and convolves their probability mass over 360 draws. This is a design-stage sensitivity calculation, **not** the preregistered confirmatory paired score interval and not a coverage guarantee.

| ON-only | OFF-only | Concordant | Observed difference | Bootstrap lower percentile | Strict `> -5 pp` screen |
| ---: | ---: | ---: | ---: | ---: | --- |
| 18 | 18 | 324 | 0 pp | -3.33 pp | pass |
| 36 | 36 | 288 | 0 pp | -4.72 pp | pass |
| 39 | 39 | 282 | 0 pp | -4.72 pp | pass |
| 40 | 40 | 280 | 0 pp | -5.00 pp | fail |
| 45 | 45 | 270 | 0 pp | -5.28 pp | fail |
| 34 | 38 | 288 | -1.11 pp | -5.83 pp | fail |

Reproduce a row with `python3 scripts/paired_acceptance_precision.py 40 40 280`. The strict inequality matters: exactly -5.00 pp fails. This screen can disagree with the final paired score method; the score interval and its computation must be frozen before any confirmation run. If expected discordance makes the gate implausible, change the design and sample size before selecting tasks, not after observing arms. No acceptance outcomes were used to choose these examples.
