-- Migração: colunas do módulo financeiro (SQLite / Turso)
-- Execute se aparecer "no such column" ao abrir /admin_financeiro
-- No SQL Server, use os blocos IF COL_LENGTH abaixo.

-- SQLite / Turso (uma vez cada; ignore erro se a coluna já existir):
ALTER TABLE financeiro ADD COLUMN barbearia_id INTEGER;
ALTER TABLE financeiro ADD COLUMN agendamento_id INTEGER;
ALTER TABLE financeiro ADD COLUMN categoria TEXT;
ALTER TABLE financeiro ADD COLUMN servico TEXT;
ALTER TABLE financeiro ADD COLUMN produto TEXT;
ALTER TABLE financeiro ADD COLUMN tags TEXT;

-- SQL Server (Management Studio):
/*
IF COL_LENGTH('dbo.financeiro', 'barbearia_id') IS NULL
    ALTER TABLE dbo.financeiro ADD barbearia_id INT NULL;
IF COL_LENGTH('dbo.financeiro', 'agendamento_id') IS NULL
    ALTER TABLE dbo.financeiro ADD agendamento_id INT NULL;
IF COL_LENGTH('dbo.financeiro', 'categoria') IS NULL
    ALTER TABLE dbo.financeiro ADD categoria NVARCHAR(50) NULL;
IF COL_LENGTH('dbo.financeiro', 'servico') IS NULL
    ALTER TABLE dbo.financeiro ADD servico NVARCHAR(500) NULL;
IF COL_LENGTH('dbo.financeiro', 'produto') IS NULL
    ALTER TABLE dbo.financeiro ADD produto NVARCHAR(500) NULL;
*/
