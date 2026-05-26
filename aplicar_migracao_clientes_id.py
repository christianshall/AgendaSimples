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
        IF COL_LENGTH('dbo.Clientes', 'id') IS NULL
            ALTER TABLE dbo.Clientes ADD id INT IDENTITY(1,1) NOT NULL;
        """
    )
    cur.execute(
        """
        IF NOT EXISTS (
            SELECT 1 FROM sys.key_constraints
            WHERE parent_object_id = OBJECT_ID('dbo.Clientes') AND type = 'PK'
        )
        ALTER TABLE dbo.Clientes ADD CONSTRAINT PK_Clientes PRIMARY KEY (id);
        """
    )
    conn.close()
    print("OK — coluna id em Clientes pronta.")


if __name__ == "__main__":
    main()
