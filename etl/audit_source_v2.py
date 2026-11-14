"""Read-only CSV inventory; optional SELECT-only Moodle schema probe.

No import, ETL, Moodle bootstrap, DDL, backup or application configuration change.
Reports contain aggregates/schema, never raw records, credentials or personal IDs.
Positional mappings are hypotheses until checked against source schema metadata.
"""
import argparse
import csv
import hashlib
import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
csv.field_size_limit(100_000_000)
USER_FIELDS = (
    "id", "auth", "deleted", "suspended", "mnethostid", "username",
    "idnumber", "email", "firstname", "lastname", "firstaccess", "lastaccess",
    "lastlogin", "institution_or_department_UNCONFIRMED", "timecreated",
)


def student_code(username, email):
    """Project convention, not a Moodle role check; reject ambiguous matches."""
    for text in (username, email.partition("@")[0]):
        matches = re.findall(r"(?<![0-9])[0-9]{7}(?![0-9])", text)
        if matches:
            unique = set(matches)
            return next(iter(unique)) if len(unique) == 1 else None
    return None


def rows(path, skip_first=False):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        if skip_first:
            # Consume exactly one logical CSV record with the permissive reader.
            # The agreed malformed first user record is never repaired or printed.
            next(csv.reader(handle), None)
        yield from csv.reader(handle, strict=True)


def profile(path, skip_first=False):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    widths, ids = Counter(), set()
    count = duplicate_ids = nonnumeric_ids = 0
    failure = None
    try:
        for row in rows(path, skip_first):
            count += 1
            widths[len(row)] += 1
            if row:
                duplicate_ids += row[0] in ids
                nonnumeric_ids += not row[0].isascii() or not row[0].isdigit()
                ids.add(row[0])
    except (csv.Error, UnicodeError):
        failure = "Invalid CSV/encoding; source values suppressed"
    return {
        "bytes": path.stat().st_size, "sha256": digest.hexdigest(),
        "parsed_rows": count, "column_counts": dict(widths),
        "duplicate_first_column": duplicate_ids,
        "skipped_first_record": skip_first, "parse_error": failure,
        "nonnumeric_first_column_rows": nonnumeric_ids,
        "header_status": "No named header inferred when all first-column values are numeric; source DDL not available",
    }


def indexed(path, skip_first=False):
    result = {}
    for row in rows(path, skip_first):
        if not row or row[0] in result:
            raise ValueError("Empty/duplicate ID; values suppressed")
        result[row[0]] = row
    return result


def relation(child, column, parent):
    return {"rows": len(child), "missing_parent_rows": sum(
        r[column] not in parent for r in child.values()
    )}


