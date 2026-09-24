"""Exact conditional paired-bootstrap sensitivity for a planned A/B sample.

This is a design-stage precision calculation, not the confirmatory quality
interval. It uses only hypothetical counts of ON-only and OFF-only successes.
"""
from __future__ import annotations

import argparse
import json


def lower_percentile(on_only: int, off_only: int, concordant: int) -> dict:
    counts = (on_only, off_only, concordant)
    if any(type(value) is not int or value < 0 for value in counts):
        raise ValueError("pair counts must be nonnegative integers")
    n = sum(counts)
    if n == 0:
        raise ValueError("at least one pair is required")

    # A bootstrap resample of paired differences draws +1, -1, or 0 from
    # the observed empirical distribution. Convolve exactly over n draws.
    plus, minus = on_only / n, off_only / n
    zero = concordant / n
    mass = {0: 1.0}
    for _ in range(n):
        next_mass = {}
        for difference, probability in mass.items():
            for step, weight in ((1, plus), (-1, minus), (0, zero)):
                if weight:
                    target = difference + step
                    next_mass[target] = next_mass.get(target, 0.0) + probability * weight
        mass = next_mass

    cumulative = 0.0
    lower_count = None
    for difference in sorted(mass):
        cumulative += mass[difference]
        if cumulative >= 0.025:
            lower_count = difference
            break
    assert lower_count is not None
    return {
        "pairs": n,
        "on_only": on_only,
        "off_only": off_only,
        "concordant": concordant,
        "observed_difference": (on_only - off_only) / n,
        "bootstrap_lower_2_5_percentile": lower_count / n,
        "strict_minus_5pp_screen_passes": lower_count / n > -0.05,
        "method": "conditional paired bootstrap; planning sensitivity only",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("on_only", type=int)
    parser.add_argument("off_only", type=int)
    parser.add_argument("concordant", type=int)
    args = parser.parse_args()
    print(json.dumps(lower_percentile(args.on_only, args.off_only, args.concordant), sort_keys=True))


if __name__ == "__main__":
    main()
