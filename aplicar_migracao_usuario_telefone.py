"""Adiciona coluna telefone em dbo.usuarios para recuperação de senha por telefone."""
import pyodbc

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DESKTOP-V1OSISF;"
    "Database=AgendaSimples;"
    "Trusted_Connection=yes;"
)


def main():
    conn = pyodbc.connect(CONN_STR)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        """
        IF COL_LENGTH('dbo.usuarios', 'telefone') IS NULL
            ALTER TABLE dbo.usuarios ADD telefone NVARCHAR(20) NULL;
        """
    )
    conn.close()
    print("OK — coluna usuarios.telefone pronta.")


if __name__ == "__main__":
    main()
