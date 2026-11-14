"""Back up the OLD environment; verify restores in isolated disposable targets.

No application configuration changes, imports into old databases, or Git actions.
Secrets stay in memory; archives contain private data and must never be committed.
"""
import argparse
import gzip
import hashlib
import json
import os
import re
import secrets
import subprocess
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, **kwargs)
    if result.returncode:
        # Commands/environment may contain passwords: never include args/output.
        raise RuntimeError("Container operation failed; sensitive output suppressed")
    return result.stdout


def inspect(name):
    return json.loads(run(["docker", "inspect", name]))[0]


def env_of(info):
    return dict(v.split("=", 1) for v in info["Config"]["Env"] if "=" in v)


def mysql(container, secret, query, db=None):
    args = ["docker", "exec", "-i", "-e", "MYSQL_PWD=" + secret, container,
            "mariadb", "-uroot", "--batch", "--skip-column-names", "--raw"]
    if db:
        args.append(db)
    return run(args, input=(query + ";\n").encode("utf-8")).decode("utf-8")


def sqlserver(secret, query):
    return run(["docker", "exec", "-e", "SQLCMDPASSWORD=" + secret,
                "datawarehouse_sqlserver", "/opt/mssql-tools18/bin/sqlcmd",
                "-S", "localhost", "-U", "sa", "-C", "-b", "-W", "-h", "-1",
                "-s", "|", "-Q", "SET NOCOUNT ON; " + query]).decode("utf-8")


def db_counts(container, secret, db):
    if db != "moodledb":
        raise ValueError("Unexpected source database")
    names = mysql(container, secret, "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA='moodledb' AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME").splitlines()
    if not all(re.fullmatch(r"[a-zA-Z0-9_]+", name) for name in names):
        raise ValueError("Unsafe table name")
    query = " UNION ALL ".join("SELECT '" + name + "', COUNT(*) FROM `" + name + "`" for name in names)
    return dict((name, int(count)) for name, count in (line.split("\t") for line in mysql(container, secret, query, db).splitlines()))


def warehouse_counts(secret, db):
    if not re.fullmatch(r"(?:lms_datawarehouse|v2_restorecheck_[0-9_]+)", db):
        raise ValueError("Unexpected database")
    text = sqlserver(secret, "SELECT t.name,SUM(p.rows) FROM [" + db + "].sys.tables t JOIN [" + db + "].sys.partitions p ON t.object_id=p.object_id WHERE p.index_id IN (0,1) GROUP BY t.name ORDER BY t.name;")
    return dict((line.split("|")[0], int(line.split("|")[1])) for line in text.splitlines() if "|" in line)


def stream_to_file(args, target, compress=False):
    # stderr is not printed because it could include source values.
    with target.open("xb") as raw:
        sink = gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=3) if compress else raw
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
                sink.write(chunk)
        finally:
            if compress:
                sink.close()
            process.stdout.close()
        if process.wait() != 0:
            raise RuntimeError("Backup stream failed; do not use partial archive")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_archive_member(name):
    return (not name.startswith(("/", "\\")) and "\\" not in name
                and not re.match(r"^[A-Za-z]:", name) and ".." not in Path(name).parts)


def protect_directory(path):
    if os.name == "nt":
        account = run(["whoami"]).decode().strip()
        run(["icacls", str(path), "/inheritance:r", "/grant:r", account + ":(OI)(CI)F"])
    else:
        path.chmod(0o700)


