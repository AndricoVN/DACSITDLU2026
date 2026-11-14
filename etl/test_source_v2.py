"""Synthetic-only tests. No Docker/SQL execution or real CSV/secret reads."""
import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

try:
    from . import source_v2 as s
except ImportError:
    import source_v2 as s


class SourceTests(unittest.TestCase):
    def test_hex_json_preserves_quotes_unicode_and_null_strings(self):
        row = ["01", "O'Reilly \"quoted\"; DROP TABLE x;", "Tiếng Việt\n😀", "NULL", "\\N", ""]
        batch = next(s.insert_batches("course", [row]))
        self.assertNotIn("Tiếng".encode(), batch)
        payload = batch.split(b"0x", 1)[1].split(b" USING", 1)[0]
        self.assertEqual(json.loads(bytes.fromhex(payload.decode())), row)
        self.assertTrue(batch.startswith(b"INSERT INTO `v2_raw_course`"))
        self.assertIn(b"(1,CONVERT", batch)

    def test_bounded_batches_and_oversize(self):
        records = [[str(i), "x" * 30] for i in range(30)]
        batches = list(s.insert_batches("course", records, max_bytes=300))
        self.assertGreater(len(batches), 1)
        self.assertTrue(all(len(batch) <= 300 for batch in batches))
        self.assertEqual(sum(batch.count(b"CONVERT(") for batch in batches), 30)
        with self.assertRaises(s.LandingError):
            list(s.insert_batches("course", [["1", "x" * 300]], max_bytes=300))
        self.assertEqual(list(s.insert_batches("course", [])), [])

    def test_large_rows_fit_8mib_batches_and_client_packet(self):
        self.assertEqual(s.MAX_BATCH_BYTES, 8 * 1024 * 1024)
        # Hex encoding makes this synthetic row just over the old 1 MiB limit.
        row = ["1", "x" * (512 * 1024)]
        batches = list(s.insert_batches("course", [row] * 9))
        self.assertEqual(len(batches), 2)
        self.assertTrue(all(1024 * 1024 < len(batch) <= s.MAX_BATCH_BYTES
                            for batch in batches))
        self.assertEqual(sum(batch.count(b"CONVERT(") for batch in batches), 9)
        with self.assertRaises(s.LandingError):
            list(s.insert_batches("course", [["1", "x" * (4 * 1024 * 1024)]]))
        self.assertGreaterEqual(s.CLIENT_MAX_ALLOWED_PACKET, 16 * 1024 * 1024)
        self.assertIn(f"--max-allowed-packet={s.CLIENT_MAX_ALLOWED_PACKET}",
                      s.MariaDB("synthetic").args)

    def test_exact_user_skip_strict_and_hash_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "new").mkdir()
            path = root / "new/dlu_user.csv"
            valid = ["2"] + ["NULL", "", "Tiếng Việt"] * 4 + ["x", "y"]
            with path.open("w", encoding="utf-8", newline="") as handle:
                handle.write('1,"broken"suffix,x\n')
                csv.writer(handle).writerow(valid)
            before = s.source_hash(path)
            self.assertEqual(list(s.source_rows("user", root)), [valid])
            result = s.scan_source("user", root)
            self.assertEqual(result["column_width"], 15)
            self.assertEqual(result["row_count"], 1)
            self.assertEqual(result["sha256"], before)
            self.assertEqual(s.source_hash(path), before)
            (root / "dlu_course.csv").write_text('1,"bad"suffix\n', encoding="utf-8")
            with self.assertRaises(csv.Error):
                list(s.source_rows("course", root))
            path.write_text('1,x\n2,"bad"suffix\n', encoding="utf-8")
            with self.assertRaises(csv.Error):
                list(s.source_rows("user", root))

    def test_read_and_database_guards(self):
        for name in ("oldusers", "quiz", "../user", "cohort", "question"):
            with self.assertRaises(s.LandingError):
                s.source_path(name)
        with self.assertRaises(s.LandingError):
            s.MariaDB("secret", database="moodledb")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            path.write_text(json.dumps({"database": "moodledb", "builder_password": "private"}))
            with self.assertRaises(s.LandingError):
                s.read_credentials(path)
            path.write_text(json.dumps({"database": s.DATABASE, "builder_password": "private"}))
            self.assertEqual(s.read_credentials(path), "private")
        with patch.object(s, "read_credentials") as credentials:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                s.main([])
            credentials.assert_not_called()

    def test_mapping_indices(self):
        self.assertEqual(len(s.RAW_FIELDS["assign"]), 30)
        for table, field, index in (("assign", "teamsubmission", 18),
                                    ("course_modules", "section", 4),
                                    ("course_modules", "availability", 19),
                                    ("course_modules", "visible", 9),
                                    ("user", "UNKNOWNidx13", 13),
                                    ("assign_submission", "latest", 8),
                                    ("assign_grades", "attemptnumber", 7)):
            self.assertEqual(s.RAW_MAPPINGS[table][field], index)

    def test_course_modules_width_and_positions(self):
        self.assertEqual(len(s.RAW_FIELDS["course_modules"]), 21)
        self.assertEqual(s.RAW_MAPPINGS["course_modules"]["section"], 4)
        self.assertEqual(s.RAW_MAPPINGS["course_modules"]["visibleoncoursepage"], 10)
        self.assertEqual(s.RAW_MAPPINGS["course_modules"]["availability"], 19)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dlu_course_modules_cntt.csv"
            row = ["1"] + ["0"] * 20
            with path.open("w", encoding="utf-8", newline="") as handle:
                csv.writer(handle).writerow(row)
            metadata = s.scan_source("course_modules", directory)
            self.assertEqual(metadata["column_width"], 21)
            self.assertEqual(metadata["row_count"], 1)
            with path.open("w", encoding="utf-8", newline="") as handle:
                csv.writer(handle).writerow(row[:-1])
            with self.assertRaises(s.LandingError):
                s.scan_source("course_modules", directory)

    def test_invalid_id_width_and_mutation(self):
        for row in ([], ["-1"], ["1;bad"], [str(2 ** 63)]):
            with self.assertRaises(s.LandingError):
                s.encode_row(row)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dlu_course.csv"
            path.write_text("1,x\n2,y,z\n", encoding="utf-8")
            with self.assertRaises(s.LandingError):
                s.scan_source("course", directory)
            path.write_text("1,x\n", encoding="utf-8")
            with patch.object(s, "source_hash", side_effect=["a", "b"]):
                with self.assertRaises(s.LandingError):
                    s.scan_source("course", directory)

    def test_resume_and_partial_refusal_without_writes(self):
        metadata = {"sha256": "a" * 64, "row_count": 2, "column_width": 3,
                    "version": s.LOADER_VERSION}
        manifest = f"{'a' * 64}\t2\t3\t{s.LOADER_VERSION}\t1"
        for answers, expected in ((["1", manifest, "2"], "skipped"),
                                  (["1", ""], None),
                                  (["1", manifest, "1"], None),
                                  (["0", manifest], None)):
            db = Mock()
            db.query.side_effect = answers
            log = Mock()
            with patch.object(s, "scan_source", return_value=metadata):
                if expected:
                    self.assertEqual(s.load_table(db, "course", progress=log), expected)
                    log.assert_called_once_with("course 2")
                else:
                    with self.assertRaises(s.LandingError):
                        s.load_table(db, "course", progress=log)
                    log.assert_not_called()
            db.stream.assert_not_called()
            self.assertTrue(all(call.args[0].startswith("SELECT") for call in db.query.call_args_list))

    def test_load_transaction_manifest_and_postverification(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "dlu_course.csv").write_text("1,x\n2,NULL\n", encoding="utf-8")
            metadata = s.scan_source("course", directory)
            manifest = f"{metadata['sha256']}\t2\t2\t{s.LOADER_VERSION}\t1"
            db = Mock()
            db.query.side_effect = ["0", "", "", "2", manifest]
            captured = []
            db.stream.side_effect = lambda chunks, **kwargs: captured.extend(chunks)
            log = Mock()
            self.assertEqual(s.load_table(db, "course", directory, log), "loaded")
            self.assertEqual(captured[0], b"START TRANSACTION;\n")
            self.assertTrue(captured[-1].endswith(b"COMMIT;\n"))
            self.assertIn(b"SELECT COUNT(*) FROM `v2_raw_course`", captured[-1])
            self.assertIn(b"v2_raw_manifest", captured[-1])
            log.assert_called_once_with("course 2")

    def test_stderr_or_exit_failure_never_succeeds_or_leaks(self):
        for code, stderr in ((0, b"private CSV secret"), (1, b"")):
            def fake_popen(args, **kwargs):
                kwargs["stderr"].write(stderr)
                process = Mock()
                process.stdin = io.BytesIO()
                process.wait.return_value = code
                return process
            with patch.object(s.subprocess, "Popen", side_effect=fake_popen):
                with self.assertRaises(s.LandingError) as caught:
                    s.MariaDB("private").query("SELECT 1")
                self.assertNotIn("private", str(caught.exception))
        db = s.MariaDB("private")
        self.assertEqual(db.args[:7], ["docker", "exec", "-i", "-e", "MYSQL_PWD=private", "moodle_db", "mariadb"])
        with self.assertRaises(s.LandingError):
            db.stream([], timeout=0)


if __name__ == "__main__":
    unittest.main()
