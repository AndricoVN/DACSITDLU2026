"""
seed_dim_date.py
Sinh dữ liệu bảng Dim_Date từ 2024 đến 2030 vào SQL Server Data Warehouse
"""
import datetime
import pyodbc

DWH_CONN_STR = (
    "DRIVER={ODBC Driver 18 for SQL Server};"
    "SERVER=sqlserver,1433;"
    "DATABASE=lms_datawarehouse;"
    "UID=sa;"
    "PWD=SuperStrongPass123!;"
    "TrustServerCertificate=yes;"
)

def get_day_name_vi(weekday_idx: int) -> str:
    names = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
    return names[weekday_idx]

def seed_dates(start_year: int = 2024, end_year: int = 2030):
    start_date = datetime.date(start_year, 1, 1)
    end_date = datetime.date(end_year, 12, 31)
    delta = datetime.timedelta(days=1)
    
    print(f"[*] Đang khởi tạo dữ liệu Dim_Date từ {start_date} đến {end_date}...")
    
    conn = pyodbc.connect(DWH_CONN_STR)
    cursor = conn.cursor()
    
    # Dùng DELETE FROM thay vì TRUNCATE để không bị chặn bởi Foreign Key
    cursor.execute("DELETE FROM Dim_Date")
    conn.commit()
    
    current_date = start_date
    batch = []
    
    while current_date <= end_date:
        date_key = int(current_date.strftime("%Y%m%d"))
        day_of_week = current_date.isoweekday() % 7 + 1  # 1: CN, 2: T2...
        day_name = get_day_name_vi(current_date.weekday())
        day_of_month = current_date.day
        day_of_year = int(current_date.strftime("%j"))
        week_of_year = current_date.isocalendar()[1]
        month_name = f"Tháng {current_date.month}"
        month_of_year = current_date.month
        quarter = (current_date.month - 1) // 3 + 1
        year = current_date.year
        is_weekend = 1 if current_date.weekday() in [5, 6] else 0
        
        batch.append((
            date_key, current_date, day_of_week, day_name,
            day_of_month, day_of_year, week_of_year, month_name,
            month_of_year, quarter, year, is_weekend
        ))
        
        if len(batch) >= 1000:
            cursor.executemany("""
                INSERT INTO Dim_Date (
                    DateKey, FullDate, DayOfWeek, DayName,
                    DayOfMonth, DayOfYear, WeekOfYear, MonthName,
                    MonthOfYear, Quarter, Year, IsWeekend
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, batch)
            conn.commit()
            batch.clear()
            
        current_date += delta
        
    if batch:
        cursor.executemany("""
            INSERT INTO Dim_Date (
                DateKey, FullDate, DayOfWeek, DayName,
                DayOfMonth, DayOfYear, WeekOfYear, MonthName,
                MonthOfYear, Quarter, Year, IsWeekend
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, batch)
        conn.commit()
        
    cursor.close()
    conn.close()
    print("[+] Hoàn tất nạp dữ liệu Dim_Date!")

if __name__ == "__main__":
    seed_dates()