def analyze(source):
    load = lambda name: indexed(source / (name + ".csv"))
    users = indexed(source / "new/dlu_user.csv", skip_first=True)
    old_users = load("dlu_user")
    courses = load("dlu_course")
    scoped = load("dlu_course_cntt")
    enrol = load("dlu_enrol")
    ue = load("dlu_user_enrolments")
    assign = load("dlu_assign")
    items = load("dlu_grade_items")
    modules = load("dlu_modules")
    cm = load("dlu_course_modules_cntt")
    sections = load("dlu_course_sections")
    categories = load("dlu_course_categories_cntt")
    contexts = load("dlu_context")
    roles = load("dlu_role")
    role_assignments = load("dlu_role_assignments")
    submissions = load("dlu_assign_submission")
    assignment_grades = load("dlu_assign_grades")

    expected_widths = {
        "new/dlu_user": (users, 15),
                "dlu_assign_submission": (submissions, 9),
                "dlu_assign_grades": (assignment_grades, 8),
                "dlu_course_modules_cntt": (cm, 21),
                "dlu_grade_items": (items, 31),
                        "dlu_user_enrolments": (ue, 9),
    }
    for _, (data, expected) in expected_widths.items():
        if expected and any(len(r) != expected for r in data.values()):
            raise ValueError("Unconfirmed row layout; values suppressed")

    # Standard Moodle positional layouts; the report explicitly flags these.
    pairs = Counter((r[3], enrol[r[2]][3]) for r in ue.values() if r[2] in enrol)
    eligible_users = {i for i, r in users.items() if student_code(r[5], r[7])}
    active_flags = {i for i in eligible_users if users[i][2:4] == ["0", "0"]}
    totals = {r[1]: r[0] for r in items.values() if r[4] == "course"}
    total_item_ids = set(totals.values())
    grade_stats = Counter()
    graded_pairs = set()
    for r in rows(source / "dlu_grade_grades.csv"):
        grade_stats["rows"] += 1
        grade_stats["missing_item_rows"] += r[1] not in items
        grade_stats["missing_new_user_rows"] += r[2] not in users
        if r[1] in items:
            grade_stats["matched_item_rows"] += 1
        if r[1] in total_item_ids:
            grade_stats["course_total_rows"] += 1
            if r[8] not in ("", "NULL", "\\N"):
                grade_stats["course_total_nonnull_rows"] += 1
                graded_pairs.add((r[2], items[r[1]][1]))
    latest = [r for r in submissions.values() if r[8] == "1"]
    latest_counts = Counter((r[2], r[1]) for r in latest if r[2] != "0")
    grade_counts = Counter((r[2], r[1], r[7]) for r in assignment_grades.values())
    # Width/order may differ from live schema; no target insert is generated.
    cm_assign_type = {i for i, r in modules.items() if r[1] == "assign"}
    assign_modules = [r for r in cm.values() if r[2] in cm_assign_type]
    availability_types = Counter()
    invalid_availability = 0
    for r in cm.values():
        # The 21-column source layout has availability at 19; live has 26 columns.
        if len(r) > 19 and r[19] not in ("", "NULL", "\\N"):
            try:
                def visit(value):
                    if isinstance(value, dict):
                        if "type" in value:
                            availability_types[str(value["type"])] += 1
                        for v in value.values():
                            visit(v)
                    elif isinstance(value, list):
                        for v in value:
                            visit(v)
                visit(json.loads(r[19]))
            except (ValueError, TypeError):
                invalid_availability += 1
    cat_ids = set(categories)
    result = {
        "mapping_status": "Positional hypotheses, NOT an import contract; verify source headers/DDL before import",
        "user_proposed_columns": list(USER_FIELDS),
        "users": {
            "old_count": len(old_users), "new_after_skipping_first": len(users),
            "old_ids_missing_in_new": len(set(old_users) - set(users)),
            "eligible_by_seven_digit_convention": len(eligible_users),
            "eligible_with_proposed_active_flags": len(active_flags),
            "username_has_more_than_seven_digit_run": sum(bool(re.search(r"[0-9]{8,}", r[5])) for r in users.values()),
            "idnumber_nonempty": sum(bool(r[6]) for r in users.values()),
            "proposed_flag_distributions": {str(c): dict(Counter(r[c] for r in users.values())) for c in (2, 3, 4)},
        },
        "enrollment": {
            "raw_rows": len(ue), "distinct_user_course_pairs": len(pairs),
            "duplicate_pair_extra_rows": sum(n - 1 for n in pairs.values()),
            "pairs_without_exported_course_total_item": sum(c not in totals for u, c in pairs),
            "pairs_without_nonnull_total_grade": len(set(pairs) - graded_pairs),
            "eligible_users_with_enrollment": len({u for u, c in pairs if u in eligible_users}),
        },
        "coverage": {
            "courses_all": len(courses), "courses_scoped": len(scoped),
            "grade_item_courses": len({r[1] for r in items.values()}),
            "course_total_item_courses": len(totals),
            "activity_metadata_courses": len({r[1] for r in cm.values()}),
            "section_courses": len({r[1] for r in sections.values()}),
            "course_category_missing_from_export": sum(r[1] not in cat_ids for r in courses.values()),
            "grade_item_types": dict(Counter((r[4] if r[5] in ("", "NULL", "\\N") else r[5]) for r in items.values())),
            "assignment_courses": len({r[1] for r in assign.values()}),
        },
        "grade_grades": dict(grade_stats),
        "submissions": {
            "rows": len(submissions), "latest_rows": len(latest),
            "userid_zero_rows": sum(r[2] == "0" for r in submissions.values()),
            "duplicate_latest_person_assignment_pairs": sum(n > 1 for n in latest_counts.values()),
            "duplicate_person_assignment_grade_attempt_keys": sum(n > 1 for n in grade_counts.values()),
            "negative_assignment_grades": sum(float(r[6]) < 0 for r in assignment_grades.values() if r[6] not in ("", "NULL", "\\N")),
        },
        "availability": {
            "activity_condition_types": dict(availability_types),
            "unparseable_activity_conditions": invalid_availability,
            "assignment_module_rows": len(assign_modules),
        },
        "relations": {
            "enrol_to_course": relation(enrol, 3, courses),
            "user_enrolments_to_enrol": relation(ue, 2, enrol),
            "user_enrolments_to_new_user": relation(ue, 3, users),
            "assign_to_course": relation(assign, 1, courses),
            "submission_to_assign": relation(submissions, 1, assign),
            "submission_to_new_user_including_zero": relation(submissions, 2, users),
            "assign_grade_to_assign": relation(assignment_grades, 1, assign),
            "assign_grade_to_new_user": relation(assignment_grades, 2, users),
            "grade_item_to_course": relation(items, 1, courses),
            "course_module_to_course": relation(cm, 1, courses),
            "course_module_to_section": relation(cm, 4, sections),
            "course_module_to_module_type": relation(cm, 2, modules),
            "role_assignment_to_role": relation(role_assignments, 1, roles),
            "role_assignment_to_context": relation(role_assignments, 2, contexts),
            "role_assignment_to_new_user": relation(role_assignments, 3, users),
        },
    }
    return result


