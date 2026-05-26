USE [AgendaSimples];
GO

IF COL_LENGTH('dbo.barbearias', 'texto_marcar_direito') IS NULL
    ALTER TABLE dbo.barbearias ADD texto_marcar_direito NVARCHAR(150) NULL;
IF COL_LENGTH('dbo.barbearias', 'foto_fundo_direito_url') IS NULL
    ALTER TABLE dbo.barbearias ADD foto_fundo_direito_url NVARCHAR(255) NULL;
GO

PRINT 'Colunas da tela /marcar prontas.';
GO
