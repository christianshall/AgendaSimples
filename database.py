"""SQLite local ou Turso (libSQL) na nuvem — conexão, schema e inicialização."""
import os
import sqlite3
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

# Vercel: /tmp; local: agenda.db na raiz do projeto
_DEFAULT_PATH = (
    os.path.join(os.environ.get("TMPDIR", "/tmp"), "agenda.db")
    if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV")
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "agenda.db")
)
DATABASE_PATH = os.environ.get("SQLITE_DATABASE_PATH", _DEFAULT_PATH)

DbError = sqlite3.Error


def _turso_credentials():
    database_url = (os.environ.get("TURSO_DATABASE_URL") or "").strip()
    auth_token = (os.environ.get("TURSO_AUTH_TOKEN") or "").strip()
    return database_url, auth_token


def _use_turso():
    database_url, auth_token = _turso_credentials()
    return bool(database_url and auth_token)


class _CompatRow:
    """Linha compatível com sqlite3.Row (índice e atributo por nome de coluna)."""

    __slots__ = ("_values", "_keys")

    def __init__(self, values, keys):
        self._values = tuple(values)
        self._keys = list(keys)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        if key in self._keys:
            return self._values[self._keys.index(key)]
        for i, col in enumerate(self._keys):
            if col.lower() == str(key).lower():
                return self._values[i]
        raise KeyError(key)

    def __getattr__(self, name):
        for i, key in enumerate(self._keys):
            if key == name or key.lower() == name.lower():
                return self._values[i]
        raise AttributeError(name)

    def __len__(self):
        return len(self._values)

    def __iter__(self):
        return iter(self._values)


class _CompatCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = getattr(cursor, "lastrowid", None)

    def execute(self, sql, parameters=()):
        result = self._cursor.execute(sql, parameters)
        self.lastrowid = getattr(self._cursor, "lastrowid", None)
        return result

    def executemany(self, sql, parameters):
        return self._cursor.executemany(sql, parameters)

    def executescript(self, sql):
        if hasattr(self._cursor, "executescript"):
            return self._cursor.executescript(sql)
        for statement in sql.split(";"):
            chunk = statement.strip()
            if chunk:
                self._cursor.execute(chunk)
        return None

    def _wrap_row(self, row):
        if row is None:
            return None
        if isinstance(row, sqlite3.Row):
            return row
        desc = getattr(self._cursor, "description", None)
        if desc:
            keys = [col[0] for col in desc]
            return _CompatRow(row, keys)
        return row

    def fetchone(self):
        return self._wrap_row(self._cursor.fetchone())

    def fetchall(self):
        rows = self._cursor.fetchall()
        return [self._wrap_row(row) for row in rows]


class _CompatConnection:
    def __init__(self, conn, is_sqlite=False):
        self._conn = conn
        self._is_sqlite = is_sqlite

    def cursor(self):
        if self._is_sqlite:
            return self._conn.cursor()
        return _CompatCursor(self._conn.cursor())

    def execute(self, sql, parameters=()):
        if hasattr(self._conn, "execute"):
            return self._conn.execute(sql, parameters)
        cur = self.cursor()
        return cur.execute(sql, parameters)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        if hasattr(self._conn, "rollback"):
            self._conn.rollback()

    def close(self):
        self._conn.close()


def _connect_turso(database_url, auth_token):
    import libsql_experimental as libsql

    try:
        conn = libsql.connect(database_url, auth_token=auth_token)
    except TypeError:
        conn = libsql.connect(database=database_url, auth_token=auth_token)
    conn.execute("PRAGMA foreign_keys = ON")
    return _CompatConnection(conn, is_sqlite=False)


def _connect_sqlite():
    db_dir = os.path.dirname(os.path.abspath(DATABASE_PATH))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return _CompatConnection(conn, is_sqlite=True)


def get_connection():
    """
    Turso (nuvem): TURSO_DATABASE_URL + TURSO_AUTH_TOKEN via libsql_experimental.
    Local: arquivo agenda.db com sqlite3 nativo.
    """
    database_url, auth_token = _turso_credentials()
    if database_url and auth_token:
        return _connect_turso(database_url, auth_token)
    return _connect_sqlite()


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
    count_row = cursor.fetchone()
    total = count_row[0] if count_row else 0
    if total == 0:
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
    """Expressão SQL para data/hora atual (SQLite / Turso)."""
    return "CURRENT_TIMESTAMP"
