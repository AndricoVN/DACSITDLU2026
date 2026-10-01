"""
Script thay the cho sqlcmd: chay dwh/schema.sql vao SQL Server bang pyodbc.
"""
import pyodbc

SERVER_CONN_STR = (
    "DRIVER={ODBC Driver 18 for SQL Server};"
    "SERVER=sqlserver,1433;"
    "UID=sa;"
    "PWD=SuperStrongPass123!;"
    "TrustServerCertificate=yes;"
)

def run_batches(cursor, sql_text):
    batches = [b.strip() for b in sql_text.split("\nGO") if b.strip()]
    for i, batch in enumerate(batches, 1):
        if not batch.strip():
            continue
        try:
            cursor.execute(batch)
            print(f"[OK] Batch {i} thuc thi thanh cong.")
        except Exception as e:
            print(f"[LOI] Batch {i}: {e}")

def main():
    with open("/dwh/schema.sql", "r", encoding="utf-8-sig") as f:
        full_sql = f.read()

    conn = pyodbc.connect(SERVER_CONN_STR, autocommit=True)
    cursor = conn.cursor()

    marker = "USE lms_datawarehouse"
    if marker in full_sql:
        before, after = full_sql.split(marker, 1)
    else:
        before, after = full_sql, ""

    print(">> Tao database (neu chua co)...")
    run_batches(cursor, before)
    cursor.close()
    conn.close()

    if after.strip():
        print(">> Ket noi lai vao lms_datawarehouse de tao bang...")
        conn2_str = SERVER_CONN_STR + "DATABASE=lms_datawarehouse;"
        conn2 = pyodbc.connect(conn2_str, autocommit=True)
        cursor2 = conn2.cursor()
        run_batches(cursor2, after)
        cursor2.close()
        conn2.close()

    print(">> HOAN TAT tao schema.")

if __name__ == "__main__":
    main()
