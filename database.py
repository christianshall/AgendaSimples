"""SQLite — conexão, schema e inicialização (Vercel + local)."""
import os
import sqlite3
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

# Vercel: filesystem efêmero — use /tmp; local: agenda.db na raiz do projeto
_DEFAULT_PATH = (
    os.path.join(os.environ.get("TMPDIR", "/tmp"), "agenda.db")
    if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV")
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "agenda.db")
)
DATABASE_PATH = os.environ.get("SQLITE_DATABASE_PATH", _DEFAULT_PATH)

DbError = sqlite3.Error


def get_connection():
    os.makedirs(os.path.dirname(os.path.abspath(DATABASE_PATH)), exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_exists(cursor, name):
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return cursor.fetchone() is not None


def init_database():
    """Cria tabelas e dados mínimos se o banco ainda não existir."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS barbearias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            slug TEXT UNIQUE,
            email TEXT,
            senha TEXT,
            plano_ativo INTEGER DEFAULT 1,
            titulo_catalogo1 TEXT,
            titulo_catalogo2 TEXT,
            titulo_catalogo3 TEXT,
            titulo_catalogo4 TEXT,
            logotipo_url TEXT,
            texto_marcar_direito TEXT,
            foto_fundo_direito_url TEXT,
            capa_catalogo1 TEXT,
            capa_catalogo2 TEXT,
            capa_catalogo3 TEXT,
            capa_catalogo4 TEXT,
            link_instagram TEXT,
            link_facebook TEXT,
            link_whatsapp TEXT,
            data_cadastro TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            email TEXT UNIQUE,
            telefone TEXT,
            senha TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'profissional',
            especialidade TEXT,
            foto_perfil TEXT
        );

        CREATE TABLE IF NOT EXISTS Clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            Nome TEXT NOT NULL,
            Dia TEXT NOT NULL,
            Hora TEXT NOT NULL,
            Servico TEXT,
            Whatsapp TEXT,
            barbeiro_id INTEGER,
            status TEXT NOT NULL DEFAULT 'Agendado',
            FOREIGN KEY (barbeiro_id) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS tb_galeria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER NOT NULL,
            categoria TEXT NOT NULL,
            caminho_foto TEXT NOT NULL,
            FOREIGN KEY (barbearia_id) REFERENCES barbearias(id)
        );

        CREATE TABLE IF NOT EXISTS tb_configuracoes (
            chave TEXT PRIMARY KEY,
            valor TEXT
        );

        CREATE TABLE IF NOT EXISTS financeiro (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            descricao TEXT,
            valor REAL NOT NULL,
            tipo_transacao TEXT NOT NULL,
            barbeiro TEXT,
            profissional_id INTEGER,
            data TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (profissional_id) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS assinaturas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER NOT NULL UNIQUE,
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            plano_status TEXT NOT NULL DEFAULT 'trialing',
            data_fim_trial TEXT,
            data_fim_plano TEXT,
            criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (barbearia_id) REFERENCES barbearias(id)
        );

        CREATE INDEX IF NOT EXISTS IX_Clientes_barbeiro_id ON Clientes(barbeiro_id);
        CREATE INDEX IF NOT EXISTS IX_Clientes_Dia_Hora_barbeiro ON Clientes(Dia, Hora, barbeiro_id);
        CREATE INDEX IF NOT EXISTS IX_assinaturas_stripe_customer ON assinaturas(stripe_customer_id);
        CREATE INDEX IF NOT EXISTS IX_assinaturas_stripe_subscription ON assinaturas(stripe_subscription_id);
        """
    )

    cursor.execute("SELECT COUNT(*) FROM barbearias")
    if cursor.fetchone()[0] == 0:
        senha_demo = generate_password_hash("admin123")
        cursor.execute(
            """
            INSERT INTO barbearias (
                nome, slug, email, senha, plano_ativo,
                titulo_catalogo1, titulo_catalogo2, titulo_catalogo3, titulo_catalogo4,
                texto_marcar_direito
            )
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                "AgendaSimples",
                "agendasimples",
                "admin@agendasimples.local",
                senha_demo,
                "Catálogo 1",
                "Catálogo 2",
                "Catálogo 3",
                "Catálogo 4",
                "AgendaSimples",
            ),
        )
        barbearia_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO usuarios (nome, email, senha, role)
            VALUES (?, ?, ?, 'admin')
            """,
            ("Administrador", "admin@agendasimples.local", senha_demo),
        )
        fim_trial = (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        agora = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            """
            INSERT INTO assinaturas (
                barbearia_id, plano_status, data_fim_trial, criado_em, atualizado_em
            )
            VALUES (?, 'trialing', ?, ?, ?)
            """,
            (barbearia_id, fim_trial, agora, agora),
        )

    conn.commit()
    conn.close()


def sql_now():
    """Expressão SQL para data/hora atual (SQLite)."""
    return "CURRENT_TIMESTAMP"