CONFIG_PROBE = r'''<?php
$t=file_get_contents('/var/www/html/config.php'); $out=[];
foreach (['dataroot','dirroot','wwwroot'] as $name) {
 if (preg_match('/\$CFG->'.$name.'\s*=\s*([\'\"])(.*?)\1\s*;/s',$t,$m)) {$out[$name]=$m[2];}
}
echo json_encode($out);
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Required: create backups and restore-check disposable targets")
    parser.add_argument("--verify-existing", type=Path, help="Reuse an existing private backup directory; do not create backups again")
    args = parser.parse_args()
    if not args.execute:
        parser.error("No changes made. Pass --execute to perform backups and isolated restore checks.")
    if args.verify_existing:
        folder = args.verify_existing.resolve()
        if folder.parent != (ROOT / "backups").resolve() or not re.fullmatch(r"[0-9]{8}_[0-9]{6}", folder.name):
            raise ValueError("Backup directory must be a timestamped direct child of backups")
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("complete"):
            raise ValueError("Backup is already verified; refusing redundant restore")
        for name in ("moodledb.sql.gz", "moodle-files.tar.gz", "warehouse.bak"):
            if digest(folder / name) != manifest["files"][name]["sha256"]:
                raise ValueError("Backup checksum mismatch")
        verify_backup(folder, manifest)
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    folder = ROOT / "backups" / stamp
    folder.mkdir(parents=True, exist_ok=False)
    protect_directory(folder)
    manifest = {"started_utc": stamp, "complete": False, "old_application_config_changed": False,
                "consistency": "Online InnoDB snapshot; filesystem archived online, not a coordinated DB/filesystem point-in-time snapshot"}
    manifest_path = folder / "manifest.json"
    def save():
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    save()
    maria_info = inspect("moodle_db")
    app_info = inspect("moodle_app")
    sql_info = inspect("datawarehouse_sqlserver")
    maria_secret = env_of(maria_info)["MYSQL_ROOT_PASSWORD"]
    sql_secret = env_of(sql_info)["MSSQL_SA_PASSWORD"]
    config = json.loads(run(["docker", "exec", "-i", "moodle_app", "php"], input=CONFIG_PROBE.encode()))
    dataroot = config.get("dataroot")
    if not dataroot or not dataroot.startswith("/") or ".." in dataroot.split("/"):
        raise RuntimeError("Cannot verify Moodle dataroot; stopped before backup")
    noninnodb = mysql("moodle_db", maria_secret, "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA='moodledb' AND TABLE_TYPE='BASE TABLE' AND ENGINE<>'InnoDB'").strip()
    if noninnodb != "0":
        raise RuntimeError("Non-InnoDB source tables: online snapshot needs an explicit quiescence plan")
    manifest["images"] = {"moodle": app_info["Image"], "mariadb": maria_info["Image"], "sqlserver": sql_info["Image"]}
    manifest["source_mariadb_counts"] = db_counts("moodle_db", maria_secret, "moodledb")
    manifest["source_warehouse_counts"] = warehouse_counts(sql_secret, "lms_datawarehouse")
    manifest["dataroot"] = dataroot
    save()
    print("Backing up MariaDB (consistent InnoDB snapshot)...", flush=True)
    dump = folder / "moodledb.sql.gz"
    stream_to_file(["docker", "exec", "-e", "MYSQL_PWD=" + maria_secret, "moodle_db",
                    "mariadb-dump", "-uroot", "--single-transaction", "--quick", "--hex-blob",
                    "--routines", "--triggers", "--events", "--skip-comments", "moodledb"], dump, True)
    print("Archiving Moodle code/config and actual dataroot...", flush=True)
    archive = folder / "moodle-files.tar.gz"
    stream_to_file(["docker", "exec", "moodle_app", "tar", "-czf", "-", "-C", "/", "var/www/html", dataroot.lstrip("/")], archive)
    member_count = 0
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if not safe_archive_member(member.name):
                raise RuntimeError("Unsafe archive entry")
            # Fully read regular files to detect gzip/tar corruption.
            if member.isfile():
                handle = tar.extractfile(member)
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    pass
                handle.close()
            member_count += 1
    manifest["archive_members_verified"] = member_count
    print("Backing up SQL Server (COPY_ONLY + CHECKSUM)...", flush=True)
    remote_dir = "/var/opt/mssql/backup/v2_" + stamp
    run(["docker", "exec", "-u", "root", "datawarehouse_sqlserver", "mkdir", "-p", remote_dir])
    run(["docker", "exec", "-u", "root", "datawarehouse_sqlserver", "chown", "mssql:mssql", remote_dir])
    remote_bak = remote_dir + "/warehouse.bak"
    sqlserver(sql_secret, "BACKUP DATABASE [lms_datawarehouse] TO DISK=N'" + remote_bak + "' WITH COPY_ONLY,CHECKSUM; RESTORE VERIFYONLY FROM DISK=N'" + remote_bak + "' WITH CHECKSUM;")
    bak = folder / "warehouse.bak"
    run(["docker", "cp", "datawarehouse_sqlserver:" + remote_bak, str(bak)])
    manifest["files"] = {p.name: {"bytes": p.stat().st_size, "sha256": digest(p)} for p in (dump, archive, bak)}
    save()

    verify_backup(folder, manifest)


def count_deltas(before, after):
    return {name: {"before": before.get(name), "after": after.get(name)}
            for name in before.keys() | after.keys() if before.get(name) != after.get(name)}


def verify_backup(folder, manifest):
    stamp = folder.name
    manifest_path = folder / "manifest.json"
    def save():
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    maria_info = inspect("moodle_db")
    maria_secret = env_of(maria_info)["MYSQL_ROOT_PASSWORD"]
    sql_secret = env_of(inspect("datawarehouse_sqlserver"))["MSSQL_SA_PASSWORD"]
    if maria_info["Image"] != manifest["images"]["mariadb"]:
        raise ValueError("Source database image changed since backup")
    dump = folder / "moodledb.sql.gz"
    remote_bak = "/var/opt/mssql/backup/v2_" + stamp + "/warehouse.bak"
    manifest["complete"] = False
    manifest["verification_started_utc"] = datetime.now(timezone.utc).isoformat()
    save()
    temp_name = "dacs_v2_restorecheck_" + stamp
    temp_volume = temp_name + "_data"
    check_secret = secrets.token_urlsafe(32)
    print("Restoring MariaDB in network-isolated disposable container...", flush=True)
    run(["docker", "volume", "create", "--label", "dacs.purpose=v2-backup-verification", temp_volume])
    run(["docker", "run", "-d", "--name", temp_name, "--network", "none",
         "--label", "dacs.purpose=v2-backup-verification", "--mount", "type=volume,source=" + temp_volume + ",target=/var/lib/mysql",
         "-e", "MARIADB_ROOT_PASSWORD=" + check_secret, maria_info["Image"]])
    try:
        for attempt in range(90):
            try:
                mysql(temp_name, check_secret, "SELECT 1")
                break
            except RuntimeError:
                time.sleep(2)
        else:
            raise RuntimeError("Restore-check database did not start")
        mysql(temp_name, check_secret, "CREATE DATABASE moodledb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        process = subprocess.Popen(["docker", "exec", "-i", "-e", "MYSQL_PWD=" + check_secret, temp_name,
                                    "mariadb", "-uroot", "moodledb"], stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with gzip.open(dump, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                process.stdin.write(chunk)
        process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError("MariaDB restore check failed")
        restored = db_counts(temp_name, check_secret, "moodledb")
        differences = count_deltas(manifest["source_mariadb_counts"], restored)
        # Original cron remains active. Its execution log is not a business table.
        # Record differences, never falsely claim exact equality for the whole DB.
        if set(differences) - {"task_log"}:
            raise RuntimeError("Restored business table counts differ; investigate online snapshot")
        manifest["mariadb_restore_verified"] = {"tables": len(restored),
            "business_table_counts_match": True, "count_differences": differences}
        save()
    finally:
        run(["docker", "rm", "-f", temp_name])
        run(["docker", "volume", "rm", temp_volume])

    print("Restoring SQL Server into disposable verification database...", flush=True)
    check_db = "v2_restorecheck_" + stamp
    files = sqlserver(sql_secret, "SELECT name,type FROM sys.master_files WHERE database_id=DB_ID('lms_datawarehouse') ORDER BY file_id")
    moves = []
    for i, line in enumerate(files.splitlines()):
        if "|" not in line:
            continue
        logical, kind = line.split("|")
        suffix = ".ldf" if kind.strip() == "1" else ".mdf"
        escaped = logical.replace("'", "''")
        moves.append("MOVE N'" + escaped + "' TO N'/var/opt/mssql/data/" + check_db + "_" + str(i) + suffix + "'")
    if not moves:
        raise RuntimeError("No SQL Server files found")
    if sqlserver(sql_secret, "SELECT COUNT(*) FROM sys.databases WHERE name='" + check_db + "'").strip() != "0":
        raise RuntimeError("Restore check target already exists; refusing overwrite")
    restored_sql = False
    try:
        sqlserver(sql_secret, "RESTORE DATABASE [" + check_db + "] FROM DISK=N'" + remote_bak + "' WITH CHECKSUM," + ",".join(moves) + ";")
        restored_sql = True
        restored = warehouse_counts(sql_secret, check_db)
        if restored != manifest["source_warehouse_counts"]:
            raise RuntimeError("SQL Server restored row counts differ")
        sqlserver(sql_secret, "DBCC CHECKDB (N'" + check_db + "') WITH PHYSICAL_ONLY,NO_INFOMSGS;")
        manifest["sqlserver_restore_verified"] = {"tables": len(restored), "all_table_counts_match": True, "physical_check": True}
        save()
    finally:
        if restored_sql:
            sqlserver(sql_secret, "ALTER DATABASE [" + check_db + "] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [" + check_db + "];")
    differences = count_deltas(manifest["source_mariadb_counts"], db_counts("moodle_db", maria_secret, "moodledb"))
    manifest["online_source_count_deltas"] = differences
    manifest["old_mariadb_counts_unchanged"] = not differences
    manifest["old_business_counts_unchanged"] = not (set(differences) - {"task_log"})
    manifest["old_warehouse_counts_unchanged"] = warehouse_counts(sql_secret, "lms_datawarehouse") == manifest["source_warehouse_counts"]
    manifest["complete"] = manifest["old_business_counts_unchanged"] and manifest["old_warehouse_counts_unchanged"]
    manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
    save()
    if not manifest["complete"]:
        raise RuntimeError("Source changed during backup; inspect manifest before proceeding")
    # The verified local native backup is retained; remove only our server-side copy.
    run(["docker", "exec", "-u", "root", "datawarehouse_sqlserver", "rm", "-f", remote_bak])
    run(["docker", "exec", "-u", "root", "datawarehouse_sqlserver", "rmdir", remote_bak.rsplit("/", 1)[0]])
    manifest["server_side_duplicate_removed"] = True
    save()
    print("PASS: both actual database restores verified; original business table counts unchanged.")
    print("Private backup directory:", folder.relative_to(ROOT).as_posix())
    print("Filesystem archive verified; online files and DB are not claimed as one point-in-time snapshot.")


if __name__ == "__main__":
    main()
