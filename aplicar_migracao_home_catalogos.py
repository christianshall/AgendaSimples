import pyodbc

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DESKTOP-V1OSISF;"
    "Database=AgendaSimples;"
    "Trusted_Connection=yes;"
)

ALTERS = [
    ("titulo_catalogo1", "NVARCHAR(100) NULL"),
    ("titulo_catalogo2", "NVARCHAR(100) NULL"),
    ("titulo_catalogo3", "NVARCHAR(100) NULL"),
    ("titulo_catalogo4", "NVARCHAR(100) NULL"),
    ("logotipo_url", "NVARCHAR(500) NULL"),
]

UPDATE_DEFAULTS = """
UPDATE dbo.barbearias
SET
    titulo_catalogo1 = COALESCE(titulo_catalogo1, N'Corte'),
    titulo_catalogo2 = COALESCE(titulo_catalogo2, N'Corte e Barba'),
    titulo_catalogo3 = COALESCE(titulo_catalogo3, N'Corte + Sobrancelha'),
    titulo_catalogo4 = COALESCE(titulo_catalogo4, N'Outros Serviços')
"""


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
        print(f"  coluna {col}: OK")

    cur.execute(UPDATE_DEFAULTS)
    conn.close()
    print("OK — catálogos e logotipo prontos em barbearias.")


if __name__ == "__main__":
    main()
