"""SQLite local ou Turso via HTTP (serverless) — conexão, schema e inicialização."""
import os
import sqlite3
from datetime import datetime, timedelta

import requests
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


def _turso_pipeline_url(database_url):
    """Converte libsql://... em https://.../v2/pipeline."""
    url = database_url.strip()
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://") :]
    elif url.startswith("http://"):
        url = "https://" + url[len("http://") :]
    url = url.rstrip("/")
    if not url.endswith("/v2/pipeline"):
        url = f"{url}/v2/pipeline"
    return url


def _python_to_turso_arg(value):
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": str(value)}
    return {"type": "text", "value": str(value)}


def _parse_turso_cell(cell):
    if not cell:
        return None
    kind = cell.get("type")
    if kind == "null":
        return None
    if kind == "integer":
        return int(cell["value"])
    if kind == "float":
        return float(cell["value"])
    if kind == "text":
        return cell["value"]
    if kind == "blob":
        import base64

        return base64.b64decode(cell["base64"])
    return cell.get("value")


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


class TursoHttpConnection:
    """Cliente Turso SQL-over-HTTP (sem drivers nativos — compatível com Vercel)."""

    def __init__(self, pipeline_url, auth_token):
        self._pipeline_url = pipeline_url
        self._auth_token = auth_token
        self._baton = None
        self._headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        }

    def _pipeline(self, requests_body):
        payload = {"requests": requests_body}
        if self._baton:
            payload["baton"] = self._baton

        response = requests.post(
            self._pipeline_url,
            json=payload,
            headers=self._headers,
            timeout=60,
        )
        if response.status_code >= 400:
            raise sqlite3.OperationalError(
                f"Turso HTTP {response.status_code}: {response.text[:500]}"
            )

        data = response.json()
        if data.get("baton"):
            self._baton = data["baton"]

        for item in data.get("results", []):
            if item.get("type") == "error":
                raise sqlite3.OperationalError(str(item))

        return data

    def cursor(self):
        return TursoHttpCursor(self)

    def commit(self):
        self.cursor().execute("COMMIT")

    def rollback(self):
        self.cursor().execute("ROLLBACK")

    def close(self):
        if self._baton:
            try:
                self._pipeline([{"type": "close"}])
            except requests.RequestException:
                pass
            self._baton = None


class TursoHttpCursor:
    def __init__(self, connection):
        self._conn = connection
        self.lastrowid = None
        self.description = None
        self._rows = []
        self._row_index = 0

    def _apply_result(self, result):
        self.lastrowid = result.get("last_insert_rowid")
        if self.lastrowid is not None:
            try:
                self.lastrowid = int(self.lastrowid)
            except (TypeError, ValueError):
                pass

        cols = result.get("cols") or []
        self.description = [(col["name"], None, None) for col in cols]

        self._rows = []
        for row in result.get("rows") or []:
            self._rows.append(
                tuple(_parse_turso_cell(cell) for cell in row)
            )
        self._row_index = 0

    def execute(self, sql, parameters=()):
        args = [_python_to_turso_arg(p) for p in parameters] if parameters else []
        stmt = {"sql": sql}
        if args:
            stmt["args"] = args

        data = self._conn._pipeline([{"type": "execute", "stmt": stmt}])

        result = {}
        for item in data.get("results", []):
            if item.get("type") == "ok":
                response = item.get("response") or {}
                if response.get("type") == "execute":
                    result = response.get("result") or {}
                    break

        self._apply_result(result)
        return self

    def executemany(self, sql, seq_of_parameters):
        for parameters in seq_of_parameters:
            self.execute(sql, parameters)
        return self

    def executescript(self, sql):
        for statement in sql.split(";"):
            chunk = statement.strip()
            if chunk:
                self.execute(chunk)
        return self

    def _wrap_row(self, row):
        if row is None:
            return None
        if self.description:
            keys = [col[0] for col in self.description]
            return _CompatRow(row, keys)
        return row

    def fetchone(self):
        if self._row_index >= len(self._rows):
            return None
        row = self._rows[self._row_index]
        self._row_index += 1
        return self._wrap_row(row)

    def fetchall(self):
        remaining = self._rows[self._row_index :]
        self._row_index = len(self._rows)
        return [self._wrap_row(row) for row in remaining]


def _connect_turso_http(database_url, auth_token):
    pipeline_url = _turso_pipeline_url(database_url)
    return TursoHttpConnection(pipeline_url, auth_token)


def _connect_sqlite():
    db_dir = os.path.dirname(os.path.abspath(DATABASE_PATH))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_connection():
    """
    Turso (nuvem): TURSO_DATABASE_URL + TURSO_AUTH_TOKEN via SQL over HTTP (requests).
    Local: arquivo agenda.db com sqlite3 nativo quando as variáveis não existem.
    """
    database_url, auth_token = _turso_credentials()
    if database_url and auth_token:
        return _connect_turso_http(database_url, auth_token)
    return _connect_sqlite()


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
