"""SQLite local ou Turso via HTTP (serverless) — conexão, schema e inicialização."""
import json
import os
import sqlite3
import traceback
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


def _turso_log_error(response, context, exc=None, payload=None):
    """Registra resposta completa do Turso nos logs (Vercel / stdout)."""
    print(f"=== Erro Turso [{context}] ===")
    if exc is not None:
        print(f"Exceção: {type(exc).__name__}: {exc}")
        traceback.print_exc()
    if payload is not None:
        try:
            print("Payload enviado:", json.dumps(payload, ensure_ascii=False)[:2000])
        except Exception:
            print("Payload enviado: (não serializável)")
    if response is not None:
        print("Erro Turso:", getattr(response, "text", str(response)))
        print("Status HTTP:", getattr(response, "status_code", "?"))
        print("Headers resposta:", dict(getattr(response, "headers", {})))
    else:
        print("Erro Turso: (sem objeto response — falha antes da resposta HTTP)")
    print("=== fim Erro Turso ===")


def _python_to_turso_arg(value):
    try:
        if value is None:
            return {"type": "null"}
        if isinstance(value, bool):
            return {"type": "integer", "value": str(int(value))}
        if isinstance(value, int):
            return {"type": "integer", "value": str(value)}
        if isinstance(value, float):
            return {"type": "float", "value": str(value)}
        return {"type": "text", "value": str(value)}
    except Exception as exc:
        raise sqlite3.OperationalError(
            f"Falha ao converter parâmetro SQL para Turso: {value!r} ({exc})"
        ) from exc


def _parse_turso_cell(cell):
    if not cell or not isinstance(cell, dict):
        return None
    kind = cell.get("type")
    if kind == "null":
        return None
    if kind == "integer":
        try:
            return int(cell.get("value", 0))
        except (TypeError, ValueError) as exc:
            raise sqlite3.OperationalError(
                f"Célula integer inválida no Turso: {cell!r} ({exc})"
            ) from exc
    if kind == "float":
        try:
            return float(cell.get("value", 0))
        except (TypeError, ValueError) as exc:
            raise sqlite3.OperationalError(
                f"Célula float inválida no Turso: {cell!r} ({exc})"
            ) from exc
    if kind == "text":
        return cell.get("value")
    if kind == "blob":
        import base64

        try:
            return base64.b64decode(cell.get("base64") or "")
        except Exception as exc:
            raise sqlite3.OperationalError(
                f"Célula blob inválida no Turso: {exc}"
            ) from exc
    return cell.get("value")


