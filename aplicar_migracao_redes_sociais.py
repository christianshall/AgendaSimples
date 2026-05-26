import pyodbc

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DESKTOP-V1OSISF;"
    "Database=AgendaSimples;"
    "Trusted_Connection=yes;"
)

COLS = [
    ("link_instagram", "NVARCHAR(500) NULL"),
    ("link_facebook", "NVARCHAR(500) NULL"),
    ("link_whatsapp", "NVARCHAR(500) NULL"),
]


def main():
    conn = pyodbc.connect(CONN_STR)
    conn.autocommit = True
    cur = conn.cursor()
    for col, typedef in COLS:
        cur.execute(
            f"""
            IF COL_LENGTH('dbo.barbearias', '{col}') IS NULL
                ALTER TABLE dbo.barbearias ADD {col} {typedef};
            """
        )
    conn.close()
    print("OK — link_instagram, link_facebook, link_whatsapp prontos.")


if __name__ == "__main__":
    main()
