USE [AgendaSimples];
GO

IF COL_LENGTH('dbo.barbearias', 'link_instagram') IS NULL
    ALTER TABLE dbo.barbearias ADD link_instagram NVARCHAR(500) NULL;
IF COL_LENGTH('dbo.barbearias', 'link_facebook') IS NULL
    ALTER TABLE dbo.barbearias ADD link_facebook NVARCHAR(500) NULL;
IF COL_LENGTH('dbo.barbearias', 'link_whatsapp') IS NULL
    ALTER TABLE dbo.barbearias ADD link_whatsapp NVARCHAR(500) NULL;
GO
