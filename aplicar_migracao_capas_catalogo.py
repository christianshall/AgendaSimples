import pyodbc

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DESKTOP-V1OSISF;"
    "Database=AgendaSimples;"
    "Trusted_Connection=yes;"
)

COLS = [
    "capa_catalogo1",
    "capa_catalogo2",
    "capa_catalogo3",
    "capa_catalogo4",
]


def main():
    conn = pyodbc.connect(CONN_STR)
    conn.autocommit = True
    cur = conn.cursor()
    for col in COLS:
        cur.execute(
            f"""
            IF COL_LENGTH('dbo.barbearias', '{col}') IS NULL
                ALTER TABLE dbo.barbearias ADD {col} NVARCHAR(255) NULL;
            """
        )
    conn.close()
    print("OK — capa_catalogo1..4 prontas em barbearias.")


if __name__ == "__main__":
    main()
