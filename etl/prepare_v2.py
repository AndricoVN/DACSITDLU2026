"""Create isolated empty Moodle/DWH v2 only after a verified backup.

Never drops/reuses old databases. Re-running requires the private ownership marker.
The web server waits for a readiness file written only after import validation.
"""
import argparse
import json
import secrets
import socket
from pathlib import Path

from backup_v2 import ROOT, env_of, inspect, mysql, protect_directory, run

STATE = ROOT / "local-v2/runtime-secrets.json"
CONTAINER = "moodle_app_v2"
CODE_VOLUME = "dacs_v2_moodle_code"
DATA_VOLUME = "dacs_v2_moodle_data"
DB = "moodledb_v2"


def config_text(password):
    # Random URL-safe secrets do not need PHP escaping; never use CSV values here.
    return """<?php
unset($CFG); global $CFG; $CFG = new stdClass();
$CFG->dbtype='mariadb'; $CFG->dblibrary='native';
$CFG->dbhost='moodle_db'; $CFG->dbname='moodledb_v2';
$CFG->dbuser='dacs_v2_app'; $CFG->dbpass='%s'; $CFG->prefix='';
$CFG->dboptions=['dbpersist'=>false,'dbport'=>3306,'dbsocket'=>'','dbcollation'=>'utf8mb4_unicode_ci'];
$CFG->wwwroot='http://localhost:8081';
$CFG->dataroot='/var/moodledata'; $CFG->admin='admin';
$CFG->directorypermissions=02770; $CFG->sessioncookie='V2';
$CFG->noemailever=true; $CFG->cron_enabled=false;
$CFG->disableupdatenotifications=true;
require_once(__DIR__ . '/lib/setup.php');
""" % password


def save(state):
    STATE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    if not args.execute:
        parser.error("Pass --execute; nothing created")
    backup = args.backup.resolve()
    if backup.parent != (ROOT / "backups").resolve():
        raise ValueError("Expected private backup under backups/")
    manifest = json.loads((backup / "manifest.json").read_text())
    if not manifest.get("complete") or not manifest.get("mariadb_restore_verified") or not manifest.get("sqlserver_restore_verified"):
        raise ValueError("Both actual restores must be verified before creating v2")
    root_secret = env_of(inspect("moodle_db"))["MYSQL_ROOT_PASSWORD"]
    if STATE.exists():
        state = json.loads(STATE.read_text())
        if state.get("database") != DB or state.get("purpose") != "dacs-real-source-v2":
            raise ValueError("Unknown state ownership")
    else:
        if mysql("moodle_db", root_secret, "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name='moodledb_v2'").strip() != "0":
            raise ValueError("V2 database already exists without ownership marker")
        if run(["docker", "ps", "-a", "--filter", "name=^/moodle_app_v2$", "--format", "{{.Names}}"]).strip():
            raise ValueError("Container already exists")
        with socket.socket() as check:
            check.bind(("127.0.0.1", 8081))
        protect_directory(STATE.parent)
        state = {"purpose": "dacs-real-source-v2", "database": DB,
                 "builder_password": secrets.token_urlsafe(36), "app_password": secrets.token_urlsafe(36),
                 "admin_username": "v2admin", "admin_password": secrets.token_urlsafe(28) + "aA1!",
                 "phase": "planned", "backup": backup.name, "image": manifest["images"]["moodle"]}
        save(state)
    if state["phase"] == "planned":
        query = "CREATE DATABASE moodledb_v2 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
        for user, key in (("dacs_v2_builder", "builder_password"), ("dacs_v2_app", "app_password")):
            query += "CREATE USER '" + user + "'@'%' IDENTIFIED BY '" + state[key] + "';"
            query += "GRANT ALL PRIVILEGES ON moodledb_v2.* TO '" + user + "'@'%';"
        mysql("moodle_db", root_secret, query)
        state["phase"] = "database_created"
        save(state)
    if state["phase"] == "database_created":
        for volume in (CODE_VOLUME, DATA_VOLUME):
            existing = run(["docker", "volume", "ls", "--filter", "name=^" + volume + "$", "--format", "{{.Name}}"]).strip()
            if existing:
                raise ValueError("Volume already exists; inspect ownership before retry")
            run(["docker", "volume", "create", "--label", "dacs.purpose=real-source-v2", volume])
        run(["docker", "run", "-d", "--name", CONTAINER, "--label", "dacs.purpose=real-source-v2",
             "--network", "dacs_default", "-p", "127.0.0.1:8081:80",
             "--mount", "type=volume,source=" + CODE_VOLUME + ",target=/var/www/html",
             "--mount", "type=volume,source=" + DATA_VOLUME + ",target=/var/moodledata",
             "-e", "APACHE_DOCUMENT_ROOT=/var/www/html/public", "--entrypoint", "/bin/sh", state["image"],
             "-c", "while [ ! -f /var/moodledata/.v2-ready ]; do sleep 1; done; exec apache2-foreground"])
        state["phase"] = "container_created"
        save(state)
    if state["phase"] == "container_created":
        run(["docker", "exec", "-i", CONTAINER, "tee", "/var/www/html/config.php"], input=config_text(state["app_password"]).encode())
        run(["docker", "exec", CONTAINER, "chown", "root:www-data", "/var/www/html/config.php"])
        run(["docker", "exec", CONTAINER, "chmod", "640", "/var/www/html/config.php"])
        run(["docker", "exec", CONTAINER, "chown", "www-data:www-data", "/var/moodledata"])
        # The image's root lib/setup.php is the Moodle 5.2 compatibility bootstrap.
        # No old config, authentication material, courses or custom scripts copied.
        state["phase"] = "configured"
        save(state)
    if state["phase"] == "configured":
        print("Installing clean Moodle schema/config in v2 only...", flush=True)
        run(["docker", "exec", "-u", "www-data", CONTAINER, "php",
             "/var/www/html/admin/cli/install_database.php", "--agree-license",
             "--fullname=DLU - Du lieu that (demo noi bo)", "--shortname=DLU v2",
             "--adminuser=" + state["admin_username"], "--adminpass=" + state["admin_password"],
             "--adminemail=admin@example.invalid"], timeout=600)
        state["phase"] = "installed"
        save(state)
    print("V2 preparation state:", state["phase"], "; web remains gated until source import passes.")
    print("Private access details retained in local-v2/runtime-secrets.json (never commit).")


if __name__ == "__main__":
    main()
