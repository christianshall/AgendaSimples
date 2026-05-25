import sqlite3

def configurar_banco_multitenant():
    # Isso vai criar o arquivo banco.db automaticamente na mesma pasta
    conn = sqlite3.connect('banco.db')
    cursor = conn.cursor()

    print("Criando tabelas para o sistema Multi-Empresa...")

    # 1. TABELA DE BARBEARIAS (Os barbeiros que vão se cadastrar e pagar mensalidade)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS barbearias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome VARCHAR(100) NOT NULL,
            slug VARCHAR(100) UNIQUE NOT NULL, -- O link único do barbeiro (ex: silva-barber)
            email VARCHAR(100) UNIQUE NOT NULL,
            senha VARCHAR(255) NOT NULL,
            plano_ativo BOOLEAN DEFAULT 1,     -- 1 para Ativo, 0 para Bloqueado (se não pagar)
            data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 2. TABELA DE AGENDAMENTOS (Vinculada a cada barbearia)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS agendamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER NOT NULL,    -- Identifica de qual barbeiro é esse agendamento
            cliente_nome VARCHAR(100) NOT NULL,
            telefone VARCHAR(20) NOT NULL,
            servico VARCHAR(50) NOT NULL,
            data VARCHAR(10) NOT NULL,
            horario VARCHAR(5) NOT NULL,
            FOREIGN KEY (barbearia_id) REFERENCES barbearias(id)
        )
    ''')

    # 3. TABELA DA GALERIA INSTAGRAM (Vinculada a cada barbearia)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS galeria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER NOT NULL,    -- Identifica de qual barbeiro é essa foto
            categoria VARCHAR(50) NOT NULL,
            foto VARCHAR(255) NOT NULL,
            FOREIGN KEY (barbearia_id) REFERENCES barbearias(id)
        )
    ''')

    conn.commit()
    conn.close()
    print("Banco de dados Multi-Empresa configurado com sucesso!")

if __name__ == '__main__':
    configurar_banco_multitenant()