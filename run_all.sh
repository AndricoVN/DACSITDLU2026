#!/bin/bash
set -e

echo "== 1. Khoi dong mariadb + sqlserver =="
docker compose up -d mariadb sqlserver
echo "Doi 20s cho sqlserver san sang..."
sleep 20

echo "== 2. Tao schema cho data warehouse (qua pyodbc, image azure-sql-edge khong co sqlcmd) =="
docker compose run --rm etl python run_schema.py

echo "== 4. Chay ETL: day du lieu Moodle -> Data Warehouse =="
docker compose run --rm etl

echo "== 5. Khoi dong backend + frontend =="
docker compose up -d backend

echo "== XONG! Mo trinh duyet: http://localhost:5050 =="