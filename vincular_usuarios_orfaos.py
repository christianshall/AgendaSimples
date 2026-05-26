"""
Vincula usuários com barbearia_id NULL (ex.: Davi) no Turso ou SQLite local.

Uso:
  python vincular_usuarios_orfaos.py
  python vincular_usuarios_orfaos.py --email davi@exemplo.com --barbearia-id 3
  python vincular_usuarios_orfaos.py --nome Davi --barbearia-id 3

Variáveis: TURSO_DATABASE_URL, TURSO_AUTH_TOKEN (ou agenda.db local).
"""
import argparse
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
    _turso_credentials,
    get_connection,
    safe_close,
    safe_commit,
    vincular_usuarios_orfaos_por_email_nome,
)


def vincular_manual(cursor, email=None, nome=None, barbearia_id=None):
    if barbearia_id is None:
        return 0
    bid = int(barbearia_id)
    if email:
        cursor.execute(
            """
            UPDATE usuarios SET barbearia_id = ?
            WHERE barbearia_id IS NULL AND LOWER(TRIM(email)) = LOWER(?)
            """,
            (bid, email.strip()),
        )
        return cursor.rowcount if hasattr(cursor, "rowcount") else 0
    if nome:
        cursor.execute(
            """
            UPDATE usuarios SET barbearia_id = ?
            WHERE barbearia_id IS NULL AND LOWER(TRIM(nome)) LIKE LOWER(?)
            """,
            (bid, f"%{nome.strip()}%"),
        )
        return cursor.rowcount if hasattr(cursor, "rowcount") else 0
    return 0


def relatorio(cursor):
    cursor.execute(
        """
        SELECT id, nome, email, role, barbearia_id
        FROM usuarios
        ORDER BY id
        """
    )
    print("\n--- usuarios ---")
    for row in cursor.fetchall() or []:
        print(
            f"  id={row[0]} nome={row[1]!r} email={row[2]!r} "
            f"role={row[3]} barbearia_id={row[4]}"
        )


def main():
    parser = argparse.ArgumentParser(description="Vincular usuarios órfãos ao tenant")
    parser.add_argument("--email", help="E-mail exato do usuário (ex.: Davi)")
    parser.add_argument("--nome", help="Parte do nome (ex.: Davi)")
    parser.add_argument("--barbearia-id", type=int, help="ID da barbearia destino")
    args = parser.parse_args()

    url, token = _turso_credentials()
    destino = "Turso" if url and token else "SQLite local"
    print(f"Destino: {destino}")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        if args.email or args.nome:
            if not args.barbearia_id:
                print("Informe --barbearia-id para vínculo manual.")
                sys.exit(1)
            vincular_manual(cursor, args.email, args.nome, args.barbearia_id)
            print("Vínculo manual aplicado.")

        restantes = vincular_usuarios_orfaos_por_email_nome(cursor)
        safe_commit(conn)
        relatorio(cursor)
        if restantes > 0:
            print(
                f"\nAinda há {restantes} usuário(s) sem barbearia_id. "
                "Use: python vincular_usuarios_orfaos.py --nome Davi --barbearia-id <ID>"
            )
            sys.exit(1)
        print("\nTodos os usuários vinculados.")
    finally:
        safe_close(conn)


if __name__ == "__main__":
    main()
