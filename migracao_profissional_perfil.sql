-- Execute no banco AgendaSimples (SQL Server)
USE AgendaSimples;
GO

IF COL_LENGTH('dbo.usuarios', 'telefone') IS NULL
    ALTER TABLE dbo.usuarios ADD telefone NVARCHAR(20) NULL;
GO

IF COL_LENGTH('dbo.usuarios', 'especialidade') IS NULL
    ALTER TABLE dbo.usuarios ADD especialidade NVARCHAR(120) NULL;
GO

IF COL_LENGTH('dbo.usuarios', 'foto_perfil') IS NULL
    ALTER TABLE dbo.usuarios ADD foto_perfil NVARCHAR(255) NULL;
GO
