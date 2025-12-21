USE [AgendaSimples];
GO

-- 1. Verifica se a coluna barbeiro_id já existe
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('dbo.Clientes') AND name = 'barbeiro_id')
BEGIN
    -- 2. Adiciona a coluna barbeiro_id na tabela Clientes
    ALTER TABLE dbo.Clientes
    ADD barbeiro_id INT NULL;
    
    PRINT 'Coluna barbeiro_id adicionada com sucesso!';
END
ELSE
BEGIN
    PRINT 'Coluna barbeiro_id já existe!';
END
GO

-- 3. Se houver registros sem barbeiro_id, atribui ao primeiro barbeiro cadastrado
-- (Ajuste conforme necessário - você pode querer atribuir manualmente)
UPDATE dbo.Clientes
SET barbeiro_id = (SELECT TOP 1 id FROM usuarios WHERE role = 'barbeiro' ORDER BY id)
WHERE barbeiro_id IS NULL;
GO

-- 4. Torna a coluna NOT NULL após atualizar os registros existentes
-- (Descomente estas linhas após garantir que todos os registros têm barbeiro_id)
-- ALTER TABLE dbo.Clientes
-- ALTER COLUMN barbeiro_id INT NOT NULL;
-- GO

-- 5. Adiciona índice para melhorar performance das consultas
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Clientes_barbeiro_id' AND object_id = OBJECT_ID('dbo.Clientes'))
BEGIN
    CREATE INDEX IX_Clientes_barbeiro_id ON dbo.Clientes(barbeiro_id);
    PRINT 'Índice IX_Clientes_barbeiro_id criado com sucesso!';
END
GO

-- 6. Adiciona índice composto para melhorar consultas por data, hora e barbeiro
IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'IX_Clientes_Dia_Hora_barbeiro' AND object_id = OBJECT_ID('dbo.Clientes'))
BEGIN
    CREATE INDEX IX_Clientes_Dia_Hora_barbeiro ON dbo.Clientes(Dia, Hora, barbeiro_id);
    PRINT 'Índice IX_Clientes_Dia_Hora_barbeiro criado com sucesso!';
END
GO

PRINT 'Migração concluída!';
GO

