"""SQLite local ou Turso/libSQL — conexão, schema e inicialização."""
import json
import os
import sqlite3
import traceback
from datetime import datetime, timedelta

import requests
from werkzeug.security import generate_password_hash

try:
    import libsql  # type: ignore
except Exception:
    libsql = None

# SQLite local apenas em desenvolvimento.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_LOCAL_SQLITE_DEFAULT = os.path.join(_BASE_DIR, "database.db")
_LEGACY_SQLITE_PATH = os.path.join(_BASE_DIR, "agenda.db")
DATABASE_PATH = os.environ.get("SQLITE_DATABASE_PATH", _LOCAL_SQLITE_DEFAULT)

DbError = sqlite3.Error


def is_producao_remota():
    """True quando o deploy deve usar banco remoto (Vercel / TURSO_DATABASE_URL)."""
    if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"):
        return True
    if (os.environ.get("TURSO_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip():
        return True
    if os.environ.get("FORCE_REMOTE_DATABASE", "").lower() in ("1", "true", "yes"):
        return True
    return False


def _turso_credentials():
    """
    URL e token do Turso/LibSQL.
    Prioriza TURSO_DATABASE_URL e TURSO_AUTH_TOKEN.
    Aceita aliases legados para retrocompatibilidade.
    Token: TURSO_AUTH_TOKEN, DATABASE_AUTH_TOKEN ou LIBSQL_AUTH_TOKEN.
    """
    # Prioridade: TURSO_DATABASE_URL.
    database_url = (
        os.environ.get("TURSO_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or os.environ.get("LIBSQL_URL")
        or ""
    ).strip()
    auth_token = (
        os.environ.get("TURSO_AUTH_TOKEN")
        or os.environ.get("DATABASE_AUTH_TOKEN")
        or os.environ.get("LIBSQL_AUTH_TOKEN")
        or ""
    ).strip()
    return database_url, auth_token


def usar_banco_remoto():
    """Indica se get_connection() usará Turso HTTP (não arquivo .db local)."""
    url, token = _turso_credentials()
    return bool(url and token)


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
        """Turso HTTP confirma cada execute no pipeline; COMMIT explícito é opcional."""
        try:
            self.cursor().execute("COMMIT")
        except Exception as exc:
            print("Turso commit (ignorado — HTTP já persiste por request):", exc)

    def rollback(self):
        """Sem transação multi-request no cliente HTTP."""
        print("Turso rollback: no-op no cliente HTTP")

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

            lid = (
                result.get("last_insert_rowid")
                or result.get("lastInsertRowid")
                or result.get("last_insert_id")
            )
            self.lastrowid = lid
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


def _connect_turso_libsql(database_url, auth_token):
    if libsql is None:
        raise sqlite3.OperationalError(
            "Biblioteca libsql indisponível no ambiente."
        )
    try:
        conn = libsql.connect(database=database_url, auth_token=auth_token)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as exc:
        raise sqlite3.OperationalError(
            f"Não foi possível conectar ao Turso via libsql: {exc}"
        ) from exc


def _connect_sqlite():
    database_path = DATABASE_PATH
    if (
        database_path == _LOCAL_SQLITE_DEFAULT
        and not os.path.exists(database_path)
        and os.path.exists(_LEGACY_SQLITE_PATH)
    ):
        database_path = _LEGACY_SQLITE_PATH
    db_dir = os.path.dirname(os.path.abspath(database_path))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(database_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _get_connection_sqlite_turso():
    """
    Produção (Vercel): TURSO_DATABASE_URL + TURSO_AUTH_TOKEN.
    Desenvolvimento: fallback local em database.db.
    """
    database_url, auth_token = _turso_credentials()
    if database_url and auth_token:
        try:
            conn = _connect_turso_libsql(database_url, auth_token)
            print("Conectado ao Turso em Produção")
            return conn
        except Exception as exc:
            print(f"Falha libsql, tentando fallback HTTP do Turso: {exc}")
            conn = _connect_turso_http(database_url, auth_token)
            print("Conectado ao Turso em Produção")
            return conn
    if is_producao_remota():
        raise sqlite3.OperationalError(
            "Banco remoto obrigatório em produção. Configure TURSO_DATABASE_URL "
            "(libsql://...) e TURSO_AUTH_TOKEN na Vercel."
        )
    print("Usando Banco Local em Desenvolvimento")
    return _connect_sqlite()


def get_connection():
    """Roteia para SQL Server (local) ou SQLite/Turso via db_adapter."""
    try:
        from db_adapter import get_backend, open_sqlserver_connection

        if get_backend() == "sqlserver":
            return open_sqlserver_connection()
    except ImportError:
        pass
    return _get_connection_sqlite_turso()


def _valor_linha(row, indice=0, nome=None):
    if row is None:
        return None
    if nome is not None:
        try:
            return row[nome]
        except (KeyError, TypeError, IndexError):
            try:
                return getattr(row, nome)
            except AttributeError:
                return None
        return None
    try:
        return row[indice]
    except (KeyError, TypeError, IndexError):
        return None


def _coluna_existe(cursor, tabela, coluna):
    """Detecta coluna via PRAGMA ou probe SELECT (compatível Turso HTTP)."""
    try:
        cursor.execute(f"PRAGMA table_info({tabela})")
        rows = cursor.fetchall()
        if rows:
            for row in rows:
                nome_col = _valor_linha(row, 1, "name")
                if nome_col == coluna:
                    return True
    except Exception as exc:
        print(f"_coluna_existe PRAGMA ({tabela}.{coluna}): {exc}")

    try:
        cursor.execute(f"SELECT {coluna} FROM {tabela} LIMIT 0")
        return True
    except Exception:
        return False


def obter_id_inserido(cursor, sql_fallback=None, params_fallback=None):
    """
    Obtém ID após INSERT — Turso HTTP pode não expor lastrowid de forma confiável.
    Ordem: cursor.lastrowid → last_insert_rowid() → consulta fallback.
    """
    if cursor.lastrowid not in (None, 0):
        try:
            return int(cursor.lastrowid)
        except (TypeError, ValueError):
            pass

    try:
        cursor.execute("SELECT last_insert_rowid() AS id")
        row = cursor.fetchone()
        val = _valor_linha(row, 0, "id")
        if val not in (None, 0):
            return int(val)
    except Exception as exc:
        print(f"obter_id_inserido last_insert_rowid(): {exc}")

    if sql_fallback:
        try:
            cursor.execute(sql_fallback, params_fallback or ())
            row = cursor.fetchone()
            val = _valor_linha(row, 0, "id")
            if val not in (None, 0):
                return int(val)
        except Exception as exc:
            print(f"obter_id_inserido fallback: {exc}")

    return None


def safe_commit(conn):
    if conn is None:
        return
    try:
        conn.commit()
    except Exception as exc:
        print(f"safe_commit: {exc}")
        traceback.print_exc()


def safe_rollback(conn):
    if conn is None:
        return
    try:
        conn.rollback()
    except Exception as exc:
        print(f"safe_rollback: {exc}")


def safe_close(conn):
    if conn is None:
        return
    try:
        conn.close()
    except Exception as exc:
        print(f"safe_close: {exc}")


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
    if not _coluna_existe(cursor, "usuarios", "barbearia_id"):
        return

    padrao = _barbearia_id_padrao(cursor)
    try:
        # Profissionais: inferir pelo tenant dos agendamentos já existentes
        cursor.execute(
            """
            UPDATE usuarios
            SET barbearia_id = (
                SELECT c.barbearia_id FROM Clientes c
                WHERE c.barbeiro_id = usuarios.id
                  AND c.barbearia_id IS NOT NULL
                LIMIT 1
            )
            WHERE barbearia_id IS NULL
              AND role IN ('barbeiro', 'profissional')
            """
        )

        # Admins sem vínculo: barbearia com mesmo e-mail
        if _coluna_existe(cursor, "barbearias", "email"):
            cursor.execute(
                """
                UPDATE usuarios
                SET barbearia_id = (
                    SELECT b.id FROM barbearias b
                    WHERE LOWER(TRIM(b.email)) = LOWER(TRIM(usuarios.email))
                    LIMIT 1
                )
                WHERE barbearia_id IS NULL AND role = 'admin'
                  AND email IS NOT NULL AND TRIM(email) <> ''
                """
            )

        # Por barbearia: profissionais órfãos herdam o admin do mesmo negócio
        cursor.execute(
            """
            SELECT DISTINCT barbearia_id FROM usuarios
            WHERE role = 'admin' AND barbearia_id IS NOT NULL
            """
        )
        for row in cursor.fetchall() or []:
            bid = _valor_linha(row, 0)
            if bid is None:
                continue
            cursor.execute(
                """
                UPDATE usuarios
                SET barbearia_id = ?
                WHERE barbearia_id IS NULL
                  AND role IN ('barbeiro', 'profissional')
                  AND id IN (
                    SELECT DISTINCT barbeiro_id FROM Clientes
                    WHERE barbearia_id = ? AND barbeiro_id IS NOT NULL
                  )
                """,
                (bid, bid),
            )

        # Único tenant: profissionais restantes recebem a barbearia do admin
        cursor.execute(
            """
            SELECT COUNT(DISTINCT barbearia_id) FROM usuarios
            WHERE role = 'admin' AND barbearia_id IS NOT NULL
            """
        )
        n_tenants = (_valor_linha(cursor.fetchone(), 0) or 0) or 0
        if n_tenants == 1:
            cursor.execute(
                """
                SELECT barbearia_id FROM usuarios
                WHERE role = 'admin' AND barbearia_id IS NOT NULL
                LIMIT 1
                """
            )
            admin_row = cursor.fetchone()
            admin_bid = _valor_linha(admin_row, 0) if admin_row else padrao
            cursor.execute(
                """
                UPDATE usuarios
                SET barbearia_id = ?
                WHERE barbearia_id IS NULL
                  AND role IN ('barbeiro', 'profissional')
                """,
                (admin_bid,),
            )

        # Demais usuários (admin antigo sem match): primeira barbearia
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


def vincular_usuarios_orfaos_por_email_nome(cursor):
    """
    Preenche usuarios.barbearia_id NULL usando e-mail, agendamentos, nome e tenant único.
    Retorna quantidade de usuários que ainda estão órfãos.
    """
    if not _coluna_existe(cursor, "usuarios", "barbearia_id"):
        return -1

    backfill_barbearia_id(cursor)

    try:
        if _coluna_existe(cursor, "barbearias", "email"):
            cursor.execute(
                """
                UPDATE usuarios
                SET barbearia_id = (
                    SELECT b.id FROM barbearias b
                    WHERE LOWER(TRIM(b.email)) = LOWER(TRIM(usuarios.email))
                    LIMIT 1
                )
                WHERE barbearia_id IS NULL
                  AND email IS NOT NULL AND TRIM(email) <> ''
                """
            )

        cursor.execute(
            """
            UPDATE usuarios
            SET barbearia_id = (
                SELECT c.barbearia_id FROM Clientes c
                WHERE c.barbeiro_id = usuarios.id
                  AND c.barbearia_id IS NOT NULL
                LIMIT 1
            )
            WHERE barbearia_id IS NULL
              AND role IN ('barbeiro', 'profissional')
            """
        )

        if _coluna_existe(cursor, "barbearias", "nome"):
            cursor.execute(
                """
                UPDATE usuarios
                SET barbearia_id = (
                    SELECT b.id FROM barbearias b
                    WHERE LOWER(TRIM(b.nome)) LIKE '%' || LOWER(TRIM(usuarios.nome)) || '%'
                       OR LOWER(TRIM(usuarios.nome)) LIKE '%' || LOWER(TRIM(b.nome)) || '%'
                    LIMIT 1
                )
                WHERE barbearia_id IS NULL
                  AND role IN ('barbeiro', 'profissional')
                  AND nome IS NOT NULL AND TRIM(nome) <> ''
                """
            )

        cursor.execute(
            """
            SELECT COUNT(DISTINCT barbearia_id) FROM usuarios
            WHERE role = 'admin' AND barbearia_id IS NOT NULL
            """
        )
        n_tenants = (_valor_linha(cursor.fetchone(), 0) or 0) or 0
        if n_tenants == 1:
            padrao = _barbearia_id_padrao(cursor)
            cursor.execute(
                """
                SELECT barbearia_id FROM usuarios
                WHERE role = 'admin' AND barbearia_id IS NOT NULL
                LIMIT 1
                """
            )
            admin_row = cursor.fetchone()
            admin_bid = _valor_linha(admin_row, 0) if admin_row else padrao
            cursor.execute(
                """
                UPDATE usuarios
                SET barbearia_id = ?
                WHERE barbearia_id IS NULL
                """,
                (admin_bid,),
            )
    except Exception as exc:
        print(f"vincular_usuarios_orfaos_por_email_nome: {exc}")

    cursor.execute(
        "SELECT COUNT(*) FROM usuarios WHERE barbearia_id IS NULL"
    )
    row = cursor.fetchone()
    return int(_valor_linha(row, 0) or 0)


def vincular_usuario_barbearia(cursor, user_id):
    """
    Tenta preencher barbearia_id de um usuário (ex.: profissional órfão).
    Retorna barbearia_id ou None.
    """
    if not _coluna_existe(cursor, "usuarios", "barbearia_id"):
        return None

    cursor.execute(
        "SELECT barbearia_id, role, email FROM usuarios WHERE id = ?",
        (user_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None

    atual = _valor_linha(row, 0, "barbearia_id")
    if atual is not None:
        return int(atual)

    role = (_valor_linha(row, 1, "role") or "").strip().lower()
    email = (_valor_linha(row, 2, "email") or "").strip()

    if role in ("barbeiro", "profissional"):
        cursor.execute(
            """
            SELECT c.barbearia_id FROM Clientes c
            WHERE c.barbeiro_id = ? AND c.barbearia_id IS NOT NULL
            LIMIT 1
            """,
            (user_id,),
        )
        ag = cursor.fetchone()
        if ag and _valor_linha(ag, 0) is not None:
            bid = int(_valor_linha(ag, 0))
            cursor.execute(
                "UPDATE usuarios SET barbearia_id = ? WHERE id = ?",
                (bid, user_id),
            )
            return bid

    if role == "admin" and email and _coluna_existe(cursor, "barbearias", "email"):
        cursor.execute(
            """
            SELECT id FROM barbearias
            WHERE LOWER(TRIM(email)) = LOWER(?) LIMIT 1
            """,
            (email,),
        )
        b = cursor.fetchone()
        if b and _valor_linha(b, 0) is not None:
            bid = int(_valor_linha(b, 0))
            cursor.execute(
                "UPDATE usuarios SET barbearia_id = ? WHERE id = ?",
                (bid, user_id),
            )
            return bid

    backfill_barbearia_id(cursor)
    cursor.execute("SELECT barbearia_id FROM usuarios WHERE id = ?", (user_id,))
    row2 = cursor.fetchone()
    if row2 and _valor_linha(row2, 0) is not None:
        return int(_valor_linha(row2, 0))
    return None


def criar_tabelas_core(cursor):
    """CREATE TABLE IF NOT EXISTS — compatível com Turso (um statement por vez)."""
    ddls = (
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
        )
        """,
        """
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
            status_trial TEXT DEFAULT 'trialing'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS tb_configuracoes (
            chave TEXT PRIMARY KEY,
            valor TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS financeiro (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER,
            descricao TEXT,
            valor REAL NOT NULL DEFAULT 0,
            tipo_transacao TEXT NOT NULL,
            categoria TEXT,
            servico TEXT,
            produto TEXT,
            tags TEXT,
            barbeiro TEXT,
            profissional_id INTEGER,
            agendamento_id INTEGER,
            data TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS Clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER,
            Nome TEXT NOT NULL,
            Dia TEXT NOT NULL,
            Hora TEXT NOT NULL,
            Servico TEXT,
            valor REAL,
            Whatsapp TEXT,
            barbeiro_id INTEGER,
            status TEXT NOT NULL DEFAULT 'Agendado'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS servicos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            ativo INTEGER DEFAULT 1,
            ordem INTEGER DEFAULT 0,
            preco REAL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS horarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER NOT NULL,
            hora TEXT NOT NULL,
            ativo INTEGER DEFAULT 1,
            ordem INTEGER DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS tb_galeria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER NOT NULL,
            categoria TEXT NOT NULL,
            caminho_foto TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS assinaturas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER NOT NULL UNIQUE,
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            plano_status TEXT NOT NULL DEFAULT 'trialing',
            data_fim_trial TEXT,
            data_fim_plano TEXT,
            criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )
    for ddl in ddls:
        try:
            cursor.execute(ddl)
        except Exception as exc:
            print(f"criar_tabelas_core: {exc}")


def garantir_comissoes_defaults(cursor, barbearia_id=None):
    """
    Garante chaves de comissão em tb_configuracoes (60% serviço, 10% produto).
    Se a tabela estiver vazia ou faltar chave para o tenant, insere padrões.
    """
    criar_tabelas_core(cursor)
    defaults_globais = (
        ("fin_pct_servico_padrao", "60"),
        ("fin_pct_produto_padrao", "10"),
    )
    for chave, valor in defaults_globais:
        try:
            cursor.execute(
                "SELECT 1 FROM tb_configuracoes WHERE chave = ? LIMIT 1", (chave,)
            )
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO tb_configuracoes (chave, valor) VALUES (?, ?)",
                    (chave, valor),
                )
        except Exception as exc:
            print(f"garantir_comissoes_defaults global ({chave}): {exc}")

    barbearia_ids = []
    if barbearia_id is not None:
        barbearia_ids = [int(barbearia_id)]
    else:
        try:
            cursor.execute("SELECT id FROM barbearias ORDER BY id")
            barbearia_ids = [
                int(_valor_linha(r, 0, "id"))
                for r in (cursor.fetchall() or [])
                if _valor_linha(r, 0, "id") is not None
            ]
        except Exception as exc:
            print(f"garantir_comissoes_defaults listar barbearias: {exc}")

    for bid in barbearia_ids:
        for sufixo, valor_pad in (("servico", "60"), ("produto", "10")):
            chave = f"fin_pct_{sufixo}_{bid}"
            try:
                cursor.execute(
                    "SELECT 1 FROM tb_configuracoes WHERE chave = ? LIMIT 1",
                    (chave,),
                )
                if not cursor.fetchone():
                    cursor.execute(
                        "INSERT INTO tb_configuracoes (chave, valor) VALUES (?, ?)",
                        (chave, valor_pad),
                    )
            except Exception as exc:
                print(f"garantir_comissoes_defaults ({chave}): {exc}")


def garantir_banco_pronto(conn=None):
    """Schema completo + migrações + defaults de comissão (Turso ou SQLite)."""
    fecha = conn is None
    if fecha:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        criar_tabelas_core(cursor)
        ensure_schema_migrations(cursor)
        garantir_comissoes_defaults(cursor)
        safe_commit(conn)
        return cursor
    finally:
        if fecha:
            safe_close(conn)


def _init_schema_sqlserver(conn=None):
    """DDL e ALTER mínimos para SQL Server (financeiro, Clientes, tb_configuracoes)."""
    fecha = conn is None
    if fecha:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        blocos = (
            """
            IF OBJECT_ID('dbo.tb_configuracoes', 'U') IS NULL
            CREATE TABLE dbo.tb_configuracoes (
                chave NVARCHAR(200) NOT NULL PRIMARY KEY,
                valor NVARCHAR(MAX) NULL
            );
            """,
            """
            IF OBJECT_ID('dbo.financeiro', 'U') IS NULL
            CREATE TABLE dbo.financeiro (
                id INT IDENTITY(1,1) PRIMARY KEY,
                barbearia_id INT NULL,
                descricao NVARCHAR(500) NULL,
                valor FLOAT NOT NULL DEFAULT 0,
                tipo_transacao NVARCHAR(50) NOT NULL,
                categoria NVARCHAR(50) NULL,
                servico NVARCHAR(500) NULL,
                produto NVARCHAR(500) NULL,
                tags NVARCHAR(500) NULL,
                barbeiro NVARCHAR(200) NULL,
                profissional_id INT NULL,
                agendamento_id INT NULL,
                data DATETIME NULL DEFAULT GETDATE()
            );
            """,
            """
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
            IF COL_LENGTH('dbo.financeiro', 'tags') IS NULL
                ALTER TABLE dbo.financeiro ADD tags NVARCHAR(500) NULL;
            """,
            """
            IF COL_LENGTH('dbo.Clientes', 'barbearia_id') IS NULL
                ALTER TABLE dbo.Clientes ADD barbearia_id INT NULL;
            IF COL_LENGTH('dbo.Clientes', 'valor') IS NULL
                ALTER TABLE dbo.Clientes ADD valor FLOAT NULL;
            """,
        )
        for ddl in blocos:
            try:
                cursor.execute(ddl)
            except Exception as exc:
                print(f"_init_schema_sqlserver: {exc}")
        garantir_comissoes_defaults(cursor)
        safe_commit(conn)
        return cursor
    finally:
        if fecha:
            safe_close(conn)


def initialize_database_schema(conn=None):
    """Detecta backend e aplica CREATE/ALTER adequados (Turso/SQLite ou T-SQL)."""
    try:
        from db_adapter import get_backend

        backend = get_backend()
    except Exception:
        backend = "sqlite"
    if backend == "sqlserver":
        return _init_schema_sqlserver(conn)
    return ensure_database_schema_sqlite(conn)


def ensure_database_schema_sqlite(conn=None):
    """
    Garante schema mínimo (SQLite / Turso).
    Verifica tabelas críticas e aplica migrações automáticas (ex.: financeiro.tags).
    """
    fecha = conn is None
    if fecha:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        criar_tabelas_core(cursor)
        ensure_schema_migrations(cursor)

        tabelas_criticas = ("financeiro", "Clientes", "tb_configuracoes")
        for tabela in tabelas_criticas:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                (tabela,),
            )
            if not cursor.fetchone():
                raise sqlite3.OperationalError(
                    f"Tabela obrigatória ausente após migração automática: {tabela}"
                )

        colunas_criticas = (
            ("financeiro", "tags", "TEXT"),
            ("financeiro", "categoria", "TEXT"),
            ("Clientes", "barbearia_id", "INTEGER"),
        )
        for tabela, coluna, tipo_sql in colunas_criticas:
            if not _coluna_existe(cursor, tabela, coluna):
                cursor.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo_sql}")

        garantir_comissoes_defaults(cursor)
        safe_commit(conn)
        return cursor
    finally:
        if fecha:
            safe_close(conn)


def ensure_database_schema(conn=None):
    """Alias de compatibilidade — delega para initialize_database_schema."""
    return initialize_database_schema(conn)


def ensure_schema_migrations_conn(conn):
    """Executa migrações e confirma no banco (obrigatório no Turso após ALTER)."""
    cursor = conn.cursor()
    criar_tabelas_core(cursor)
    ensure_schema_migrations(cursor)
    safe_commit(conn)
    return cursor


def ensure_schema_migrations(cursor):
    """Adiciona colunas e tabelas novas em bancos já existentes (SQLite / Turso)."""
    # Clientes = tabela de agendamentos (não existe tabela "agenda" separada).
    # financeiro.categoria distingue Serviço | Produto | Despesa; colunas servico/produto
    # guardam o nome digitado quando existirem no banco legado.
    alteracoes = [
        ("usuarios", "data_cadastro", "TEXT"),
        ("usuarios", "status_trial", "TEXT"),
        ("usuarios", "barbearia_id", "INTEGER"),
        ("Clientes", "barbearia_id", "INTEGER"),
        ("Clientes", "valor", "REAL"),
        ("financeiro", "barbearia_id", "INTEGER"),
        ("financeiro", "agendamento_id", "INTEGER"),
        ("financeiro", "categoria", "TEXT"),
        ("financeiro", "valor", "REAL"),
        ("financeiro", "servico", "TEXT"),
        ("financeiro", "produto", "TEXT"),
        ("financeiro", "tags", "TEXT"),
        ("servicos", "preco", "REAL"),
        ("barbearias", "ramo_atividade", "TEXT"),
        ("assinaturas", "email_trial_lembrete_em", "TEXT"),
    ]
    for tabela, coluna, tipo_sql in alteracoes:
        try:
            if not _coluna_existe(cursor, tabela, coluna):
                cursor.execute(
                    f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo_sql}"
                )
        except Exception as exc:
            print(f"ensure_schema_migrations ({tabela}.{coluna}): {exc}")

    for ddl_tabela in (
        """
        CREATE TABLE IF NOT EXISTS servicos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            ativo INTEGER DEFAULT 1,
            ordem INTEGER DEFAULT 0,
            FOREIGN KEY (barbearia_id) REFERENCES barbearias(id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS horarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barbearia_id INTEGER NOT NULL,
            hora TEXT NOT NULL,
            ativo INTEGER DEFAULT 1,
            ordem INTEGER DEFAULT 0,
            FOREIGN KEY (barbearia_id) REFERENCES barbearias(id)
        )
        """,
    ):
        try:
            cursor.execute(ddl_tabela)
        except Exception as exc:
            print(f"ensure_schema_migrations (CREATE TABLE): {exc}")

    backfill_barbearia_id(cursor)

    indices_tenant = (
        ("IX_usuarios_barbearia", "usuarios", "barbearia_id"),
        ("IX_Clientes_barbearia", "Clientes", "barbearia_id"),
        ("IX_financeiro_barbearia", "financeiro", "barbearia_id"),
        ("IX_servicos_barbearia", "servicos", "barbearia_id"),
        ("IX_horarios_barbearia", "horarios", "barbearia_id"),
    )
    for nome_idx, tabela_idx, coluna_idx in indices_tenant:
        if not _coluna_existe(cursor, tabela_idx, coluna_idx):
            continue
        try:
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS {nome_idx} ON {tabela_idx}({coluna_idx})"
            )
        except Exception as exc:
            print(f"ensure_schema_migrations (índice {nome_idx}): {exc}")


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
            valor REAL,
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
            categoria TEXT,
            servico TEXT,
            produto TEXT,
            tags TEXT,
            barbeiro TEXT,
            profissional_id INTEGER,
            agendamento_id INTEGER,
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

        criar_tabelas_core(cursor)
        ensure_schema_migrations(cursor)
        garantir_comissoes_defaults(cursor)

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

        garantir_comissoes_defaults(cursor)
        safe_commit(conn)
        safe_close(conn)
        print("init_database: OK")

    except Exception as exc:
        print("=== Erro init_database (app continuará carregando) ===")
        print(f"init_database: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        print("=== fim Erro init_database ===")


def sql_now():
    """Expressão SQL para data/hora atual (SQLite / Turso)."""
    return "CURRENT_TIMESTAMP"