# Read config text, not include/require: no Moodle bootstrap/cache/session writes.
PHP_PROBE = r'''<?php
function cfgval($text, $name) {
    $pattern = '/\$CFG->' . preg_quote($name, '/') . '\s*=\s*([\'\"])(.*?)\1\s*;/s';
    return preg_match($pattern, $text, $m) ? $m[2] : null;
}
try {
    $text = file_get_contents('/var/www/html/config.php');
    $db = cfgval($text, 'dbname'); $prefix = cfgval($text, 'prefix');
    $host = cfgval($text, 'dbhost');
    if (!$db || !$host || $prefix === null) { throw new Exception('Unsupported config'); }
    $pdo = new PDO('mysql:host='.$host.';dbname='.$db.';charset=utf8mb4',
        cfgval($text, 'dbuser'), cfgval($text, 'dbpass'),
        [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
    $pdo->exec('START TRANSACTION READ ONLY');
    $out = ['database'=>$db, 'prefix'=>$prefix, 'bootstrap_used'=>false,
        'transaction'=>'READ ONLY', 'schema'=>[], 'counts'=>[]];
    $out['server'] = $pdo->query('SELECT VERSION() AS version, @@session.time_zone AS session_timezone, @@system_time_zone AS system_timezone')->fetch(PDO::FETCH_ASSOC);
    $out['existing_v2_database'] = (bool)$pdo->query("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME='moodledb_v2'")->fetchColumn();
    $stmt = $pdo->prepare('SELECT name,value FROM `'.$prefix.'config` WHERE name IN (\'version\',\'release\',\'timezone\',\'siteadmins\')');
    $stmt->execute();
    foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $r) {
        if ($r['name'] !== 'siteadmins') { $out['moodle'][$r['name']] = $r['value']; }
    }
    $stmt = $pdo->prepare('SELECT plugin,name,value FROM `'.$prefix.'config_plugins` WHERE name=\'version\' ORDER BY plugin');
    $stmt->execute(); $out['plugin_versions'] = $stmt->fetchAll(PDO::FETCH_ASSOC);
    $tables = ['user','course','course_categories','course_modules','course_sections','context','role','role_assignments','enrol','user_enrolments','assign','assign_submission','assign_grades','grade_items','grade_grades','modules','files','groups','groups_members','config'];
    $stmt = $pdo->prepare('SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=? AND TABLE_NAME=? ORDER BY ORDINAL_POSITION');
    foreach ($tables as $table) {
        $stmt->execute([$db, $prefix.$table]); $out['schema'][$table] = $stmt->fetchAll(PDO::FETCH_ASSOC);
        $out['counts'][$table] = (int)$pdo->query('SELECT COUNT(*) FROM `'.$prefix.$table.'`')->fetchColumn();
    }
    $out['code_versions'] = [];
    foreach (['/var/www/html/public/version.php', '/var/www/html/version.php'] as $file) {
        if (is_file($file)) {
            $versiontext = file_get_contents($file);
            if (preg_match('/\\$version\\s*=\\s*([0-9.]+)/', $versiontext, $match)) {
                $out['code_versions'][] = ['file'=>$file, 'version'=>$match[1]];
            }
        }
    }
    $out['table_count'] = (int)$pdo->query('SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=DATABASE()')->fetchColumn();
    $out['attendance_tables'] = $pdo->query("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME LIKE '%attendance%'")->fetchAll(PDO::FETCH_COLUMN);
    $pdo->rollBack();
    echo json_encode($out, JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT|JSON_THROW_ON_ERROR);
} catch (Throwable $e) { fwrite(STDERR, "Read-only probe failed; details suppressed to protect credentials.\n"); exit(1); }
'''


