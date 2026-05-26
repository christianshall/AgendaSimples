-- Catálogos dinâmicos da Home + logotipo (por barbearia)
USE [AgendaSimples];
GO

IF COL_LENGTH('dbo.barbearias', 'titulo_catalogo1') IS NULL
    ALTER TABLE dbo.barbearias ADD titulo_catalogo1 NVARCHAR(100) NULL;
IF COL_LENGTH('dbo.barbearias', 'titulo_catalogo2') IS NULL
    ALTER TABLE dbo.barbearias ADD titulo_catalogo2 NVARCHAR(100) NULL;
IF COL_LENGTH('dbo.barbearias', 'titulo_catalogo3') IS NULL
    ALTER TABLE dbo.barbearias ADD titulo_catalogo3 NVARCHAR(100) NULL;
IF COL_LENGTH('dbo.barbearias', 'titulo_catalogo4') IS NULL
    ALTER TABLE dbo.barbearias ADD titulo_catalogo4 NVARCHAR(100) NULL;
IF COL_LENGTH('dbo.barbearias', 'logotipo_url') IS NULL
    ALTER TABLE dbo.barbearias ADD logotipo_url NVARCHAR(500) NULL;
GO

UPDATE dbo.barbearias
SET
    titulo_catalogo1 = COALESCE(titulo_catalogo1, N'Corte'),
    titulo_catalogo2 = COALESCE(titulo_catalogo2, N'Corte e Barba'),
    titulo_catalogo3 = COALESCE(titulo_catalogo3, N'Corte + Sobrancelha'),
    titulo_catalogo4 = COALESCE(titulo_catalogo4, N'Outros Serviços');
GO

PRINT 'Migração home/catálogos concluída.';
GO
