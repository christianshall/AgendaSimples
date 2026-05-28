"""Catálogo de serviços e produtos por estabelecimento (agenda + financeiro)."""
from __future__ import annotations

from typing import Any, Optional

import db_adapter as db
from database import PRODUTOS_PADRAO, SERVICOS_PADRAO, obter_id_inserido


def _ifnull_preco(expr: str = "preco") -> str:
    if db.get_backend() == "sqlserver":
        return f"ISNULL({expr}, 0)"
    return f"IFNULL({expr}, 0)"


def _ifnull_ativo(expr: str = "ativo") -> str:
    if db.get_backend() == "sqlserver":
        return f"ISNULL({expr}, 1)"
    return f"IFNULL({expr}, 1)"


def _preco_padrao_servico(nome: str) -> float:
    padroes = {
        "Corte": 45.0,
        "Corte e Barba": 65.0,
        "Corte + Sobrancelha": 55.0,
        "Barba": 35.0,
    }
    return float(padroes.get((nome or "").strip(), 0.0))


def _row_item(row, com_id: bool = True) -> dict[str, Any]:
    if com_id:
        return {
            "id": db.row_get(row, "id", index=0),
            "nome": (db.row_get(row, "nome", index=1) or "").strip(),
            "preco": float(db.row_get(row, "preco", index=2) or 0),
            "ativo": int(db.row_get(row, "ativo", index=3) or 1),
            "ordem": int(db.row_get(row, "ordem", index=4) or 0),
        }
    return {
        "nome": (db.row_get(row, "nome", index=0) or "").strip(),
        "preco": float(db.row_get(row, "preco", index=1) or 0),
    }


def _tem_tabela(nome: str, *, conn=None) -> bool:
    return db.coluna_existe(nome, "barbearia_id", conn=conn)


def listar_servicos(
    barbearia_id: int, *, apenas_ativos: bool = False, conn=None
) -> list[dict[str, Any]]:
    if not _tem_tabela("servicos", conn=conn):
        return [
            {"id": None, "nome": n, "preco": _preco_padrao_servico(n), "ativo": 1, "ordem": i}
            for i, n in enumerate(SERVICOS_PADRAO)
        ]
    ativo_expr = _ifnull_ativo("ativo")
    sql = f"""
        SELECT id, nome, {_ifnull_preco('preco')} AS preco, {ativo_expr} AS ativo, ordem
        FROM servicos
        WHERE barbearia_id = ?
    """
    params: list[Any] = [int(barbearia_id)]
    if apenas_ativos:
        sql += f" AND {ativo_expr} = 1"
    sql += " ORDER BY ordem, nome"
    rows = db.execute_query(sql, tuple(params), conn=conn)
    itens = [_row_item(r) for r in rows or []]
    if itens:
        return itens
    return [
        {"id": None, "nome": n, "preco": _preco_padrao_servico(n), "ativo": 1, "ordem": i}
        for i, n in enumerate(SERVICOS_PADRAO)
    ]


def listar_produtos(
    barbearia_id: int, *, apenas_ativos: bool = False, conn=None
) -> list[dict[str, Any]]:
    if not _tem_tabela("produtos", conn=conn):
        return [
            {"id": None, "nome": n, "preco": float(p), "ativo": 1, "ordem": i}
            for i, (n, p) in enumerate(PRODUTOS_PADRAO)
        ]
    ativo_expr = _ifnull_ativo("ativo")
    sql = f"""
        SELECT id, nome, {_ifnull_preco('preco')} AS preco, {ativo_expr} AS ativo, ordem
        FROM produtos
        WHERE barbearia_id = ?
    """
    params: list[Any] = [int(barbearia_id)]
    if apenas_ativos:
        sql += f" AND {ativo_expr} = 1"
    sql += " ORDER BY ordem, nome"
    rows = db.execute_query(sql, tuple(params), conn=conn)
    itens = [_row_item(r) for r in rows or []]
    if itens:
        return itens
    return [
        {"id": None, "nome": n, "preco": float(p), "ativo": 1, "ordem": i}
        for i, (n, p) in enumerate(PRODUTOS_PADRAO)
    ]


def listar_servicos_agenda(barbearia_id: int, *, conn=None) -> list[dict[str, Any]]:
    """Somente ativos, formato {nome, preco} para selects."""
    return [
        {"nome": s["nome"], "preco": float(s.get("preco") or 0)}
        for s in listar_servicos(barbearia_id, apenas_ativos=True, conn=conn)
    ]


def listar_produtos_agenda(barbearia_id: int, *, conn=None) -> list[dict[str, Any]]:
    return [
        {"nome": p["nome"], "preco": float(p.get("preco") or 0)}
        for p in listar_produtos(barbearia_id, apenas_ativos=True, conn=conn)
    ]


def obter_preco_servico(barbearia_id: int, nome_servico: str, *, conn=None) -> float:
    nome = (nome_servico or "").strip()
    if not nome:
        return 0.0
    if _tem_tabela("servicos", conn=conn) and db.coluna_existe(
        "servicos", "preco", conn=conn
    ):
        row = db.execute_query(
            f"""
            SELECT {_ifnull_preco('preco')} FROM servicos
            WHERE barbearia_id = ? AND LOWER(TRIM(nome)) = LOWER(?)
              AND {_ifnull_ativo('ativo')} = 1
            LIMIT 1
            """,
            (int(barbearia_id), nome),
            fetch="one",
            conn=conn,
        )
        if row is not None:
            return float(db.row_get(row, "preco", index=0) or 0)
    return _preco_padrao_servico(nome)


