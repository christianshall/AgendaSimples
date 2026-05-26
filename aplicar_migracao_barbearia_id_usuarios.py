"""
Migração: coluna barbearia_id em usuarios + backfill (SQLite local ou Turso).

Uso:
  python aplicar_migracao_barbearia_id_usuarios.py

Requer TURSO_DATABASE_URL e TURSO_AUTH_TOKEN no ambiente (ou .env na raiz).
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

from database import (
    _coluna_existe,
    _turso_credentials,
    backfill_barbearia_id,
    ensure_schema_migrations,
    get_connection,
    safe_close,
    safe_commit,
    vincular_usuario_barbearia,
)


def _relatorio(cursor):
    if not _coluna_existe(cursor, "usuarios", "barbearia_id"):
        print("ERRO: coluna barbearia_id ainda não existe em usuarios.")
        return

    cursor.execute(
        """
        SELECT id, nome, email, role, barbearia_id
        FROM usuarios
        ORDER BY id
        """
    )
    rows = cursor.fetchall() or []
    print("\n--- usuarios após migração ---")
    orfaos = 0
    for r in rows:
        uid = r[0] if not hasattr(r, "keys") else r["id"]
        nome = r[1] if not hasattr(r, "keys") else r["nome"]
        email = r[2] if not hasattr(r, "keys") else r["email"]
        role = r[3] if not hasattr(r, "keys") else r["role"]
        bid = r[4] if not hasattr(r, "keys") else r["barbearia_id"]
        status = "OK" if bid is not None else "ÓRFÃO"
        if bid is None:
            orfaos += 1
        print(f"  id={uid} nome={nome!r} role={role} barbearia_id={bid} [{status}]")

    if orfaos:
        print(f"\nAtenção: {orfaos} usuário(s) ainda sem barbearia_id.")
    else:
        print("\nTodos os usuários possuem barbearia_id.")


def main():
    url, token = _turso_credentials()
    destino = "Turso" if url and token else f"SQLite ({os.environ.get('SQLITE_DATABASE_PATH', 'agenda.db')})"
    print(f"Destino: {destino}")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        print("1) ensure_schema_migrations (ADD COLUMN barbearia_id se faltar)...")
        ensure_schema_migrations(cursor)

        print("2) backfill_barbearia_id...")
        backfill_barbearia_id(cursor)

        print("3) vincular usuários órfãos (incl. profissionais como Davi)...")
        cursor.execute(
            "SELECT id, nome FROM usuarios WHERE barbearia_id IS NULL"
        )
        for row in cursor.fetchall() or []:
            uid = row[0]
            nome = row[1] if len(row) > 1 else "?"
            bid = vincular_usuario_barbearia(cursor, uid)
            print(f"   usuário {uid} ({nome}): barbearia_id -> {bid}")

        cursor.execute(
            "SELECT id, nome, barbearia_id FROM usuarios WHERE LOWER(nome) LIKE '%davi%'"
        )
        for row in cursor.fetchall() or []:
            uid, nome, bid = row[0], row[1], row[2]
            if bid is None:
                bid = vincular_usuario_barbearia(cursor, uid)
            print(f"   Davi id={uid} nome={nome!r} barbearia_id={bid}")

        safe_commit(conn)
        _relatorio(cursor)
        print("\nMigração concluída com sucesso.")
    except Exception as exc:
        print(f"Falha na migração: {exc}")
        raise
    finally:
        safe_close(conn)


if __name__ == "__main__":
    main()
