-- ID único por agendamento (Post/Redirect/Get na tela de sucesso)
USE [AgendaSimples];
GO

IF COL_LENGTH('dbo.Clientes', 'id') IS NULL
BEGIN
    ALTER TABLE dbo.Clientes ADD id INT IDENTITY(1,1) NOT NULL;
    PRINT 'Coluna id adicionada em Clientes.';
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.key_constraints
    WHERE parent_object_id = OBJECT_ID('dbo.Clientes')
      AND type = 'PK'
)
BEGIN
    ALTER TABLE dbo.Clientes ADD CONSTRAINT PK_Clientes PRIMARY KEY (id);
    PRINT 'PK_Clientes criada.';
END
GO
