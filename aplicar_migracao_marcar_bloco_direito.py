import pyodbc

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DESKTOP-V1OSISF;"
    "Database=AgendaSimples;"
    "Trusted_Connection=yes;"
)

ALTERS = [
    ("texto_marcar_direito", "NVARCHAR(150) NULL"),
    ("foto_fundo_direito_url", "NVARCHAR(255) NULL"),
]


def main():
    conn = pyodbc.connect(CONN_STR)
    conn.autocommit = True
    cur = conn.cursor()
    for col, typedef in ALTERS:
        cur.execute(
            f"""
            IF COL_LENGTH('dbo.barbearias', '{col}') IS NULL
                ALTER TABLE dbo.barbearias ADD {col} {typedef};
            """
        )
    conn.close()
    print("OK — texto_marcar_direito e foto_fundo_direito_url prontos.")


if __name__ == "__main__":
    main()
