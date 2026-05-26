-- Adiciona vínculo do lançamento financeiro ao profissional (usuário barbeiro)
USE [AgendaSimples];
GO

IF COL_LENGTH('dbo.financeiro', 'profissional_id') IS NULL
BEGIN
    ALTER TABLE dbo.financeiro
        ADD profissional_id INT NULL;

    ALTER TABLE dbo.financeiro
        ADD CONSTRAINT FK_financeiro_profissional
        FOREIGN KEY (profissional_id) REFERENCES dbo.usuarios(id);

    PRINT 'Coluna profissional_id adicionada em financeiro.';
END
ELSE
    PRINT 'Coluna profissional_id já existe.';
GO
