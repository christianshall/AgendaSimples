"""
Cria a tabela dbo.assinaturas no SQL Server (AgendaSimples) e
insere trial de 7 dias para barbearias que ainda não têm assinatura.

Uso (na pasta do projeto):
    python criar_tabela_assinaturas.py
"""
import pyodbc

# Mesma conexão do app.py — ajuste Server/Database se necessário
CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DESKTOP-V1OSISF;"
    "Database=AgendaSimples;"
    "Trusted_Connection=yes;"
)

TRIAL_DAYS = 7

SQL_CRIAR_TABELA = """
IF OBJECT_ID('dbo.assinaturas', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.assinaturas (
        id INT IDENTITY(1,1) PRIMARY KEY,
        barbearia_id INT NOT NULL,
        stripe_customer_id NVARCHAR(255) NULL,
        stripe_subscription_id NVARCHAR(255) NULL,
        plano_status NVARCHAR(50) NOT NULL DEFAULT 'trialing',
        data_fim_trial DATETIME NULL,
        data_fim_plano DATETIME NULL,
        criado_em DATETIME NOT NULL DEFAULT GETDATE(),
        atualizado_em DATETIME NOT NULL DEFAULT GETDATE(),
        CONSTRAINT FK_assinaturas_barbearia
            FOREIGN KEY (barbearia_id) REFERENCES dbo.barbearias(id),
        CONSTRAINT UQ_assinaturas_barbearia UNIQUE (barbearia_id)
    );

    CREATE INDEX IX_assinaturas_stripe_customer
        ON dbo.assinaturas (stripe_customer_id)
        WHERE stripe_customer_id IS NOT NULL;

    CREATE INDEX IX_assinaturas_stripe_subscription
        ON dbo.assinaturas (stripe_subscription_id)
        WHERE stripe_subscription_id IS NOT NULL;
END
"""

SQL_BACKFILL_TRIAL = """
INSERT INTO dbo.assinaturas (barbearia_id, plano_status, data_fim_trial, criado_em, atualizado_em)
SELECT b.id, 'trialing', DATEADD(day, ?, GETDATE()), GETDATE(), GETDATE()
FROM dbo.barbearias b
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.assinaturas a WHERE a.barbearia_id = b.id
)
"""


def main():
    print("Conectando ao SQL Server...")
    conn = pyodbc.connect(CONN_STR)
    conn.autocommit = True
    cursor = conn.cursor()

    print("1/2 Criando tabela assinaturas (se não existir)...")
    cursor.execute(SQL_CRIAR_TABELA)

    cursor.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'assinaturas'"
    )
    if cursor.fetchone()[0] == 0:
        print("ERRO: tabela assinaturas não foi criada. Verifique se dbo.barbearias existe.")
        conn.close()
        return

    print("   OK — tabela dbo.assinaturas existe.")

    print(f"2/2 Inserindo trial de {TRIAL_DAYS} dias para barbearias sem assinatura...")
    cursor.execute(SQL_BACKFILL_TRIAL, (TRIAL_DAYS,))
    print(f"   OK — {cursor.rowcount} registro(s) inserido(s).")

    cursor.execute("SELECT COUNT(*) FROM dbo.assinaturas")
    total = cursor.fetchone()[0]
    print(f"\nConcluído. Total de assinaturas no banco: {total}")
    print("Reinicie o Flask e acesse /admin_agenda novamente.")

    conn.close()


if __name__ == "__main__":
    main()
