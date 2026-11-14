"""Synthetic-only tests: no Docker, database or real personal data required."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from audit_source_v2 import indexed, profile, relation, rows, student_code


class AuditSourceTests(unittest.TestCase):
    def test_student_code_priority_and_email(self):
        self.assertEqual(student_code("tx2000000", "x@invalid.test"), "2000000")
        self.assertEqual(student_code("staff", "sv2411847@invalid.test"), "2411847")
        self.assertEqual(student_code("2411847", "2000000@invalid.test"), "2411847")
        self.assertIsNone(student_code("staff", "x@2411847.invalid"))

    def test_candidate_rejects_ambiguous_or_long_digit_runs(self):
        # Conservative audit candidate rule, not an implemented login policy.
        self.assertIsNone(student_code("12345678", "x@invalid.test"))
        self.assertIsNone(student_code("1234567x7654321", "x@invalid.test"))
        self.assertIsNone(student_code("１２３４５６７", "x@invalid.test"))

    def test_skip_exactly_one_malformed_record_and_keep_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.csv"
            contents = '1,"broken"suffix,x\n2,manual,0\n3,manual,1\n'
            path.write_text(contents, encoding="utf-8")
            with self.assertRaises(csv.Error):
                list(rows(path))
            self.assertEqual(list(rows(path, True)), [["2", "manual", "0"], ["3", "manual", "1"]])
            self.assertEqual(path.read_text(encoding="utf-8"), contents)

    def test_profile_multiline_vietnamese_and_no_personal_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.csv"
            path.write_text('1,"Tiếng Việt\nxuống dòng",private@example.test\n2,x,y\n', encoding="utf-8")
            result = profile(path)
            self.assertEqual(result["parsed_rows"], 2)
            self.assertEqual(result["column_counts"], {3: 2})
            self.assertIsNone(result["parse_error"])
            self.assertNotIn("private@example.test", json.dumps(result))
            self.assertNotIn("Tiếng", json.dumps(result, ensure_ascii=False))

    def test_profile_invalid_csv_and_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.csv"
            path.write_text('1,x\n1,y\n', encoding="utf-8")
            self.assertEqual(profile(path)["duplicate_first_column"], 1)
            with self.assertRaises(ValueError):
                indexed(path)
            path.write_text('1,"broken"suffix\n', encoding="utf-8")
            self.assertIsNotNone(profile(path)["parse_error"])

    def test_missing_relation_is_counted_not_repaired(self):
        child = {"1": ["1", "10"], "2": ["2", "20"]}
        self.assertEqual(relation(child, 1, {"10": []}), {"rows": 2, "missing_parent_rows": 1})
        self.assertEqual(child["2"][1], "20")


if __name__ == "__main__":
    unittest.main()
