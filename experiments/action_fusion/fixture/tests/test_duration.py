import unittest

from duration import parse_duration


class DurationTests(unittest.TestCase):
    def test_existing_integer_seconds(self):
        self.assertEqual(parse_duration(0), 0)
        self.assertEqual(parse_duration(7), 7)

    def test_minutes(self):
        self.assertEqual(parse_duration("3m"), 180)

    def test_hours(self):
        self.assertEqual(parse_duration("2h"), 7200)

    def test_negative_string(self):
        with self.assertRaises(ValueError):
            parse_duration("-1s")


if __name__ == "__main__":
    unittest.main()