def obter_preco_produto(barbearia_id: int, nome_produto: str, *, conn=None) -> float:
    nome = (nome_produto or "").strip()
    if not nome:
        return 0.0
    if _tem_tabela("produtos", conn=conn):
        row = db.execute_query(
            f"""
            SELECT {_ifnull_preco('preco')} FROM produtos
            WHERE barbearia_id = ? AND LOWER(TRIM(nome)) = LOWER(?)
              AND {_ifnull_ativo('ativo')} = 1
            LIMIT 1
            """,
            (int(barbearia_id), nome),
            fetch="one",
            conn=conn,
        )
        if row is not None:
            return float(db.row_get(row, "preco", index=0) or 0)
    for n, p in PRODUTOS_PADRAO:
        if n.strip().lower() == nome.lower():
            return float(p)
    return 0.0


def _proxima_ordem(cursor, tabela: str, barbearia_id: int) -> int:
    cursor.execute(
        f"SELECT COALESCE(MAX(ordem), -1) + 1 FROM {tabela} WHERE barbearia_id = ?",
        (int(barbearia_id),),
    )
    row = cursor.fetchone()
    return int((row[0] if row else 0) or 0)


def salvar_servico(
    barbearia_id: int,
    nome: str,
    preco: float,
    *,
    item_id: Optional[int] = None,
    ativo: int = 1,
    conn=None,
) -> int:
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Nome do serviço é obrigatório.")
    preco = max(0.0, float(preco or 0))
    ativo = 1 if int(ativo) else 0

    def _run(c):
        cur = db.cursor(c)
        if item_id:
            cur.execute(
                """
                UPDATE servicos SET nome = ?, preco = ?, ativo = ?
                WHERE id = ? AND barbearia_id = ?
                """,
                (nome, preco, ativo, int(item_id), int(barbearia_id)),
            )
            return int(item_id)
        ordem = _proxima_ordem(cur, "servicos", barbearia_id)
        cur.execute(
            """
            INSERT INTO servicos (barbearia_id, nome, ativo, ordem, preco)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(barbearia_id), nome, ativo, ordem, preco),
        )
        lid = cur.lastrowid
        if lid not in (None, 0):
            return int(lid)
        return int(
            obter_id_inserido(
                cur,
                """
                SELECT id FROM servicos
                WHERE barbearia_id = ? AND nome = ?
                ORDER BY id DESC LIMIT 1
                """,
                (int(barbearia_id), nome),
            )
            or 0
        )

    if conn is not None:
        return _run(conn)
    with db.connection_scope() as c:
        return _run(c)


def salvar_produto(
    barbearia_id: int,
    nome: str,
    preco: float,
    *,
    item_id: Optional[int] = None,
    ativo: int = 1,
    conn=None,
) -> int:
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Nome do produto é obrigatório.")
    preco = max(0.0, float(preco or 0))
    ativo = 1 if int(ativo) else 0

    def _run(c):
        cur = db.cursor(c)
        if item_id:
            cur.execute(
                """
                UPDATE produtos SET nome = ?, preco = ?, ativo = ?
                WHERE id = ? AND barbearia_id = ?
                """,
                (nome, preco, ativo, int(item_id), int(barbearia_id)),
            )
            return int(item_id)
        ordem = _proxima_ordem(cur, "produtos", barbearia_id)
        cur.execute(
            """
            INSERT INTO produtos (barbearia_id, nome, ativo, ordem, preco)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(barbearia_id), nome, ativo, ordem, preco),
        )
        lid = cur.lastrowid
        if lid not in (None, 0):
            return int(lid)
        return int(
            obter_id_inserido(
                cur,
                """
                SELECT id FROM produtos
                WHERE barbearia_id = ? AND nome = ?
                ORDER BY id DESC LIMIT 1
                """,
                (int(barbearia_id), nome),
            )
            or 0
        )

    if conn is not None:
        return _run(conn)
    with db.connection_scope() as c:
        return _run(c)


def excluir_servico(item_id: int, barbearia_id: int, *, conn=None) -> None:
    db.execute_write(
        "UPDATE servicos SET ativo = 0 WHERE id = ? AND barbearia_id = ?",
        (int(item_id), int(barbearia_id)),
        conn=conn,
    )


def excluir_produto(item_id: int, barbearia_id: int, *, conn=None) -> None:
    db.execute_write(
        "UPDATE produtos SET ativo = 0 WHERE id = ? AND barbearia_id = ?",
        (int(item_id), int(barbearia_id)),
        conn=conn,
    )


def seed_catalogo_padrao(cursor, barbearia_id: int) -> None:
    """Garante serviços, produtos e horários padrão para estabelecimento novo."""
    from database import seed_servicos_horarios_padrao

    seed_servicos_horarios_padrao(cursor, barbearia_id)

    if not _tem_tabela("produtos"):
        return
    cursor.execute(
        "SELECT COUNT(*) FROM produtos WHERE barbearia_id = ?",
        (barbearia_id,),
    )
    if (cursor.fetchone() or [0])[0] == 0:
        for ordem, (nome, preco) in enumerate(PRODUTOS_PADRAO):
            cursor.execute(
                """
                INSERT INTO produtos (barbearia_id, nome, ativo, ordem, preco)
                VALUES (?, ?, 1, ?, ?)
                """,
                (barbearia_id, nome, ordem, float(preco)),
            )