def _extract_execute_result(data, context="pipeline"):
    """Extrai o primeiro result de execute do JSON do Turso."""
    if not isinstance(data, dict):
        _turso_log_error(None, f"{context}: JSON não é objeto", payload={"data": data})
        raise sqlite3.OperationalError(
            f"Resposta Turso inválida (esperado objeto JSON): {type(data).__name__}"
        )

    results = data.get("results")
    if not isinstance(results, list):
        _turso_log_error(None, f"{context}: sem lista results", payload=data)
        raise sqlite3.OperationalError(
            "Resposta Turso sem campo 'results' ou formato inesperado."
        )

    for index, item in enumerate(results):
        if not isinstance(item, dict):
            print(f"Erro Turso: item results[{index}] não é dict: {item!r}")
            continue

        if item.get("type") == "error":
            _turso_log_error(None, f"{context}: results[{index}] error", payload=data)
            raise sqlite3.OperationalError(
                f"Turso retornou erro na operação {index}: {item}"
            )

        if item.get("type") == "ok":
            response = item.get("response") or {}
            if response.get("type") == "execute":
                result = response.get("result")
                if isinstance(result, dict):
                    return result
                print(
                    f"Erro Turso: execute sem result dict em results[{index}]:",
                    json.dumps(item, ensure_ascii=False)[:1500],
                )

    _turso_log_error(None, f"{context}: nenhum execute encontrado", payload=data)
    raise sqlite3.OperationalError(
        "Resposta Turso sem resultado 'execute' utilizável."
    )


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

    def _pipeline(self, requests_body, context="pipeline"):
        payload = {"requests": requests_body}
        if self._baton:
            payload["baton"] = self._baton

        response = None
        try:
            response = requests.post(
                self._pipeline_url,
                json=payload,
                headers=self._headers,
                timeout=60,
            )
        except requests.Timeout as exc:
            _turso_log_error(response, f"{context}: timeout", exc, payload)
            raise sqlite3.OperationalError(
                f"Turso HTTP timeout ao chamar {self._pipeline_url}"
            ) from exc
        except requests.RequestException as exc:
            _turso_log_error(response, f"{context}: request", exc, payload)
            raise sqlite3.OperationalError(
                f"Falha de rede ao conectar ao Turso: {exc}"
            ) from exc

        try:
            if response.status_code >= 400:
                _turso_log_error(response, f"{context}: HTTP {response.status_code}", payload=payload)
                raise sqlite3.OperationalError(
                    f"Turso HTTP {response.status_code}"
                )

            try:
                data = response.json()
            except json.JSONDecodeError as exc:
                _turso_log_error(response, f"{context}: JSON inválido", exc, payload)
                raise sqlite3.OperationalError(
                    "Turso retornou corpo que não é JSON válido."
                ) from exc

            if isinstance(data, dict) and data.get("baton"):
                self._baton = data["baton"]

            return _extract_execute_result(data, context=context) if self._is_execute_only(
                requests_body
            ) else data

        except sqlite3.OperationalError:
            raise
        except Exception as exc:
            _turso_log_error(response, f"{context}: inesperado", exc, payload)
            raise sqlite3.OperationalError(
                f"Erro inesperado ao processar resposta Turso: {exc}"
            ) from exc

    @staticmethod
    def _is_execute_only(requests_body):
        """True se o pipeline tem só executes (sem close) — retorna último result."""
        if not requests_body:
            return False
        return all(r.get("type") == "execute" for r in requests_body)

    def cursor(self):
        return TursoHttpCursor(self)

    def commit(self):
        try:
            self.cursor().execute("COMMIT")
        except Exception as exc:
            print("Erro Turso commit:", exc)
            traceback.print_exc()
            raise

    def rollback(self):
        try:
            self.cursor().execute("ROLLBACK")
        except Exception as exc:
            print("Erro Turso rollback:", exc)
            traceback.print_exc()
            raise

    def close(self):
        if not self._baton:
            return
        try:
            payload = {"requests": [{"type": "close"}], "baton": self._baton}
            response = requests.post(
                self._pipeline_url,
                json=payload,
                headers=self._headers,
                timeout=30,
            )
            if response.status_code >= 400:
                _turso_log_error(response, "close", payload=payload)
        except Exception as exc:
            print("Erro Turso close (ignorado):", exc)
        finally:
            self._baton = None


