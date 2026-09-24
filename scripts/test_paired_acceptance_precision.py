"""Check paired precision at the proposed five-point quality boundary."""
import unittest

try:
    from scripts.paired_acceptance_precision import lower_percentile
except ModuleNotFoundError:
    from paired_acceptance_precision import lower_percentile


class PairedAcceptancePrecisionTests(unittest.TestCase):
    def test_discordance_can_reverse_quality_feasibility_at_same_point_estimate(self):
        low = lower_percentile(39, 39, 282)
        high = lower_percentile(40, 40, 280)
        self.assertEqual(low["observed_difference"], high["observed_difference"])
        self.assertEqual(low["bootstrap_lower_2_5_percentile"], -17 / 360)
        self.assertEqual(high["bootstrap_lower_2_5_percentile"], -18 / 360)
        self.assertTrue(low["strict_minus_5pp_screen_passes"])
        self.assertFalse(high["strict_minus_5pp_screen_passes"])

    def test_all_concordant_has_zero_width(self):
        self.assertEqual(lower_percentile(0, 0, 360)["bootstrap_lower_2_5_percentile"], 0)

    def test_invalid_counts_rejected(self):
        for values in ((0, 0, 0), (-1, 1, 2), (True, 1, 2)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                lower_percentile(*values)


if __name__ == "__main__":
    unittest.main()
