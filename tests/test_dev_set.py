import json
import unittest
from collections import Counter

from scripts.select_dev_set import allocate, canonical_digest, select


class DevelopmentSetTests(unittest.TestCase):
    def test_original_proportions_round_to_20_and_10(self):
        self.assertEqual(allocate({"Query": 195, "Management": 105}, 30),
                         {"Query": 20, "Management": 10})

    def test_selection_is_fixed_unique_and_independent_of_input_order(self):
        rows = ([{"instance_id": f"Q_{i}", "category": "Query"} for i in range(195)]
                + [{"instance_id": f"M_{i}", "category": "Management"} for i in range(105)])
        chosen = select(rows, 30, 20261014)
        self.assertEqual(chosen, select(list(reversed(rows)), 30, 20261014))
        self.assertEqual(len(chosen), 30)
        self.assertEqual(len(set(chosen)), 30)
        categories = {row["instance_id"]: row["category"] for row in rows}
        self.assertEqual(Counter(categories[task_id] for task_id in chosen),
                         {"Query": 20, "Management": 10})

    def test_small_category_is_not_oversampled(self):
        self.assertEqual(allocate({"Query": 9, "Management": 1}, 10),
                         {"Query": 9, "Management": 1})
        with self.assertRaises(ValueError):
            allocate({"Query": 9, "Management": 1}, 11)

    def test_source_fingerprint_ignores_line_endings_and_key_order(self):
        lf = '{"instance_id":"Q_1","category":"Query"}\n'
        crlf = '{"category":"Query","instance_id":"Q_1"}\r\n'
        self.assertEqual(canonical_digest([json.loads(lf)]), canonical_digest([json.loads(crlf)]))
        self.assertNotEqual(canonical_digest([json.loads(lf)]),
                            canonical_digest([{"instance_id": "Q_2", "category": "Query"}]))


if __name__ == "__main__":
    unittest.main()
