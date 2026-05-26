import pyodbc

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DESKTOP-V1OSISF;"
    "Database=AgendaSimples;"
    "Trusted_Connection=yes;"
)

SQL = """
IF COL_LENGTH('dbo.Clientes', 'status') IS NULL
    ALTER TABLE dbo.Clientes ADD status NVARCHAR(50) NOT NULL DEFAULT 'Agendado';
"""


def main():
    conn = pyodbc.connect(CONN_STR)
    conn.autocommit = True
    conn.cursor().execute(SQL)
    conn.close()
    print("OK — coluna status em Clientes pronta.")


if __name__ == "__main__":
    main()