class TursoHttpCursor:
    def __init__(self, connection):
        self._conn = connection
        self.lastrowid = None
        self.description = None
        self._rows = []
        self._row_index = 0

    def _apply_result(self, result):
        try:
            if not isinstance(result, dict):
                raise sqlite3.OperationalError(
                    f"Resultado Turso não é dict: {type(result).__name__}"
                )

            self.lastrowid = result.get("last_insert_rowid")
            if self.lastrowid is not None:
                try:
                    self.lastrowid = int(self.lastrowid)
                except (TypeError, ValueError):
                    pass

            cols = result.get("cols") or []
            self.description = []
            for col in cols:
                if isinstance(col, dict):
                    self.description.append((col.get("name", "?"), None, None))
                elif isinstance(col, str):
                    self.description.append((col, None, None))
                else:
                    self.description.append(("?", None, None))

            self._rows = []
            for row_index, row in enumerate(result.get("rows") or []):
                try:
                    if not isinstance(row, (list, tuple)):
                        raise sqlite3.OperationalError(
                            f"Linha {row_index} inválida: {row!r}"
                        )
                    self._rows.append(
                        tuple(_parse_turso_cell(cell) for cell in row)
                    )
                except Exception as exc:
                    print(f"Erro Turso ao parsear linha {row_index}: {row!r}")
                    traceback.print_exc()
                    raise
            self._row_index = 0

        except sqlite3.OperationalError:
            raise
        except Exception as exc:
            print("Erro Turso _apply_result:", result)
            traceback.print_exc()
            raise sqlite3.OperationalError(
                f"Falha ao interpretar resultado Turso: {exc}"
            ) from exc

    def execute(self, sql, parameters=()):
        try:
            args = (
                [_python_to_turso_arg(p) for p in parameters] if parameters else []
            )
            stmt = {"sql": sql}
            if args:
                stmt["args"] = args

            result = self._conn._pipeline(
                [{"type": "execute", "stmt": stmt}],
                context=f"execute: {sql[:80]}",
            )
            if isinstance(result, dict):
                self._apply_result(result)
            return self

        except sqlite3.OperationalError:
            raise
        except Exception as exc:
            print(f"Erro Turso execute SQL: {sql[:200]}")
            traceback.print_exc()
            raise sqlite3.OperationalError(
                f"Falha ao executar SQL no Turso: {exc}"
            ) from exc

    def executemany(self, sql, seq_of_parameters):
        for parameters in seq_of_parameters:
            self.execute(sql, parameters)
        return self

    def executescript(self, sql):
        statements = []
        for statement in sql.split(";"):
            chunk = statement.strip()
            if chunk:
                statements.append(chunk)

        if not statements:
            return self

        try:
            requests_body = [
                {"type": "execute", "stmt": {"sql": chunk}} for chunk in statements
            ]
            payload = {"requests": requests_body}
            if self._conn._baton:
                payload["baton"] = self._conn._baton

            response = None
            try:
                response = requests.post(
                    self._conn._pipeline_url,
                    json=payload,
                    headers=self._conn._headers,
                    timeout=120,
                )
            except requests.RequestException as exc:
                _turso_log_error(response, "executescript: request", exc, payload)
                raise sqlite3.OperationalError(
                    f"Falha de rede no executescript Turso: {exc}"
                ) from exc

            if response.status_code >= 400:
                _turso_log_error(response, "executescript: HTTP", payload=payload)
                raise sqlite3.OperationalError(
                    f"Turso HTTP {response.status_code} no executescript"
                )

            try:
                data = response.json()
            except json.JSONDecodeError as exc:
                _turso_log_error(response, "executescript: JSON", exc, payload)
                raise sqlite3.OperationalError(
                    "Turso executescript: resposta não é JSON."
                ) from exc

            if isinstance(data, dict) and data.get("baton"):
                self._conn._baton = data["baton"]

            results = data.get("results") or []
            last_execute = None
            for item in results:
                if (
                    isinstance(item, dict)
                    and item.get("type") == "ok"
                    and (item.get("response") or {}).get("type") == "execute"
                ):
                    last_execute = (item.get("response") or {}).get("result") or {}

            if isinstance(last_execute, dict):
                self._apply_result(last_execute)

            return self

        except sqlite3.OperationalError:
            raise
        except Exception as exc:
            print("Erro Turso executescript")
            traceback.print_exc()
            raise sqlite3.OperationalError(
                f"Falha no executescript Turso: {exc}"
            ) from exc

    def _wrap_row(self, row):
        if row is None:
            return None
        if self.description:
            keys = [col[0] for col in self.description]
            return _CompatRow(row, keys)
        return row

    def fetchone(self):
        try:
            if self._row_index >= len(self._rows):
                return None
            row = self._rows[self._row_index]
            self._row_index += 1
            return self._wrap_row(row)
        except Exception as exc:
            print("Erro Turso fetchone:", exc)
            traceback.print_exc()
            raise

    def fetchall(self):
        try:
            remaining = self._rows[self._row_index :]
            self._row_index = len(self._rows)
            return [self._wrap_row(row) for row in remaining]
        except Exception as exc:
            print("Erro Turso fetchall:", exc)
            traceback.print_exc()
            raise


def _connect_turso_http(database_url, auth_token):
    try:
        pipeline_url = _turso_pipeline_url(database_url)
        print(f"Turso HTTP: conectando em {pipeline_url[:60]}...")
        return TursoHttpConnection(pipeline_url, auth_token)
    except Exception as exc:
        print("Erro Turso _connect_turso_http:", exc)
        traceback.print_exc()
        raise sqlite3.OperationalError(
            f"Não foi possível configurar cliente Turso HTTP: {exc}"
        ) from exc


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


def _coluna_existe(cursor, tabela, coluna):
    cursor.execute(f"PRAGMA table_info({tabela})")
    return any(row[1] == coluna for row in cursor.fetchall())


SERVICOS_PADRAO = (
    "Corte",
    "Corte e Barba",
    "Corte + Sobrancelha",
    "Barba",
    "Outro",
)

