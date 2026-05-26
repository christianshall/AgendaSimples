"""Adiciona coluna profissional_id na tabela financeiro (SQL Server)."""
import pyodbc

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DESKTOP-V1OSISF;"
    "Database=AgendaSimples;"
    "Trusted_Connection=yes;"
)

SQL = """
IF COL_LENGTH('dbo.financeiro', 'profissional_id') IS NULL
BEGIN
    ALTER TABLE dbo.financeiro ADD profissional_id INT NULL;
    ALTER TABLE dbo.financeiro
        ADD CONSTRAINT FK_financeiro_profissional
        FOREIGN KEY (profissional_id) REFERENCES dbo.usuarios(id);
END
"""


def main():
    conn = pyodbc.connect(CONN_STR)
    conn.autocommit = True
    conn.cursor().execute(SQL)
    conn.close()
    print("OK — coluna profissional_id pronta na tabela financeiro.")


if __name__ == "__main__":
    main()
