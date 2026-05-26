USE [AgendaSimples];
GO

IF COL_LENGTH('dbo.barbearias', 'capa_catalogo1') IS NULL
    ALTER TABLE dbo.barbearias ADD capa_catalogo1 NVARCHAR(255) NULL;
IF COL_LENGTH('dbo.barbearias', 'capa_catalogo2') IS NULL
    ALTER TABLE dbo.barbearias ADD capa_catalogo2 NVARCHAR(255) NULL;
IF COL_LENGTH('dbo.barbearias', 'capa_catalogo3') IS NULL
    ALTER TABLE dbo.barbearias ADD capa_catalogo3 NVARCHAR(255) NULL;
IF COL_LENGTH('dbo.barbearias', 'capa_catalogo4') IS NULL
    ALTER TABLE dbo.barbearias ADD capa_catalogo4 NVARCHAR(255) NULL;
GO
