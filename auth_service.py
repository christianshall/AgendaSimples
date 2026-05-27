"""Autenticação e sessão — queries via db_adapter (SQLite/Turso ou SQL Server)."""
from __future__ import annotations

from typing import Any, Optional

import db_adapter as db
from database import _valor_linha, obter_id_inserido, vincular_usuario_barbearia


def parse_usuario_login(row) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    if isinstance(row, dict):
        return {
            "id": row.get("id"),
            "nome": row.get("nome"),
            "role": row.get("role"),
            "senha": row.get("senha"),
            "barbearia_id": row.get("barbearia_id"),
        }
    return {
        "id": _valor_linha(row, 0, "id"),
        "nome": _valor_linha(row, 1, "nome"),
        "role": _valor_linha(row, 2, "role") or _valor_linha(row, nome="role"),
        "senha": _valor_linha(row, 3, "senha") or _valor_linha(row, nome="senha"),
        "barbearia_id": _valor_linha(row, 4, "barbearia_id")
        or _valor_linha(row, nome="barbearia_id"),
    }


def buscar_usuario_por_email(email: str, *, conn=None) -> Optional[dict[str, Any]]:
    row = db.execute_query(
        """
        SELECT id, nome, role, senha, barbearia_id
        FROM usuarios
        WHERE LOWER(TRIM(email)) = LOWER(?)
        """,
        (email,),
        fetch="one",
        conn=conn,
    )
    return parse_usuario_login(row)


def barbearia_id_do_usuario(
    user_id: int, *, conn=None, tentar_corrigir: bool = True
) -> Optional[int]:
    row = db.execute_query(
        "SELECT barbearia_id FROM usuarios WHERE id = ?",
        (int(user_id),),
        fetch="one",
        conn=conn,
    )
    if not row:
        return None
    bid = db.row_get(row, "barbearia_id", index=0)
    if bid is None and tentar_corrigir and conn is not None:
        bid = vincular_usuario_barbearia(db.cursor(conn), int(user_id))
    elif bid is None and tentar_corrigir:
        with db.connection_scope() as c:
            bid = vincular_usuario_barbearia(db.cursor(c), int(user_id))
    if bid is None:
        return None
    return int(bid)


def email_ja_cadastrado(email: str, ignorar_id: Optional[int] = None, *, conn=None) -> bool:
    if ignorar_id:
        row = db.execute_query(
            """
            SELECT id FROM usuarios
            WHERE LOWER(TRIM(email)) = LOWER(?) AND id <> ?
            LIMIT 1
            """,
            (email, ignorar_id),
            fetch="one",
            conn=conn,
        )
    else:
        row = db.execute_query(
            """
            SELECT id FROM usuarios
            WHERE LOWER(TRIM(email)) = LOWER(?)
            LIMIT 1
            """,
            (email,),
            fetch="one",
            conn=conn,
        )
    return row is not None


def buscar_barbearia_por_id(barbearia_id: int, *, conn=None) -> Optional[dict]:
    row = db.execute_query(
        "SELECT id, nome, slug FROM barbearias WHERE id = ?",
        (barbearia_id,),
        fetch="one",
        conn=conn,
    )
    if not row:
        return None
    return {
        "id": db.row_get(row, "id", index=0),
        "nome": db.row_get(row, "nome", index=1),
        "slug": db.row_get(row, "slug", index=2),
    }


def buscar_barbearia_por_slug(slug: str, *, conn=None) -> Optional[dict]:
    slug = (slug or "").strip()
    if not slug:
        return None
    row = db.execute_query(
        """
        SELECT id, nome, slug FROM barbearias
        WHERE LOWER(TRIM(slug)) = LOWER(?)
        LIMIT 1
        """,
        (slug,),
        fetch="one",
        conn=conn,
    )
    if not row:
        return None
    return {
        "id": db.row_get(row, "id", index=0),
        "nome": db.row_get(row, "nome", index=1),
        "slug": db.row_get(row, "slug", index=2),
    }


def buscar_barbearia_admin_por_email(email: str, *, conn=None) -> Optional[dict]:
    row = db.execute_query(
        """
        SELECT id, nome, slug FROM barbearias
        WHERE LOWER(TRIM(email)) = LOWER(?)
        LIMIT 1
        """,
        (email,),
        fetch="one",
        conn=conn,
    )
    if not row:
        return None
    return {
        "id": db.row_get(row, "id", index=0),
        "nome": db.row_get(row, "nome", index=1),
        "slug": db.row_get(row, "slug", index=2),
    }


def atualizar_usuario_barbearia(user_id: int, barbearia_id: int, *, conn=None) -> None:
    db.execute_write(
        "UPDATE usuarios SET barbearia_id = ? WHERE id = ?",
        (int(barbearia_id), int(user_id)),
        conn=conn,
    )


def atualizar_senha_usuario(user_id: int, senha_hash: str, *, conn=None) -> None:
    db.execute_write(
        "UPDATE usuarios SET senha = ? WHERE id = ?",
        (senha_hash, int(user_id)),
        conn=conn,
    )


def resolver_barbearia(identificador: str, *, conn=None) -> Optional[dict]:
    identificador = (identificador or "").strip()
    if not identificador:
        return None
    if identificador.isdigit():
        return buscar_barbearia_por_id(int(identificador), conn=conn)
    return buscar_barbearia_por_slug(identificador, conn=conn)


def slug_ou_email_ja_usado(slug: str, email: str, *, conn=None) -> bool:
    row = db.execute_query(
        """
        SELECT id FROM barbearias
        WHERE slug = ? OR LOWER(TRIM(email)) = LOWER(?)
        LIMIT 1
        """,
        (slug, email),
        fetch="one",
        conn=conn,
    )
    return row is not None


def obter_id_barbearia_apos_insert(email: str, *, conn) -> Optional[int]:
    return obter_id_inserido(
        db.cursor(conn),
        "SELECT id FROM barbearias WHERE LOWER(TRIM(email)) = LOWER(?) ORDER BY id DESC LIMIT 1",
        (email,),
    )


def obter_id_usuario_apos_insert(email: str, *, conn) -> Optional[int]:
    return obter_id_inserido(
        db.cursor(conn),
        """
        SELECT id FROM usuarios
        WHERE LOWER(TRIM(email)) = LOWER(?)
        ORDER BY id DESC LIMIT 1
        """,
        (email,),
    )


def buscar_usuario_resumo_por_email(email: str, *, conn=None) -> Optional[dict]:
    row = db.execute_query(
        """
        SELECT id, nome, role, barbearia_id FROM usuarios
        WHERE LOWER(TRIM(email)) = LOWER(?)
        LIMIT 1
        """,
        (email,),
        fetch="one",
        conn=conn,
    )
    if not row:
        return None
    return {
        "id": db.row_get(row, "id", index=0),
        "nome": db.row_get(row, "nome", index=1),
        "role": db.row_get(row, "role", index=2),
        "barbearia_id": db.row_get(row, "barbearia_id", index=3),
    }
