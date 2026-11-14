"""Isolated raw landing only; never writes Moodle core tables.

Mappings are source-position specs, not permission to import into core. Source
role IDs/capabilities, enrol methods/status and context IDs/path/depth are raw
only: later milestones must verify and map them, never assume target semantics.
Old users must not be reused. CSV strings (including NULL and empty) stay strings.
"""
import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

try:
    from .audit_source_v2 import rows
except ImportError:
    from audit_source_v2 import rows

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "Data_11-2026"
SECRETS_PATH = ROOT / "local-v2" / "runtime-secrets.json"
DATABASE = "moodledb_v2"
MANIFEST_TABLE = "v2_raw_manifest"
LOADER_VERSION = "raw-array-v1"
MAX_BATCH_BYTES = 8 * 1024 * 1024
CLIENT_MAX_ALLOWED_PACKET = 16 * 1024 * 1024
TABLE_TIMEOUT = 15 * 60
COMMAND_TIMEOUT = 60
# Public keys are raw aliases; no cohort, quiz, question or old-user sources.
SOURCES = {
    "user": "new/dlu_user.csv", "course": "dlu_course.csv",
    "course_categories": "dlu_course_categories_cntt.csv",
    "course_scope": "dlu_course_cntt.csv",
    "course_modules": "dlu_course_modules_cntt.csv",
    **{name: "dlu_" + name + ".csv" for name in (
        "course_sections", "enrol", "user_enrolments", "assign",
        "assign_submission", "assign_grades", "grade_items", "grade_grades",
        "modules", "role", "role_assignments", "context")},
}
RAW_FIELDS = {
    "user": ("id auth deleted suspended mnethostid username idnumber email "
             "firstname lastname firstaccess lastaccess lastlogin UNKNOWNidx13 timecreated").split(),
    "assign_submission": "id assignment userid timecreated timemodified status groupid attemptnumber latest".split(),
    "assign_grades": "id assignment userid timecreated timemodified grader grade attemptnumber".split(),

    "course_modules": ("id course module instance section idnumber added score indent "
                       "visible visibleoncoursepage visibleold groupmode groupingid completion completiongradeitemnumber "
                       "completionview completionexpected showdescription availability deletioninprogress").split(),
}
# Moodle 3.9-shaped candidate spec; source DDL/version remains unconfirmed.
RAW_FIELDS["assign"] = ("id course name intro introformat alwaysshowdescription nosubmissions "
    "submissiondrafts sendnotifications sendlatenotifications duedate allowsubmissionsfromdate "
    "grade timemodified requiresubmissionstatement completionsubmit cutoffdate gradingduedate "
    "teamsubmission requireallteammemberssubmit teamsubmissiongroupingid blindmarking "
    "hidegrader revealidentities attemptreopenmethod maxattempts markingworkflow markingallocation "
    "sendstudentnotifications preventsubmissionnotingroup").split()
RAW_MAPPINGS = {name: {field: index for index, field in enumerate(fields)}
                for name, fields in RAW_FIELDS.items()}


class LandingError(RuntimeError):
    """Safe message only: never include subprocess output or CSV values."""


def raw_table(name):
    if name not in SOURCES:
        raise LandingError("Unknown selected table")
    return "v2_raw_" + name


def source_path(name, source_dir=SOURCE_DIR):
    raw_table(name)
    return Path(source_dir) / SOURCES[name]


