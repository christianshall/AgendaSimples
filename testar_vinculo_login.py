"""
Testes rápidos: INSERT com barbearia_id e bloqueio sem vínculo.
  python testar_vinculo_login.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

# Força SQLite isolado para o teste
os.environ["TURSO_DATABASE_URL"] = ""
os.environ["TURSO_AUTH_TOKEN"] = ""
_db = os.path.join(tempfile.gettempdir(), "agenda_test_vinculo.db")
os.environ["SQLITE_DATABASE_PATH"] = _db
if os.path.isfile(_db):
    os.remove(_db)

from database import (
    _valor_linha,
    get_connection,
    init_database,
    safe_close,
    safe_commit,
)
from werkzeug.security import generate_password_hash


def _inserir_profissional_teste(cursor, nome, email, senha_hash, barbearia_id):
    """Réplica da validação de INSERT do app (sem carregar Flask)."""
    if barbearia_id is None:
        raise ValueError("barbearia_id obrigatório")
    bid = int(barbearia_id)
    if bid < 1:
        raise ValueError("barbearia_id inválido")
    cols = ["nome", "email", "senha", "role", "barbearia_id"]
    vals = [nome, email, senha_hash, "profissional", bid]
    ph = ", ".join("?" for _ in vals)
    cursor.execute(
        f"INSERT INTO usuarios ({', '.join(cols)}) VALUES ({ph})",
        tuple(vals),
    )


def _linha_usuario_login_teste(row):
    if row is None:
        return None
    return {
        "id": _valor_linha(row, 0, "id"),
        "nome": _valor_linha(row, 1, "nome"),
        "role": _valor_linha(row, 2, "role"),
        "senha": _valor_linha(row, 3, "senha"),
        "barbearia_id": _valor_linha(row, 4, "barbearia_id"),
    }


def test_insert_profissional_com_barbearia_id():
    init_database()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO barbearias (nome, slug, email, senha, plano_ativo, data_cadastro)
        VALUES ('Teste Shop', 'teste-shop', 'admin@test.com', 'x', 1, '2026-01-01')
        """
    )
    bid = cur.lastrowid
    _inserir_profissional_teste(
        cur,
        "Davi Teste",
        "davi@test.com",
        generate_password_hash("123456"),
        bid,
    )
    cur.execute(
        "SELECT barbearia_id FROM usuarios WHERE email = 'davi@test.com'"
    )
    row = cur.fetchone()
    assert row and int(row[0]) == int(bid), f"barbearia_id esperado {bid}, obteve {row}"
    safe_commit(conn)
    safe_close(conn)
    print("OK — INSERT profissional grava barbearia_id")


def test_insert_sem_barbearia_id_falha():
    conn = get_connection()
    cur = conn.cursor()
    try:
        _inserir_profissional_teste(
            cur, "X", "x@test.com", "hash", None
        )
        raise AssertionError("deveria falhar sem barbearia_id")
    except ValueError:
        print("OK — INSERT bloqueado sem barbearia_id")
    finally:
        safe_close(conn)


def test_parser_login_com_barbearia_id():
    class Row:
        def __init__(self):
            self._d = {
                "id": 9,
                "nome": "Davi",
                "role": "profissional",
                "senha": "h",
                "barbearia_id": 2,
            }

        def __getitem__(self, k):
            return self._d[k]

    parsed = _linha_usuario_login_teste(Row())
    assert parsed["barbearia_id"] == 2
    print("OK — parser login lê barbearia_id")


if __name__ == "__main__":
    test_insert_sem_barbearia_id_falha()
    test_parser_login_com_barbearia_id()
    test_insert_profissional_com_barbearia_id()
    print("\nTodos os testes passaram.")
