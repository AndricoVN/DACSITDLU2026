"""Offline safety tests; never create backups, connect to DBs or run Docker."""
import unittest
from unittest.mock import patch

from backup_v2 import count_deltas, db_counts, safe_archive_member, warehouse_counts


class BackupSafetyTests(unittest.TestCase):
    def test_archive_paths(self):
        self.assertTrue(safe_archive_member("var/www/html/config.php"))
        for path in ("../secret", "/etc/passwd", "var/../../secret", "C:/secret", "\\secret"):
            self.assertFalse(safe_archive_member(path))

    def test_source_database_allowlist(self):
        with patch("backup_v2.run") as execute:
            with self.assertRaises(ValueError):
                db_counts("moodle_db", "not-a-secret", "another_database")
            execute.assert_not_called()

    def test_warehouse_database_allowlist(self):
        with patch("backup_v2.run") as execute:
            for name in ("master", "lms_datawarehouse_v2", "lms_datawarehouse;DROP DATABASE x"):
                with self.assertRaises(ValueError):
                    warehouse_counts("not-a-secret", name)
            execute.assert_not_called()

    def test_count_deltas_do_not_hide_operational_or_business_changes(self):
        before = {"user": 2, "task_log": 5}
        after = {"user": 3, "task_log": 6}
        delta = count_deltas(before, after)
        self.assertEqual(set(delta), {"user", "task_log"})
        self.assertEqual(delta["user"], {"before": 2, "after": 3})
        self.assertEqual(count_deltas(before, before), {})


if __name__ == "__main__":
    unittest.main()