HORARIOS_PADRAO = (
    "08:00", "09:00", "10:00", "11:00",
    "12:00", "13:00", "14:00", "15:00",
    "16:00", "17:00", "18:00", "19:00", "20:00",
)


def seed_servicos_horarios_padrao(cursor, barbearia_id):
    """Cria serviços e horários padrão para um estabelecimento novo."""
    cursor.execute(
        "SELECT COUNT(*) FROM servicos WHERE barbearia_id = ?",
        (barbearia_id,),
    )
    if (cursor.fetchone() or [0])[0] == 0:
        for ordem, nome in enumerate(SERVICOS_PADRAO):
            cursor.execute(
                """
                INSERT INTO servicos (barbearia_id, nome, ativo, ordem)
                VALUES (?, ?, 1, ?)
                """,
                (barbearia_id, nome, ordem),
            )

    cursor.execute(
        "SELECT COUNT(*) FROM horarios WHERE barbearia_id = ?",
        (barbearia_id,),
    )
    if (cursor.fetchone() or [0])[0] == 0:
        for ordem, hora in enumerate(HORARIOS_PADRAO):
            cursor.execute(
                """
                INSERT INTO horarios (barbearia_id, hora, ativo, ordem)
                VALUES (?, ?, 1, ?)
                """,
                (barbearia_id, hora, ordem),
            )


def _barbearia_id_padrao(cursor):
    cursor.execute("SELECT id FROM barbearias ORDER BY id LIMIT 1")
    row = cursor.fetchone()
    return row[0] if row else 1


def backfill_barbearia_id(cursor):
    """Preenche barbearia_id em registros antigos (pré multi-tenant)."""
    padrao = _barbearia_id_padrao(cursor)
    try:
        cursor.execute(
            "UPDATE usuarios SET barbearia_id = ? WHERE barbearia_id IS NULL",
            (padrao,),
        )
        cursor.execute(
            """
            UPDATE Clientes
            SET barbearia_id = (
                SELECT u.barbearia_id FROM usuarios u
                WHERE u.id = Clientes.barbeiro_id
            )
            WHERE barbearia_id IS NULL AND barbeiro_id IS NOT NULL
            """,
        )
        cursor.execute(
            "UPDATE Clientes SET barbearia_id = ? WHERE barbearia_id IS NULL",
            (padrao,),
        )
        cursor.execute(
            """
            UPDATE financeiro
            SET barbearia_id = (
                SELECT u.barbearia_id FROM usuarios u
                WHERE u.id = financeiro.profissional_id
            )
            WHERE barbearia_id IS NULL AND profissional_id IS NOT NULL
            """,
        )
        cursor.execute(
            "UPDATE financeiro SET barbearia_id = ? WHERE barbearia_id IS NULL",
            (padrao,),
        )
    except Exception as exc:
        print(f"backfill_barbearia_id: {exc}")


