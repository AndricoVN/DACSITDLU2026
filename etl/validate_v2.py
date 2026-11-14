"""Validate milestone 2, then open ONLY the loopback Moodle v2 endpoint.

HTTP checks use real sessions but do not log source usernames, passwords or HTML.
"""
import argparse
import http.cookiejar
import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from audit_source_v2 import ROOT
from backup_v2 import count_deltas, db_counts, env_of, inspect, run, sqlserver, warehouse_counts
from source_v2 import MariaDB, read_credentials, source_hash, source_path


class HiddenInputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("type") == "hidden" and attrs.get("name"):
            self.values[attrs["name"]] = attrs.get("value", "")


def web_login(username, password, admin=False):
    jar = http.cookiejar.CookieJar()
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    root = "http://localhost:8081"
    with client.open(root + "/login/index.php", timeout=45) as response:
        page = response.read().decode("utf-8")
    parser = HiddenInputs()
    parser.feed(page)
    if not parser.values.get("logintoken"):
        raise RuntimeError("V2 login token missing")
    data = {**parser.values, "username": username, "password": password}
    with client.open(root + "/login/index.php", data=urllib.parse.urlencode(data).encode(), timeout=60) as response:
        result = response.read().decode("utf-8")
        if "/login/" in response.url or response.status != 200:
            raise RuntimeError("V2 HTTP authentication failed; page suppressed")
    if not any(cookie.name == "MoodleSessionV2" for cookie in jar):
        raise RuntimeError("V2 session cookie not isolated")
    endpoint = "/course/management.php" if admin else "/my/"
    with client.open(root + endpoint, timeout=60) as response:
        result = response.read().decode("utf-8")
        if response.status != 200 or "/login/" in response.url:
            raise RuntimeError("V2 authenticated page failed")
    for marker in ("Fatal error", "Coding error detected", "Exception -", 'class="errorcode"'):
        if marker in result:
            raise RuntimeError("V2 page contains an application error; HTML suppressed")
    return {"login": True, "session_cookie_isolated": True, "authenticated_page": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", required=True)
    args = parser.parse_args()
    state_path = ROOT / "local-v2/runtime-secrets.json"
    state = json.loads(state_path.read_text())
    if state.get("database") != "moodledb_v2":
        raise ValueError("Wrong target")
    report = {"milestone": 2, "complete": False, "ui_browser_tested": False}
    db = MariaDB(read_credentials())
    manifest = db.query("SELECT table_name,source_sha256,row_count FROM v2_raw_manifest WHERE complete=1 ORDER BY table_name")
    raw = {}
    for line in manifest.splitlines():
        table, checksum, count = line.split("\t")
        name = table.removeprefix("v2_raw_")
        if source_hash(source_path(name)) != checksum:
            raise RuntimeError("CSV changed since raw landing")
        if db.query("SELECT COUNT(*) FROM `" + table + "`") != count:
            raise RuntimeError("Raw row count mismatch")
        raw[name] = int(count)
    if len(raw) != 17:
        raise RuntimeError("Incomplete raw manifest")
    report["raw_counts"] = raw
    # Existing builder must have no permissions on the old source.
    denied = False
    try:
        db.query("SELECT COUNT(*) FROM moodledb.user")
    except RuntimeError:
        denied = True
    if not denied:
        raise RuntimeError("V2 builder can read old database")
    report["builder_isolated"] = True
    sql_secret = env_of(inspect("datawarehouse_sqlserver"))["MSSQL_SA_PASSWORD"]
    count = sqlserver(sql_secret, "SELECT COUNT(*) FROM lms_datawarehouse_v2.sys.tables;").strip()
    rows = sqlserver(sql_secret, "SELECT COALESCE(SUM(p.rows),0) FROM lms_datawarehouse_v2.sys.tables t JOIN lms_datawarehouse_v2.sys.partitions p ON p.object_id=t.object_id WHERE p.index_id IN (0,1);").strip()
    if not count.isdigit() or int(count) == 0 or rows != "0":
        raise RuntimeError("DWH v2 must have schema and no data")
    report["warehouse"] = {"tables": int(count), "rows": 0}
    # Prove a second schema application fails without touching existing v2 data.
    attempt = subprocess.run(["docker", "exec", "-i", "-e", "SQLCMDPASSWORD=" + sql_secret,
                              "datawarehouse_sqlserver", "/opt/mssql-tools18/bin/sqlcmd",
                              "-S", "localhost", "-U", "sa", "-C", "-b"],
                             input=(ROOT / "dwh/schema_v2.sql").read_bytes(), capture_output=True, timeout=30)
    if attempt.returncode == 0:
        raise RuntimeError("DWH schema guard did not fail on existing target")
    report["warehouse_existing_target_guard"] = True
    run(["docker", "cp", str(ROOT / "etl/validate_moodle_v2.php"), "moodle_app_v2:/opt/dacs-v2/validate.php"])
    run(["docker", "exec", "moodle_app_v2", "chown", "-R", "www-data:www-data", "/var/moodledata"])
    process = subprocess.run(["docker", "exec", "-i", "-u", "www-data", "moodle_app_v2", "php", "/opt/dacs-v2/validate.php"],
                             input=json.dumps({"admin_username": state["admin_username"], "admin_password": state["admin_password"]}).encode(),
                             capture_output=True, timeout=180)
    if process.returncode:
        # Diagnostic stays private; it may include source values.
        (ROOT / "local-v2/validation-errors.log").write_bytes(process.stdout + process.stderr)
        raise RuntimeError("Native Moodle v2 validation failed; details kept private")
    native = json.loads(process.stdout)
    if not all(native["checks"].values()):
        raise RuntimeError("Native Moodle checks incomplete")
    report["native_checks"] = native["checks"]
    info = inspect("moodle_app_v2")
    ports = info["NetworkSettings"]["Ports"]["80/tcp"]
    if not ports or any(p["HostIp"] != "127.0.0.1" or p["HostPort"] != "8081" for p in ports):
        raise RuntimeError("V2 web must be bound to loopback:8081 only")
    if any(m.get("Name") == "dacs_moodle_data" for m in info["Mounts"]):
        raise RuntimeError("Old code/data volume shared")
    report["loopback_only"] = True
    report["volumes_isolated"] = True
    run(["docker", "exec", "moodle_app_v2", "touch", "/var/moodledata/.v2-ready"])
    for i in range(30):
        try:
            with urllib.request.urlopen("http://localhost:8081/login/index.php", timeout=10) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(1)
    else:
        raise RuntimeError("V2 web did not become ready")
    report["admin_http"] = web_login(state["admin_username"], state["admin_password"], admin=True)
    report["student_http"] = web_login(native["student_username"], native["student_username"])
    backup = ROOT / "backups" / state["backup"] / "manifest.json"
    old = json.loads(backup.read_text())
    root_secret = env_of(inspect("moodle_db"))["MYSQL_ROOT_PASSWORD"]
    differences = count_deltas(old["source_mariadb_counts"], db_counts("moodle_db", root_secret, "moodledb"))
    if set(differences) - {"task_log"}:
        raise RuntimeError("Original business counts changed; investigate before milestone acceptance")
    if warehouse_counts(sql_secret, "lms_datawarehouse") != old["source_warehouse_counts"]:
        raise RuntimeError("Original DWH counts changed")
    report["old_business_counts_unchanged"] = True
    report["old_operational_count_deltas"] = differences
    report["scope"] = "Source raw + real users/enrollments + hidden course shells. No reconstruction of activities, files or full gradebook."
    report["complete"] = True
    (ROOT / "local-v2/milestone2-report.json").write_text(json.dumps(report, indent=2) + "\n")
    state["phase"] = "validated"
    state_path.write_text(json.dumps(state, indent=2) + "\n")
    print("PASS: milestone 2 native/HTTP/isolation/source/DWH checks complete.")
    print("Raw tables:", len(raw), "; DWH tables:", count, "; DWH rows: 0")
    print("Moodle v2: http://localhost:8081 (loopback only); old application not switched.")


if __name__ == "__main__":
    main()
