USE [AgendaSimples];
GO

-- 1. Verifica se a tabela Agendamentos já existe para evitar erros
IF OBJECT_ID('Agendamentos', 'U') IS NOT NULL
    DROP TABLE Agendamentos;
GO

-- 2. Cria a tabela Agendamentos no esquema padrão (dbo)
CREATE TABLE Agendamentos (
    -- ID primário auto-incrementável (necessário para o SQLAlchemy)
    AgendamentoID INT IDENTITY(1,1) PRIMARY KEY,
    
    -- Campos mapeados na classe Agendamento do Flask
    Nome VARCHAR(100) NOT NULL,      -- Corresponde a NomeCliente
    Servico VARCHAR(100) NOT NULL,   -- Corresponde a ServicoDesc
    
    -- Campos de Data e Hora (Chave para a lógica de agendamento)
    DataHoraInicio DATETIME NOT NULL,
    DataHoraFim DATETIME NOT NULL,
    
    -- Campos de controle (Opcionais, mas incluídos no modelo Python)
    ClienteID INT NOT NULL DEFAULT 1,
    BarbeiroID INT NOT NULL DEFAULT 1,
    Status VARCHAR(50) NOT NULL DEFAULT 'Confirmado',
    
    -- Garante que não haja agendamentos duplicados no mesmo horário
    CONSTRAINT UQ_DataHoraInicio UNIQUE (DataHoraInicio)
);
GO