def ensure_schema_migrations(cursor):
    """Adiciona colunas e tabelas novas em bancos já existentes (SQLite / Turso)."""
    alteracoes = [
        ("usuarios", "data_cadastro", "TEXT"),
        ("usuarios", "status_trial", "TEXT"),
        ("usuarios", "barbearia_id", "INTEGER"),
        ("Clientes", "barbearia_id", "INTEGER"),
        ("financeiro", "barbearia_id", "INTEGER"),
        ("barbearias", "ramo_atividade", "TEXT"),
    ]
    for tabela, coluna, tipo_sql in alteracoes:
        try:
            if not _coluna_existe(cursor, tabela, coluna):
                cursor.execute(
                    f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo_sql}"
                )
        except Exception as exc:
            print(f"ensure_schema_migrations ({tabela}.{coluna}): {exc}")

    try:
        cursor.executescript(
            """
        CREATE TABLE IF NOT EXISTS servicos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            ativo INTEGER DEFAULT 1,
            ordem INTEGER DEFAULT 0,
            FOREIGN KEY (barbearia_id) REFERENCES barbearias(id)
        );

        CREATE TABLE IF NOT EXISTS horarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER NOT NULL,
            hora TEXT NOT NULL,
            ativo INTEGER DEFAULT 1,
            ordem INTEGER DEFAULT 0,
            FOREIGN KEY (barbearia_id) REFERENCES barbearias(id)
        );

        CREATE INDEX IF NOT EXISTS IX_servicos_barbearia ON servicos(barbearia_id);
        CREATE INDEX IF NOT EXISTS IX_horarios_barbearia ON horarios(barbearia_id);
        CREATE INDEX IF NOT EXISTS IX_usuarios_barbearia ON usuarios(barbearia_id);
        CREATE INDEX IF NOT EXISTS IX_Clientes_barbearia ON Clientes(barbearia_id);
        CREATE INDEX IF NOT EXISTS IX_financeiro_barbearia ON financeiro(barbearia_id);
        """
        )
    except Exception as exc:
        print(f"ensure_schema_migrations (servicos/horarios): {exc}")

    backfill_barbearia_id(cursor)

    indices_tenant = [
        "CREATE INDEX IF NOT EXISTS IX_Clientes_barbearia ON Clientes(barbearia_id)",
        "CREATE INDEX IF NOT EXISTS IX_servicos_barbearia ON servicos(barbearia_id)",
        "CREATE INDEX IF NOT EXISTS IX_horarios_barbearia ON horarios(barbearia_id)",
        "CREATE INDEX IF NOT EXISTS IX_usuarios_barbearia ON usuarios(barbearia_id)",
        "CREATE INDEX IF NOT EXISTS IX_financeiro_barbearia ON financeiro(barbearia_id)",
    ]
    for sql_idx in indices_tenant:
        try:
            cursor.execute(sql_idx)
        except Exception as exc:
            print(f"ensure_schema_migrations (índice): {exc}")


def init_database():
    """Cria tabelas e dados mínimos se o banco ainda não existir."""
    try:
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
            data_cadastro TEXT DEFAULT CURRENT_TIMESTAMP,
            ramo_atividade TEXT
        );

        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER,
            nome TEXT NOT NULL,
            email TEXT UNIQUE,
            telefone TEXT,
            senha TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'profissional',
            especialidade TEXT,
            foto_perfil TEXT,
            data_cadastro TEXT DEFAULT CURRENT_TIMESTAMP,
            status_trial TEXT DEFAULT 'trialing',
            FOREIGN KEY (barbearia_id) REFERENCES barbearias(id)
        );

        CREATE TABLE IF NOT EXISTS servicos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            ativo INTEGER DEFAULT 1,
            ordem INTEGER DEFAULT 0,
            FOREIGN KEY (barbearia_id) REFERENCES barbearias(id)
        );

        CREATE TABLE IF NOT EXISTS horarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER NOT NULL,
            hora TEXT NOT NULL,
            ativo INTEGER DEFAULT 1,
            ordem INTEGER DEFAULT 0,
            FOREIGN KEY (barbearia_id) REFERENCES barbearias(id)
        );

        CREATE TABLE IF NOT EXISTS Clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER,
            Nome TEXT NOT NULL,
            Dia TEXT NOT NULL,
            Hora TEXT NOT NULL,
            Servico TEXT,
            Whatsapp TEXT,
            barbeiro_id INTEGER,
            status TEXT NOT NULL DEFAULT 'Agendado',
            FOREIGN KEY (barbearia_id) REFERENCES barbearias(id),
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
            barbearia_id INTEGER,
            descricao TEXT,
            valor REAL NOT NULL,
            tipo_transacao TEXT NOT NULL,
            barbeiro TEXT,
            profissional_id INTEGER,
            data TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (barbearia_id) REFERENCES barbearias(id),
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

        ensure_schema_migrations(cursor)

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
            INSERT INTO usuarios (nome, email, senha, role, barbearia_id, status_trial)
            VALUES (?, ?, ?, 'admin', ?, 'trialing')
            """,
                (
                    "Administrador",
                    "admin@agendasimples.local",
                    senha_demo,
                    barbearia_id,
                ),
            )
            seed_servicos_horarios_padrao(cursor, barbearia_id)
            fim_trial = (datetime.utcnow() + timedelta(days=7)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
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
        print("init_database: OK")

    except Exception as exc:
        print("=== Erro init_database (app continuará carregando) ===")
        print(f"init_database: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        print("=== fim Erro init_database ===")


def sql_now():
    """Expressão SQL para data/hora atual (SQLite / Turso)."""
    return "CURRENT_TIMESTAMP"
