#!/usr/bin/env python3
"""Testa conexão Turso/libSQL — use: python testar_turso.py (com .env ou vars da Vercel)."""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

def main():
    from database import (
        _turso_credentials,
        get_connection,
        safe_close,
        usar_banco_remoto,
        is_producao_remota,
    )

    url, token = _turso_credentials()
    print("=== Diagnóstico Turso / AgendaSimples ===\n")
    print(f"VERCEL / produção remota: {bool(os.environ.get('VERCEL'))} / {is_producao_remota()}")
    print(f"usar_banco_remoto(): {usar_banco_remoto()}")
    print(f"TURSO_DATABASE_URL definida: {bool(url)}")
    if url:
        print(f"  URL (prefixo): {url[:40]}...")
    print(f"TURSO_AUTH_TOKEN definido: {bool(token)}")
    if token:
        print(f"  Token (tamanho): {len(token)} caracteres")

    if not url or not token:
        print("\n[ERRO] Credenciais Turso ausentes neste ambiente.")
        print("   Local: copie .env.example para .env e preencha TURSO_*")
        print("   Vercel: Settings → Environment Variables")
        return 1

    print("\nConectando...")
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 AS ok")
        row = cur.fetchone()
        ok = row[0] if row and not hasattr(row, "keys") else (row["ok"] if row else None)
        print(f"[OK] SELECT 1 -> {ok}")

        tabelas = ("barbearias", "usuarios", "financeiro", "Clientes", "assinaturas")
        for t in tabelas:
            try:
                cur.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name = ?",
                    (t,),
                )
                r = cur.fetchone()
                existe = (r[0] if r else 0) > 0
                if existe:
                    cur.execute(f"SELECT COUNT(*) FROM {t}")
                    cnt = cur.fetchone()[0]
                    print(f"[OK] Tabela {t}: {cnt} registro(s)")
                else:
                    print(f"[AVISO] Tabela {t}: nao encontrada")
            except Exception as e:
                print(f"[FALHA] Tabela {t}: {e}")

        safe_close(conn)
        print("\n[OK] Turso respondendo corretamente neste ambiente.")
        return 0
    except Exception as exc:
        print(f"\n[ERRO] Falha na conexao: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