def source_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(MAX_BATCH_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_rows(name, source_dir=SOURCE_DIR):
    """Only new/user skips exactly the first logical record via audit reader."""
    yield from rows(source_path(name, source_dir), skip_first=name == "user")


def scan_source(name, source_dir=SOURCE_DIR):
    path = source_path(name, source_dir)
    checksum = source_hash(path)
    count, width = 0, None
    for row in source_rows(name, source_dir):
        # Validate BIGINT and single-row batch capacity before creating a table.
        list(insert_batches(name, [row]))
        if width is None:
            width = len(row)
        if len(row) != width:
            raise LandingError("Inconsistent source column width")
        count += 1
    if name in RAW_FIELDS and width is not None and width != len(RAW_FIELDS[name]):
        raise LandingError("Source mapping width mismatch")
    if source_hash(path) != checksum:
        raise LandingError("Source changed during iteration")
    return {"sha256": checksum, "row_count": count, "column_width": width or 0,
            "version": LOADER_VERSION}


def encode_row(row):
    if not row or not re.fullmatch(r"[0-9]+", row[0]) or int(row[0]) > 9223372036854775807:
        raise LandingError("Invalid source BIGINT ID")
    payload = json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return (str(int(row[0])) + ",CONVERT(0x" + payload.hex() + " USING utf8mb4)").encode("ascii")


def insert_batches(name, records, max_bytes=MAX_BATCH_BYTES):
    prefix = ("INSERT INTO `" + raw_table(name) + "` (source_id,row_json) VALUES ").encode()
    values = []
    batch_size = len(prefix) + 2  # Include the final semicolon and newline.
    for row in records:
        value = b"(" + encode_row(row) + b")"
        if len(prefix) + len(value) + 2 > max_bytes:
            raise LandingError("Raw row exceeds batch limit")
        separator_size = 1 if values else 0
        if batch_size + separator_size + len(value) > max_bytes:
            yield b"".join((prefix, b",".join(values), b";\n"))
            values = []
            batch_size = len(prefix) + 2
            separator_size = 0
        values.append(value)
        batch_size += separator_size + len(value)
    if values:
        yield b"".join((prefix, b",".join(values), b";\n"))


def read_credentials(path=SECRETS_PATH):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("database") != DATABASE:
            raise LandingError("Refusing non-v2 database")
        password = data["builder_password"]
        if not isinstance(password, str) or not password or "\x00" in password:
            raise LandingError("Invalid builder credential")
        return password
    except LandingError:
        raise
    except Exception:
        raise LandingError("Cannot read builder credentials") from None


class MariaDB:
    def __init__(self, password, database=DATABASE):
        if database != DATABASE:
            raise LandingError("Refusing non-v2 database")
        self.args = ["docker", "exec", "-i", "-e", "MYSQL_PWD=" + password,
                     "moodle_db", "mariadb", "-u", "dacs_v2_builder", database,
                     "--default-character-set=utf8mb4",
                     f"--max-allowed-packet={CLIENT_MAX_ALLOWED_PACKET}",
                     "--batch", "--skip-column-names", "--raw"]

    def stream(self, chunks, timeout=COMMAND_TIMEOUT):
        # Files avoid pipe deadlocks and keep all diagnostics out of stdout.
        if timeout <= 0:
            raise LandingError("Table deadline exceeded")
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            process = subprocess.Popen(self.args, stdin=subprocess.PIPE, stdout=out, stderr=err)
            timer = threading.Timer(timeout, process.kill)
            timer.start()
            try:
                for chunk in chunks:
                    process.stdin.write(chunk)
                process.stdin.close()
                code = process.wait(timeout=timeout)
                err.seek(0)
                if code or err.read(1):
                    raise LandingError("Database operation failed; output suppressed")
                out.seek(0)
                return out.read().decode("utf-8").strip()
            except Exception:
                process.kill()
                process.wait(timeout=COMMAND_TIMEOUT)
                raise LandingError("Database operation failed; output suppressed") from None
            finally:
                timer.cancel()
                process.stdin.close()

    def query(self, sql, timeout=COMMAND_TIMEOUT):
        return self.stream([(sql + ";\n").encode("utf-8")], timeout)


MANIFEST_DDL = f"""CREATE TABLE IF NOT EXISTS `{MANIFEST_TABLE}` (
 table_name VARCHAR(64) PRIMARY KEY, source_sha256 CHAR(64) NOT NULL,
 row_count BIGINT NOT NULL, expected_count BIGINT NOT NULL,
 column_width INT NOT NULL, loader_version VARCHAR(64) NOT NULL,
 complete TINYINT NOT NULL CHECK (complete=1),
 CHECK (row_count=expected_count)
) ENGINE=InnoDB"""


def manifest_values(name, metadata):
    # All literals are internal identifiers/hash/count/version, never CSV values.
    return (f"'{raw_table(name)}','{metadata['sha256']}',"
            f"(SELECT COUNT(*) FROM `{raw_table(name)}`),{metadata['row_count']},"
            f"{metadata['column_width']},'{LOADER_VERSION}',1")


def load_table(db, name, source_dir=SOURCE_DIR, progress=print):
    deadline = time.monotonic() + TABLE_TIMEOUT

    def query(sql):
        return db.query(sql, timeout=min(COMMAND_TIMEOUT, deadline - time.monotonic()))

    metadata = scan_source(name, source_dir)
    table = raw_table(name)
    exists = query(f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{DATABASE}' AND table_name='{table}'")
    manifest = query(f"SELECT source_sha256,row_count,column_width,loader_version,complete FROM `{MANIFEST_TABLE}` WHERE table_name='{table}'")
    expected = "\t".join(map(str, (metadata['sha256'], metadata['row_count'],
                                    metadata['column_width'], LOADER_VERSION, 1)))
    if exists == "1":
        if manifest == expected and query(f"SELECT COUNT(*) FROM `{table}`") == str(metadata['row_count']):
            progress(f"{name} {metadata['row_count']}")
            return "skipped"
        raise LandingError("Existing raw table is partial or mismatched; refusing changes")
    if exists != "0" or manifest:
        raise LandingError("Inconsistent raw manifest; refusing changes")
    query(f"CREATE TABLE `{table}` (source_id BIGINT PRIMARY KEY, row_json LONGTEXT NOT NULL CHECK (JSON_VALID(row_json) AND JSON_TYPE(row_json)='ARRAY')) ENGINE=InnoDB")

    def statements():
        yield b"START TRANSACTION;\n"
        yield from insert_batches(name, source_rows(name, source_dir))
        if source_hash(source_path(name, source_dir)) != metadata['sha256']:
            raise LandingError("Source changed during iteration")
        # CHECK count=expected_count aborts before COMMIT on a count mismatch.
        yield (f"INSERT INTO `{MANIFEST_TABLE}` VALUES ({manifest_values(name, metadata)});\nCOMMIT;\n").encode()

    db.stream(statements(), timeout=deadline - time.monotonic())
    if source_hash(source_path(name, source_dir)) != metadata['sha256']:
        raise LandingError("Source changed during loading; table retained for inspection")
    if query(f"SELECT COUNT(*) FROM `{table}`") != str(metadata['row_count']):
        raise LandingError("Raw count verification failed")
    if query(f"SELECT source_sha256,row_count,column_width,loader_version,complete FROM `{MANIFEST_TABLE}` WHERE table_name='{table}'") != expected:
        raise LandingError("Manifest verification failed")
    progress(f"{name} {metadata['row_count']}")
    return "loaded"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", required=True)
    parser.add_argument("--tables", nargs="+", choices=tuple(SOURCES), default=list(SOURCES))
    args = parser.parse_args(argv)
    try:
        db = MariaDB(read_credentials())
        if db.query("SELECT DATABASE()") != DATABASE:
            raise LandingError("Refusing non-v2 database")
        db.query(MANIFEST_DDL)
        for name in dict.fromkeys(args.tables):
            load_table(db, name)
    except Exception:
        # No traceback: CSV, OS and subprocess exceptions can carry PII/secrets.
        parser.exit(1, "Raw landing failed; sensitive details suppressed; existing tables retained\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
