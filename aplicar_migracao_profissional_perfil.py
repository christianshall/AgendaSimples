"""Colunas de perfil do profissional em dbo.usuarios."""
import pyodbc

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DESKTOP-V1OSISF;"
    "Database=AgendaSimples;"
    "Trusted_Connection=yes;"
)

COLS = [
    ("telefone", "NVARCHAR(20) NULL"),
    ("especialidade", "NVARCHAR(120) NULL"),
    ("foto_perfil", "NVARCHAR(255) NULL"),
]


def main():
    conn = pyodbc.connect(CONN_STR)
    conn.autocommit = True
    cur = conn.cursor()
    for col, typedef in COLS:
        cur.execute(
            f"""
            IF COL_LENGTH('dbo.usuarios', '{col}') IS NULL
                ALTER TABLE dbo.usuarios ADD {col} {typedef};
            """
        )
    conn.close()
    print("OK — telefone, especialidade e foto_perfil em usuarios.")


if __name__ == "__main__":
    main()