def probe_moodle(container):
    result = subprocess.run(
        ["docker", "exec", "-i", container, "php"], input=PHP_PROBE,
        text=True, encoding="utf-8", capture_output=True, timeout=120,
    )
    if result.returncode:
        raise RuntimeError("Read-only Moodle probe failed; raw output suppressed")
    return json.loads(result.stdout)


def probe_mssql(container):
    # Existing container secret stays in memory and is not printed or persisted.
    inspected = subprocess.run(
        ["docker", "inspect", container], capture_output=True, text=True,
        encoding="utf-8", check=True, timeout=20,
    )
    env = dict(v.split("=", 1) for v in json.loads(inspected.stdout)[0]["Config"]["Env"] if "=" in v)
    secret = env.get("MSSQL_SA_PASSWORD") or env.get("SA_PASSWORD")
    if not secret:
        raise RuntimeError("SQL Server container secret unavailable; no connection attempted")
    query = """SET NOCOUNT ON;
SELECT CAST(SERVERPROPERTY('ProductVersion') AS varchar(100)) AS server_version,
       SYSDATETIMEOFFSET() AS server_time;
SELECT name, state_desc FROM sys.databases
WHERE name IN ('lms_datawarehouse','lms_datawarehouse_v2');
SELECT t.name AS table_name, SUM(p.rows) AS row_count
FROM lms_datawarehouse.sys.tables t
JOIN lms_datawarehouse.sys.partitions p ON p.object_id=t.object_id
WHERE p.index_id IN (0,1) GROUP BY t.name ORDER BY t.name;
SELECT t.name AS table_name, c.name AS column_name, ty.name AS type_name,
       c.precision, c.scale, c.is_nullable
FROM lms_datawarehouse.sys.columns c
JOIN lms_datawarehouse.sys.tables t ON c.object_id=t.object_id
JOIN lms_datawarehouse.sys.types ty ON c.user_type_id=ty.user_type_id
ORDER BY t.name,c.column_id;
"""
    process = subprocess.run(
        ["docker", "exec", "-e", "SQLCMDPASSWORD=" + secret, container,
         "/opt/mssql-tools18/bin/sqlcmd", "-S", "localhost", "-U", "sa", "-C",
         "-b", "-W", "-s", "|", "-Q", query],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    if process.returncode:
        raise RuntimeError("SELECT-only SQL Server probe failed; raw output suppressed")
    return {"queries": "SELECT metadata/counts only", "output_lines": process.stdout.splitlines()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "Data_11-2026")
    parser.add_argument("--output", type=Path, default=ROOT / "local-v2/audit.json")
    parser.add_argument("--moodle-container", help="Optional Docker container for SELECT-only schema probe")
    parser.add_argument("--mssql-container", help="Optional Docker SQL Server container for SELECT-only inventory")
    parser.add_argument("--probe-only", action="store_true")
    args = parser.parse_args()
    report = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "database_mutations": False}
    if not args.probe_only:
        report["files"] = {}
        for path in sorted(args.source.rglob("*.csv")):
            name = path.relative_to(args.source).as_posix()
            report["files"][name] = profile(path, skip_first=name == "new/dlu_user.csv")
        if any(p["parse_error"] for p in report["files"].values()):
            raise ValueError("CSV validation failed; no database action performed")
        report["analysis"] = analyze(args.source)
    if args.moodle_container:
        report["moodle"] = probe_moodle(args.moodle_container)
    if args.mssql_container:
        report["mssql"] = probe_mssql(args.mssql_container)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Aggregate report written; no raw personal data or database changes.")
    if "files" in report:
        print("CSV files:", len(report["files"]))
        print(json.dumps(report["analysis"], ensure_ascii=True, indent=2))
    if "mssql" in report:
        print("SQL Server: SELECT-only inventory completed; schema/counts saved locally.")
    if "moodle" in report:
        m = report["moodle"]
        print("Moodle:", json.dumps(m.get("moodle", {}), ensure_ascii=True))
        print("Database:", m["database"], "tables:", m["table_count"], "v2 exists:", m["existing_v2_database"])
        print("Live counts:", json.dumps(m["counts"]))


if __name__ == "__main__":
    main()
