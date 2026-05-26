-- Migração: tabela de assinaturas SaaS + Stripe (SQL Server)
-- Execute no banco AgendaSimples antes de usar o módulo de pagamentos.

USE [AgendaSimples];
GO

IF OBJECT_ID('dbo.assinaturas', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.assinaturas (
        id INT IDENTITY(1,1) PRIMARY KEY,
        barbearia_id INT NOT NULL,
        stripe_customer_id NVARCHAR(255) NULL,
        stripe_subscription_id NVARCHAR(255) NULL,
        plano_status NVARCHAR(50) NOT NULL DEFAULT 'trialing',
        -- active | trialing | past_due | canceled | incomplete
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

    PRINT 'Tabela assinaturas criada com sucesso.';
END
ELSE
    PRINT 'Tabela assinaturas já existe.';
GO

-- Barbearias já existentes: trial de 7 dias retroativo
INSERT INTO dbo.assinaturas (barbearia_id, plano_status, data_fim_trial, criado_em, atualizado_em)
SELECT b.id, 'trialing', DATEADD(day, 7, GETDATE()), GETDATE(), GETDATE()
FROM dbo.barbearias b
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.assinaturas a WHERE a.barbearia_id = b.id
);
GO

-- Opcional: remover coluna legada plano_ativo se quiser centralizar tudo em assinaturas
-- ALTER TABLE dbo.barbearias DROP COLUMN plano_ativo;
GO
