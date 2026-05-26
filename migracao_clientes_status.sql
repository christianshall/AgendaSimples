USE [AgendaSimples];
GO

IF COL_LENGTH('dbo.Clientes', 'status') IS NULL
BEGIN
    ALTER TABLE dbo.Clientes
        ADD status NVARCHAR(50) NOT NULL DEFAULT 'Agendado';
    PRINT 'Coluna status adicionada em Clientes.';
END
GO
